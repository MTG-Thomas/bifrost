"""
Bifrost API - FastAPI Application

Main entry point for the FastAPI application.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError, NoResultFound, OperationalError

from src.config import get_settings
from src.models.contracts.common import ErrorResponse
from src.core.csrf import CSRFMiddleware
from src.core.embed_middleware import EmbedScopeMiddleware
from src.core.database import close_db, init_db
from src.core.pubsub import manager as pubsub_manager
from src.observability.otel import configure_tracing, instrument_fastapi

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Suppress noisy third-party loggers
logging.getLogger("aiormq").setLevel(logging.WARNING)
logging.getLogger("aio_pika").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("aiobotocore").setLevel(logging.WARNING)
logging.getLogger("s3transfer").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logging.getLogger("src.services.execution").setLevel(logging.DEBUG)
logging.getLogger("bifrost").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting Bifrost API...")
    settings = get_settings()

    # Initialize tracing
    configure_tracing("bifrost-api", settings.environment)

    logger.info("Initializing database connection...")
    await init_db()
    logger.info("Database connection established")

    from src.core.entity_change_hook import register_entity_change_hooks
    register_entity_change_hooks()

    logger.info("Registering workflow endpoints...")
    await register_dynamic_workflow_endpoints(app)

    if settings.default_user_email and settings.default_user_password:
        await create_default_user()

    from src.services.file_index_reconciler import reconcile_file_index
    from src.core.database import get_session_factory

    async def _run_reconciler():
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                stats = await reconcile_file_index(db)
                await db.commit()
                logger.info(f"File index reconciliation complete: {stats}")
        except Exception as e:
            logger.warning(f"File index reconciliation failed: {e}")

    asyncio.create_task(_run_reconciler())

    logger.info(f"Bifrost API started in {settings.environment} mode")

    yield

    logger.info("Shutting down Bifrost API...")

    await pubsub_manager.close()
    await close_db()
    logger.info("Bifrost API shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Bifrost API",
        description="MSP automation platform API",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Instrument FastAPI with OpenTelemetry
    instrument_fastapi(app)

    # (rest unchanged omitted for brevity)

    return app

app = create_app()
