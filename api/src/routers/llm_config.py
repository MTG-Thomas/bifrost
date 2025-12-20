"""
LLM Configuration Admin Router

Admin endpoints for managing LLM provider configuration.
Requires platform admin access.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from src.core.auth import CurrentActiveUser, RequirePlatformAdmin
from src.core.database import DbSession
from src.models.contracts.llm import (
    LLMConfigRequest,
    LLMConfigResponse,
    LLMModelsResponse,
    LLMTestRequest,
    LLMTestResponse,
)
from src.services.llm_config_service import LLMConfigService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/llm",
    tags=["LLM Configuration"],
    dependencies=[RequirePlatformAdmin],  # All endpoints require platform admin
)


@router.get("/config")
async def get_llm_config(
    db: DbSession,
    user: CurrentActiveUser,
) -> LLMConfigResponse | None:
    """
    Get current LLM provider configuration.

    Returns the configuration without the API key (only indicates if it's set).
    Requires platform admin access.
    """
    service = LLMConfigService(db)
    config = await service.get_config()

    if not config:
        return None

    return LLMConfigResponse(
        provider=config.provider,  # type: ignore[arg-type]
        model=config.model,
        endpoint=config.endpoint,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        default_system_prompt=config.default_system_prompt,
        is_configured=config.is_configured,
        api_key_set=config.api_key_set,
    )


@router.post("/config", status_code=status.HTTP_200_OK)
async def set_llm_config(
    request: LLMConfigRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> LLMConfigResponse:
    """
    Set LLM provider configuration.

    The API key will be encrypted before storage.
    Requires platform admin access.
    """
    service = LLMConfigService(db)

    await service.save_config(
        provider=request.provider,
        model=request.model,
        api_key=request.api_key,
        endpoint=request.endpoint,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        default_system_prompt=request.default_system_prompt,
        updated_by=user.email,
    )

    await db.commit()

    logger.info(f"LLM config updated by {user.email}: provider={request.provider}, model={request.model}")

    return LLMConfigResponse(
        provider=request.provider,
        model=request.model,
        endpoint=request.endpoint,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        default_system_prompt=request.default_system_prompt,
        is_configured=True,
        api_key_set=True,
    )


@router.delete("/config", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_config(
    db: DbSession,
    user: CurrentActiveUser,
) -> None:
    """
    Delete LLM provider configuration.

    Requires platform admin access.
    """
    service = LLMConfigService(db)
    deleted = await service.delete_config()

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LLM configuration not found",
        )

    await db.commit()
    logger.info(f"LLM config deleted by {user.email}")


@router.post("/test")
async def test_llm_connection(
    request: LLMTestRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> LLMTestResponse:
    """
    Test LLM connection with provided credentials.

    Tests the connection without saving the configuration.
    Useful for validating API keys before committing.
    Requires platform admin access.
    """
    service = LLMConfigService(db)

    # Temporarily save the config to test
    # We'll roll back the transaction so it's not persisted
    await service.save_config(
        provider=request.provider,
        model=request.model,
        api_key=request.api_key,
        endpoint=request.endpoint,
        updated_by=user.email,
    )

    result = await service.test_connection()

    # Rollback to not persist the test config
    await db.rollback()

    return LLMTestResponse(
        success=result.success,
        message=result.message,
        models=result.models,
    )


@router.post("/test-saved")
async def test_saved_llm_connection(
    db: DbSession,
    user: CurrentActiveUser,
) -> LLMTestResponse:
    """
    Test connection using saved LLM configuration.

    Tests the currently saved configuration.
    Requires platform admin access.
    """
    service = LLMConfigService(db)
    config = await service.get_config()

    if not config or not config.is_configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LLM configuration not found",
        )

    result = await service.test_connection()

    return LLMTestResponse(
        success=result.success,
        message=result.message,
        models=result.models,
    )


@router.get("/models")
async def list_llm_models(
    db: DbSession,
    user: CurrentActiveUser,
) -> LLMModelsResponse:
    """
    List available models from the configured LLM provider.

    Works with OpenAI and Anthropic (both support model listing).
    Requires platform admin access.
    """
    service = LLMConfigService(db)
    config = await service.get_config()

    if not config or not config.is_configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LLM configuration not found",
        )

    models = await service.list_models()

    if models is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not retrieve models from provider",
        )

    return LLMModelsResponse(
        models=models,
        provider=config.provider,
    )
