from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse

SERVICE_ROOT = Path(__file__).resolve().parent
THEMES_DIR = SERVICE_ROOT / "themes"
PANDOC_TIMEOUT_SECONDS = 30
ALLOWED_DATA_URL_PREFIXES = ("data:image/", "data:font/")

THEME_PATHS = {
    "business_case": THEMES_DIR / "business_case.css",
    "clean_report": THEMES_DIR / "clean_report.css",
    "executive_brief": THEMES_DIR / "executive_brief.css",
}


class RenderError(RuntimeError):
    """Raised when the renderer cannot create a PDF."""


@dataclass(slots=True)
class RenderResult:
    filename: str
    pdf_bytes: bytes
    theme: str
    title: str | None


def sanitize_filename(value: str | None, *, fallback: str = "document") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or fallback


def resolve_theme_path(theme: str) -> Path:
    resolved = THEME_PATHS.get(str(theme or "").strip().lower())
    if resolved is None:
        supported = ", ".join(sorted(THEME_PATHS))
        raise ValueError(f"Unsupported theme '{theme}'. Supported themes: {supported}")
    if not resolved.exists():
        raise FileNotFoundError(f"Theme CSS file does not exist: {resolved}")
    return resolved


def _stylesheet_link(theme_path: Path) -> str:
    return f'<link rel="stylesheet" href="{theme_path.resolve().as_uri()}">'


def _is_path_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _safe_url_fetcher(url: str):
    """Restrict WeasyPrint resource loading to bundled themes and safe data URLs."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme == "data" and url.lower().startswith(ALLOWED_DATA_URL_PREFIXES):
        from weasyprint import default_url_fetcher  # pyright: ignore[reportMissingImports]

        return default_url_fetcher(url)

    if scheme == "file":
        path = Path(parsed.path).resolve()
        if _is_path_relative_to(path, THEMES_DIR.resolve()):
            from weasyprint import default_url_fetcher  # pyright: ignore[reportMissingImports]

            return default_url_fetcher(url)

    raise ValueError("Blocked unsafe document resource URL")


def _wrap_html_document(*, html_body: str, title: str | None, theme_path: Path) -> str:
    title_text = title or "Bifrost Document"
    stylesheet = _stylesheet_link(theme_path)
    if "<html" in html_body.lower():
        if "</head>" in html_body.lower():
            pattern = re.compile(r"</head>", flags=re.IGNORECASE)
            return pattern.sub(f"  {stylesheet}\n</head>", html_body, count=1)
        return f"{stylesheet}\n{html_body}"

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>{title_text}</title>
    {stylesheet}
  </head>
  <body>
    {html_body}
  </body>
</html>
"""


def markdown_to_html(markdown: str, *, title: str | None = None) -> str:
    cmd = [
        "pandoc",
        "--from",
        "gfm",
        "--to",
        "html5",
        "--standalone",
    ]
    if title:
        cmd.extend(["--metadata", f"title={title}"])
    try:
        completed = subprocess.run(
            cmd,
            input=markdown,
            check=True,
            capture_output=True,
            text=True,
            timeout=PANDOC_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise RenderError("pandoc is not installed in the renderer image") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RenderError(f"pandoc failed to render markdown: {stderr or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError(
            f"pandoc exceeded the {PANDOC_TIMEOUT_SECONDS}-second render limit"
        ) from exc
    return completed.stdout


def render_pdf_from_html(
    *,
    html: str,
    title: str | None,
    theme: str,
    filename: str | None = None,
) -> RenderResult:
    theme_path = resolve_theme_path(theme)
    rendered_html = _wrap_html_document(html_body=html, title=title, theme_path=theme_path)
    try:
        from weasyprint import HTML  # pyright: ignore[reportMissingImports]
        pdf_bytes = HTML(
            string=rendered_html,
            base_url=str(THEMES_DIR),
            url_fetcher=_safe_url_fetcher,
        ).write_pdf()
    except Exception as exc:  # pragma: no cover - WeasyPrint internals
        raise RenderError(f"WeasyPrint failed to render PDF: {exc}") from exc

    resolved_name = sanitize_filename(filename or title, fallback="document")
    if not resolved_name.endswith(".pdf"):
        resolved_name = f"{resolved_name}.pdf"
    return RenderResult(
        filename=resolved_name,
        pdf_bytes=pdf_bytes,
        theme=theme,
        title=title,
    )


def render_pdf_from_markdown(
    *,
    markdown: str,
    title: str | None,
    theme: str,
    filename: str | None = None,
) -> RenderResult:
    html = markdown_to_html(markdown, title=title)
    return render_pdf_from_html(
        html=html,
        title=title,
        theme=theme,
        filename=filename,
    )
