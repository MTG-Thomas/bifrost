from __future__ import annotations

import sys
import types

from src.services import llms_txt


def _module(name: str, **attrs: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def test_generate_sdk_tokens_returns_empty_sections_when_sdk_import_fails(
    monkeypatch,
) -> None:
    real_import = __import__

    def fail_sdk_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "src.services.mcp_server.tools.sdk":
            raise ImportError("fastmcp unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fail_sdk_import)

    assert llms_txt._generate_sdk_tokens() == {
        "decorator_docs": "",
        "context_docs": "",
        "error_docs": "",
        "sdk_module_docs": "",
        "sdk_models_docs": "",
    }


def test_generate_sdk_tokens_builds_docs_from_imported_sdk_helpers(
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def module_docs(name: str, module: object) -> str:
        calls.append((name, module))
        return f"{name} docs" if name in {"agents", "forms"} else ""

    sdk = _module(
        "src.services.mcp_server.tools.sdk",
        _generate_decorator_docs=lambda: "decorators",
        _generate_context_docs=lambda: "context",
        _generate_error_docs=lambda: "errors",
        _generate_module_docs=module_docs,
        _generate_models_docs=lambda: "models",
    )
    bifrost = _module(
        "bifrost",
        **{
            name: object()
            for name in [
                "agents",
                "ai",
                "config",
                "executions",
                "files",
                "forms",
                "integrations",
                "knowledge",
                "organizations",
                "roles",
                "tables",
                "users",
                "workflows",
            ]
        },
    )

    monkeypatch.setitem(sys.modules, "src.services.mcp_server", _module("src.services.mcp_server"))
    monkeypatch.setitem(sys.modules, "src.services.mcp_server.tools", _module("src.services.mcp_server.tools"))
    monkeypatch.setitem(sys.modules, "src.services.mcp_server.tools.sdk", sdk)
    monkeypatch.setitem(sys.modules, "bifrost", bifrost)

    tokens = llms_txt._generate_sdk_tokens()

    assert tokens == {
        "decorator_docs": "decorators",
        "context_docs": "context",
        "error_docs": "errors",
        "sdk_module_docs": "agents docs\nforms docs",
        "sdk_models_docs": "models",
    }
    assert [name for name, _ in calls] == [
        "agents",
        "ai",
        "config",
        "executions",
        "files",
        "forms",
        "integrations",
        "knowledge",
        "organizations",
        "roles",
        "tables",
        "users",
        "workflows",
    ]


def test_generate_model_tokens_groups_runtime_and_manifest_models(
    monkeypatch,
) -> None:
    seen: list[tuple[str, list[str]]] = []

    def models_to_markdown(models, title: str) -> str:
        names = [name for _, name in models]
        seen.append((title, names))
        return f"{title}: {','.join(names)}"

    dummy_classes = {
        name: type(name, (), {})
        for name in [
            "FormCreate",
            "FormUpdate",
            "FormSchema",
            "FormField",
            "AgentCreate",
            "AgentUpdate",
            "TableCreate",
            "TableUpdate",
            "ManifestWorkflow",
            "ManifestForm",
            "ManifestAgent",
            "ManifestApp",
            "ManifestIntegration",
            "ManifestConfig",
            "ManifestTable",
            "ManifestEventSource",
            "ManifestEventSubscription",
        ]
    }

    monkeypatch.setitem(sys.modules, "src.services.mcp_server", _module("src.services.mcp_server"))
    monkeypatch.setitem(
        sys.modules,
        "src.services.mcp_server.schema_utils",
        _module("src.services.mcp_server.schema_utils", models_to_markdown=models_to_markdown),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.models.contracts.forms",
        _module(
            "src.models.contracts.forms",
            FormCreate=dummy_classes["FormCreate"],
            FormUpdate=dummy_classes["FormUpdate"],
            FormSchema=dummy_classes["FormSchema"],
            FormField=dummy_classes["FormField"],
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.models.contracts.agents",
        _module(
            "src.models.contracts.agents",
            AgentCreate=dummy_classes["AgentCreate"],
            AgentUpdate=dummy_classes["AgentUpdate"],
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.models.contracts.tables",
        _module(
            "src.models.contracts.tables",
            TableCreate=dummy_classes["TableCreate"],
            TableUpdate=dummy_classes["TableUpdate"],
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "bifrost.manifest",
        _module(
            "bifrost.manifest",
            **{name: dummy_classes[name] for name in dummy_classes if name.startswith("Manifest")},
        ),
    )

    tokens = llms_txt._generate_model_tokens()

    assert tokens["form_model_docs"] == "Form Models: FormCreate,FormUpdate,FormSchema,FormField"
    assert tokens["agent_model_docs"] == "Agent Models: AgentCreate,AgentUpdate"
    assert tokens["table_model_docs"] == "Table Models: TableCreate,TableUpdate"
    assert tokens["manifest_docs"].startswith("Manifest YAML Models")
    assert seen[-1][1] == [
        "ManifestWorkflow — .bifrost/workflows.yaml entry",
        "ManifestForm — .bifrost/forms.yaml entry",
        "ManifestAgent — .bifrost/agents.yaml entry",
        "ManifestApp — .bifrost/apps.yaml entry",
        "ManifestTable — .bifrost/tables.yaml entry",
        "ManifestIntegration — .bifrost/integrations.yaml entry",
        "ManifestConfig — .bifrost/configs.yaml entry",
        "ManifestEventSource — .bifrost/events.yaml entry",
        "ManifestEventSubscription — subscription within an event source",
    ]


def test_generate_llms_txt_replaces_template_tokens(tmp_path, monkeypatch) -> None:
    template = tmp_path / "llms.txt.md"
    template.write_text(
        "{decorator_docs}\n{form_model_docs}\n{sdk_module_docs}\n{unknown_token}",
        encoding="utf-8",
    )

    monkeypatch.setattr(llms_txt, "_TEMPLATE_PATH", template)
    monkeypatch.setattr(
        llms_txt,
        "_generate_sdk_tokens",
        lambda: {"decorator_docs": "decorators", "sdk_module_docs": "modules"},
    )
    monkeypatch.setattr(
        llms_txt,
        "_generate_model_tokens",
        lambda: {"form_model_docs": "forms"},
    )

    assert llms_txt.generate_llms_txt() == "decorators\nforms\nmodules\n{unknown_token}"
