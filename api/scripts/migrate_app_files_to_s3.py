"""
Migrate app files from app_files table to S3 (file_index + _repo/).

This is a management command (NOT an alembic migration) because it requires
S3 access which isn't available during alembic migrations.

IMPORTANT: Run this BEFORE applying the drop_app_files_tables migration.

Usage:
    python -m scripts.migrate_app_files_to_s3

What it does:
1. Queries all applications with their slugs (using raw SQL)
2. For each app, gets draft version files from app_files table
3. Writes each file to S3 _repo/apps/{slug}/{path} and inserts into file_index
4. If app has active_version_id, creates published_snapshot from active version files
5. Logs progress and is idempotent (safe to re-run)

Important: App files have already been exported to exported-apps/ as a backup.
"""

import asyncio
import hashlib
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.config import get_settings
from src.models.orm.file_index import FileIndex
from src.services.file_storage.service import FileStorageService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def migrate_app_files(db: AsyncSession) -> dict:
    """Migrate all app files from the app_files table to S3/file_index.

    Uses raw SQL to query app_files/app_versions since ORM models may be removed.
    Returns a summary dict with counts.
    """
    stats = {
        "apps_processed": 0,
        "apps_skipped": 0,
        "draft_files_migrated": 0,
        "draft_files_skipped": 0,
        "snapshots_created": 0,
        "errors": [],
    }

    # Check if app_files table exists (migration may have already run)
    table_check = await db.execute(text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'app_files')"
    ))
    if not table_check.scalar():
        logger.info("app_files table does not exist - migration already applied or not needed")
        return stats

    # Get all applications with their version pointers using raw SQL
    # (in case ORM columns have been removed)
    apps_result = await db.execute(text(
        "SELECT id, slug, draft_version_id, active_version_id, published_snapshot "
        "FROM applications ORDER BY slug"
    ))
    apps = apps_result.fetchall()

    logger.info(f"Found {len(apps)} applications to process")

    storage = FileStorageService(db)

    for app_row in apps:
        app_id, slug, draft_version_id, active_version_id, published_snapshot = app_row
        logger.info(f"Processing app: {slug} (id={app_id})")
        stats["apps_processed"] += 1

        try:
            # --- Migrate draft version files ---
            if draft_version_id:
                draft_files = await db.execute(text(
                    "SELECT path, source FROM app_files "
                    "WHERE app_version_id = :version_id ORDER BY path"
                ), {"version_id": draft_version_id})
                draft_rows = draft_files.fetchall()

                logger.info(f"  Draft version has {len(draft_rows)} files")

                for file_path, source in draft_rows:
                    full_path = f"apps/{slug}/{file_path}"

                    # Check if already migrated (idempotent)
                    existing = await db.execute(
                        select(FileIndex.path).where(FileIndex.path == full_path)
                    )
                    if existing.scalar_one_or_none():
                        logger.debug(f"    Skipping {full_path} (already exists)")
                        stats["draft_files_skipped"] += 1
                        continue

                    # Write to S3 + file_index
                    source = source or ""
                    await storage.write_file(
                        path=full_path,
                        content=source.encode("utf-8"),
                        updated_by="migration",
                    )
                    stats["draft_files_migrated"] += 1
                    logger.info(f"    Migrated: {full_path}")

            # --- Create published_snapshot from active version ---
            if active_version_id and not published_snapshot:
                active_files = await db.execute(text(
                    "SELECT path, source FROM app_files "
                    "WHERE app_version_id = :version_id ORDER BY path"
                ), {"version_id": active_version_id})
                active_rows = active_files.fetchall()

                if active_rows:
                    snapshot = {}
                    for file_path, source in active_rows:
                        full_path = f"apps/{slug}/{file_path}"
                        source = source or ""
                        content_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
                        snapshot[full_path] = content_hash

                        # Also ensure the file exists in S3
                        existing = await db.execute(
                            select(FileIndex.path).where(FileIndex.path == full_path)
                        )
                        if not existing.scalar_one_or_none():
                            await storage.write_file(
                                path=full_path,
                                content=source.encode("utf-8"),
                                updated_by="migration",
                            )

                    # Update published_snapshot using raw SQL
                    import json
                    await db.execute(text(
                        "UPDATE applications SET published_snapshot = :snapshot WHERE id = :app_id"
                    ), {"snapshot": json.dumps(snapshot), "app_id": app_id})

                    stats["snapshots_created"] += 1
                    logger.info(
                        f"  Created published_snapshot with {len(snapshot)} files"
                    )

            await db.flush()

        except Exception as e:
            error_msg = f"Error processing app {slug}: {e}"
            logger.error(error_msg)
            stats["errors"].append(error_msg)

    await db.commit()
    return stats


async def main():
    """Run the migration."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    logger.info("Starting app files migration to S3...")
    logger.info(f"Database: {settings.database_url.split('@')[-1]}")

    async with async_session() as db:
        stats = await migrate_app_files(db)

    logger.info("=" * 60)
    logger.info("Migration complete!")
    logger.info(f"  Apps processed:        {stats['apps_processed']}")
    logger.info(f"  Apps skipped:          {stats['apps_skipped']}")
    logger.info(f"  Draft files migrated:  {stats['draft_files_migrated']}")
    logger.info(f"  Draft files skipped:   {stats['draft_files_skipped']}")
    logger.info(f"  Snapshots created:     {stats['snapshots_created']}")
    if stats["errors"]:
        logger.warning(f"  Errors: {len(stats['errors'])}")
        for err in stats["errors"]:
            logger.warning(f"    - {err}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
