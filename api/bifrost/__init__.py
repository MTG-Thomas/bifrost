"""
Bifrost Platform SDK

Provides Python API access to platform features from workflows.

All SDK methods are async and must be awaited.

Usage:
    from bifrost import organizations, workflows, files, forms, executions, roles
    from bifrost import config, integrations, ai, knowledge

Example:
    # AI Completions
    response = await ai.complete("Summarize this text...")
    print(response.content)

    # Structured AI output with Pydantic
    from pydantic import BaseModel
    class Summary(BaseModel):
        title: str
        points: list[str]
    result = await ai.complete("Summarize...", response_format=Summary)
    print(result.title)  # Typed!

    # AI with RAG context
    response = await ai.complete(
        "What is our refund policy?",
        knowledge=["policies", "faq"]
    )

    # Streaming AI
    async for chunk in ai.stream("Write a story..."):
        print(chunk.content, end="")

    # Knowledge Store (RAG)
    await knowledge.store(
        "Our refund policy allows 30-day returns.",
        namespace="policies",
        key="refund-policy"
    )
    results = await knowledge.search("return policy", namespace="policies")
    for doc in results:
        print(doc.content, doc.score)

    # Create an organization
    org = await organizations.create("Acme Corp", domain="acme.com")

    # List workflows
    wf_list = await workflows.list()
    for wf in wf_list:
        print(wf.name, wf.description)

    # Local filesystem operations
    files.write("data/temp.txt", "content", location="temp")
    data = files.read("data/customers.csv", location="workspace")

    # List executions
    recent = await executions.list(limit=10)
    for execution in recent:
        print(f"{execution.workflow_name}: {execution.status}")

    # Create a role
    role = await roles.create("Manager", permissions=["users.read", "users.write"])

    # Manage configuration
    await config.set("api_url", "https://api.example.com")
    url = await config.get("api_url")
    cfg = await config.list()
    print(cfg.api_url)  # Dot notation access

    # Get integration with OAuth tokens
    integration = await integrations.get("HaloPSA")
    if integration and integration.oauth:
        client_id = integration.oauth.client_id
        refresh_token = integration.oauth.refresh_token
"""

from dataclasses import dataclass

from .ai import ai, AIResponse, AIStreamChunk
from .config import config
from .executions import executions
from .files import files
from .forms import forms
from .integrations import integrations
from .knowledge import knowledge, KnowledgeDocument, NamespaceInfo
from .organizations import organizations
from .roles import roles
from .users import users
from .workflows import workflows

# Import decorators and context from shared module
from src.sdk.decorators import workflow, data_provider
from src.sdk.context import ExecutionContext, Organization

# Import context proxy for accessing ExecutionContext without parameter
from ._context import context
from src.sdk.errors import UserError, WorkflowError, ValidationError, IntegrationError, ConfigurationError
from src.models.enums import ExecutionStatus, ConfigType, FormFieldType
from src.models import (
    OAuthCredentials,
    IntegrationType,
    Role,
    Form,
)

# SDK Response Models (for typed return values)
from src.models.contracts.sdk import (
    ConfigData,
    IntegrationData,
    OAuthCredentials as SDKOAuthCredentials,
)
# Use existing contract models
from src.models.contracts.executions import WorkflowExecution, ExecutionLogPublic
from src.models.contracts.forms import FormPublic
from src.models.contracts.workflows import WorkflowMetadata
from src.models.contracts.integrations import IntegrationMappingResponse

# For backwards compatibility with type stubs
@dataclass
class Caller:
    """User who triggered the workflow execution."""
    user_id: str
    email: str
    name: str

__all__ = [
    # SDK Modules
    'ai',
    'config',
    'executions',
    'files',
    'forms',
    'integrations',
    'knowledge',
    'organizations',
    'roles',
    'users',
    'workflows',
    # AI Module Types
    'AIResponse',
    'AIStreamChunk',
    # Knowledge Module Types
    'KnowledgeDocument',
    'NamespaceInfo',
    # Decorators
    'workflow',
    'data_provider',
    # Context
    'context',
    'ExecutionContext',
    'Organization',
    'Caller',
    # Enums
    'ExecutionStatus',
    'ConfigType',
    'FormFieldType',
    'IntegrationType',
    # ORM Models (for type hints)
    'OAuthCredentials',
    'Role',
    'Form',
    # SDK Response Models
    'ConfigData',
    'IntegrationData',
    'SDKOAuthCredentials',
    # Contract Models (reused from API contracts)
    'WorkflowExecution',
    'ExecutionLogPublic',
    'FormPublic',
    'WorkflowMetadata',
    'IntegrationMappingResponse',
    # Errors
    'UserError',
    'WorkflowError',
    'ValidationError',
    'IntegrationError',
    'ConfigurationError',
]

__version__ = '2.0.0'
