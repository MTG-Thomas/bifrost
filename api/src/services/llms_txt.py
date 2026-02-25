"""
LLMs.txt generator — reads the template and fills auto-generated tokens.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "llms.txt.md"


def _generate_sdk_tokens() -> dict[str, str]:
    """Generate all SDK-related tokens from actual source code."""
    try:
        from src.services.mcp_server.tools.sdk import (
            _generate_decorator_docs,
            _generate_context_docs,
            _generate_error_docs,
            _generate_module_docs,
            _generate_models_docs,
        )
        from bifrost import (
            ai, config, executions, files, forms,
            integrations, knowledge, organizations,
            roles, tables, users, workflows,
        )

        modules = [
            ("ai", ai), ("config", config), ("executions", executions),
            ("files", files), ("forms", forms), ("integrations", integrations),
            ("knowledge", knowledge), ("organizations", organizations),
            ("roles", roles), ("tables", tables), ("users", users),
            ("workflows", workflows),
        ]

        module_lines = []
        for name, module in modules:
            doc = _generate_module_docs(name, module)
            if doc:
                module_lines.append(doc)

        return {
            "decorator_docs": _generate_decorator_docs(),
            "context_docs": _generate_context_docs(),
            "error_docs": _generate_error_docs(),
            "sdk_module_docs": "\n".join(module_lines),
            "sdk_models_docs": _generate_models_docs(),
        }
    except ImportError as e:
        logger.warning(f"Could not generate SDK tokens: {e}")
        return {
            "decorator_docs": "",
            "context_docs": "",
            "error_docs": "",
            "sdk_module_docs": "",
            "sdk_models_docs": "",
        }


def _generate_model_tokens() -> dict[str, str]:
    """Generate Pydantic model documentation tokens."""
    from src.services.mcp_server.schema_utils import models_to_markdown
    from src.models.contracts.forms import (
        FormCreate, FormUpdate, FormSchema, FormField,
    )
    from src.models.contracts.agents import AgentCreate, AgentUpdate
    from src.models.contracts.tables import TableCreate, TableUpdate

    form_docs = models_to_markdown([
        (FormCreate, "FormCreate"),
        (FormUpdate, "FormUpdate"),
        (FormSchema, "FormSchema"),
        (FormField, "FormField"),
    ], "Form Models")

    agent_docs = models_to_markdown([
        (AgentCreate, "AgentCreate"),
        (AgentUpdate, "AgentUpdate"),
    ], "Agent Models")

    table_docs = models_to_markdown([
        (TableCreate, "TableCreate"),
        (TableUpdate, "TableUpdate"),
    ], "Table Models")

    return {
        "form_model_docs": form_docs,
        "agent_model_docs": agent_docs,
        "table_model_docs": table_docs,
    }


def generate_llms_txt() -> str:
    """Read the template and fill all tokens. Regenerated per request."""
    template = _TEMPLATE_PATH.read_text()

    tokens: dict[str, str] = {}
    tokens.update(_generate_sdk_tokens())
    tokens.update(_generate_model_tokens())

    for key, value in tokens.items():
        template = template.replace("{" + key + "}", value)

    return template
