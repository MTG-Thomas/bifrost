# Kubernetes Production Readiness Review (PoC → Production)

Date: 2026-03-25  
Scope reviewed: `k8s/` manifests and deployment guidance in `DEPLOYMENT.md`.

## Executive recommendation

Use **AKS + managed data plane** for production (PostgreSQL Flexible Server + Azure Cache for Redis + Azure Blob S3 endpoint + Key Vault), and keep Bifrost workloads (API, client, worker, scheduler) on Kubernetes.

For an **in-cluster S3-compatible service that replaces MinIO**, use **SeaweedFS S3 Gateway** as the preferred default for production PoCs and short-term production if managed object storage is not yet available.

- Why: S3 compatibility, simpler footprint than Ceph RGW, and no MinIO dependency.
- Caveat: this should still be treated as an interim architecture compared with managed object storage durability/SLA.

## What already exists (good foundations)

1. **Kubernetes manifests are already present** for API/client/worker/scheduler/rabbitmq and include readiness/liveness probes and resource requests/limits.
2. **Scheduler singleton semantics are explicitly documented and encoded** (`replicas: 1`, `Recreate`).
3. **Storage abstraction is already S3-first in app config** (`BIFROST_S3_ENDPOINT_URL`, access/secret key, bucket, region).
4. **Azure migration intent already exists** in deployment docs, including AKS, Key Vault, ACR, and managed Azure services.

## Gaps that block production readiness

### 1) k8s baseline is incomplete as a standalone production bundle

Current `k8s/` includes API, client, worker, scheduler, rabbitmq, but **does not include Redis manifests** even though Redis is required by config/docs. Also, kustomization references a missing `coding-agent` deployment path.

Impact:
- `kubectl apply -k k8s/` cannot be treated as a complete production install.
- GitOps reconciliation will fail if missing resources remain referenced.

### 2) RabbitMQ persistence is not production-safe yet

RabbitMQ currently uses `emptyDir`, which means broker state is lost when the pod is rescheduled.

Impact:
- Potential message loss or queue reset on node disruption.
- Unsafe for production workflow durability expectations.

### 3) Secret/config workflow is PoC-oriented

Docs still describe creating Kubernetes secrets from `.env` and keeping substantial config in basic ConfigMaps/Secrets.

Impact:
- Rotation and audit are harder than with cloud-native secret integration.

### 4) Ingress/TLS/network policy are guidance-only, not operationalized

Ingress is documented as an example but not included as a hardened default with policy controls.

Impact:
- Teams can drift into inconsistent ingress, certificate, and east-west network posture.

### 5) No explicit k3s production profile documented

There is no repo documentation that defines a production k3s reference architecture (storage class, ingress choice, HA control plane, backup strategy, upgrade cadence).

Impact:
- "Current k3s setup" knowledge is implicit/tribal; hard to operate repeatably.

## Recommended target architecture

## A) Preferred production target (recommended)

- **Control plane / runtime**: AKS.
- **API/client/worker/scheduler**: in AKS.
- **PostgreSQL**: Azure Database for PostgreSQL Flexible Server.
- **Redis**: Azure Cache for Redis.
- **Queue**: either managed broker replacement after protocol validation, or RabbitMQ on AKS with persistent volumes.
- **Object storage**: Azure Blob S3-compatible endpoint (or another managed S3 API provider).
- **Secrets**: Azure Key Vault + CSI driver / External Secrets.

This aligns with existing `DEPLOYMENT.md` direction, but requires converting examples into deployable overlays and runbooks.

## B) If storage must stay inside the cluster now (MinIO replacement)

Adopt **SeaweedFS S3 Gateway** in-cluster.

### Why SeaweedFS over other in-cluster options

- Lighter operational profile than Ceph RGW for this workload size.
- S3 API compatibility sufficient for Bifrost's current use (bucket/object read-write + presigned URLs).
- Straightforward migration path from MinIO bucket/object model.

### Implementation pattern

- Deploy SeaweedFS via Helm in a dedicated namespace (`storage-system`).
- Back SeaweedFS volumes with a replicated StorageClass (zone-aware if possible).
- Expose internal S3 endpoint via ClusterIP service.
- Set:
  - `BIFROST_S3_ENDPOINT_URL=http://seaweedfs-s3.storage-system.svc.cluster.local:8333`
  - `BIFROST_S3_BUCKET=<bucket>`
  - `BIFROST_S3_ACCESS_KEY` / `BIFROST_S3_SECRET_KEY`
- Run smoke test job (adapt existing `k8s/minio-smoke-job.yaml` naming).

### Migration from existing MinIO data

1. Freeze writes (maintenance mode / stop worker+scheduler temporarily).
2. Mirror bucket objects from MinIO to SeaweedFS endpoint with `mc mirror`.
3. Validate object counts and random checksum samples.
4. Switch Bifrost S3 endpoint secret.
5. Restart API/worker/scheduler deployments.
6. Run post-cutover validation (file read/write, workflow execution, import/export).
7. Keep old MinIO bucket read-only for rollback window.

## Productionization work plan (sequenced)

### Phase 0 — Stabilize manifests (1-2 days)

- Fix `k8s/kustomization.yml` missing resource references.
- Add missing platform dependencies (at minimum Redis deployment/service or external dependency docs).
- Convert RabbitMQ `emptyDir` to PVC-backed StatefulSet (or explicitly externalize broker).
- Add PodDisruptionBudgets for API and worker.
- Add HPA for API and worker; keep scheduler fixed at 1.

### Phase 1 — Environment overlays (2-4 days)

- Introduce overlays:
  - `k8s/overlays/poc-k3s`
  - `k8s/overlays/prod-aks`
- Move all environment-specific values out of base manifests.
- Replace ad hoc secret creation with External Secrets/CSI integration.

### Phase 2 — Storage cutover (2-5 days)

- Choose one:
  - **Preferred**: managed object storage endpoint.
  - **Fallback**: SeaweedFS in-cluster.
- Execute migration runbook and smoke/perf tests.

### Phase 3 — Hardening + SRE controls (3-5 days)

- Ingress standardization (TLS, websocket headers, body limits).
- NetworkPolicy default-deny plus required allow rules.
- Backup/restore automation and quarterly restore drills.
- Observability baseline (metrics, logs, alerts, SLOs).

## Exit criteria to call production-ready

- Reproducible GitOps deployment from overlays, no manual YAML edits.
- Zero `emptyDir` for stateful dependencies.
- Documented and tested backup/restore for DB + object storage.
- Secret rotation procedure tested.
- Load test passed at expected concurrency.
- Runbooks exist for incident response, upgrade, and rollback.

## Additional controls to add for a production Kubernetes environment

### A) Reliability and safe rollout controls

- **PodDisruptionBudgets (PDBs)** for API and worker to preserve minimum availability during node maintenance.
- **Topology spread constraints** so replicas are distributed across nodes/zones.
- **Priority classes** (API > worker > batch jobs) to protect core control plane behavior during resource pressure.
- **Startup probes** on API and long-boot workers to prevent premature restarts.
- **Progressive delivery** (Argo Rollouts or canary strategy in ingress) for safer upgrades.
- **Vertical Pod Autoscaler (recommendation mode)** to continuously tune requests/limits based on observed usage.

### B) Security of incoming traffic (north-south)

- Standardize on a single ingress path (Application Gateway WAF or nginx ingress + cert-manager).
- Enforce:
  - TLS 1.2+ everywhere
  - automatic certificate rotation
  - HSTS headers
  - request-size limits and websocket timeout policy
  - IP allowlists for admin-only surfaces
- Add **WAF managed rules** and custom signatures for common API abuse patterns.
- Put public endpoints behind **DDoS protection** and rate limiting.
- Split public vs internal ingress classes where needed (for admin/ops endpoints).

### C) Security of internal traffic (east-west)

- Default-deny **NetworkPolicies** in every namespace, then allow only explicit service-to-service paths.
- Require **mTLS between workloads** via service mesh (Istio/Linkerd/Consul) or CNI-native encryption policy.
- Encrypt pod-to-managed-service traffic (Postgres/Redis/blob endpoints) with strict TLS verification.
- Restrict egress with an **egress gateway** or Cilium/Calico egress policies.
- Add DNS egress allowlists to only required external APIs.

### D) Supply chain and runtime hardening

- Enforce image signing and verification (Cosign + admission policy).
- Admission controls (OPA Gatekeeper/Kyverno) for:
  - no privileged containers
  - read-only root filesystem by default
  - required resource limits/requests
  - required labels/annotations/owners
- Continuous image vulnerability scanning in CI and registry.
- Runtime threat detection (Falco or managed equivalent) with alert routing.
- Regular secret rotation and short-lived credentials via workload identity.

### E) Observability and incident response

- SLOs with alerts for API availability, queue lag, worker failure rate, scheduler heartbeat, and object-storage errors.
- Centralized logs with correlation IDs and retention policy.
- Synthetic checks (UI login + workflow run + file read/write) on a fixed cadence.
- Runbook automation for common incidents (queue backlog, failing migrations, storage auth failures).

### F) Native ticket submission to PSA (recommended)

Implement automated PSA ticket creation as part of alerting/health workflows:

1. **Event sources**:
   - Prometheus Alertmanager webhooks
   - Kubernetes events (CrashLoopBackOff, OOMKilled, ImagePullBackOff)
   - Synthetic check failures
2. **Routing service**:
   - Lightweight webhook adapter that maps alerts to PSA ticket schema
   - De-duplication key (`cluster + namespace + workload + alertname`)
   - Severity mapping (P1/P2/P3)
3. **Enrichment**:
   - Deployment revision, pod logs excerpt, Grafana link, runbook link
4. **Bi-directional updates**:
   - Auto-resolve PSA ticket when alert clears
   - Post ticket ID back into alert annotations/Slack thread

Suggested first tickets to automate:
- API unavailable > 5 minutes
- Worker queue lag above threshold for > 10 minutes
- Scheduler down or duplicate scheduler detected
- Database connectivity failure
- S3 endpoint auth/timeout failures

## Security baseline checklist (traffic in/out of cluster)

- [ ] TLS termination policy defined and enforced for all ingress.
- [ ] WAF + rate limits enabled on internet-facing entrypoints.
- [ ] Default-deny NetworkPolicy enabled cluster-wide.
- [ ] mTLS or equivalent encryption for east-west service traffic.
- [ ] Egress restricted to approved destinations only.
- [ ] Private endpoints used for managed data services (no public exposure).
- [ ] Audit logs enabled for ingress, Kubernetes API, and secret access.
- [ ] PSA ticketing integration tested with both fire and clear events.

## Direct answer to "replace MinIO"

If S3-compatible storage must run **inside the cluster**, replace MinIO with **SeaweedFS S3 Gateway** now, then plan a second-stage migration to managed object storage when platform constraints allow.

If managed services are acceptable immediately, skip in-cluster object storage and move directly to Azure Blob S3 endpoint (or equivalent managed S3 API).
