# Execution-variable secret persistence incident (2026-07-22)

## Status

- Severity: P0
- Endpoint rollout: paused
- Affected workflow: `a2e75961-9f0e-4a96-9ee5-a9d240cdda08`
- Prevention: implemented in PR #477; deployment proof pending
- Existing history remediation: not authorized
- SIDEXIS credential rotation: not authorized

This note contains identifiers and metadata only. It intentionally excludes
execution parameters, results, variables, generated scripts, credentials,
connection strings, and authentication material.

## Summary

The execution engine captures workflow locals for operator diagnostics. Before
this repair, those locals were scrubbed only against secrets dynamically
registered in the execution context. A locally composed endpoint script could
therefore pass through the worker result and repository unchanged and be stored
in PostgreSQL `executions.variables` JSONB. Platform-admin execution readback
then returned the stored variables.

The repair adds two independent pre-persistence boundaries:

1. sanitize captured variables before the worker/Redis result handoff; and
2. sanitize again in the execution repository immediately before JSON
   serialization and the PostgreSQL update.

The sanitizer recursively redacts secret-bearing names, executable text,
`SecretString` values, and binary values. Regression tests use synthetic marker
strings only and prove that worker output, repository parameters, and the
admin-readback shape cannot recover the markers.

## Affected active records

Metadata-only queries found 11 successful executions spanning 2026-07-11
through 2026-07-22. No affected parameters, results, variables, or log payloads
were opened during the inventory.

Customer, endpoint, SQL-instance, and execution-ID mappings are intentionally
excluded from this repository. The exact list is held in access-controlled
incident record `P0-BIFROST-EXECVAR-2026-07-22`. That record is the only
authority for remediation binding and credential-rotation scope.

## Storage and retention map

This is a 2026-07-22 metadata-only production snapshot.

| Surface | Current evidence | Remediation/expiry behavior |
| --- | --- | --- |
| Primary PostgreSQL | `psql-mtg-bifrost-poc-01`, PostgreSQL 16, primary role, no HA, no read replicas. All 11 rows have non-null `variables` and the affected key. | Exact active rows can be transactionally replaced with a redaction sentinel. |
| PostgreSQL automated backup/PITR | Seven automatic daily full backups are currently listed, 2026-07-16 through 2026-07-22. Retention is 7 days; geo-redundant backup is disabled. WAL supports PITR inside that window. | Managed snapshots and WAL cannot be surgically edited. A restore to a time before active-row redaction will resurrect the old values. Let them expire naturally, and require the redaction runbook before any restored database is made accessible. |
| Separate PostgreSQL backup/replica | No Azure PostgreSQL replica, Azure Backup item, Data Protection item, long-term-retention job, or operator-created backup was found. | No separately managed database copy was identified. |
| Redis execution context | Managed Redis AOF is enabled at one-second frequency; RDB is disabled. Ten IDs had no current key. The latest execution had one `bifrost:exec:{id}:context` key with the source-defined one-hour TTL when inspected. | The live key expires automatically, but key expiry alone is not proof that old bytes have been eliminated from provider-managed AOF storage. Azure does not expose AOF files, rewrite timing, or a supported surgical-erasure operation to this operator. Treat historical AOF bytes as provider-controlled until normal rewrite/storage lifecycle completes; obtain Microsoft support confirmation if an elimination attestation is required. No provider-side deletion is currently authorized. |
| RabbitMQ | All workflow, retry, and poison queues had zero ready, unacknowledged, and total messages. | No queued copy remains. |
| Kubernetes container logs | Current API logs contained short execution-ID/status lines only; no matching long payload-shaped line was found. Other current app/worker logs had no matches. | Node/container rotation applies. No Azure diagnostic setting exports AKS logs. |
| OpenTelemetry / Tempo | Trace and log export goes to the in-cluster collector and Tempo. Tempo block retention is 168 hours. Searches for all 11 execution IDs returned zero traces; collector matches were short metadata lines only. | No affected trace was identified. Existing blocks expire at 7 days and are not a surgical record-edit surface. |
| Log Analytics | Workspace default retention is 30 days; `BifrostObserver_CL` is 31 days. A 14-day exact-ID query returned zero records. PostgreSQL, Redis, and AKS have no diagnostic settings. | No matching record exists to purge. If later evidence changes that conclusion, use the supported Log Analytics purge process under separate approval. |
| Azure Storage / exports | The platform object account exposes `autotask-ticket-attachments` and `bifrost-objects`; the GitHub-events account exposes only its function containers. Repo and live configuration show no PostgreSQL or execution-history export job. Current operator identity cannot enumerate blob names without a Data Reader role. | No export is evidenced. Do not use account keys merely to widen discovery; grant time-bounded metadata read access only if an operator requires a stronger blob-name inventory. |
| Recovery Services / Data Protection | Recovery Services protects a legacy VM only. Data Protection protects the platform blob account only. | Neither vault is a copy of managed PostgreSQL execution rows. |
| Downstream APM | No Sentry DSN is configured. The only Application Insights resource is the unrelated GitHub-events function. | No affected downstream APM copy was identified. |

Azure PostgreSQL backup behavior is documented at
<https://learn.microsoft.com/azure/postgresql/backup-restore/concepts-backup-restore>.
Managed Redis persistence behavior is documented at
<https://learn.microsoft.com/azure/redis/how-to-persistence>.
Log Analytics retention behavior is documented at
<https://learn.microsoft.com/azure/azure-monitor/logs/data-retention-configure>.

## Active-row remediation procedure (not authorized)

Preferred action: redact `variables` for exactly the 11 IDs while preserving
the execution records and all non-variable history.

1. Confirm prevention is deployed and the synthetic canary passes.
2. Freeze admin read access to the 11 records for the maintenance window.
3. Load the exact 11 UUIDs from access-controlled incident record
   `P0-BIFROST-EXECVAR-2026-07-22` into an asyncpg `list[UUID]`; do not paste
   them into shell history. Open one database transaction and lock the rows
   with this bound query. Select counts and booleans only; do not return JSONB:

   ```sql
   SELECT id
   FROM executions
   WHERE workflow_id = 'a2e75961-9f0e-4a96-9ee5-a9d240cdda08'::uuid
     AND id = ANY($1::uuid[])
   ORDER BY id
   FOR UPDATE;
   ```

4. Define `matching_row_count` as the number of rows returned by that query.
   Separately count locked rows with non-null variables. Require
   `matching_row_count = 11` and `variables_nonnull_count = 11`. Any mismatch
   rolls back and stops.
5. Replace the whole `variables` document, rather than trying to find every
   nested secret-bearing local:

   ```sql
   UPDATE executions
   SET variables = jsonb_build_object(
       'redacted', true,
       'reason', 'security_incident_2026_07_22'
   )
   WHERE workflow_id = 'a2e75961-9f0e-4a96-9ee5-a9d240cdda08'::uuid
     AND id = ANY($1::uuid[]);
   ```

   Execute this with asyncpg and bind the same access-controlled `list[UUID]`
   as parameter `$1`; no manual SQL substitution is permitted.

6. Before commit, verify by count/boolean only that all 11 rows equal the
   sentinel and that no other row changed. Roll back on any contradiction.
7. Commit, then verify the public/admin API returns only the sentinel for each
   ID. Do not print the complete execution objects.
8. Record the transaction time. Treat all PITR restore points before that time
   as contaminated until the same exact-ID redaction is applied to the restored
   database. Retain normal recovery posture and let those restore points expire
   after the configured seven-day window.

The procedure is fully reversible until step 7 by transaction rollback. After
commit, deliberate redaction should not be made reversible by creating another
secret-bearing copy. If legal or evidentiary requirements demand post-commit
reversal, that is a separate operator decision: restore a pre-redaction PITR
point into an isolated server with access logging and no application ingress,
then destroy that server when the approved evidence task ends. A point-in-time
restore is server-wide, not a surgical edit.

Deleting all 11 execution rows is an alternative, but it removes audit history
and is not recommended while exact-field redaction is sufficient.

## SIDEXIS credential rotation plan (not authorized)

The current credential authority is a vendor-documented SIDEXIS 4.3 default
embedded in the authored workspace workflow. The value must not remain in
authored source, Git history, execution history, operator notes, or deployment
logs. Future automation should resolve a per-customer/per-instance record from
the approved Keeper/IT Glue lane and pass it as an in-memory secret type.

Rotation scope is the five `SIDEXIS_SQL` bindings in the access-controlled
incident record. Do not add any other instance without separate evidence that
it used the affected credential.

For each instance, strictly one at a time:

1. Re-prove customer, device, hostname, and `SIDEXIS_SQL` binding; require the
   device online and no active Ninja job.
2. Locate or create the exact customer Keeper record. Generate a unique random
   per-instance credential directly into the vault; never stage it in a file,
   shell history, workflow input, or ticket.
3. Validate a separate customer-specific break-glass/sysadmin login before
   changing the vendor login. Stop if no independent recovery login works.
4. Inventory service/application dependencies on the vendor login using
   metadata-only configuration checks. Coordinate with the dental application
   owner/vendor if SIDEXIS services still consume it.
5. Schedule a customer maintenance window and notify the assigned MTG owner/NOC
   of the exact instance and rollback contact.
6. Change only the login on that exact SQL instance, update every proven
   dependent secret store in the same window, and read back login validity and
   sysadmin membership without returning credential material.
7. Validate, in order: SIDEXIS application login, dependent Windows services,
   SQLWriter/VSS health, backup health, customer smoke test, and the
   non-mutating FRK health verifier.
8. Soak the instance before proceeding to the next customer.

Rollback requires the independently validated sysadmin login. During the
maintenance window only, keep the previous credential in a restricted,
time-limited Keeper rollback field. If application validation fails, restore
the old login credential and dependent configuration, revalidate the
application, stop the wave, and involve the vendor. Delete the rollback field
after the agreed soak period.

Required communication:

- customer maintenance notice describing possible brief SIDEXIS interruption;
- internal assignment of one owner, verifier, and rollback authority per site;
- vendor coordination where the default is product-managed or undocumented
  consumers are found; and
- completion notice recording only instance identity, timestamps, validation
  state, and vault record reference, never credential material.

## Operator decisions still required

1. Approve exact-field active-row redaction for the 11 IDs, or choose full-row
   deletion instead.
2. Accept transaction/PITR-only reversibility (recommended), or separately
   authorize creation of a new restricted evidence copy.
3. Keep the seven-day PostgreSQL recovery window and let contaminated restore
   points expire naturally (recommended), or accept the recovery-risk tradeoff
   of broader backup destruction/retention changes.
4. Approve the five-site credential rotation and customer/vendor communication
   windows.
