# Autotask RMM Migration Worksheet

This note captures the current Bifrost-backed review of Autotask automation-created tickets for DattoRMM to NinjaOne migration planning.

Window reviewed:
- `2026-01-01` through `2026-03-30`

Current working automation signals:
- Creator resources:
  - `RMMAPI User`
  - `Autotask Administrator`
  - `MSPintegrations API`
  - `CyberCNS API`
- Sources:
  - `RMM Agent`
  - `Monitoring Alert`
  - `Recurring`
- Queues:
  - `System Monitoring`
  - `Synology`
- Contact emails:
  - `notifications@itglue.com`
  - `noreply@meraki.com`

Current baseline from `Autotask: Report Automation Impact`:
- `2,765 / 5,950` automation-related tickets (`46.47%`)
- `613.32h` downstream worked time

Company classification note:
- The Autotask company cache now stores `company_classification` and `company_classification_label`.
- The automation-impact report now exposes top automation companies and top automation company classifications.
- A full quarter work-item resync is needed before those classification buckets are fully trustworthy across the historical ticket set.

Notes on interpretation:
- `Recurring` is real automation, but it is mostly scheduled service automation rather than reactive RMM alerting.
- Workflow-state queues like `Triage`, `Quick Fix`, `Accepted`, and `Merged Tickets` should not be used as automation-origin signals.
- `Email` is too broad to use as a global automation-origin signal. Sender-specific rules are preferred.

## Recommended Buckets

### Keep And Migrate

| Pattern / Signal | Approx Volume | Suggested NinjaOne Approach | Notes |
|---|---:|---|---|
| `Server Down` | `150` | Native device/server-down monitoring with direct ticket creation | High-signal outage detection |
| Service failure / stopped service alerts | recurring | Service health policies with thresholding | Strong machine-detectable condition |
| Hardware critical alerts | recurring | Hardware health monitors with vendor/event filtering | High operational value |
| Security / ransomware detections | lower volume | Security integration or scripted alert ingestion | High consequence, worth preserving |
| Vulnerability / EPSS checks | moderate | Security workflow integration | More security than pure RMM, still valuable |

### Migrate But Tune

| Pattern / Signal | Approx Volume | Suggested NinjaOne Approach | Notes |
|---|---:|---|---|
| `Backup finished ... Failed` | `115` keyword matches | Group by endpoint/customer, suppress duplicates, escalate after repeat failures | High noise today |
| Cove backup failure alerts | multiple repeated families | Consolidated backup-health workflow with cooldown windows | Valuable but noisy |
| Low disk-space alerts | many repeated variants | Threshold + duration + repeat suppression | Useful, but currently over-ticketing |
| `Reboot Needed` | `143` | Maintenance/approval workflow instead of direct ticket per event | Likely too chatty as-is |

### Separate From RMM Migration

| Pattern / Signal | Approx Volume | Suggested Treatment | Notes |
|---|---:|---|---|
| `Recurring` monthly check-ins / scheduled maintenance | `654` (`Source = Recurring`) | Review as scheduled automation, not as DattoRMM-to-NinjaOne work | Real automation, different class |
| Sender-specific automation mailflows | low so far in sampled rule set | Keep as explicit sender rules | Avoid generic `Email` classification |

### Do Not Use As Origin Signals

| Queue / Signal | Why Not |
|---|---|
| `Accepted` | Workflow state after technician acceptance |
| `Quick Fix` | Workflow state / handling bucket, not origin |
| `Triage` | New-ticket intake state, not origin |
| `Merged Tickets` | Ticket lifecycle artifact, not origin |
| `Email` | Too broad; mixes human and automated flows |

## RMMAPI User Review

`RMMAPI User` is the main DattoRMM-to-NinjaOne migration focus.

Quarter totals within the current automation-related set:
- `1,275` tickets

Dominant sources:
- `RMM Agent`: `1,090`
- `Monitoring Alert`: `185`

Dominant queues:
- `Quick Fix`: `538`
- `System Monitoring`: `343`
- `Accepted`: `209`
- `Triage`: `87`

Repeated noisy patterns worth review:
- `Server Down`: `150`
- `Reboot Needed`: `143`
- `Backup finished ... Failed`: `115`
- `Severity: Critical ... power supply redundancy is lost`: `27`

Other repeated families:
- `Monitor Cove Data Protection ... Failed backups found`
- Low disk-space alerts on `C:`
- `Carestream Keyservice not running`
- `vmms isStopped`

## High-Time Automation Tickets

These automation-related tickets had unusually high worked time in the reviewed window:

| Ticket | Hours | Why It Matters |
|---|---:|---|
| `T20260101.0004` | `16.03` | Ransomware detection; high-value automation catch |
| `T20260210.0052` | `11.05` | Automation-created but still required heavy technician effort |
| `T20260204.0039` | `7.87` | Real incident with meaningful downstream labor |
| `T20260302.0133` | `6.78` | Vulnerability automation with clear operational value |
| `T20260311.0022` | `6.70` | Automation-created incident that still required manual service work |

## Current Recommendation

Use two reporting cuts:

1. `Automation-created, all`
   - Creator resources
   - `RMM Agent`
   - `Monitoring Alert`
   - `Recurring`
   - `System Monitoring`
   - `Synology`
   - explicit sender rules

2. `Automation-created, RMM/alert-focused`
   - Creator resources
   - `RMM Agent`
   - `Monitoring Alert`
   - `System Monitoring`
   - `Synology`
   - explicit sender rules

The second cut is the better metric for DattoRMM to NinjaOne migration planning.

## Migration Priority

Use the `RMM/alert-focused` cut as the primary migration backlog:
- `RMM Agent`
- `Monitoring Alert`
- `RMMAPI User`
- `System Monitoring`
- `Synology`
- explicit sender rules where needed

Approximate quarter volume in that migration-focused slice:
- `1,626` tickets

### Wave 1: High-Value Monitoring

Migrate first:
- `Server Down`
- service failure / stopped service alerts
- hardware critical alerts
- ransomware / security detections

Reason:
- highest operational value
- most clearly maps to RMM monitoring
- most obvious regression risk if lost during migration

Top customer candidates for Wave 1 based on observed volume and signal concentration:
- `Winthrop Capital Management`
- `INIM`
- `Green Dental Lyons`
- `Anthony Products`
- `Indiana Health Group`

### Wave 2: Important But Noisy

Migrate second, with redesign:
- low disk-space alerts
- Cove backup failures
- `Backup finished ... Failed`
- `Reboot Needed`

Reason:
- large volume
- real value
- currently noisy enough that straight porting would preserve avoidable ticket churn

Top customer candidates for Wave 2:
- `Insource`
- `Potts, Hannah & Fischer, P.C.`
- `Starr Oral Surgery`
- `Noles Family Dental`
- `Declaration Dental`

### Wave 3: Scheduled / Non-Reactive Automation

Handle separately from the core RMM migration:
- `Recurring` monthly check-ins
- recurring scheduled maintenance
- sender-specific email automations that are not RMM-originated

Reason:
- real automation, but different implementation and business value than reactive monitoring

## Customer Priorities

These customer accounts showed the highest ticket volume in the RMM-focused slice reviewed so far.

| Customer | Approx Tickets | Dominant Families | Suggested Priority |
|---|---:|---|---|
| `Winthrop Capital Management` | `43` | low disk, server down | `High` |
| `INIM` | `42` | low disk, server down | `High` |
| `Green Dental Lyons` | `40` | server down, reboot needed, Cove backup failures | `High` |
| `Kluth Richardson Family and Cosmetic Dentistry` | `39` | mixed RMM noise, backup failures | `Medium` |
| `Potts, Hannah & Fischer, P.C.` | `38` | low disk, server down, reboot needed | `High` |
| `Thompson Family Dentistry` | `36` | mixed, server down, reboot needed | `Medium` |
| `Law Office of Elizabeth Homes, LLC` | `34` | mixed, low disk | `Medium` |
| `Carmel Dental Group` | `32` | mixed, server down | `Medium` |
| `Anthony Products` | `32` | server down, reboot needed, Cove backup failures | `High` |
| `Insource` | `30` | low disk, backup failures | `High` |

Priority guidance:
- `High`: customers with strong concentration in outage, disk, backup, or core infrastructure alert families
- `Medium`: customers with meaningful volume but a more mixed pattern set

## Pattern Priorities

These are the strongest immediate pattern candidates in the RMM-focused slice:

| Pattern Family | Approx Volume | Migration Priority | Suggested Treatment |
|---|---:|---|---|
| `Server Down` | `150` | `P1` | migrate directly |
| `low disk free` | `210` | `P1/P2` | migrate with tuning and suppression |
| `Cove backup failed` | `156` | `P1/P2` | migrate with grouping and cooldowns |
| `Reboot Needed` | `143` | `P2` | redesign into maintenance workflow |
| `Backup finished ... Failed` | `66` in explicit family, larger in related variants | `P2` | consolidate duplicate backup-failure paths |
| `power supply` / hardware critical | `25+` | `P1` | migrate directly |
| `security risks detected` | `25` | `P1` | migrate directly |
| `LSV sync error` | `25` | `P2` | migrate with backup-alert rationalization |
| `vmms stopped` | `17` | `P1` | migrate directly |
| `Synology` security / DSM alerts | narrower | `P1/P2` | migrate, but validate signal quality and routing |

Practical next step:
1. migrate `P1` patterns for the `High` priority customers first
2. redesign `P2` patterns before porting them
3. keep `Recurring` and other scheduled automation out of the first NinjaOne migration tranche
