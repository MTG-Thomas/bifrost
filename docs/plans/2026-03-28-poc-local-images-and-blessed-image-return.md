# PoC Cluster Local Images And Return To Blessed Images

Date: 2026-03-28
Status: local planning doc, not yet committed or upstreamed

## Goal

Document the current proof-of-concept cluster image state, reconstruct how the `localhost/...` workaround was applied, and define the safest path back to blessed images.

## Current state

Active namespace: `bifrost`

Current deployment images:

- `bifrost-api`: `localhost/bifrost-api:main-20260327d`
- `bifrost-scheduler`: `localhost/bifrost-api:main-20260327d`
- `bifrost-worker`: `localhost/bifrost-api:main-20260327d`
- `bifrost-client`: `localhost/bifrost-client:main-20260327b`
- `minio`: `minio/minio:latest`
- `rabbitmq`: `docker.io/rabbitmq:3.13-management-alpine`
- `redis`: `redis:7`

All Bifrost app workloads are currently using `imagePullPolicy: IfNotPresent`.

There is also secret drift:

- committed last-applied API manifest references `bifrost-secrets`
- live API, worker, scheduler, and init container all reference `bifrost-secrets-full`

`bifrost-secrets-full` is the real runtime secret and includes critical keys that are not present in `bifrost-secrets`, including:

- `BIFROST_DATABASE_URL`
- `BIFROST_DATABASE_URL_SYNC`
- `BIFROST_REDIS_URL`
- `BIFROST_RABBITMQ_URL`
- `BIFROST_RABBITMQ_USERNAME`
- `BIFROST_SECRET_KEY`
- `BIFROST_PUBLIC_URL`
- WebAuthn settings

That means image rollback is separable from manifest rollback. Returning to blessed images can be done safely by updating deployment images only, but reapplying older manifests without reconciling secret references would likely break the app.

There is also config precedence drift:

- API, worker, scheduler, and init load `envFrom` in this order:
  1. `bifrost-config`
  2. `bifrost-secrets-full`
- several keys overlap between the ConfigMap and the secret
- the secret wins at runtime because it is loaded second

Important overlapping values:

- `BIFROST_PUBLIC_URL`
  - ConfigMap: `https://bifrost-poc-host.netbird.cloud:18443`
  - Secret: `https://bifrost-mtg.eu1.netbird.services`
- `BIFROST_WEBAUTHN_ORIGIN`
  - ConfigMap: `https://bifrost-poc-host.netbird.cloud:18443`
  - Secret: `https://bifrost-mtg.eu1.netbird.services`
- `BIFROST_WEBAUTHN_RP_ID`
  - ConfigMap: `bifrost-poc-host.netbird.cloud`
  - Secret: `bifrost-mtg.eu1.netbird.services`

So even if the live ingress host is `bifrost-poc-host.netbird.cloud`, the effective application runtime may still believe its canonical public URL is the NetBird service hostname from the secret. That needs to be reconciled before any manifest cleanup or ingress normalization work.

Published blessed images are already present in node containerd:

- `docker.io/jackmusick/bifrost-api:latest`
- `docker.io/jackmusick/bifrost-client:latest`

That means a rollback to blessed images does not require solving registry reachability first.

## What we reconstructed

### API workaround path

Two API workaround paths existed.

Older path:

- built locally on-host with Podman/Buildah
- used tag `localhost/bifrost-api:feat-autotask-cove-integrations`
- then deployment image and pull policy were patched

Newer path:

- staged stripped build context in `/tmp/bifrost-build-api`
- ran completed pod `kaniko-bifrost-api`
- wrote `/tmp/bifrost-kaniko-out/bifrost-api-main-20260327d.tar`
- imported that tarball into k3s containerd
- updated API, worker, and scheduler to `localhost/bifrost-api:main-20260327d`
- restarted those deployments

Supporting artifacts found on-host:

- `/tmp/bifrost-build.log`
- `/tmp/bifrost-import.log`
- `/tmp/bifrost-rollout.log`
- `/tmp/bifrost-api-patch.json`
- `/tmp/bifrost-worker-patch.json`
- `/tmp/bifrost-scheduler-patch.json`
- completed pod `kaniko-bifrost-api`

### Client workaround path

The exact command trail was not preserved, but the shape is still clear.

- staged client build context in `/tmp/bifrost-client-build/client`
- local tags in containerd:
  - `localhost/bifrost-client:main-20260327`
  - `localhost/bifrost-client:main-20260327b`
- deployment moved from blessed image to `main-20260327`, then to `main-20260327b`

Most likely mechanism:

- local Podman/Buildah build
- image tar export
- `k3s ctr -n k8s.io images import`
- `kubectl set image deployment/bifrost-client ...`

## Why the current state is risky

- the cluster depends on node-local images that are not reproducibly documented
- if the node is replaced or containerd state is lost, the live image tags disappear
- the committed manifests do not describe the live deployment state
- rollback/rebuild knowledge is currently tribal
- `latest` tags are still used for some infra images, which adds separate drift risk

## Desired end state

Preferred order:

1. upstream blessed images if they solve the production issue cleanly
2. if not, our own controlled container registry with pinned tags
3. avoid long-term dependence on `localhost/...` images

## Short-term safest path

Short term, the safest operational move is not to rebuild local images again until the rollout procedure is fully documented.

Before any future local-image refresh:

1. document exact build steps for API and client
2. document exact import steps into k3s containerd
3. document exact deployment update and rollback commands
4. capture the reason blessed images were not sufficient

## Return-to-blessed-image plan

### Preconditions

Before rollback:

1. capture current live manifests and deployment images
2. confirm published images exist in node containerd
3. confirm there is no unsaved local-only fix still needed in the running app
4. choose a maintenance window or low-risk period
5. preserve the live `bifrost-secrets-full` references for API, worker, scheduler, and init

### Commands to stage rollback

Inspect first:

```bash
sudo k3s kubectl get deploy -n bifrost -o wide
sudo k3s kubectl get pods -n bifrost -o wide
sudo k3s ctr -n k8s.io images list | grep -E 'jackmusick/bifrost-(api|client):latest'
```

Rollback API family:

```bash
kubectl set image deployment/bifrost-api api=docker.io/jackmusick/bifrost-api:latest init=docker.io/jackmusick/bifrost-api:latest -n bifrost
kubectl set image deployment/bifrost-worker worker=docker.io/jackmusick/bifrost-api:latest -n bifrost
kubectl set image deployment/bifrost-scheduler scheduler=docker.io/jackmusick/bifrost-api:latest -n bifrost
kubectl rollout status deployment/bifrost-api -n bifrost
kubectl rollout status deployment/bifrost-worker -n bifrost
kubectl rollout status deployment/bifrost-scheduler -n bifrost
```

Rollback client:

```bash
kubectl set image deployment/bifrost-client client=docker.io/jackmusick/bifrost-client:latest -n bifrost
kubectl rollout status deployment/bifrost-client -n bifrost
```

### Validation after rollback

```bash
kubectl get pods -n bifrost -w
kubectl logs -n bifrost deploy/bifrost-api --tail=200
kubectl logs -n bifrost deploy/bifrost-client --tail=200
kubectl get ingress -n bifrost bifrost-ingress
```

Validate:

- UI loads through the ingress URL
- API health succeeds
- worker starts cleanly
- scheduler starts cleanly
- login and a basic workflow execution succeed

### Fast rollback to local images if needed

If blessed images fail and the local images are still present in containerd:

```bash
kubectl set image deployment/bifrost-api api=localhost/bifrost-api:main-20260327d init=localhost/bifrost-api:main-20260327d -n bifrost
kubectl set image deployment/bifrost-worker worker=localhost/bifrost-api:main-20260327d -n bifrost
kubectl set image deployment/bifrost-scheduler scheduler=localhost/bifrost-api:main-20260327d -n bifrost
kubectl set image deployment/bifrost-client client=localhost/bifrost-client:main-20260327b -n bifrost
kubectl rollout status deployment/bifrost-api -n bifrost
kubectl rollout status deployment/bifrost-worker -n bifrost
kubectl rollout status deployment/bifrost-scheduler -n bifrost
kubectl rollout status deployment/bifrost-client -n bifrost
```

## Medium-term better path

If upstream images remain unsuitable, move to a controlled registry path instead of node-local tags.

Minimum acceptable improvement:

1. choose our registry
2. publish immutable tags for API and client
3. update cluster manifests to those tags
4. reconcile manifests to the actual runtime secret/config references
5. stop relying on ad hoc local imports
6. document rollout and rollback in the repo

That keeps the workaround reproducible even if the node is lost.

## Open questions

- what specific production issue forced the move away from blessed images
- whether that issue exists in current upstream images
- whether the client workaround fixed anything independently, or only tracked the API workaround
- whether `jackmusick/*` or `ghcr.io/jackmusick/*` should be the long-term blessed path for this cluster
