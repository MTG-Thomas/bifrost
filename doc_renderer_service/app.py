from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator

from doc_renderer_service.rendering import (
    RenderError,
    render_pdf_from_html,
    render_pdf_from_markdown,
)


class RenderRequestBase(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    theme: str = Field(default="clean_report", max_length=64)
    filename: str | None = Field(default=None, max_length=200)

    @field_validator("theme")
    @classmethod
    def normalize_theme(cls, value: str) -> str:
        return str(value or "").strip().lower()


class MarkdownRenderRequest(RenderRequestBase):
    markdown: str = Field(min_length=1)


class HtmlRenderRequest(RenderRequestBase):
    html: str = Field(min_length=1)


def _validate_bearer(authorization: str | None, expected_token: str) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = authorization.split(" ", 1)[1].strip()
    if token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid renderer bearer token",
        )


def create_app(*, api_token: str) -> FastAPI:
    app = FastAPI(
        title="Bifrost Document Renderer",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/render/markdown-pdf")
    async def render_markdown_pdf(
        payload: MarkdownRenderRequest,
        authorization: str | None = Header(default=None),
    ) -> Response:
        _validate_bearer(authorization, api_token)
        try:
            result = render_pdf_from_markdown(
                markdown=payload.markdown,
                title=payload.title,
                theme=payload.theme,
                filename=payload.filename,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except RenderError as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

        return Response(
            content=result.pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{result.filename}"'},
        )

    @app.post("/render/html-pdf")
    async def render_html_pdf(
        payload: HtmlRenderRequest,
        authorization: str | None = Header(default=None),
    ) -> Response:
        _validate_bearer(authorization, api_token)
        try:
            result = render_pdf_from_html(
                html=payload.html,
                title=payload.title,
                theme=payload.theme,
                filename=payload.filename,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except RenderError as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

        return Response(
            content=result.pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{result.filename}"'},
        )

    return app

