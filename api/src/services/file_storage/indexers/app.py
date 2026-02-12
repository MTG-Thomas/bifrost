"""
App indexer for parsing and indexing apps from GitHub sync.

Handles:
- apps/{slug}/app.json -> Application record
- apps/{slug}/**/* -> file_index entries via FileStorageService

App files are stored in S3 at _repo/apps/{slug}/ paths and indexed
in the file_index table. No AppVersion or AppFile tables are used.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.applications import Application

logger = logging.getLogger(__name__)


def _serialize_app_to_json(app: Application) -> bytes:
    """
    Serialize an Application to JSON bytes for GitHub export.

    Includes:
    - id (required for entity matching during sync)
    - name, slug, description, icon

    Excludes instance-specific fields:
    - permissions, access_level, organization_id, role_ids
    - created_by, created_at, updated_at, published_at, published_snapshot
    """
    app_data: dict[str, Any] = {
        "id": str(app.id),
        "name": app.name,
        "slug": app.slug,
    }

    if app.description:
        app_data["description"] = app.description
    if app.icon:
        app_data["icon"] = app.icon

    return json.dumps(app_data, indent=2).encode("utf-8")


class AppIndexer:
    """
    Indexes apps from GitHub sync.

    Handles:
    - apps/{slug}/app.json -> creates/updates Application
    - apps/{slug}/**/* -> stored in file_index via FileStorageService
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def index_app_json(
        self,
        path: str,
        content: bytes,
    ) -> bool:
        """
        Parse and index app from app.json file.

        Creates or updates the Application record based on slug.
        Instance-specific fields are set to safe defaults on create,
        preserved on update.

        Args:
            path: File path (e.g., "apps/my-app/app.json")
            content: JSON content bytes

        Returns:
            True if content was modified, False otherwise
        """
        try:
            app_data = json.loads(content.decode("utf-8"))
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in app file: {path}")
            return False

        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "apps" or parts[2] != "app.json":
            logger.warning(f"Invalid app.json path format: {path}")
            return False

        slug = parts[1]

        name = app_data.get("name")
        if not name:
            logger.warning(f"App file missing name: {path}")
            return False

        json_slug = app_data.get("slug")
        if json_slug and json_slug != slug:
            logger.warning(
                f"Slug mismatch in {path}: path has '{slug}', JSON has '{json_slug}'. Using path slug."
            )

        now = datetime.now(timezone.utc)

        existing_app = await self._get_app_by_slug(slug)

        if existing_app:
            existing_app.name = name
            existing_app.description = app_data.get("description")
            existing_app.icon = app_data.get("icon")
            existing_app.updated_at = now
            logger.info(f"Updated app: {slug}")
        else:
            app_id = uuid4()
            new_app = Application(
                id=app_id,
                name=name,
                slug=slug,
                description=app_data.get("description"),
                icon=app_data.get("icon"),
                organization_id=None,
                access_level="role_based",
                created_at=now,
                updated_at=now,
                created_by="github_sync",
            )
            self.db.add(new_app)
            await self.db.flush()
            logger.info(f"Created app: {slug}")

        return False

    async def index_app_file(
        self,
        path: str,
        content: bytes,
    ) -> bool:
        """
        Parse and index an app code file into the file_index.

        Files are stored at apps/{slug}/{relative_path} in the file_index
        table and S3 via the normal FileStorageService write path.

        Args:
            path: File path (e.g., "apps/my-app/pages/index.tsx")
            content: File content bytes

        Returns:
            True if content was modified, False otherwise
        """
        parts = path.split("/", 2)
        if len(parts) < 3 or parts[0] != "apps":
            logger.warning(f"Invalid app file path format: {path}")
            return False

        slug = parts[1]
        relative_path = parts[2]

        if relative_path == "app.json":
            return False

        app = await self._get_app_by_slug(slug)
        if not app:
            logger.warning(f"App not found for file {path}. Index app.json first.")
            return False

        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning(f"Invalid UTF-8 in app file: {path}")
            return False

        # File is written to file_index + S3 by the caller (FileStorageService.write_file).
        # This method just validates the app exists.
        logger.debug(f"Indexed app file: {relative_path} in app {slug}")
        return False

    async def delete_app(self, slug: str) -> int:
        """
        Delete an application by slug.

        Called when app.json is deleted from remote.

        Args:
            slug: App slug

        Returns:
            Number of apps deleted (0 or 1)
        """
        from sqlalchemy import delete as sql_delete

        stmt = sql_delete(Application).where(Application.slug == slug)
        result = await self.db.execute(stmt)
        count = result.rowcount if result.rowcount else 0

        if count > 0:
            logger.info(f"Deleted app: {slug}")

        return count

    async def delete_app_file(self, path: str) -> int:
        """
        Delete an app file from the file_index.

        Called when a file is deleted from remote.

        Args:
            path: Full path (e.g., "apps/my-app/pages/old.tsx")

        Returns:
            Number of files deleted
        """
        from sqlalchemy import delete as sql_delete
        from src.models.orm.file_index import FileIndex

        parts = path.split("/", 2)
        if len(parts) < 3 or parts[0] != "apps":
            return 0

        stmt = sql_delete(FileIndex).where(FileIndex.path == path)
        result = await self.db.execute(stmt)
        count = result.rowcount if result.rowcount else 0

        if count > 0:
            logger.info(f"Deleted app file: {path}")

        return count

    async def _get_app_by_slug(self, slug: str) -> Application | None:
        """Get an application by slug."""
        stmt = select(Application).where(Application.slug == slug)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_app_by_id(self, app_id: UUID | str) -> Application | None:
        """Get an application by ID."""
        if isinstance(app_id, str):
            app_id = UUID(app_id)
        stmt = select(Application).where(Application.id == app_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def import_app(
        self,
        app_dir: str,
        files: dict[str, bytes],
    ) -> Application | None:
        """
        Import an app atomically with all its files.

        Creates/updates the Application record from app.json, then writes
        all code files to the file_index table.

        Args:
            app_dir: App directory path (e.g., "apps/my-app")
            files: Dict mapping relative paths to content
                   {"app.json": content, "pages/index.tsx": content, ...}

        Returns:
            The created/updated Application, or None on failure
        """
        from sqlalchemy.dialects.postgresql import insert
        from src.models.orm.file_index import FileIndex

        if "app.json" not in files:
            logger.warning(f"Missing app.json in app bundle: {app_dir}")
            return None

        try:
            app_data = json.loads(files["app.json"].decode("utf-8"))
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in app.json for: {app_dir}")
            return None

        parts = app_dir.split("/")
        if len(parts) < 2 or parts[0] != "apps":
            logger.warning(f"Invalid app directory path: {app_dir}")
            return None

        slug = parts[1]
        name = app_data.get("name")
        if not name:
            logger.warning(f"App missing name in: {app_dir}")
            return None

        app_uuid_str = app_data.get("id")
        now = datetime.now(timezone.utc)

        # Try to find existing app by UUID first, then by slug
        existing_app = None
        if app_uuid_str:
            existing_app = await self._get_app_by_id(app_uuid_str)

        if not existing_app:
            existing_app = await self._get_app_by_slug(slug)

        if existing_app:
            existing_app.name = name
            existing_app.description = app_data.get("description")
            existing_app.icon = app_data.get("icon")
            existing_app.updated_at = now

            if existing_app.slug != slug:
                existing_app.slug = slug

            app = existing_app
            logger.info(f"Updated app atomically: {slug}")
        else:
            app_id = UUID(app_uuid_str) if app_uuid_str else uuid4()
            new_app = Application(
                id=app_id,
                name=name,
                slug=slug,
                description=app_data.get("description"),
                icon=app_data.get("icon"),
                organization_id=None,
                access_level="role_based",
                created_at=now,
                updated_at=now,
                created_by="github_sync",
            )
            self.db.add(new_app)
            await self.db.flush()
            app = new_app
            logger.info(f"Created app atomically: {slug}")

        # Write all code files to file_index
        for file_path, content in files.items():
            if file_path == "app.json":
                continue

            try:
                source = content.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning(f"Invalid UTF-8 in app file: {app_dir}/{file_path}")
                continue

            full_path = f"apps/{slug}/{file_path}"
            content_hash = None

            stmt = insert(FileIndex).values(
                path=full_path,
                content=source,
                content_hash=content_hash,
                updated_at=now,
            ).on_conflict_do_update(
                index_elements=[FileIndex.path],
                set_={
                    "content": source,
                    "content_hash": content_hash,
                    "updated_at": now,
                },
            )
            await self.db.execute(stmt)

        logger.info(f"Imported app atomically with {len(files) - 1} code files: {slug}")
        return app
