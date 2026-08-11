# Verified platform workspace history with a GitHub App

Bifrost can close an activated workspace `_repo` changeset by creating a
GitHub-managed, verified commit on the configured workspace branch. This path
uses `createCommitOnBranch`; it does not use the saved personal token or a
GitPython `repo.index.commit()` for platform-authored changeset history.

## Security boundary

Create and install the App only as an explicit operator action. The platform
code and this runbook do not create an App, install it, change a ruleset, or
deploy configuration.

Use the narrowest App configuration:

- Repository access: only the workspace-history repository.
- Repository permission: **Contents — Read and write**.
- No organization permissions and no webhook subscriptions are required.
- Store the private key in the deployment secret store. Do not save it in the
  GitHub integration record, repository, container image, or logs.
- Inject the private key only into the API process that performs Git closure.
  Do not place it in shared worker, scheduler, or workflow-runner environment
  configuration where user-authored workloads could inherit it.

For each closure, Bifrost mints a GitHub App JWT and exchanges it for a
short-lived installation token scoped again to the configured repository and
`contents: write`. Installation tokens are not persisted.

## Platform configuration

Keep the repository URL and branch in the existing organization-scoped GitHub
configuration. Provide these API process environment variables through the
deployment's secret/configuration system:

| Variable | Secret | Purpose |
| --- | --- | --- |
| `BIFROST_GITHUB_APP_ID` | No | GitHub App ID (not the OAuth client ID) |
| `BIFROST_GITHUB_APP_INSTALLATION_ID` | No | Installation ID for the repository owner |
| `BIFROST_GITHUB_APP_PRIVATE_KEY` | Yes | PEM private key; literal newlines or escaped `\n` are accepted |

All three values are required. A saved GitHub personal token is intentionally
not a fallback for changeset closure. If App configuration is incomplete, the
workspace activation remains durable and the changeset records Git closure as
not configured.

## Commit contract

Call activation with `push: true` whenever `commit_message` is supplied:

```json
{
  "commit_message": "Publish approved production source",
  "push": true,
  "plan_id": "plan-42",
  "protected_main_source_sha": "0123456789abcdef0123456789abcdef01234567"
}
```

The authenticated operator email and workspace changeset ID are always added
to the commit body. `plan_id` and `protected_main_source_sha` are appended when
provided. The remote mutation contains only the changeset's exact staged paths.
Bifrost first requires those paths at the remote parent to match the
changeset's base hashes, then uses that parent OID as `expectedHeadOid`; a path
change or ref race fails closed instead of overwriting concurrent GitHub work.

Before a changeset reports `committed`, Bifrost requires all of the following:

1. The configured ref resolves to the created commit (or the same changeset's
   already-created commit remains reachable during a closure retry).
2. The remote commit tree exists and every staged path reads back with the
   activated SHA-256 content, including confirmed deletions.
3. GitHub reports `signature.isValid`, `signature.wasSignedByGitHub`, and
   signature state `VALID`.

If the remote mutation succeeds but readback or signing verification fails,
the activated workspace is not rolled back. The candidate commit SHA and
original provenance remain in the retry record. A retry verifies that commit
instead of replaying workspace activation or blindly creating a duplicate.

If activation records Git closure as `not_configured`, configure all three App
values, restart the API process, then retry the existing changeset without a
new commit message:

```http
POST /api/workspace-repo-changesets/CHANGESET_ID/retry-git-closure
Content-Type: application/json

{"push": true}
```

The saved operator and commit provenance are reused and workspace activation
is not replayed. Legacy failure records without saved provenance require the
original `commit_message`; a different message is rejected. The same retry
endpoint recovers a durable `pending` record left by an API process stop.

## Operator verification

After deployment and an operator-approved canary changeset, read the returned
`commit_sha` from the changeset and verify the authoritative GitHub record:

```bash
gh api repos/OWNER/REPOSITORY/commits/COMMIT_SHA \
  --jq '{sha: .sha, verification: .commit.verification}'
```

Require `verification.verified == true`, confirm the commit body trailers, and
compare the configured branch ref to the expected canary result. Do not infer
success from the changeset status alone during rollout; retain the GitHub
readback as the production acceptance evidence.
