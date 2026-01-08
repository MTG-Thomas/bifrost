"""
GitHub Setup Consumer

Processes GitHub repository setup jobs from RabbitMQ queue.
Handles cloning repository, uploading to S3, and indexing files.
"""

import logging
from typing import Any
from uuid import uuid4

from src.core.database import get_db_context
from src.core.locks import (
    GITHUB_SETUP_LOCK_NAME,
    get_lock_service,
)
from src.jobs.rabbitmq import BaseConsumer
from src.models.contracts.notifications import (
    NotificationStatus,
    NotificationUpdate,
)
from src.services.notification_service import get_notification_service

logger = logging.getLogger(__name__)

# Queue name
QUEUE_NAME = "github-setup-jobs"


class GitHubSetupConsumer(BaseConsumer):
    """
    Consumer for GitHub setup operations queue.

    Message format:
    {
        "type": "github_setup",
        "job_id": "uuid",
        "notification_id": "uuid",
        "org_id": "organization-id",
        "user_id": "user-id",
        "user_email": "user@example.com",
        "repo_url": "https://github.com/owner/repo",
        "branch": "main",
        "token": "github-token"
    }
    """

    def __init__(self):
        super().__init__(
            queue_name=QUEUE_NAME,
            prefetch_count=1,
        )

    async def process_message(self, message_data: dict[str, Any]) -> None:
        """Process a GitHub setup message."""
        operation_type = message_data.get("type", "github_setup")

        if operation_type == "github_setup":
            await self._handle_github_setup(message_data)
        else:
            logger.error(f"Unknown operation type: {operation_type}")
            raise ValueError(f"Unknown operation type: {operation_type}")

    async def _handle_github_setup(self, message_data: dict[str, Any]) -> None:
        """
        Handle GitHub setup - clone repo, upload to S3, index files.

        Steps:
        1. Acquire setup lock
        2. Clone repository from GitHub
        3. Upload workspace files to S3
        4. Index files in database (workflows, data providers, forms)
        5. Save GitHub configuration
        6. Release lock
        """
        job_id = message_data.get("job_id", str(uuid4()))
        notification_id = message_data.get("notification_id")
        org_id = message_data.get("org_id")
        user_id = message_data.get("user_id")
        user_email = message_data.get("user_email")
        repo_url = message_data.get("repo_url")
        branch = message_data.get("branch", "main")
        token = message_data.get("token")

        logger.info(
            "Processing GitHub setup",
            extra={"job_id": job_id, "org_id": org_id, "repo_url": repo_url},
        )

        notification_service = get_notification_service()
        lock_service = get_lock_service()

        # Helper to update notification
        async def update_notification(
            description: str | None = None,
            status: NotificationStatus | None = None,
            percent: float | None = None,
            error: str | None = None,
            result: dict[str, Any] | None = None,
        ) -> None:
            if notification_id:
                await notification_service.update_notification(
                    notification_id,
                    NotificationUpdate(
                        description=description,
                        status=status,
                        percent=percent,
                        error=error,
                        result=result,
                    ),
                )

        lock_acquired = False

        try:
            # Validate required fields (org_id is optional - defaults to GLOBAL for platform-level)
            if not user_id:
                raise ValueError("user_id is required")
            if not repo_url:
                raise ValueError("repo_url is required")
            if not token:
                raise ValueError("token is required")

            # Step 1: Acquire lock
            lock_acquired, lock_info = await lock_service.acquire_lock(
                lock_name=GITHUB_SETUP_LOCK_NAME,
                owner_user_id=user_id,
                owner_email=user_email or "unknown",
                operation="GitHub repository setup",
                ttl_seconds=600,  # 10 minutes for setup
            )

            if not lock_acquired:
                error_msg = "Another GitHub setup is in progress"
                if lock_info:
                    error_msg = f"GitHub setup in progress by {lock_info.owner_email}"
                await update_notification(
                    status=NotificationStatus.FAILED,
                    error=error_msg,
                )
                raise ValueError(error_msg)

            # Step 2: Clone repository
            await update_notification(
                status=NotificationStatus.RUNNING,
                description="Cloning repository from GitHub...",
                percent=10,
            )

            # Create context for services (use GLOBAL for platform-level GitHub setup)
            class Context:
                def __init__(self, org_id: str | None):
                    self.org_id = org_id or "GLOBAL"
                    self.scope = org_id or "GLOBAL"

            context = Context(org_id)

            from src.services.git_integration import GitIntegrationService

            git_service = GitIntegrationService()

            # Initialize (clone) the repository
            backup_info = await git_service.initialize_repo(
                token=token,
                repo_url=repo_url,
                branch=branch,
            )

            # Extend lock after clone
            await lock_service.extend_lock(
                lock_name=GITHUB_SETUP_LOCK_NAME,
                owner_user_id=user_id,
                additional_seconds=300,
            )

            # Step 3: Upload to S3
            await update_notification(
                description="Uploading files to storage...",
                percent=40,
            )

            from src.services.file_storage import FileStorageService

            async with get_db_context() as db:
                storage = FileStorageService(db)
                uploaded_files = await storage.upload_from_directory(
                    local_path=git_service.workspace_path,
                    updated_by=user_email or "system",
                )
                await db.commit()

            # Step 4: Save GitHub configuration
            await update_notification(
                description="Finalizing configuration...",
                percent=80,
            )

            await git_service._save_github_config(
                context=context,
                repo_url=repo_url,
                token=token,
                branch=branch,
                updated_by=user_email or "system",
            )

            # Step 5: Complete
            await update_notification(
                status=NotificationStatus.COMPLETED,
                description="GitHub integration configured successfully",
                percent=100,
                result={
                    "files_indexed": len(uploaded_files),
                    "repo_url": repo_url,
                    "branch": branch,
                    "backup_path": backup_info.get("backup_path") if backup_info else None,
                },
            )

            logger.info(
                f"GitHub setup completed successfully: {job_id}, indexed {len(uploaded_files)} files"
            )

        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"GitHub setup error: {job_id}",
                extra={"error": error_msg},
                exc_info=True,
            )

            await update_notification(
                status=NotificationStatus.FAILED,
                error=error_msg,
            )

            # Don't re-raise - job is complete (failed)

        finally:
            # Release lock if we acquired it
            if lock_acquired:
                await lock_service.release_lock(
                    lock_name=GITHUB_SETUP_LOCK_NAME,
                    owner_user_id=user_id,
                )
