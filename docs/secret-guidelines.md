Kubernetes secret handling guidelines (summary)

- Always use `kubectl create secret generic <name> --from-literal=K=V ...` or generate the full YAML with `--dry-run=client -o yaml` and `kubectl apply -f` to replace a secret. Avoid editing a secret YAML and running `kubectl apply` if you are only changing one key (it replaces the whole secret).

- To patch a single key safely, use: `kubectl patch secret <name> -n <ns> --type=json -p='[{"op":"replace","path":"/data/KEY","value":"$(echo -n NEWVALUE | base64)"}]'`.

- For rotation: 1) create a full new secret manifest, 2) apply it, 3) restart dependent deployments (or roll the pods) to pick up new envs. Don't rely on in-process reload.

- Recommended: use SealedSecrets or ExternalSecrets to centralize and automate secrets management. Store authoritative secrets in pass and copy into cluster via CI or operator.

- Add a post-deploy smoke test (S3 put/list, DB connect, RabbitMQ connect) to detect broken secrets early.
