# Bifrost Open-Source Security Baseline

This baseline is intentionally tuned to Bifrost's architecture: a multi-tenant
FastAPI and React automation platform with encrypted OAuth/config secrets,
Redis and RabbitMQ execution state, S3-backed workspace/app files, MCP and
agent surfaces, and worker-side dynamic code execution.

## Local Commands

Run the fast local checks before security-sensitive changes:

```bash
python -m pip install semgrep pip-audit
semgrep scan --config .semgrep/bifrost.yml --exclude userland --exclude userland-powershell-review-fixes --exclude client/src/lib/v1.d.ts --severity ERROR --error
pip-audit -r requirements.txt --strict
npm --prefix client audit --audit-level=high --omit=dev
osv-scanner scan --lockfile client/package-lock.json --lockfile requirements.txt
gitleaks protect --staged --redact
trivy config --severity HIGH,CRITICAL .
```

Dependency checks are advisory in the PR workflow until the current lockfile
findings are remediated. Promote them to blocking by removing
`continue-on-error` from the dependency job once that baseline is clean.
The PR Gitleaks job scans the checked-out tree, not full Git history, because
the existing history currently contains findings that need separate cleanup.

Keep personal pre-commit hooks local to `.git/hooks` and helper excludes in
`.git/info/exclude`. Shared security policy lives in the tracked workflow and
config files.

## Threats Covered First

- Cross-tenant scope mistakes in routers and repositories.
- Ad hoc superuser JWT issuance outside the auth/security boundary.
- Secret, token, password, and OAuth credential logging.
- Unsafe subprocess and dynamic execution surfaces.
- Raw HTML rendering in the React client.
- Dependency, container, Kubernetes, and committed-secret exposure.

## CI Layers

- `security-pr.yml`: fast PR checks for Semgrep, Gitleaks, dependency advisories,
  OSV-Scanner, and Trivy config/filesystem scanning. Semgrep and Gitleaks block
  PRs; dependency and Trivy hardening findings initially report until the
  existing baseline is remediated.
- `codeql.yml`: deeper Python and JavaScript/TypeScript CodeQL analysis on PRs,
  weekly schedule, and manual dispatch.
- `security-nightly.yml`: authenticated ZAP and Schemathesis runs against a
  disposable Compose stack.

## Validation Probes

Use temporary scratch branches to confirm detectors still fire:

- Hardcode a fake live-looking key and confirm Gitleaks/Trivy block it.
- Add a logger call containing `refresh_token` and confirm Semgrep blocks it.
- Create an ad hoc superuser access token outside auth/security and confirm
  Semgrep blocks it.
- Add unsafe `dangerouslySetInnerHTML` without DOMPurify and confirm Semgrep or
  CodeQL flags it.
- Add a deliberate API 500 on malformed request data and confirm Schemathesis
  reproduces it in the nightly workflow.
