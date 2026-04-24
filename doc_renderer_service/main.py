from __future__ import annotations

import os

from doc_renderer_service.app import create_app


def _load_api_token() -> str:
    token = os.getenv("DOC_RENDERER_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DOC_RENDERER_API_TOKEN is required")
    return token


app = create_app(api_token=_load_api_token())

