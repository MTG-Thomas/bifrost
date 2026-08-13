from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_snyk_scan_failures_are_not_tolerated() -> None:
    workflow = (REPO_ROOT / ".github/workflows/snyk.yml").read_text(encoding="utf-8")

    assert "continue-on-error" not in workflow
    assert "SNYK_TOKEN is required" in workflow
    assert "exit 1" in workflow
    assert '      - "requirements*.lock"' in workflow
    assert '      - "k8s/**"' in workflow
    assert "--policy-path=.snyk" in workflow


def test_snyk_policy_exceptions_are_time_bounded() -> None:
    policy = (REPO_ROOT / ".snyk").read_text(encoding="utf-8")

    assert policy.count("expires: 2026-09-13") == 4
    assert "no compatible release currently permits" in policy
