## NetBird on k3s

This directory holds the repo-backed configuration for the initial NetBird
operator rollout on the Bifrost k3s cluster.

Scope of the first pass:
- install the NetBird Kubernetes operator
- expose the Kubernetes API privately to the NetBird mesh
- prepare the cluster for private service exposure later

Deliberate non-goals for the first pass:
- public reverse proxy exposure
- Gateway API beta usage
- broad service annotation rollout

Operational note:
- the initial policy uses the NetBird `All` group as the source group so remote
  access works immediately for existing peers
- tighten this later once a dedicated admin peer/group exists
