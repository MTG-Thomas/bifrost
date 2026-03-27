# Meraki Admin Baseline Remediation

## Goal

Use the `Midtown Technology Group` Meraki organization as the operational
baseline for standard Midtown admin access and remediate selected missing
Midtown admins across other Meraki organizations.

For this pass, `eric@carbonpeaktech.com` is treated as a valid Midtown-related
admin and baseline exception.

## Baseline

Baseline organization:

- `Midtown Technology Group`

Standard admin set for this pass:

- `patrick@midtowntg.com`
- `steven@midtowntg.com`
- `trevor@midtowntg.com`
- `miles@midtowntg.com`
- `matt@midtowntg.com`
- `bfunk@midtowntg.com`
- `chris@midtowntg.com`
- `scott@midtowntg.com`

Observed baseline access shape for these admins:

- `orgAccess = full`
- no tag restrictions
- no network restrictions

## Workflows Added

Reusable Meraki workflows added for this operational pattern:

- `Meraki: Audit Admins Against Baseline Organization`
- `Meraki: Sync Admins From Baseline Organization`
- `Meraki: Remove Admin Across Organizations`
- `Meraki: Get Admin Governance Policy`
- `Meraki: Save Admin Governance Policy`
- `Meraki Admin Governance` app

These workflows are intended to support standard add/change remediation based on
a known-good Meraki org rather than an inferred domain-wide heuristic. They now
also support explicit org exclusions for legacy or vendor-disabled orgs.

The durable policy for exclusions and procurement-license org handling now lives
in Bifrost config and is managed through the `Meraki Admin Governance` app
rather than being treated as intrinsic workflow defaults. The workflows still
accept override parameters for one-off execution, but persistent operational
policy should now be changed through that config surface.

## Live Remediation Scope

The remediation pass targets all auditable Meraki organizations missing any of
the selected standard admins above.

Known non-remediated orgs due Meraki API `403` on unlicensed orgs:

- `Jacobson Hile Kight`
- `Cynthia L Hovey DDS`
- `Connected Healthcare Systems`

Known intentionally excluded org:

- `Taylor Computer Solutions`

Known excluded org list for future baseline runs:

- `Taylor Computer Solutions`
- `Jacobson Hile Kight`
- `Cynthia L Hovey DDS`
- `Connected Healthcare Systems`
- `MTG Kntlnd Licenses`
- `MTG More Licenses`
- `MTG WAP Licenses`
- `MTGLicense`

The standard baseline workflows now exclude those orgs by default so technician
onboarding or broad Midtown-admin rollout runs do not copy customer-facing
baseline admins into the procurement/license staging orgs.

## Outcome

Before remediation, the selected admin set had `238` missing placements across
the audited Meraki estate.

After remediation, the selected admin set is fully aligned across the active
Meraki orgs we intend to manage.

Remaining admin drift for the selected set exists only in explicitly excluded or
vendor-disabled orgs:

- intentionally excluded: `Taylor Computer Solutions`
- Meraki-disabled for non-payment:
  - `Jacobson Hile Kight`
  - `Cynthia L Hovey DDS`
  - `Connected Healthcare Systems`

## Follow-up Cleanup

Confirmed typo account cleanup:

- `tleuke@midtowntg.com` was removed across the active managed Meraki orgs via
  `Meraki: Remove Admin Across Organizations`

Broader Meraki hygiene work still remaining after the baseline remediation and
typo-account cleanup:

- review and remove legacy or vendor admins such as:
  - `stephen@bionic-cat.com`
  - `doug@techsupportindy.com`
  - `dawn@gethotboxpizza.com`
- review intentional extra Midtown admins not present in the baseline org, such
  as:
  - `mgarcia@midtowntg.com`
  - `regina@midtowntg.com`
  - `kmiller@midtowntg.com`
- decide whether the broader baseline should also include additional Midtown
  staff currently missing from many orgs, especially:
  - additional staff beyond the now-remediated second batch, if needed

## Follow-up Remediation Batch

A second Midtown-admin remediation batch was executed using the same
`Meraki: Sync Admins From Baseline Organization` primitive, but in
single-admin executions after the combined batch timed out.

Second-batch admins:

- `koerner@midtowntg.com`
- `adam@midtowntg.com`
- `mike@midtowntg.com`
- `doug@midtowntg.com`
- `tim@midtowntg.com`

Observed change totals from the successful per-admin workflow executions:

- `koerner@midtowntg.com`: `11` creates, `1` update
- `adam@midtowntg.com`: `9` creates, `32` updates
- `mike@midtowntg.com`: `6` creates, `36` updates
- `doug@midtowntg.com`: `4` creates, `22` updates
- `tim@midtowntg.com`: `7` creates, `2` updates

Net second-batch totals:

- `37` admin creates
- `93` admin updates

During this pass, the workflow exposed a primitive bug: the sync logic was
deduping against only the filtered/eligible admin set, which could trigger a
duplicate-email create attempt when Meraki already had the admin in a filtered
status. That was fixed so baseline sync now dedupes against all discovered org
admins before deciding whether to create or update.

Post-change audit result for the second-batch admin set:

- `0` remaining missing placements across the active managed estate
- exclusions remain unchanged:
  - `Taylor Computer Solutions`
  - `Jacobson Hile Kight`
  - `Cynthia L Hovey DDS`
  - `Connected Healthcare Systems`

## Team Note

This change standardizes the selected Midtown Meraki admin accounts by copying
their baseline access model from the `Midtown Technology Group` org into other
Meraki orgs where they were missing. The intent is to reduce admin drift and
make future Meraki admin additions or changes repeatable from a single baseline.
