# PoC Cluster Access And Inventory

This note captures the working access path to the proof-of-concept Bifrost k3s
cluster, the failure modes we hit while trying to reconnect, and the live image
inventory observed on 2026-03-28.

The goal is to stop rediscovering the same access details and to make it
obvious when the live cluster has drifted from repo expectations.

## Canonical Access Path

Use plain SSH to the stable NetBird hostname for the VM that hosts k3s:

```bash
ssh -F /dev/null -i ~/.ssh/id_ed25519 debian@bifrost-poc-host.netbird.cloud
```

Notes:

- username: `debian`
- hostname: `bifrost-poc-host.netbird.cloud`
- do not rely on the OpenSSH NetBird helper integration being active
- on this workstation, bypassing SSH config with `-F /dev/null` was the most
  reliable and least ambiguous path

Once connected, use the host-local k3s kubectl:

```bash
sudo k3s kubectl get ns
sudo k3s kubectl get pods -A -o wide
sudo k3s kubectl get deploy,statefulset,daemonset -A \
  -o custom-columns='KIND:.kind,NAMESPACE:.metadata.namespace,NAME:.metadata.name,IMAGES:.spec.template.spec.containers[*].image'
sudo k3s kubectl get pods -A \
  -o custom-columns='NAMESPACE:.metadata.namespace,POD:.metadata.name,STATUS:.status.phase,IMAGES:.spec.containers[*].image'
```

## Failure Modes We Hit

When access was broken from WSL, these were the exact signatures:

### Remote kube API timed out

```text
Unable to connect to the server: dial tcp 10.1.23.114:6443: i/o timeout
```

and later:

```text
Unable to connect to the server: net/http: request canceled while waiting for connection (Client.Timeout exceeded while awaiting headers)
```

Meaning:

- kubeconfig existed and pointed at the expected host
- direct remote kube API access was not usable from the workstation at that time

### SSH hung during key exchange

Plain SSH and the NetBird-integrated SSH path both reached the server, then hung
at:

```text
expecting SSH2_MSG_KEX_ECDH_REPLY
```

Meaning:

- host resolution worked
- TCP port 22 was open
- the server replied with its SSH banner
- the SSH transport stalled before auth began

This is not a bad key or bad username symptom. It points to transport, overlay,
or access-policy instability.

### SSH later rejected with a policy-style message

After a policy change, the same path returned:

```text
Not allowed at this time
Connection reset by peer
```

Meaning:

- the host was reachable
- the server actively rejected the connection
- this looked like an access-control or server-side policy decision rather than
  a generic network failure

### SSH eventually recovered

After policy/path changes settled, plain SSH succeeded with:

```bash
ssh -F /dev/null -i ~/.ssh/id_ed25519 debian@bifrost-poc-host.netbird.cloud
```

Takeaway:

- the access path can change state over time during policy propagation
- do not assume one failed attempt proves the path or credentials are wrong
- record the exact failure text before changing multiple things at once

## Useful Preflight Checks

From the workstation:

```bash
getent hosts bifrost-poc-host.netbird.cloud
ping -c 1 bifrost-poc-host.netbird.cloud
timeout 10 bash -lc 'cat < /dev/null > /dev/tcp/bifrost-poc-host.netbird.cloud/22' && echo open
timeout 20 ssh -F /dev/null -vvv -o BatchMode=yes -i ~/.ssh/id_ed25519 \
  debian@bifrost-poc-host.netbird.cloud 'exit'
```

Interpretation:

- DNS/ping success means the NetBird path is at least partially up
- `open` on port 22 means the VM is reachable on SSH
- if SSH hangs in KEX, it is not yet an auth problem
- if SSH returns `Not allowed at this time`, investigate access policy before
  changing keys or users

## Live Cluster Inventory On 2026-03-28

Observed namespaces:

- `bifrost`
- `cert-manager`
- `database`
- `default`
- `ingress-nginx`
- `kube-node-lease`
- `kube-public`
- `kube-system`
- `messaging`
- `metallb-system`
- `netbird`
- `objectstore`

Observed Bifrost app stack images in namespace `bifrost`:

- `bifrost-api`: `localhost/bifrost-api:main-20260327d`
- `bifrost-scheduler`: `localhost/bifrost-api:main-20260327d`
- `bifrost-worker`: `localhost/bifrost-api:main-20260327d`
- `bifrost-client`: `localhost/bifrost-client:main-20260327b`
- `rabbitmq`: `docker.io/rabbitmq:3.13-management-alpine`
- `redis`: `redis:7`
- `minio`: `minio/minio:latest`

Important implication:

- the running PoC app stack is not using the repo-default published runtime
  images
- it is using host-local images tagged under `localhost/...`

Repo expectation in `docker-compose.yml` is:

- `jackmusick/bifrost-api:latest`
- `jackmusick/bifrost-client:latest`

So live PoC runtime is intentionally or accidentally drifted from the repo’s
normal published-image expectation.

## Other Drift And Hygiene Findings

### Overlapping infrastructure tracks

There are separate infra-oriented namespaces/resources outside the `bifrost`
namespace:

- `database/pg-bifrost-postgresql`
- `messaging/rabbit-bifrost-rabbitmq`
- `objectstore/minio-bifrost`

This suggests the PoC cluster is carrying more than one deployment pattern or a
partial migration history.

### Unhealthy or incomplete resources

Observed on 2026-03-28:

- `messaging/rabbit-bifrost-rabbitmq-0` was `Pending`
- `objectstore/minio-bifrost-console` was `Pending`
- several `netbird-operator-config-kubernetes-service-expose-*` pods were
  `Failed`

These should be treated as operational debt, not background noise.

### Weak image pinning

Several live resources still rely on mutable tags:

- `minio/minio:latest`
- `redis:7`
- `docker.io/minio/console:latest`
- `registry-1.docker.io/bitnami/postgresql:latest`

This increases drift risk and makes rollback/forensics harder.

## Recommended Next Steps

1. Decide whether `localhost/bifrost-api:*` and `localhost/bifrost-client:*`
   are the intentional source of truth for the PoC cluster or just ad hoc local
   patching.
2. Document the rollout path that produces those local tags.
3. Clean up overlapping namespace ownership for database, messaging, and
   object storage.
4. Replace mutable `latest` tags with pinned versions where possible.
5. Add a small checked-in helper script for cluster inventory so image/state
   snapshots are repeatable.

## Minimal Repeatable Inventory Commands

Run these from the workstation:

```bash
ssh -F /dev/null -i ~/.ssh/id_ed25519 debian@bifrost-poc-host.netbird.cloud \
  'hostname && whoami && sudo k3s kubectl get ns'

ssh -F /dev/null -i ~/.ssh/id_ed25519 debian@bifrost-poc-host.netbird.cloud \
  "sudo k3s kubectl get pods -A -o custom-columns='NAMESPACE:.metadata.namespace,POD:.metadata.name,STATUS:.status.phase,IMAGES:.spec.containers[*].image'"

ssh -F /dev/null -i ~/.ssh/id_ed25519 debian@bifrost-poc-host.netbird.cloud \
  "sudo k3s kubectl get deploy,statefulset,daemonset -A -o custom-columns='KIND:.kind,NAMESPACE:.metadata.namespace,NAME:.metadata.name,IMAGES:.spec.template.spec.containers[*].image'"
```

These are the commands used to produce the inventory in this document.
