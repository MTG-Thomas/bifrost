from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_snyk_scan_failures_are_not_tolerated() -> None:
    workflow = (REPO_ROOT / ".github/workflows/snyk.yml").read_text(encoding="utf-8")

    assert "continue-on-error" not in workflow
    assert "SNYK_TOKEN is required" in workflow
    assert "exit 1" in workflow
    assert '      - "requirements*.lock"' in workflow
    assert '      - "k8s/**"' in workflow
