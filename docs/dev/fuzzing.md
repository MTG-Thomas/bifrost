# Fuzzing And Property Tests

Bifrost uses lightweight property-based tests as the first fuzzing layer:

- Python: Hypothesis tests run inside pytest.
- TypeScript: fast-check tests run inside Vitest.

Keep these tests deterministic, local, and focused on pure boundaries before
adding coverage-guided fuzzing infrastructure.

## Good Initial Targets

Prefer surfaces that accept user-authored, workflow-authored, serialized, or
vendor-sourced input:

- Manifest YAML import/export round trips.
- Solution package and file payload parsing.
- Editor search and regex validation.
- Cron expression validation and preview.
- Webhook payload parsing.
- SDK/reference scanning over arbitrary Python-like text.
- Frontend form/schema generation and API error normalization.

## Test Shape

Each property test should state one invariant. Good examples:

- Literal search matches normal substring semantics.
- Invalid regex patterns fail with controlled validation errors.
- Exported manifests import without data loss.
- Malformed webhook payloads either parse into trusted data or fail cleanly.
- Frontend schema generation never throws for supported field definitions.

Keep default PR budgets modest. Use explicit settings in the test when the
default is too slow or too shallow.

## Commands

Run targeted Python property tests:

```bash
./test.sh tests/unit/services/test_editor_search.py -m property
```

Run a deeper Python property pass:

```bash
BIFROST_FUZZ_EXAMPLES=100000 \
BIFROST_REGEX_VALIDATION_FUZZ_EXAMPLES=100000 \
BIFROST_REGEX_SEARCH_FUZZ_EXAMPLES=100000 \
./test.sh tests/unit/services/test_editor_search.py -m property
```

Run targeted frontend property tests:

```bash
cd client
npm test -- --run src/lib/api-error.property.test.ts
```

Run a deeper frontend property pass:

```bash
cd client
BIFROST_FAST_CHECK_RUNS=1000000 npm test -- --run src/lib/api-error.property.test.ts
```

Run the scheduled fuzzing workflow manually from GitHub Actions when you want a
clean Linux readback without waiting for the weekly schedule.

Run ClusterFuzzLite code-change fuzzing from GitHub Actions when a change touches
the Atheris-backed harnesses or the parser surfaces they exercise. The workflow
is intentionally Linux-only and Docker-backed because ClusterFuzzLite's Python
support runs through Atheris and the OSS-Fuzz builder images.

Run OpenAPI contract fuzzing from GitHub Actions when endpoint contracts,
validation, or response schemas change. The `API Contract Fuzzing` workflow boots
the test stack and runs Schemathesis against `/openapi.json` with a bounded
example count. Keep this stack-backed because it exercises live HTTP behavior,
not just pure functions.

Run the deterministic fuzz corpus harnesses:

```bash
cd api
../.venv/bin/python -m fuzz.runner
```

On Windows:

```powershell
Set-Location api
..\.venv\Scripts\python.exe -m fuzz.runner
```

## Harnesses And Corpora

Dedicated harnesses live under `api/fuzz/`:

- `editor-search` exercises literal and regex editor search.
- `cron-parser` exercises cron validation and human-readable conversion.
- `webhook-request` exercises webhook body decoding and JSON parsing.

Seed inputs live in `api/fuzz/corpora/<target>/`. Keep them small and
human-reviewable. When a property test, harness run, ClusterFuzzLite job, or
future OSS-Fuzz run finds a bug, add the minimized input to the matching corpus
and add a normal regression test when the behavior needs a precise assertion.

ClusterFuzzLite entry points live under `api/fuzz/atheris_targets/`. They should
stay as thin Atheris wrappers around `api/fuzz/harnesses.py`; put target behavior
in the shared harness functions so corpus tests, manual runs, and
ClusterFuzzLite exercise the same logic.

Stateful and metamorphic property tests live next to the fuzz corpus tests under
`api/tests/unit/fuzz/`. Use stateful tests when a sequence of valid operations can
break an invariant, and metamorphic tests when transformed inputs should preserve
some relationship even if exact output is hard to enumerate.

Corpus filenames should describe the class of input, not the date or incident.
Use incident IDs in comments or PR text, not as the only durable identifier.

## CI Budgets

Normal PR CI runs the property tests through the unit test suites at modest
budgets. The scheduled `Fuzzing` workflow runs higher budgets and the corpus
harnesses without making routine PRs wait on deep fuzzing.

Current scheduled budgets:

- `BIFROST_FUZZ_EXAMPLES=10000`
- `BIFROST_REGEX_VALIDATION_FUZZ_EXAMPLES=10000`
- `BIFROST_REGEX_SEARCH_FUZZ_EXAMPLES=5000`
- `BIFROST_FAST_CHECK_RUNS=100000`

For local investigation, increase those values to `10000` or higher on pure
targets that stay fast.

The Schemathesis workflow currently uses `--max-examples 50` per operation and
only checks for unexpected server errors. Raise this after the initial contract
fuzzing signal is clean; schema/status-code conformance will be more valuable
after the OpenAPI responses are known to be tight enough to avoid noisy failures.

Observed local budget boundaries:

- Frontend API error properties passed at `BIFROST_FAST_CHECK_RUNS=1000000`
  in about 92 seconds on the Windows workstation.
- Backend editor-search properties passed at 100000 examples per split target,
  but took about 5 minutes. Keep scheduled backend regex-search budgets below
  frontend budgets unless running a deliberate long fuzz session.

## Escalation Path

Use this progression:

1. Add Hypothesis or fast-check tests to normal unit suites.
2. Add repo-owned fuzz harnesses for narrow parser/security surfaces.
3. Add ClusterFuzzLite code-change fuzzing for stable Atheris wrappers.
4. Add stack-backed Schemathesis runs for OpenAPI contract fuzzing.
5. Add stateful or model-based tests for lifecycle-heavy behavior.
6. Add metamorphic tests for hard-oracle invariants.
7. Run targeted mutation testing to find weak assertions before broadening a
   fuzz target.
8. Propose full OSS-Fuzz onboarding upstream after Bifrost has several stable
   fuzz targets and useful seed corpora.

## Mutation Testing

Mutation testing is an assertion-quality check, not a normal PR gate. Start with
small pure targets before trying broad backend modules:

```bash
scripts/run_mutation_spike.sh
```

The current `mutmut` config is scoped to cron parsing and editor search. Review
surviving mutants manually, then either strengthen assertions or document why a
survivor is acceptable before raising any mutation threshold.

Every discovered failing input should become a normal regression test or a
checked-in seed corpus entry before the fuzzing bug is closed.
