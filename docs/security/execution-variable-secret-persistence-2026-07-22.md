# Execution-variable secret persistence security advisory

## Summary

A defect could allow sensitive values captured from workflow execution locals to
be persisted with execution records. The remediation now sanitizes captured
variables before result handoff and again immediately before persistence.

The sanitizer recursively redacts sensitive values, including values associated
with secret-bearing names, executable text, `SecretString` values, and binary
data. Regression coverage verifies that synthetic secret markers cannot be
recovered through worker output, persistence inputs, or execution readback.

## Operational information

Incident records, affected-resource identifiers, infrastructure details,
retention posture, and remediation status are maintained only in the
access-controlled incident-response system. They are intentionally not
published in this repository.

If you believe your deployment may be affected, contact your security or
support representative through the established private support channel.
