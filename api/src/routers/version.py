import asyncio
import logging
import threading

from fastapi import APIRouter
from pydantic import BaseModel

from shared.contract_version import get_contract_version
from shared.version import get_version
from src.services.sdk_package import sdk_contract_version, sdk_fingerprint

router = APIRouter(prefix="/api/version", tags=["version"])

logger = logging.getLogger(__name__)

_SDK_FINGERPRINT_UNSET = object()
_sdk_fingerprint_value: str | object = _SDK_FINGERPRINT_UNSET
_sdk_fingerprint_lock = threading.Lock()


class VersionResponse(BaseModel):
    version: str
    contract_version: int
    sdk_fingerprint: str
    sdk_contract_version: int


def get_sdk_fingerprint() -> str:
    """SDK content fingerprint, degrading to ``"unavailable"`` rather than
    failing the whole /api/version response.

    ``sdk_fingerprint`` shells out to node/esbuild on first call per version
    (lru_cached thereafter via ``_built_bundle``); a broken node toolchain in
    this environment must not take down an otherwise-healthy version
    endpoint. Broad except is intentional here: any failure of the build
    subprocess (missing binary, timeout, non-zero exit, ...) should degrade
    the same way, and the exception is logged so the underlying cause is
    still visible.
    """
    global _sdk_fingerprint_value

    if _sdk_fingerprint_value is not _SDK_FINGERPRINT_UNSET:
        return str(_sdk_fingerprint_value)

    # functools.lru_cache does not coalesce concurrent misses and does not
    # cache failures. Keep the expensive Node/esbuild cold path single-flight
    # and memoize its degraded result for the lifetime of this API process.
    with _sdk_fingerprint_lock:
        if _sdk_fingerprint_value is not _SDK_FINGERPRINT_UNSET:
            return str(_sdk_fingerprint_value)
        try:
            _sdk_fingerprint_value = sdk_fingerprint(get_version())
        except Exception:  # noqa: BLE001 - build-toolchain failure must degrade, not 500 /api/version; logged below
            logger.exception("failed to compute SDK fingerprint")
            _sdk_fingerprint_value = "unavailable"
        return str(_sdk_fingerprint_value)


@router.get("", response_model=VersionResponse)
async def get_version_info() -> VersionResponse:
    return VersionResponse(
        version=get_version(),
        contract_version=get_contract_version(),
        # The cold path shells out to node/esbuild (up to 120s). Keep that
        # synchronous build off the API event loop, matching /api/sdk/download.
        sdk_fingerprint=await asyncio.to_thread(get_sdk_fingerprint),
        sdk_contract_version=sdk_contract_version(),
    )
