# Bifrost Mature Fuzzing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a four-layer fuzzing lane for Bifrost: property tests, dedicated harnesses, scheduled deep runs, and corpus/regression discipline.

**Architecture:** Keep routine fuzzing inside existing pytest/Vitest unit surfaces, add deterministic Python harnesses under `api/fuzz/`, and run higher budgets through a scheduled GitHub Actions workflow. Coverage-guided fuzzing remains a later escalation once the deterministic harnesses and corpora are stable.

**Tech Stack:** Python pytest, Hypothesis, TypeScript Vitest, fast-check, GitHub Actions, Bifrost repo-owned corpus files.

---

### Task 1: Property Tests In Normal CI

**Files:**
- Modify: `api/tests/unit/services/test_editor_search.py`
- Modify: `client/src/lib/api-error.property.test.ts`
- Modify: `api/pytest.ini`
- Modify: `pyproject.toml`
- Modify: `requirements.lock`
- Modify: `client/package.json`
- Modify: `client/package-lock.json`

- [x] **Step 1: Add Hypothesis and fast-check dependencies**

Add `hypothesis` to Python dependencies and `fast-check` to client dev dependencies.

- [x] **Step 2: Add backend property tests**

Add literal-search and regex-search properties for `src.services.editor.search._search_content`.

- [x] **Step 3: Add frontend property tests**

Add API error normalization properties for `parseApiError` and `getErrorMessage`.

- [x] **Step 4: Verify property tests**

Run:

```powershell
$env:BIFROST_FUZZ_EXAMPLES='10000'; .\.venv\Scripts\python.exe -m pytest api\tests\unit\services\test_editor_search.py -m property -vv
$env:BIFROST_FAST_CHECK_RUNS='10000'; npm test -- --run src/lib/api-error.property.test.ts
```

Expected: both pass.

### Task 2: Dedicated Fuzz Harnesses

**Files:**
- Create: `api/fuzz/__init__.py`
- Create: `api/fuzz/harnesses.py`
- Create: `api/fuzz/runner.py`
- Create: `api/fuzz/corpora/cron-parser/*.txt`
- Create: `api/fuzz/corpora/editor-search/*.txt`
- Create: `api/fuzz/corpora/webhook-request/*`
- Create: `api/tests/unit/fuzz/test_harness_runner.py`

- [x] **Step 1: Write failing harness tests**

Test that every registered harness has seed corpus cases, seed corpus cases do
not crash, unknown targets are rejected, and corpus case IDs are stable.

- [x] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest api\tests\unit\fuzz\test_harness_runner.py -q
```

Expected initial failure: `ModuleNotFoundError: No module named 'fuzz'`.

- [x] **Step 3: Add harness implementation**

Implement deterministic harnesses for editor search, cron parser, and webhook
request body parsing.

- [x] **Step 4: Add seed corpora**

Add small human-reviewable seed files for valid, invalid, and edge-ish inputs.

- [x] **Step 5: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest api\tests\unit\fuzz\test_harness_runner.py -q
```

Expected: all harness tests pass.

### Task 3: Scheduled Deep Fuzzing

**Files:**
- Create/modify: `.github/workflows/fuzzing.yml`

- [x] **Step 1: Add scheduled workflow**

Run weekly and on manual dispatch. Install Python/client deps from lockfiles.

- [x] **Step 2: Run higher property budgets**

Use `BIFROST_FUZZ_EXAMPLES=1000` and `BIFROST_FAST_CHECK_RUNS=1000`.

- [x] **Step 3: Run corpus harnesses**

Run:

```bash
cd api
../.venv/bin/python -m fuzz.runner
```

Expected: prints the number of corpus cases and exits zero.

### Task 4: Corpus And Regression Discipline

**Files:**
- Modify: `docs/dev/fuzzing.md`
- Create: `docs/superpowers/plans/2026-07-04-bifrost-mature-fuzzing.md`

- [x] **Step 1: Document target selection**

Document high-value fuzz targets and when to use property tests versus harnesses.

- [x] **Step 2: Document corpus handling**

Document where seed corpora live, naming guidance, and how minimized failures
become corpus entries plus normal regression tests.

- [x] **Step 3: Document escalation path**

Document the path from property tests to deterministic harnesses, then
ClusterFuzzLite, then upstream OSS-Fuzz onboarding.

### Task 5: Final Verification

**Files:**
- All files above.

- [x] **Step 1: Run focused Python tests**

```powershell
.\.venv\Scripts\python.exe -m pytest api\tests\unit\services\test_editor_search.py api\tests\unit\fuzz\test_harness_runner.py -q
```

- [x] **Step 2: Run corpus harnesses**

```powershell
Set-Location api
..\.venv\Scripts\python.exe -m fuzz.runner
Set-Location ..
```

- [x] **Step 3: Run frontend property tests**

```powershell
Set-Location client
$env:BIFROST_FAST_CHECK_RUNS='10000'
npm test -- --run src/lib/api-error.property.test.ts
Set-Location ..
```

- [x] **Step 4: Run hygiene checks**

```powershell
git diff --check
python api\scripts\check_github_action_pins.py
python api\scripts\check_mtg_ci_boundaries.py
```

Expected: all pass, with any skipped platform checks reported explicitly.
