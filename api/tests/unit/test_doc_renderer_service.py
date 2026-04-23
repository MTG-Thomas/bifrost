from __future__ import annotations

from fastapi.testclient import TestClient
from pathlib import Path
import pytest
import sys
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from doc_renderer_service.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "doc_renderer_service.app.render_pdf_from_markdown",
        lambda **kwargs: type(
            "Result",
            (),
            {"pdf_bytes": b"%PDF-markdown", "filename": "rendered-markdown.pdf"},
        )(),
    )
    monkeypatch.setattr(
        "doc_renderer_service.app.render_pdf_from_html",
        lambda **kwargs: type(
            "Result",
            (),
            {"pdf_bytes": b"%PDF-html", "filename": "rendered-html.pdf"},
        )(),
    )
    return TestClient(create_app(api_token="test-token"))


def test_markdown_endpoint_renders_pdf(client: TestClient) -> None:
    response = client.post(
        "/render/markdown-pdf",
        headers={"Authorization": "Bearer test-token"},
        json={
            "title": "Quarterly Brief",
            "theme": "clean_report",
            "markdown": "# Hello",
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].endswith('"rendered-markdown.pdf"')
    assert response.content == b"%PDF-markdown"


def test_html_endpoint_renders_pdf(client: TestClient) -> None:
    response = client.post(
        "/render/html-pdf",
        headers={"Authorization": "Bearer test-token"},
        json={
            "title": "Status Report",
            "theme": "executive_brief",
            "html": "<h1>Hello</h1>",
        },
    )
    assert response.status_code == 200
    assert response.content == b"%PDF-html"


def test_renderer_rejects_missing_bearer_token(client: TestClient) -> None:
    response = client.post(
        "/render/markdown-pdf",
        json={"theme": "clean_report", "markdown": "# Hello"},
    )
    assert response.status_code == 401


def test_renderer_rejects_invalid_theme(client: TestClient, monkeypatch) -> None:
    def raise_value_error(**kwargs):
        raise ValueError("Unsupported theme 'nope'. Supported themes: business_case, clean_report, executive_brief")

    monkeypatch.setattr(
        "doc_renderer_service.app.render_pdf_from_html",
        raise_value_error,
    )
    response = client.post(
        "/render/html-pdf",
        headers={"Authorization": "Bearer test-token"},
        json={"theme": "nope", "html": "<p>bad</p>"},
    )
    assert response.status_code == 400


def test_renderer_bubbles_render_failures(client: TestClient, monkeypatch) -> None:
    from doc_renderer_service.rendering import RenderError

    def raise_render_error(**kwargs):
        raise RenderError("pandoc failed")

    monkeypatch.setattr(
        "doc_renderer_service.app.render_pdf_from_markdown",
        raise_render_error,
    )
    response = client.post(
        "/render/markdown-pdf",
        headers={"Authorization": "Bearer test-token"},
        json={"theme": "clean_report", "markdown": "# Hello"},
    )
    assert response.status_code == 500


def test_markdown_renderer_passes_markdown_to_pandoc_stdin(monkeypatch) -> None:
    from doc_renderer_service.rendering import markdown_to_html

    calls = {}

    def fake_run(cmd, *, input, check, capture_output, text):
        calls["cmd"] = cmd
        calls["input"] = input
        calls["check"] = check
        calls["capture_output"] = capture_output
        calls["text"] = text
        return SimpleNamespace(stdout="<h1>Hello</h1>")

    monkeypatch.setattr("doc_renderer_service.rendering.subprocess.run", fake_run)

    html = markdown_to_html("# Hello", title="Greeting")

    assert html == "<h1>Hello</h1>"
    assert calls == {
        "cmd": [
            "pandoc",
            "--from",
            "gfm",
            "--to",
            "html5",
            "--standalone",
            "--metadata",
            "title=Greeting",
        ],
        "input": "# Hello",
        "check": True,
        "capture_output": True,
        "text": True,
    }
