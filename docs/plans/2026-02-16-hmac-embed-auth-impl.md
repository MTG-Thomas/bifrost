# HMAC-Authenticated App Embedding Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow Bifrost apps to be embedded in external systems via iframes with HMAC-SHA256 verification, issuing scoped embed JWTs for workflow access.

**Architecture:** New `app_embed_secrets` table stores Fernet-encrypted shared secrets per app. A public `/embed/apps/{slug}` endpoint verifies HMAC signatures against these secrets, issues 8-hour embed JWTs, and serves the app. Embed JWTs authenticate workflow execution calls using the system user identity.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, HMAC-SHA256, Fernet encryption, JWT

**Design doc:** `docs/plans/2026-02-16-hmac-embed-auth-design.md`

---

### Task 1: ORM Model — `AppEmbedSecret`

**Files:**
- Create: `api/src/models/orm/app_embed_secrets.py`
- Modify: `api/src/models/orm/__init__.py` (add import)

**Step 1: Create the ORM model**

Create `api/src/models/orm/app_embed_secrets.py`:

```python
"""ORM model for app embed secrets."""

from datetime import datetime, timezone
from uuid import uuid4, UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base


class AppEmbedSecret(Base):
    """Shared secret for HMAC-authenticated iframe embedding."""

    __tablename__ = "app_embed_secrets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    application = relationship("Application", back_populates="embed_secrets")

    __table_args__ = (
        Index("ix_app_embed_secrets_application_id", "application_id"),
    )
```

**Step 2: Add relationship to Application model**

In `api/src/models/orm/applications.py`, add to the `Application` class relationships:

```python
embed_secrets: Mapped[list["AppEmbedSecret"]] = relationship(
    "AppEmbedSecret", back_populates="application", cascade="all, delete-orphan", passive_deletes=True
)
```

Add the import at the top if needed for TYPE_CHECKING.

**Step 3: Add import to ORM `__init__.py`**

In `api/src/models/orm/__init__.py`, add:

```python
from src.models.orm.app_embed_secrets import AppEmbedSecret
```

And add `"AppEmbedSecret"` to `__all__`.

**Step 4: Commit**

```bash
git add api/src/models/orm/app_embed_secrets.py api/src/models/orm/applications.py api/src/models/orm/__init__.py
git commit -m "feat: add AppEmbedSecret ORM model"
```

---

### Task 2: Alembic Migration

**Files:**
- Create: `api/alembic/versions/20260216_add_app_embed_secrets.py`

**Step 1: Create the migration**

```bash
cd api && alembic revision -m "add app_embed_secrets table"
```

Edit the generated file to contain:

```python
"""add app_embed_secrets table

Revision ID: 20260216_embed_secrets
Revises: <previous_revision>
Create Date: 2026-02-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260216_embed_secrets"
down_revision = "<previous_revision>"  # Fill from alembic output
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_embed_secrets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_app_embed_secrets_application_id", "app_embed_secrets", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_app_embed_secrets_application_id")
    op.drop_table("app_embed_secrets")
```

**Step 2: Commit**

```bash
git add api/alembic/versions/
git commit -m "feat: add app_embed_secrets migration"
```

---

### Task 3: Pydantic Contract Models

**Files:**
- Modify: `api/src/models/contracts/applications.py` (add embed secret models)

**Step 1: Add embed secret request/response models**

At the end of `api/src/models/contracts/applications.py`, add:

```python
# --- Embed Secrets ---

class EmbedSecretCreate(BaseModel):
    """Request to create an embed secret for an app."""
    name: str = Field(..., max_length=255, description="Label for this secret (e.g., 'Halo Production')")
    secret: str | None = Field(default=None, description="Shared secret. If omitted, one is auto-generated.")


class EmbedSecretResponse(BaseModel):
    """Embed secret metadata (never includes the raw secret after creation)."""
    id: str
    name: str
    is_active: bool
    created_at: datetime


class EmbedSecretCreatedResponse(EmbedSecretResponse):
    """Response when creating an embed secret — includes raw secret shown once."""
    raw_secret: str


class EmbedSecretUpdate(BaseModel):
    """Request to update an embed secret."""
    is_active: bool | None = None
    name: str | None = Field(default=None, max_length=255)
```

**Step 2: Commit**

```bash
git add api/src/models/contracts/applications.py
git commit -m "feat: add embed secret Pydantic contract models"
```

---

### Task 4: Embed Secret CRUD Router

**Files:**
- Create: `api/src/routers/app_embed_secrets.py`
- Modify: `api/src/routers/__init__.py` (add export)
- Modify: `api/src/main.py` (register router)

**Step 1: Write the failing test**

Create `api/tests/e2e/api/test_app_embed_secrets.py`:

```python
"""E2E tests for app embed secret CRUD."""

import pytest


def _create_app(client, headers, slug):
    r = client.post("/api/applications", headers=headers, json={"name": slug, "slug": slug})
    assert r.status_code == 201, r.text
    return r.json()


def _delete_app(client, headers, app_id):
    r = client.delete(f"/api/applications/{app_id}", headers=headers)
    assert r.status_code in (200, 204), r.text


@pytest.mark.e2e
class TestAppEmbedSecrets:
    @pytest.fixture
    def test_app(self, e2e_client, platform_admin):
        app = _create_app(e2e_client, platform_admin.headers, "embed-secret-test")
        yield app
        _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_create_embed_secret_autogenerated(self, e2e_client, platform_admin, test_app):
        """Creating a secret without providing one should auto-generate it."""
        r = e2e_client.post(
            f"/api/applications/{test_app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Test Secret"},
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert "raw_secret" in data
        assert len(data["raw_secret"]) > 20
        assert data["name"] == "Test Secret"
        assert data["is_active"] is True

    def test_create_embed_secret_user_provided(self, e2e_client, platform_admin, test_app):
        """Creating a secret with a user-provided value should store and return it."""
        provided = "my-halo-secret-abc123"
        r = e2e_client.post(
            f"/api/applications/{test_app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Halo Prod", "secret": provided},
        )
        assert r.status_code == 201, r.text
        assert r.json()["raw_secret"] == provided

    def test_list_embed_secrets_hides_raw(self, e2e_client, platform_admin, test_app):
        """Listing secrets should NOT return the raw secret value."""
        # Create one first
        e2e_client.post(
            f"/api/applications/{test_app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Listed Secret"},
        )
        r = e2e_client.get(
            f"/api/applications/{test_app['id']}/embed-secrets",
            headers=platform_admin.headers,
        )
        assert r.status_code == 200, r.text
        secrets = r.json()
        assert len(secrets) >= 1
        for s in secrets:
            assert "raw_secret" not in s
            assert "secret_encrypted" not in s

    def test_deactivate_embed_secret(self, e2e_client, platform_admin, test_app):
        """Should be able to deactivate an embed secret."""
        create_r = e2e_client.post(
            f"/api/applications/{test_app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "To Deactivate"},
        )
        secret_id = create_r.json()["id"]
        r = e2e_client.patch(
            f"/api/applications/{test_app['id']}/embed-secrets/{secret_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["is_active"] is False

    def test_delete_embed_secret(self, e2e_client, platform_admin, test_app):
        """Should be able to delete an embed secret."""
        create_r = e2e_client.post(
            f"/api/applications/{test_app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "To Delete"},
        )
        secret_id = create_r.json()["id"]
        r = e2e_client.delete(
            f"/api/applications/{test_app['id']}/embed-secrets/{secret_id}",
            headers=platform_admin.headers,
        )
        assert r.status_code == 204, r.text

    def test_non_admin_cannot_manage_secrets(self, e2e_client, org1_user, test_app):
        """Regular org users should NOT be able to manage embed secrets."""
        r = e2e_client.post(
            f"/api/applications/{test_app['id']}/embed-secrets",
            headers=org1_user.headers,
            json={"name": "Unauthorized"},
        )
        assert r.status_code == 403, r.text
```

**Step 2: Run tests to verify they fail**

Run: `./test.sh tests/e2e/api/test_app_embed_secrets.py -v`
Expected: FAIL (404 — endpoint doesn't exist yet)

**Step 3: Implement the router**

Create `api/src/routers/app_embed_secrets.py`:

```python
"""CRUD endpoints for app embed secrets."""

import logging
import secrets
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select

from src.core.auth import Context, CurrentSuperuser
from src.core.security import encrypt_secret, decrypt_secret
from src.models.contracts.applications import (
    EmbedSecretCreate,
    EmbedSecretCreatedResponse,
    EmbedSecretResponse,
    EmbedSecretUpdate,
)
from src.models.orm.app_embed_secrets import AppEmbedSecret
from src.models.orm.applications import Application

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/applications/{app_id}/embed-secrets",
    tags=["App Embed Secrets"],
)


async def _get_app_or_404(ctx: Context, app_id: UUID) -> Application:
    result = await ctx.db.execute(select(Application).where(Application.id == app_id))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


@router.post(
    "",
    response_model=EmbedSecretCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an embed secret for an app",
)
async def create_embed_secret(
    body: EmbedSecretCreate,
    ctx: Context,
    user: CurrentSuperuser,
    app_id: UUID = Path(...),
):
    await _get_app_or_404(ctx, app_id)

    raw_secret = body.secret or secrets.token_urlsafe(32)
    encrypted = encrypt_secret(raw_secret)

    record = AppEmbedSecret(
        application_id=app_id,
        name=body.name,
        secret_encrypted=encrypted,
        created_by=user.user_id,
    )
    ctx.db.add(record)
    await ctx.db.commit()
    await ctx.db.refresh(record)

    return EmbedSecretCreatedResponse(
        id=str(record.id),
        name=record.name,
        is_active=record.is_active,
        created_at=record.created_at,
        raw_secret=raw_secret,
    )


@router.get(
    "",
    response_model=list[EmbedSecretResponse],
    summary="List embed secrets for an app",
)
async def list_embed_secrets(
    ctx: Context,
    user: CurrentSuperuser,
    app_id: UUID = Path(...),
):
    await _get_app_or_404(ctx, app_id)

    result = await ctx.db.execute(
        select(AppEmbedSecret)
        .where(AppEmbedSecret.application_id == app_id)
        .order_by(AppEmbedSecret.created_at.desc())
    )
    records = result.scalars().all()

    return [
        EmbedSecretResponse(
            id=str(r.id),
            name=r.name,
            is_active=r.is_active,
            created_at=r.created_at,
        )
        for r in records
    ]


@router.patch(
    "/{secret_id}",
    response_model=EmbedSecretResponse,
    summary="Update an embed secret",
)
async def update_embed_secret(
    body: EmbedSecretUpdate,
    ctx: Context,
    user: CurrentSuperuser,
    app_id: UUID = Path(...),
    secret_id: UUID = Path(...),
):
    result = await ctx.db.execute(
        select(AppEmbedSecret).where(
            AppEmbedSecret.id == secret_id,
            AppEmbedSecret.application_id == app_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Embed secret not found")

    if body.is_active is not None:
        record.is_active = body.is_active
    if body.name is not None:
        record.name = body.name

    await ctx.db.commit()
    await ctx.db.refresh(record)

    return EmbedSecretResponse(
        id=str(record.id),
        name=record.name,
        is_active=record.is_active,
        created_at=record.created_at,
    )


@router.delete(
    "/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an embed secret",
)
async def delete_embed_secret(
    ctx: Context,
    user: CurrentSuperuser,
    app_id: UUID = Path(...),
    secret_id: UUID = Path(...),
):
    result = await ctx.db.execute(
        select(AppEmbedSecret).where(
            AppEmbedSecret.id == secret_id,
            AppEmbedSecret.application_id == app_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Embed secret not found")

    await ctx.db.delete(record)
    await ctx.db.commit()
```

**Step 4: Register the router**

In `api/src/routers/__init__.py`, add:
```python
from src.routers.app_embed_secrets import router as app_embed_secrets_router
```
Add `"app_embed_secrets_router"` to `__all__`.

In `api/src/main.py`, add:
```python
app.include_router(app_embed_secrets_router)
```

**Step 5: Run tests to verify they pass**

Run: `./test.sh tests/e2e/api/test_app_embed_secrets.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add api/src/routers/app_embed_secrets.py api/src/routers/__init__.py api/src/main.py api/tests/e2e/api/test_app_embed_secrets.py
git commit -m "feat: add embed secret CRUD endpoints with tests"
```

---

### Task 5: HMAC Verification Utility

**Files:**
- Create: `api/src/services/embed_auth.py`
- Create: `api/tests/unit/services/test_embed_auth.py`

**Step 1: Write the failing unit tests**

Create `api/tests/unit/services/test_embed_auth.py`:

```python
"""Unit tests for HMAC embed verification."""

import hashlib
import hmac as hmac_module

import pytest

from src.services.embed_auth import verify_embed_hmac, compute_embed_hmac


class TestComputeEmbedHmac:
    def test_single_param(self):
        result = compute_embed_hmac({"agent_id": "42"}, "my-secret")
        expected = hmac_module.new(
            b"my-secret", b"agent_id=42", hashlib.sha256
        ).hexdigest()
        assert result == expected

    def test_multiple_params_sorted(self):
        """Params should be sorted alphabetically by key."""
        result = compute_embed_hmac(
            {"ticket_id": "1001", "agent_id": "42"}, "my-secret"
        )
        expected = hmac_module.new(
            b"my-secret", b"agent_id=42&ticket_id=1001", hashlib.sha256
        ).hexdigest()
        assert result == expected

    def test_empty_params(self):
        result = compute_embed_hmac({}, "my-secret")
        expected = hmac_module.new(
            b"my-secret", b"", hashlib.sha256
        ).hexdigest()
        assert result == expected


class TestVerifyEmbedHmac:
    def test_valid_hmac(self):
        secret = "test-secret"
        params = {"agent_id": "42", "ticket_id": "1001"}
        valid_hmac = compute_embed_hmac(params, secret)
        params_with_hmac = {**params, "hmac": valid_hmac}
        assert verify_embed_hmac(params_with_hmac, secret) is True

    def test_invalid_hmac(self):
        params = {"agent_id": "42", "hmac": "invalid-garbage"}
        assert verify_embed_hmac(params, "test-secret") is False

    def test_tampered_param(self):
        secret = "test-secret"
        valid_hmac = compute_embed_hmac({"agent_id": "42"}, secret)
        tampered = {"agent_id": "99", "hmac": valid_hmac}
        assert verify_embed_hmac(tampered, secret) is False

    def test_missing_hmac_param(self):
        assert verify_embed_hmac({"agent_id": "42"}, "test-secret") is False
```

**Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/services/test_embed_auth.py -v`
Expected: FAIL (module not found)

**Step 3: Implement the verification utility**

Create `api/src/services/embed_auth.py`:

```python
"""HMAC verification for embedded app authentication."""

import hashlib
import hmac as hmac_module


def compute_embed_hmac(params: dict[str, str], secret: str) -> str:
    """Compute HMAC-SHA256 over sorted query params (Shopify-style).

    Args:
        params: Query parameters (excluding 'hmac' key).
        secret: Shared secret.

    Returns:
        Hex-encoded HMAC digest.
    """
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def verify_embed_hmac(query_params: dict[str, str], secret: str) -> bool:
    """Verify an HMAC-signed set of query parameters.

    Removes the 'hmac' key, sorts the remaining params alphabetically,
    joins as key=value&key=value, and verifies against HMAC-SHA256.

    Args:
        query_params: All query parameters including 'hmac'.
        secret: Shared secret.

    Returns:
        True if the HMAC is valid.
    """
    received_hmac = query_params.get("hmac")
    if not received_hmac:
        return False

    remaining = {k: v for k, v in query_params.items() if k != "hmac"}
    expected = compute_embed_hmac(remaining, secret)
    return hmac_module.compare_digest(expected, received_hmac)
```

**Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/services/test_embed_auth.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/embed_auth.py api/tests/unit/services/test_embed_auth.py
git commit -m "feat: add HMAC verification utility for embed auth"
```

---

### Task 6: Embed JWT — Token Creation and Auth Dependency

**Files:**
- Modify: `api/src/core/security.py` (add `create_embed_token`)
- Modify: `api/src/core/auth.py` (add `get_embed_user` dependency)

**Step 1: Write failing unit test for embed token creation**

Create `api/tests/unit/core/test_embed_token.py`:

```python
"""Unit tests for embed token creation and validation."""

import pytest
from uuid import uuid4

from src.core.security import create_embed_token, decode_token


class TestEmbedToken:
    def test_create_and_decode(self):
        app_id = str(uuid4())
        org_id = str(uuid4())
        verified_params = {"agent_id": "42", "ticket_id": "1001"}

        token = create_embed_token(
            app_id=app_id,
            org_id=org_id,
            verified_params=verified_params,
        )

        payload = decode_token(token, expected_type="embed")
        assert payload is not None
        assert payload["app_id"] == app_id
        assert payload["org_id"] == org_id
        assert payload["verified_params"] == verified_params
        assert payload["type"] == "embed"

    def test_embed_token_rejected_as_access(self):
        """Embed tokens must NOT be accepted as access tokens."""
        token = create_embed_token(
            app_id=str(uuid4()),
            org_id=str(uuid4()),
            verified_params={},
        )
        payload = decode_token(token, expected_type="access")
        assert payload is None
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/core/test_embed_token.py -v`
Expected: FAIL (function not defined)

**Step 3: Implement `create_embed_token`**

In `api/src/core/security.py`, add after `create_mfa_token`:

```python
def create_embed_token(
    app_id: str,
    org_id: str | None,
    verified_params: dict[str, str],
) -> str:
    """Create a short-lived JWT for embed iframe sessions.

    Args:
        app_id: Application UUID.
        org_id: Organization UUID (from the app).
        verified_params: HMAC-verified query parameters.

    Returns:
        Encoded JWT string.
    """
    from src.core.constants import SYSTEM_USER_ID

    return create_access_token(
        data={
            "sub": SYSTEM_USER_ID,
            "app_id": app_id,
            "org_id": org_id,
            "verified_params": verified_params,
            "email": "embed@internal.gobifrost.com",
        },
        expires_delta=timedelta(hours=8),
    ).replace('"type": "access"', '')  # We need to override the type
```

Wait — `create_access_token` hardcodes `"type": "access"`. We need a different approach. Add a standalone function:

```python
def create_embed_token(
    app_id: str,
    org_id: str | None,
    verified_params: dict[str, str],
) -> str:
    """Create an 8-hour JWT for embed iframe sessions."""
    from src.core.constants import SYSTEM_USER_ID

    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=8)

    to_encode = {
        "sub": SYSTEM_USER_ID,
        "app_id": app_id,
        "org_id": org_id,
        "verified_params": verified_params,
        "email": "embed@internal.gobifrost.com",
        "exp": expire,
        "type": "embed",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }

    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
```

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/core/test_embed_token.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/core/security.py api/tests/unit/core/test_embed_token.py
git commit -m "feat: add create_embed_token for iframe sessions"
```

---

### Task 7: Embed Entry Point — `/embed/apps/{slug}`

**Files:**
- Create: `api/src/routers/embed.py`
- Modify: `api/src/routers/__init__.py` (add export)
- Modify: `api/src/main.py` (register router)
- Create: `api/tests/e2e/api/test_embed.py`

**Step 1: Write the failing E2E test**

Create `api/tests/e2e/api/test_embed.py`:

```python
"""E2E tests for HMAC-authenticated embed entry point."""

import hashlib
import hmac as hmac_module

import pytest


def _create_app(client, headers, slug):
    r = client.post("/api/applications", headers=headers, json={"name": slug, "slug": slug})
    assert r.status_code == 201, r.text
    return r.json()


def _delete_app(client, headers, app_id):
    r = client.delete(f"/api/applications/{app_id}", headers=headers)
    assert r.status_code in (200, 204), r.text


def _compute_hmac(params: dict[str, str], secret: str) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


@pytest.mark.e2e
class TestEmbedEntryPoint:
    @pytest.fixture
    def test_app_with_secret(self, e2e_client, platform_admin):
        app = _create_app(e2e_client, platform_admin.headers, "embed-entry-test")
        r = e2e_client.post(
            f"/api/applications/{app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Test"},
        )
        assert r.status_code == 201, r.text
        raw_secret = r.json()["raw_secret"]
        yield {"app": app, "secret": raw_secret}
        _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_valid_hmac_returns_embed_token(self, e2e_client, test_app_with_secret):
        app = test_app_with_secret["app"]
        secret = test_app_with_secret["secret"]
        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, secret)

        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        assert r.status_code == 200, r.text
        # Should set an embed_token cookie
        assert "embed_token" in r.cookies

    def test_invalid_hmac_rejected(self, e2e_client, test_app_with_secret):
        app = test_app_with_secret["app"]
        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={"agent_id": "42", "hmac": "invalid-garbage"},
        )
        assert r.status_code == 403, r.text

    def test_missing_hmac_rejected(self, e2e_client, test_app_with_secret):
        app = test_app_with_secret["app"]
        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={"agent_id": "42"},
        )
        assert r.status_code == 403, r.text

    def test_no_embed_secrets_configured(self, e2e_client, platform_admin):
        """App with no embed secrets should reject all embed requests."""
        app = _create_app(e2e_client, platform_admin.headers, "embed-no-secret")
        try:
            r = e2e_client.get(
                f"/embed/apps/{app['slug']}",
                params={"agent_id": "42", "hmac": "anything"},
            )
            assert r.status_code == 403, r.text
        finally:
            _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_deactivated_secret_rejected(self, e2e_client, platform_admin, test_app_with_secret):
        """Deactivated secrets should not verify."""
        app = test_app_with_secret["app"]
        secret = test_app_with_secret["secret"]

        # Get the secret ID and deactivate it
        r = e2e_client.get(
            f"/api/applications/{app['id']}/embed-secrets",
            headers=platform_admin.headers,
        )
        secret_id = r.json()[0]["id"]
        e2e_client.patch(
            f"/api/applications/{app['id']}/embed-secrets/{secret_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )

        # Now try to use it
        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, secret)
        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={**params, "hmac": hmac_val},
        )
        assert r.status_code == 403, r.text
```

**Step 2: Run tests to verify they fail**

Run: `./test.sh tests/e2e/api/test_embed.py -v`
Expected: FAIL (404 — endpoint doesn't exist)

**Step 3: Implement the embed entry point router**

Create `api/src/routers/embed.py`:

```python
"""Public embed entry point — HMAC-verified iframe loading."""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.database import get_async_session
from src.core.security import create_embed_token, decrypt_secret
from src.models.orm.app_embed_secrets import AppEmbedSecret
from src.models.orm.applications import Application
from src.services.embed_auth import verify_embed_hmac

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/embed",
    tags=["Embed"],
)


@router.get("/apps/{slug}")
async def embed_app(
    request: Request,
    response: Response,
    slug: str = Path(...),
):
    """Public entry point for HMAC-authenticated iframe embedding.

    Verifies the HMAC signature against the app's embed secrets,
    issues an 8-hour embed JWT cookie, and returns a minimal HTML
    shell that loads the app.
    """
    # Collect all query params
    query_params = dict(request.query_params)

    if "hmac" not in query_params:
        raise HTTPException(status_code=403, detail="Missing HMAC signature")

    # Look up the app and its active embed secrets (no auth required — this is public)
    async with get_async_session() as db:
        result = await db.execute(
            select(Application)
            .where(Application.slug == slug)
            .options(selectinload(Application.embed_secrets))
        )
        app = result.scalar_one_or_none()

    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    active_secrets = [s for s in app.embed_secrets if s.is_active]
    if not active_secrets:
        raise HTTPException(status_code=403, detail="No embed secrets configured")

    # Try each active secret
    verified = False
    for secret_record in active_secrets:
        raw_secret = decrypt_secret(secret_record.secret_encrypted)
        if verify_embed_hmac(query_params, raw_secret):
            verified = True
            break

    if not verified:
        raise HTTPException(status_code=403, detail="Invalid HMAC signature")

    # Extract verified params (everything except hmac)
    verified_params = {k: v for k, v in query_params.items() if k != "hmac"}

    # Issue embed JWT
    embed_token = create_embed_token(
        app_id=str(app.id),
        org_id=str(app.organization_id) if app.organization_id else None,
        verified_params=verified_params,
    )

    # Set cookie and return confirmation
    # NOTE: In a full implementation, this would return an HTML shell
    # that loads the app's compiled JS. For now, return JSON with the token
    # set as a cookie. The frontend embed loader will be built separately.
    response.set_cookie(
        key="embed_token",
        value=embed_token,
        httponly=True,
        samesite="none",  # Required for cross-origin iframes
        secure=True,       # Required when samesite=none
        max_age=8 * 3600,
        path="/",
    )

    return {
        "status": "ok",
        "app_id": str(app.id),
        "app_name": app.name,
        "verified_params": verified_params,
    }
```

**Important note:** The `get_async_session` usage here bypasses the normal auth-injected `Context`. This endpoint is intentionally public — no user auth required. Check the existing pattern for how `get_async_session` is used as a context manager (look at `api/src/core/database.py` for the pattern — it may be `async_session_factory()` or similar). Adjust the import and usage to match the project's session factory pattern.

**Step 4: Register the router**

In `api/src/routers/__init__.py`, add:
```python
from src.routers.embed import router as embed_router
```
Add `"embed_router"` to `__all__`.

In `api/src/main.py`, add:
```python
app.include_router(embed_router)
```

**Step 5: Run tests to verify they pass**

Run: `./test.sh tests/e2e/api/test_embed.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add api/src/routers/embed.py api/src/routers/__init__.py api/src/main.py api/tests/e2e/api/test_embed.py
git commit -m "feat: add HMAC-verified embed entry point"
```

---

### Task 8: Embed JWT Auth for Workflow Execution

**Files:**
- Modify: `api/src/core/auth.py` (accept embed tokens for specific endpoints)
- Modify: `api/src/routers/workflows.py` (allow embed token on execute endpoint)
- Create: `api/tests/e2e/api/test_embed_workflow_execution.py`

This is the most architecturally significant task. The embed JWT needs to authenticate workflow execution calls. The approach:

1. Add a `get_embed_user` dependency that decodes `type="embed"` tokens from the `embed_token` cookie
2. Create a combined dependency that accepts EITHER a normal user OR an embed token
3. On the workflow execute endpoint, accept the combined dependency

**Step 1: Write the failing E2E test**

Create `api/tests/e2e/api/test_embed_workflow_execution.py`:

```python
"""E2E tests for workflow execution via embed token."""

import hashlib
import hmac as hmac_module

import pytest


def _compute_hmac(params: dict[str, str], secret: str) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


@pytest.mark.e2e
class TestEmbedWorkflowExecution:
    """Test that embed tokens can execute workflows."""

    @pytest.fixture
    def embed_session(self, e2e_client, platform_admin):
        """Create an app with embed secret and get an embed token."""
        # Create app
        r = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": "embed-wf-test", "slug": "embed-wf-test"},
        )
        assert r.status_code == 201, r.text
        app = r.json()

        # Create embed secret
        r = e2e_client.post(
            f"/api/applications/{app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Test"},
        )
        raw_secret = r.json()["raw_secret"]

        # Get embed token via HMAC-verified entry point
        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, raw_secret)
        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={**params, "hmac": hmac_val},
        )
        assert r.status_code == 200, r.text
        embed_token = r.cookies.get("embed_token")
        assert embed_token, "Expected embed_token cookie"

        yield {
            "app": app,
            "embed_token": embed_token,
            "verified_params": params,
        }

        # Cleanup
        e2e_client.delete(f"/api/applications/{app['id']}", headers=platform_admin.headers)

    def test_embed_token_can_execute_workflow(self, e2e_client, embed_session):
        """An embed token should be able to execute workflows."""
        # This test depends on having a test workflow available.
        # The exact implementation will depend on what workflows exist in the test env.
        # For now, verify the token is accepted by the execute endpoint (even if the
        # workflow doesn't exist, we should get a 404 not a 401/403).
        r = e2e_client.post(
            "/api/workflows/execute",
            cookies={"embed_token": embed_session["embed_token"]},
            json={
                "workflow_name": "nonexistent-workflow-for-auth-test",
                "parameters": {},
            },
        )
        # Should get 404 (workflow not found) rather than 401/403 (unauthorized)
        assert r.status_code != 401, f"Embed token rejected: {r.text}"
        assert r.status_code != 403, f"Embed token forbidden: {r.text}"
```

**Step 2: This task requires careful integration with the existing auth system**

The implementation details here will depend on exactly how `get_current_user_optional` and the `Context` dependency chain work. The engineer should:

1. Add `embed_token` cookie reading to `get_current_user_optional` in `auth.py`
2. When an embed token is found, create a `UserPrincipal` with system user identity
3. Ensure the `ExecutionContext` carries the embed metadata (app_id, verified_params)

This is the most complex integration point and may require design adjustments during implementation. The key constraint is: **embed tokens must only work for workflow execution, not for admin endpoints**.

**Step 3: Commit**

```bash
git add api/src/core/auth.py api/src/routers/workflows.py api/tests/e2e/api/test_embed_workflow_execution.py
git commit -m "feat: accept embed JWT for workflow execution"
```

---

### Task 9: CSRF and Security Headers

**Files:**
- Modify: `api/src/core/csrf.py` (add embed path exemptions if needed)
- Verify X-Frame-Options / CSP behavior on embed routes

**Step 1: Check if CSRF needs changes**

The embed token is set as a cookie. When the app (loaded in the iframe) makes POST requests to `/api/workflows/execute`, it sends the `embed_token` cookie. The CSRF middleware currently only bypasses CSRF for `Authorization` header and `X-Bifrost-Key` header auth.

Cookie-based embed auth WILL trigger CSRF enforcement. Options:
- Add `/embed/` prefix to `CSRF_EXEMPT_PREFIXES`
- Or have the embed app send the embed token as a `Authorization: Bearer` header instead of a cookie

The engineer should evaluate which approach is cleaner during implementation. Using Bearer headers avoids cookie/CSRF issues entirely but requires the frontend embed shell to extract the token and include it in requests.

**Step 2: Add CSP headers for embed route**

The embed entry point should set permissive framing headers:
```python
response.headers["Content-Security-Policy"] = "frame-ancestors *"
response.headers["X-Frame-Options"] = "ALLOWALL"
```

Non-embed routes should NOT have these permissive headers.

**Step 3: Commit**

```bash
git add api/src/core/csrf.py
git commit -m "feat: configure CSRF and framing headers for embed routes"
```

---

### Task 10: Final Integration Testing & Cleanup

**Files:**
- Run all existing tests to check for regressions
- Run pyright and ruff for type/lint checks
- Verify the full embed flow end-to-end

**Step 1: Run full test suite**

```bash
./test.sh
```
Expected: All tests pass, including new embed tests.

**Step 2: Type and lint checks**

```bash
cd api && pyright
cd api && ruff check .
```
Expected: No errors.

**Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: address test/lint issues from embed auth implementation"
```
