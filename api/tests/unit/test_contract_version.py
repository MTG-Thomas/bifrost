"""CLI compatibility contract tripwire.

Two jobs:

1. **Legacy bridge sync** — the CLI and server copies of the frozen version-10
   transition marker must agree while ``GET /api/version`` exposes it for CLIs
   released before minimum-version gating was restored.

2. **Tripwire** — a fingerprint over the contract surface the CLI actually
   depends on (the request/response DTOs it sends + the routes it calls). Any
   change to that surface flips the fingerprint, failing this test until the
   author makes an explicit decision: raise ``MIN_CLI_VERSION`` to the release
   containing a CLI-impacting/breaking change, or just refresh the fingerprint
   for a compatible additive/cosmetic change. This makes a missed compatibility
   decision a red test instead of a production incident.

The fingerprint is computed live, in-process, and only ever compared to a
constant committed in THIS file — never shipped or compared across machines —
so cross-machine schema-serialization differences are irrelevant.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

# Standalone bifrost SDK package import (mirrors test_contracts_parity.py).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.contract_version import CONTRACT_VERSION as CLI_CONTRACT_VERSION  # noqa: E402
from shared.contract_version import CONTRACT_VERSION as SERVER_CONTRACT_VERSION  # noqa: E402

# DTOs the CLI sends/receives. Server-canonical classes (the wire truth).
from src.models.contracts.agents import AgentCreate, AgentUpdate  # noqa: E402
from src.models.contracts.applications import (  # noqa: E402
    ApplicationCreate,
    ApplicationUpdate,
)
from src.models.contracts.platform_jobs import (  # noqa: E402
    PlatformJobAccepted,
    PlatformJobPublic,
)
from src.models.contracts.claims import CustomClaimCreate, CustomClaimUpdate  # noqa: E402
from src.models.contracts.config import ConfigCreate, ConfigUpdate  # noqa: E402
from src.models.contracts.events import (  # noqa: E402
    EventSourceCreate,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionUpdate,
)
from src.models.contracts.executions import (  # noqa: E402
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
)
from src.models.contracts.forms import FormCreate, FormUpdate  # noqa: E402
from src.models.contracts.integrations import (  # noqa: E402
    IntegrationCreate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    IntegrationUpdate,
)
from src.models.contracts.organizations import (  # noqa: E402
    OrganizationCreate,
    OrganizationUpdate,
)
from src.models.contracts.solutions import (  # noqa: E402
    SolutionCandidateDeployEnqueued,
    SolutionDeployEnqueued,
    SolutionDeployJobStatus,
)
from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate  # noqa: E402
from src.models.contracts.tables import TableCreate, TableUpdate  # noqa: E402
from src.models.contracts.users import RoleCreate, RoleUpdate  # noqa: E402
from src.models.contracts.workflows import WorkflowUpdateRequest  # noqa: E402
from src.models.contracts.workspace_promotions import (  # noqa: E402
    WorkspacePromotionCanaryAccepted,
    WorkspacePromotionCanaryRequest,
    WorkspacePromotionArtifactResponse,
    WorkspacePromotionPreviewRequest,
    WorkspacePromotionPreviewResponse,
    WorkspaceReleaseActivateRequest,
    WorkspaceReleasePrepareRequest,
    WorkspaceReleaseStatusResponse,
    WorkspaceLiveStatusResponse,
)

import inspect  # noqa: E402

from pydantic import BaseModel  # noqa: E402

from src.models.contracts import cli as _cli_contracts  # noqa: E402

# ---------------------------------------------------------------------------
# The contract surface the CLI depends on.
# ---------------------------------------------------------------------------

#: The explicit CRUD + execute DTOs the `bifrost <entity>` command surface uses.
_COMMAND_DTOS: list[type] = [
    OrganizationCreate,
    OrganizationUpdate,
    RoleCreate,
    RoleUpdate,
    WorkflowUpdateRequest,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    FormCreate,
    FormUpdate,
    AgentCreate,
    AgentUpdate,
    ApplicationCreate,
    ApplicationUpdate,
    PlatformJobAccepted,
    PlatformJobPublic,
    IntegrationCreate,
    IntegrationUpdate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    ConfigCreate,
    ConfigUpdate,
    CustomClaimCreate,
    CustomClaimUpdate,
    TableCreate,
    TableUpdate,
    EventSourceCreate,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionUpdate,
    SolutionDeployEnqueued,
    SolutionCandidateDeployEnqueued,
    SolutionDeployJobStatus,
    PolicyRuleCreate,
    PolicyRuleUpdate,
    WorkspacePromotionPreviewRequest,
    WorkspacePromotionPreviewResponse,
    WorkspacePromotionArtifactResponse,
    WorkspacePromotionCanaryRequest,
    WorkspacePromotionCanaryAccepted,
    WorkspaceReleasePrepareRequest,
    WorkspaceReleaseActivateRequest,
    WorkspaceReleaseStatusResponse,
    WorkspaceLiveStatusResponse,
]

#: Every request/response DTO the in-workflow SDK sends/parses against
#: ``/api/sdk/*`` lives in ``src.models.contracts.cli``. We pull them in
#: programmatically so a NEW SDK DTO is automatically fingerprinted — no manual
#: list to forget to update. A retype on any of these silently breaks running
#: workflows on a stale CLI, so they belong in the contract.
_SDK_DTOS: list[type] = [
    obj
    for _name, obj in inspect.getmembers(_cli_contracts, inspect.isclass)
    if issubclass(obj, BaseModel)
    and obj is not BaseModel
    and obj.__module__ == _cli_contracts.__name__
]

#: Request/response DTOs the CLI/SDK sends or parses. Type-aware via JSON Schema.
#: This is the real wire contract: the command DTOs (`bifrost <entity>` /
#: `workflows execute`) plus EVERY SDK DTO (pulled in programmatically, so new
#: ones are auto-covered). A field removed/renamed/retyped here is exactly what
#: silently corrupts a stale CLI, and it is caught completely and automatically.
CONTRACT_FINGERPRINT_MODELS: list[type] = _COMMAND_DTOS + _SDK_DTOS

#: We deliberately do NOT fingerprint the full route list. Route strings are a
#: weak, noisy proxy: hand-listing ~100 `/api/*` paths is perpetually incomplete
#: (every omission is a false-negative), and a route rename produces a clean 404
#: — not the silent corruption a response-shape change causes, which the DTOs
#: above already catch. We keep only `/api/version` itself, since the gate's own
#: handshake depends on that literal path.
CLI_ROUTES: tuple[str, ...] = ("/api/version",)

#: Committed fingerprint of the contract surface above. If a code change flips
#: the live fingerprint, this test fails — update this value, and raise the
#: server's MIN_CLI_VERSION IF the change requires a new CLI. See module docstring.
EXPECTED_CONTRACT_FINGERPRINT = (
    # ApplicationCreate.app_model default flipped inline_v1 → standalone_v2
    # (2026-06-13). CONTRACT_VERSION bumped to 3: an old CLI would default a new
    # `apps create` to v1 against a v2-default server, so old clients are gated.
    #
    # SDKIntegrationsGetRequest gained optional `solution` (2026-06-14,
    # RequiredConnectionUnset escalation). ADDITIVE — an old CLI simply omits the
    # field and keeps silent-None behavior, so no CONTRACT_VERSION bump; fingerprint
    # refreshed only.
    #
    # Solution deploy now returns 202 + deploy_job_id and the CLI polls
    # SolutionDeployJobStatus for the prior summary shape (2026-06-17).
    # CONTRACT_VERSION bumped to 5.
    #
    # Solution deploy now uploads a workspace zip as multipart/form-data instead
    # of the legacy JSON bundle request body (2026-06-21).
    # CONTRACT_VERSION bumped to 6.
    #
    # TablePolicies policies union widened to list[Policy | PolicyRuleRef] (2026-06-23).
    # ADDITIVE: old CLIs send plain inline rules; PolicyRuleRef is a new optional
    # variant — no old client is broken. Fingerprint refreshed only.
    #
    # PolicyRuleCreate + PolicyRuleUpdate added to CLI contract surface (2026-06-23).
    # ADDITIVE: new entity group (policy-rule), no existing DTOs changed.
    # Fingerprint refreshed only.
    #
    # Solution install (zip + from-repo) is now async: POST /install and
    # /install/from-repo return 202 + deploy_job_id (was 200/201 + Solution) and
    # the CLI/UI poll SolutionDeployJobStatus for the solution_id. That job status'
    # install_id widened to nullable (a zip install resolves its target inside the
    # job) — a response-shape change the CLI parses (2026-07-02).
    # CONTRACT_VERSION bumped to 7.
    #
    # Application publish now returns 202 + a standardized platform job instead
    # of ApplicationPublic, and the CLI polls PlatformJobPublic for durable
    # progress/result/error (2026-07-28). CONTRACT_VERSION bumped to 8.
    #
    # Forms gained optional confirmation_markdown (2026-08-04). ADDITIVE: old
    # clients omit it and continue receiving the prior default confirmation.
    #
    # RoleCreate/RoleUpdate gained additive scopes fields (2026-07-30).
    # Old CLIs omit them and retain empty-scope custom-role behavior.
    # Forms also gained optional confirmation_markdown (2026-08-04). All are
    # additive; fingerprint refreshed after merging the contract surfaces.
    #
    # CLI/SDK file write and delete requests gained optional guarded-mutation
    # fields (2026-08-08). ADDITIVE: old clients omit them and retain the prior
    # unconditional behavior; new clients can reject stale writes and deletes.
    #
    # PlatformJobStatus gained `waiting` for durable parent jobs (2026-08-07).
    # CONTRACT_VERSION bumped to 9 because an older CLI cannot parse the new
    # enum value while polling PlatformJobPublic.
    # Fingerprint refreshed against the combined MTG fork and upstream v9
    # contract surfaces; the additional fork fields were already shipped.
    #
    # Solution deploy enqueue now requires candidate_id (2026-08-12), binding
    # an accepted asynchronous job to the exact reviewed bundle. CONTRACT_VERSION
    # bumped to 10 because a stale CLI cannot verify that invariant.
    #
    # Workspace promotion preview moved to immutable artifact v2 (2026-08-19):
    # reviewed production source is named by protected Git commit/tree, closure
    # bytes are server-fetched, and the response binds content, candidate,
    # release, effective file, and registration identities. CONTRACT_VERSION
    # bumped to 11 because v1 clients cannot safely interpret or activate v2.
    # The same unreleased v11 contract includes reviewed-artifact-only canary
    # request/acceptance DTOs, immutable preparation proof, and tagged
    # canary/risk-acknowledgement activation authorization; local draft uploads
    # are not executable.
    # Workspace release history status gained optional deadline, overdue, and
    # runtime/history verification fields (2026-08-24). ADDITIVE: v11 clients
    # ignore them and retain their existing pending/locked state handling.
    # Agent create/update replaced llm_model with llm_profile_id and the SDK AI
    # surface adopted reusable model profiles (2026-08-24). BREAKING: the
    # compatible CLI ships behind the unreleased 1.2.3 minimum-version floor.
    # Additive attachment, artifact, model-profile, logo, usage-cache, and
    # platform-job memory fields are included in the same fingerprint refresh.
    # Reviewed promotion gained an optional declared multi-root cohort and
    # additive source-release evidence (2026-08-24). Single-root v11 clients
    # retain their existing request and response behavior.
    "21bee37d3cb63f1c877fc3ad919dc4053dc001308c3b4bb2161dd1e9412343a6"
)


def _fingerprint(models: list[type], routes: tuple[str, ...]) -> str:
    """Deterministic sha256 over model JSON schemas + the route list."""
    h = hashlib.sha256()
    for model in sorted(models, key=lambda m: m.__name__):
        schema = model.model_json_schema()
        h.update(model.__name__.encode())
        h.update(json.dumps(schema, sort_keys=True).encode())
    h.update(json.dumps(sorted(routes)).encode())
    return h.hexdigest()


def test_cli_and_server_contract_version_agree() -> None:
    """The two copies of the frozen transition marker must match."""
    assert CLI_CONTRACT_VERSION == SERVER_CONTRACT_VERSION, (
        f"CONTRACT_VERSION drift: CLI={CLI_CONTRACT_VERSION} "
        f"(api/bifrost/contract_version.py) vs "
        f"server={SERVER_CONTRACT_VERSION} (api/shared/contract_version.py). "
        "The legacy transition marker must remain synchronized."
    )


def test_contract_fingerprint_tripwire() -> None:
    """A change to the CLI-consumed contract surface forces a decision."""
    current = _fingerprint(CONTRACT_FINGERPRINT_MODELS, CLI_ROUTES)
    assert current == EXPECTED_CONTRACT_FINGERPRINT, (
        "A CLI-consumed contract (DTO schema) changed.\n"
        f"  current fingerprint: {current}\n"
        "  - CLI-IMPACTING/BREAKING change (field removed/renamed/retyped, "
        "response shape the CLI parses changed): raise MIN_CLI_VERSION in "
        "api/shared/version.py to the release containing the compatible CLI, "
        "then update EXPECTED_CONTRACT_FINGERPRINT below.\n"
        "  - COSMETIC/ADDITIVE (description tweak, new optional field the CLI "
        "ignores): just update EXPECTED_CONTRACT_FINGERPRINT; leave the frozen "
        "legacy contract marker and MIN_CLI_VERSION unchanged.\n"
        "See test_contract_version.py module docstring."
    )
