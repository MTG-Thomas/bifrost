"""
Public LLMs.txt endpoint — single document for all platform documentation.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["docs"])


@router.get("/api/llms.txt", response_class=PlainTextResponse)
async def get_llms_txt() -> str:
    """Return the full platform documentation as a single markdown document."""
    from src.services.llms_txt import generate_llms_txt
    return generate_llms_txt()
