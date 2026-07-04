from __future__ import annotations

import ast

import pytest

from src.services.file_storage.ast_parser import ASTMetadataParser


def _expr(source: str) -> ast.AST:
    return ast.parse(source).body[0].value  # type: ignore[union-attr]


def _function(source: str) -> ast.FunctionDef:
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


class TestParseDecorator:
    def test_accepts_bare_supported_decorator(self) -> None:
        parser = ASTMetadataParser()
        decorator = _function("@workflow\ndef run():\n    pass").decorator_list[0]

        assert parser.parse_decorator(decorator) == ("workflow", {})

    def test_accepts_call_decorator_with_literal_kwargs(self) -> None:
        parser = ASTMetadataParser()
        decorator = _function(
            '@bifrost.tool(name="Create Ticket", retry=True, tags=["halo", "ticket"])\n'
            "def run():\n"
            "    pass"
        ).decorator_list[0]

        assert parser.parse_decorator(decorator) == (
            "tool",
            {"name": "Create Ticket", "retry": True, "tags": ["halo", "ticket"]},
        )

    def test_rejects_unrelated_decorators(self) -> None:
        parser = ASTMetadataParser()
        decorator = _function("@pytest.mark.slow\ndef run():\n    pass").decorator_list[0]

        assert parser.parse_decorator(decorator) is None


class TestAstValueToPython:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("'hello'", "hello"),
            ("42", 42),
            ("['a', 1]", ["a", 1]),
            ("{'enabled': True, 'limit': 3}", {"enabled": True, "limit": 3}),
            ("unknown_name", None),
            ("call()", None),
        ],
    )
    def test_converts_literal_ast_nodes(self, source: str, expected: object) -> None:
        assert ASTMetadataParser().ast_value_to_python(_expr(source)) == expected


class TestAnnotationConversion:
    @pytest.mark.parametrize(
        ("annotation", "expected"),
        [
            ("str", "string"),
            ("int", "int"),
            ("float", "float"),
            ("bool", "bool"),
            ("list[str]", "list"),
            ("dict[str, object]", "json"),
            ("Optional[str]", "string"),
            ("Literal['open', 'closed']", "string"),
            ("CustomType", "json"),
            ("str | None", "string"),
        ],
    )
    def test_annotation_to_ui_type(self, annotation: str, expected: str) -> None:
        func = _function(f"def run(value: {annotation}):\n    pass")

        assert ASTMetadataParser().annotation_to_ui_type(func.args.args[0].annotation) == expected

    @pytest.mark.parametrize(
        ("annotation", "expected"),
        [
            ("str", "str"),
            ("module.Type", "module.Type"),
            ("list[str]", "list[...]"),
            ("str | None", "str | None"),
        ],
    )
    def test_annotation_to_string(self, annotation: str, expected: str) -> None:
        func = _function(f"def run(value: {annotation}):\n    pass")

        assert ASTMetadataParser().annotation_to_string(func.args.args[0].annotation) == expected

    @pytest.mark.parametrize(
        ("annotation", "is_optional"),
        [
            ("Optional[str]", True),
            ("str | None", True),
            ("None | str", True),
            ("str", False),
        ],
    )
    def test_is_optional_annotation(self, annotation: str, is_optional: bool) -> None:
        func = _function(f"def run(value: {annotation}):\n    pass")

        assert ASTMetadataParser().is_optional_annotation(func.args.args[0].annotation) is is_optional

    def test_extract_literal_options(self) -> None:
        func = _function("def run(status: Literal['open', 'closed']):\n    pass")

        assert ASTMetadataParser().extract_literal_options(func.args.args[0].annotation) == [
            {"label": "open", "value": "open"},
            {"label": "closed", "value": "closed"},
        ]

    def test_extract_literal_options_returns_none_for_non_literal(self) -> None:
        func = _function("def run(status: str):\n    pass")

        assert ASTMetadataParser().extract_literal_options(func.args.args[0].annotation) is None


class TestExtractParametersFromAst:
    def test_extracts_parameter_metadata_and_skips_context(self) -> None:
        func = _function(
            "def run("
            "self, context, title: str, max_count: int = 3, "
            "status: Literal['open', 'closed'] = 'open', note: str | None = None"
            "):\n"
            "    pass"
        )

        assert ASTMetadataParser().extract_parameters_from_ast(func) == [
            {
                "name": "title",
                "type": "string",
                "required": True,
                "label": "Title",
            },
            {
                "name": "max_count",
                "type": "int",
                "required": False,
                "label": "Max Count",
                "default_value": 3,
            },
            {
                "name": "status",
                "type": "string",
                "required": False,
                "label": "Status",
                "default_value": "open",
                "options": [
                    {"label": "open", "value": "open"},
                    {"label": "closed", "value": "closed"},
                ],
            },
            {
                "name": "note",
                "type": "string",
                "required": False,
                "label": "Note",
            },
        ]

    def test_skips_execution_context_annotation(self) -> None:
        func = _function("def run(value: str, ctx: ExecutionContext):\n    pass")

        assert ASTMetadataParser().extract_parameters_from_ast(func) == [
            {
                "name": "value",
                "type": "string",
                "required": True,
                "label": "Value",
            }
        ]
