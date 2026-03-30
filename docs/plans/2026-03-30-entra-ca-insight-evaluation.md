# Entra CA Insight Evaluation for Bifrost Multi-Tenant CA Hygiene

Date: 2026-03-30

## Feasibility Verdict

**Verdict: Conditionally green (recommended as a wrapped subprocess integration, not a fork initially).**

`entra-ca-insight` is technically integratable with Bifrost as a scheduled workflow because:

- It is Python CLI-first and already supports non-interactive execution with explicit arguments (`python -m caInsight ...`).
- Its analysis is read-only against Microsoft Graph and then performed offline after data retrieval.
- It produces a structured JSON report that can be consumed programmatically.

However, there are **material caveats** that must be addressed in the integration wrapper:

1. **Authentication model mismatch (current upstream UX is token-in, not app-only first-class).**
   - Upstream docs center on manually providing a bearer token and even describe browser token extraction.
   - It can still be automated if Bifrost mints an app-only Graph token and passes it via `--token`.
2. **Single-run local state model (cache/db/files) is not multi-tenant safe by default.**
   - It uses fixed local paths (`cache/`, `portal.html`, `caInsight.db`), so tenant isolation must be enforced by execution sandboxing/workdir strategy.
3. **License is GPL-3.0.**
   - This is potentially a commercial/platform distribution concern. Treat this as a legal review gate before broad productization.

## Evidence Summary from Upstream

### Architecture and execution model

- CLI entrypoint and required args are explicit in code (`--token`, `--include-assignments`, `--target-resources`).
- Upstream architecture documentation states a core analysis engine and local storage layer + web/API layer.

### Auth behavior

- The CLI accepts a raw access token argument and validates via `/me`, indicating expected delegated-style token validation path.
- Usage docs explicitly describe manual token extraction from the Entra portal as the baseline method.
- Usage docs also note CI/CD automation via service principal is possible.

### Structured output

- JSON output is first-class (`generate_json_report`) and includes metadata + results + excluded policy list.
- CLI supports `--output` for deterministic file naming.

### Built-in state/history

- Caches are written under a local `cache/` directory.
- Web API stores history in a local SQLite DB (`caInsight.db`) and imports JSON runs.
- Report generator includes tenant ID in output directory naming when token includes `tid`.

### License

- Repository license file is GPL-3.0.

## Recommended Integration Design (Bifrost)

### 1) Workflow shape

Create a Bifrost integration module (wrapper) that:

1. Receives tenant scope from Bifrost job scheduler (weekly default).
2. Retrieves tenant-specific Graph app credentials from Azure Key Vault.
3. Mints app-only Graph token per tenant at runtime.
4. Executes `entra-ca-insight` via subprocess in an **isolated per-tenant workdir**.
5. Reads JSON output and normalizes into Bifrost-native finding schema.
6. Compares against previous scan snapshot from Bifrost state store.
7. Emits actions (dry-run first):
   - HaloPSA ticket on new/regressed gaps.
   - Hudu documentation update with current summary.

### 2) Schedule and scoping

- **Cadence:** Weekly per tenant (recommended baseline), plus on-demand run.
- **Scope dimensions per run:**
  - assignments: `users`, `guests`, `agent-identities`, `workload-identities`
  - targets: `cloud-apps`, `agent-resources`, and `user-actions` where applicable
- Run either:
  - one broad workflow fan-out per tenant; or
  - separate child tasks per assignment/target combo for better retries/timeouts.

### 3) Credentials and auth (Key Vault-first)

Use Key Vault only:

- Secret naming convention example per tenant:
  - `entra-{tenantKey}-client-id`
  - `entra-{tenantKey}-client-secret`
  - `entra-{tenantKey}-tenant-id`
- Runtime:
  - Bifrost obtains secrets through existing SecretManagement/Az.KeyVault pattern.
  - Wrapper requests app-only token for `https://graph.microsoft.com/.default`.
  - Pass token directly to CLI `--token`.

**Important:** upstream token validation currently calls `/me`; this can fail for app-only tokens in some implementations. Validate this behavior in a PoC. If it fails, wrapper options are:

1. Try upstream-compatible delegated token only for now (not preferred for MSP ops).
2. Contribute/maintain minimal patch to validate token against an app-compatible endpoint.
3. Skip upstream validation call in a thin wrapper and let real API calls fail fast.

### 4) Tenant isolation and no-state-bleed

Because upstream uses fixed local paths, enforce isolation by process/workdir:

- For each tenant run, create ephemeral directory:
  - `/tmp/bifrost/ca-insight/{tenantId}/{runId}/`
- Run subprocess with `cwd` set to this directory.
- Copy or mount tool code read-only; keep cache/db/output inside run directory.
- Collect JSON artifact, then delete ephemeral workspace (except optional debug retention).

This avoids cross-tenant cache contamination and avoids shared `caInsight.db` issues.

### 5) State management: native SQLite vs Bifrost external state

**Recommendation: manage state in Bifrost, not upstream SQLite.**

Reasons:

- Upstream SQLite is local-process/UI oriented, not your operational source of truth.
- Bifrost needs cross-tenant correlation, ticket dedupe, and workflow-level idempotency.
- External state simplifies regression logic and downstream integrations (HaloPSA/Hudu).

Use upstream JSON as the canonical import artifact each run; persist normalized findings and run metadata in Bifrost integration storage.

### 6) Finding lifecycle and downstream actions

Define finding fingerprint (example):

- `tenant_id + assignment + target_resource + normalized_permutation_signature`

Statuses:

- `new`: not seen before
- `persisting`: seen before still open
- `regressed`: previously resolved, now back
- `resolved`: no longer present

Action policy:

- **HaloPSA**
  - Create/update ticket only for `new` and `regressed` findings (or severity threshold).
  - Map to client-specific site/customer and security ticket type/status.
  - Include deterministic external key for dedupe.
- **Hudu**
  - Update tenant CA hygiene page with latest summary + trend + top risky gaps.

### 7) Dry-run / confirmation gate

Default workflow mode should be `dry_run=true`:

- Runs analysis + diff + proposed actions.
- Produces execution summary artifact.
- Requires explicit confirmation (or policy flag) to perform HaloPSA/Hudu write actions.

## Subprocess vs Fork

Given your preference, start with **subprocess invocation** plus wrapper.

Fork only if one of these proves necessary:

1. App-only token support is blocked by upstream token validation behavior.
2. JSON schema stability is insufficient and requires deterministic machine contract.
3. You need direct library calls for performance/control not achievable via CLI.

## Blockers / Risks

1. **GPL-3.0 licensing**
   - If you distribute this bundled inside commercial Bifrost runtime artifacts, copyleft obligations may trigger.
   - Likely safer pattern: optional external tool execution with clear separation, or re-implement analysis logic natively.
   - Requires legal review before shipping as default platform component.

2. **Auth fit for GDAP app-only pattern**
   - Docs are delegated-token oriented; app-only may still work if token accepted and Graph permissions granted with admin consent per tenant, but needs validation.

3. **Output contract maturity**
   - JSON is structured enough for automation, but schema versioning guarantees are informal.
   - Wrapper should include tolerant parsing + schema guardrails.

## If Integration Is Not Clean: Bifrost-Native Alternative

If licensing/auth/output concerns block adoption, build a BiFrost-native CA analyzer:

- Single Graph read pass for policies, named locations, identities in scope.
- Deterministic permutation generator and policy evaluator in Bifrost integration module.
- Native multi-tenant state and diff model.
- First-class HaloPSA/Hudu connectors with dry-run/approval gates.

This eliminates GPL coupling and gives full contract control, at cost of initial engineering effort.

## Minimal Implementation Stub (Conceptual)

```python
# Pseudocode only
for tenant in scheduled_tenants:
    secrets = key_vault_get(tenant)
    token = mint_graph_app_token(secrets)

    with isolated_workdir(tenant, run_id) as wd:
        cmd = [
            "python", "-m", "caInsight",
            "--token", token,
            "--include-assignments", "users",
            "--target-resources", "cloud-apps",
            "--output", f"{tenant}_users_cloud_apps"
        ]
        run_subprocess(cmd, cwd=wd)
        report = load_json(find_output_json(wd))

    findings = normalize(report, tenant)
    diff = compare_with_previous(findings, tenant)

    proposals = build_halo_hudu_actions(diff)
    if not dry_run and confirmation_granted:
        execute_actions(proposals)
```

## Recommended Next Steps

1. Perform a 3-tenant PoC using app-only tokens from Key Vault.
2. Validate whether `/me`-based token validation blocks app-only runs.
3. Validate JSON parsing against at least two scan types (`users/cloud-apps`, `guests/agent-resources`).
4. Run legal review on GPL-3.0 integration posture.
5. If clear, ship wrapper MVP with dry-run default and HaloPSA action gating.
