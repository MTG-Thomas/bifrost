# OWASP ZAP Baseline Scanning

Bifrost uses a sanctioned OWASP ZAP baseline workflow for passive DAST coverage of the exposed proof deployment.

## Scope

The workflow is intentionally limited to known Midtown-owned Bifrost endpoints:

- POC: `https://20.9.81.122`

The workflow does not accept arbitrary URLs. Add or replace targets in code review so scan scope stays explicit. When a stable hostname replaces the current Azure public IP, update both the workflow matrix and this document in the same change.

## What It Runs

The workflow uses `zaproxy/action-baseline` with the stable ZAP container. This runs the ZAP spider briefly and waits for passive scanning to complete. It is intended for staging and proof systems because it does not run active attack payloads.

Current settings:

- manual `workflow_dispatch` target selection: `all` or `poc`;
- weekly scheduled scan on Monday morning UTC;
- passive baseline only;
- no GitHub issue auto-writing;
- reports uploaded as workflow artifacts;
- `fail_action: false` while the first findings are triaged.

## Operating Notes

- Treat this as perimeter and unauthenticated coverage first.
- Do not enable active scans against the proof environment until there is a seeded throwaway organization, a low-privilege test user, and a cleanup/reset path.
- Do not scan customer production domains or third-party systems from this workflow.
- Do not put bearer tokens, cookies, passwords, or TOTP secrets into workflow logs.
- Review reports after each run and convert real findings into tracked remediation work.

## Next Hardening Steps

1. Review the first baseline artifacts and classify findings into true positives, accepted proof limits, and false positives.
2. Add a small ZAP rules file only after triage, keeping each ignore documented.
3. Replace the public IP target with the stable Bifrost hostname when DNS is ready.
4. Add authenticated coverage with a disposable user and seeded proof data.
5. Consider a separate active-scan workflow gated by an environment approval and pointed only at resettable test data.
