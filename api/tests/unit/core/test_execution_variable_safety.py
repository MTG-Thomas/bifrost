from __future__ import annotations

import json

from src.core.execution_variable_safety import sanitize_execution_variables
from src.core.secret_string import REDACTED, SecretString


SYNTHETIC_SCRIPT_MARKER = "SYNTHETIC_EXECUTION_SCRIPT_MARKER"
SYNTHETIC_CREDENTIAL_MARKER = "SYNTHETIC_CREDENTIAL_MARKER"


def test_sensitive_variable_names_are_redacted_recursively() -> None:
    variables = {
        "status": "safe",
        "script": SYNTHETIC_SCRIPT_MARKER,
        "nested": {
            "clientSecret": SYNTHETIC_CREDENTIAL_MARKER,
            "description": "safe description",
        },
        "items": [{"powerShellScript": SYNTHETIC_SCRIPT_MARKER}],
    }

    sanitized = sanitize_execution_variables(variables)

    assert sanitized == {
        "status": "safe",
        "script": REDACTED,
        "nested": {
            "clientSecret": REDACTED,
            "description": "safe description",
        },
        "items": [{"powerShellScript": REDACTED}],
    }
    serialized = json.dumps(sanitized)
    assert SYNTHETIC_SCRIPT_MARKER not in serialized
    assert SYNTHETIC_CREDENTIAL_MARKER not in serialized


def test_executable_text_is_redacted_under_a_generic_container() -> None:
    generated = f"$ErrorActionPreference = 'Stop'\nWrite-Output '{SYNTHETIC_SCRIPT_MARKER}'"

    sanitized = sanitize_execution_variables({"payload": [generated]})

    assert sanitized == {"payload": [REDACTED]}
    assert SYNTHETIC_SCRIPT_MARKER not in json.dumps(sanitized)


def test_single_line_command_is_redacted_under_a_generic_container() -> None:
    generated = f"Invoke-Expression '{SYNTHETIC_SCRIPT_MARKER}'"

    assert sanitize_execution_variables({"payload": generated}) == {
        "payload": REDACTED
    }


def test_secret_string_is_redacted_without_plaintext_recovery() -> None:
    sanitized = sanitize_execution_variables(
        {"value": SecretString(SYNTHETIC_CREDENTIAL_MARKER)}
    )

    assert sanitized == {"value": REDACTED}
    assert SYNTHETIC_CREDENTIAL_MARKER not in json.dumps(sanitized)


def test_description_is_not_misclassified_as_script() -> None:
    assert sanitize_execution_variables(
        {"description": "safe", "status_code": 200}
    ) == {
        "description": "safe",
        "status_code": 200,
    }


def test_authorization_code_remains_sensitive() -> None:
    assert sanitize_execution_variables(
        {"authorization_code": SYNTHETIC_CREDENTIAL_MARKER}
    ) == {"authorization_code": REDACTED}


def test_nested_container_and_opaque_values_fail_safe() -> None:
    class OpaqueValue:
        pass

    sanitized = sanitize_execution_variables(
        {
            "tuple_value": ("safe",),
            "set_value": {7},
            "binary_value": b"not-persisted",
            "opaque_value": OpaqueValue(),
        }
    )

    assert sanitized == {
        "tuple_value": ("safe",),
        "set_value": {7},
        "binary_value": REDACTED,
        "opaque_value": "<OpaqueValue>",
    }
