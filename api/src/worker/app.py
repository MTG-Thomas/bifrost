"""
Bifrost Worker - Background Worker Service

Worker application entry point.
Handles RabbitMQ message consumption for workflow execution and package installation.

This container is responsible for:
- Consuming workflow execution messages from RabbitMQ
- Executing workflow code (with thread pool for blocking code)
- Pushing results to Redis for sync execution requests
- Package installation

Can be scaled horizontally (replicas: N) for increased throughput.
"""

import asyncio
import logging
import os
import signal
from pathlib import Path

from src.config import get_settings
from src.core.database import init_db, close_db
from src.jobs.rabbitmq import rabbitmq
from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.jobs.consumers.package_install import PackageInstallConsumer
from src.jobs.consumers.agent_run import AgentRunConsumer
from src.jobs.summarize_worker import (
    SummarizeBackfillConsumer,
    SummarizeConsumer,
    TuneChatConsumer,
)

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

# Enable DEBUG for execution engine to troubleshoot workflows
logging.getLogger("src.services.execution").setLevel(logging.DEBUG)
logging.getLogger("bifrost").setLevel(logging.DEBUG)
logging.getLogger("src.jobs.consumers.workflow_execution").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)

_CONSUMER_NAMES = (
    "workflow",
    "package-install",
    "agent-run",
    "summarize",
    "summarize-backfill",
    "tune-chat",
)


def validate_worker_runtime() -> None:
    """Fail before queue consumption when required TLS runtime files are unusable."""
    import certifi

    ca_bundle = Path(certifi.where())
    if not ca_bundle.is_file():
        raise RuntimeError(f"Worker CA bundle is missing: {ca_bundle}")
    try:
        with ca_bundle.open("rb") as stream:
            if not stream.read(1):
                raise RuntimeError(f"Worker CA bundle is empty: {ca_bundle}")
    except OSError as exc:
        raise RuntimeError(f"Worker CA bundle is unreadable: {ca_bundle}") from exc


def consumer_factories():
    """Build factories lazily so configuration and test patches take effect."""
    def workflow_consumer():
        queue_name = os.environ.get("BIFROST_WORKFLOW_QUEUE_NAME", "")
        if configured_consumer_names() == ["workflow"] and not queue_name.endswith(
            "-canary"
        ):
            raise ValueError(
                "workflow-only workers require an explicit isolated -canary queue"
            )
        queue_name = queue_name or "workflow-executions"
        if queue_name == "workflow-executions":
            return WorkflowExecutionConsumer()
        return WorkflowExecutionConsumer(queue_name=queue_name)

    return {
        "workflow": workflow_consumer,
        "package-install": PackageInstallConsumer,
        "agent-run": AgentRunConsumer,
        "summarize": SummarizeConsumer,
        "summarize-backfill": SummarizeBackfillConsumer,
        "tune-chat": TuneChatConsumer,
    }


def configured_consumer_names() -> list[str]:
    """Return the explicit worker consumer set, failing closed on typos."""
    configured = os.environ.get("BIFROST_WORKER_CONSUMERS")
    if configured is None:
        return list(_CONSUMER_NAMES)
    raw = configured.strip()
    if not raw:
        raise ValueError("BIFROST_WORKER_CONSUMERS must select at least one consumer")
    names = [name.strip() for name in raw.split(",") if name.strip()]
    if not names:
        raise ValueError("BIFROST_WORKER_CONSUMERS must select at least one consumer")
    unknown = sorted(set(names) - set(_CONSUMER_NAMES))
    if unknown:
        raise ValueError(f"Unknown BIFROST_WORKER_CONSUMERS values: {unknown}")
    return names


class Worker:
    """
    Background jobs worker.

    Manages RabbitMQ consumers for:
    - Workflow execution (with Redis result push for sync requests)
    - Package installation
    """

    def __init__(self):
        self.settings = get_settings()
        self.running = False
        self._shutdown_event = asyncio.Event()
        self._consumers: list = []
        self._stopping = False
        self._stop_task: asyncio.Task[None] | None = None
        self._stop_error: Exception | None = None

    async def start(self) -> None:
        """Start the worker.

        On any startup failure, fully tear down whatever has been started
        so the process can exit cleanly. Without this, a partially-started
        worker leaks its process pool's template child process, which in
        turn blocks Python's multiprocessing atexit cleanup inside
        waitpid() — leaving PID 1 hung forever and the container looking
        healthy to Kubernetes even though the worker is dead.
        """
        self.running = True
        logger.info("Starting Bifrost Worker...")
        logger.info(f"Environment: {self.settings.environment}")

        try:
            validate_worker_runtime()

            # Initialize database connection
            logger.info("Initializing database connection...")
            await init_db()
            # Configure the ORM before accepting queue messages. Lazy mapper
            # setup otherwise lands on the first execution-row insert and can
            # add hundreds of milliseconds to the first workflow after start.
            from sqlalchemy.orm import configure_mappers

            configure_mappers()
            from src.services.execution_attempts import (
                require_execution_operations_schema,
            )

            await require_execution_operations_schema()
            logger.info("Database connection established")

            # Initialize and start RabbitMQ consumers
            logger.info("Starting RabbitMQ consumers...")
            await self._start_consumers()
        except Exception:
            logger.error("Startup failed; tearing down partially-started worker")
            await self._cleanup_after_failed_start()
            raise

        logger.info("Bifrost Worker started")
        logger.info("Waiting for messages... (Ctrl+C to stop)")

        # Keep running until shutdown
        await self._shutdown_event.wait()
        if self._stop_error is not None:
            raise self._stop_error

    async def _cleanup_after_failed_start(self) -> None:
        """Best-effort teardown of any resources started before a failure.

        Called when start() raises partway through. Must be tolerant of
        consumers that never got past __init__, and of consumers whose
        own stop() might also fail.
        """
        for consumer in self._consumers:
            try:
                await consumer.stop()
            except Exception as e:
                logger.error(
                    f"Error stopping consumer {consumer.queue_name} during cleanup: {e}"
                )

        try:
            await rabbitmq.close()
        except Exception as e:
            logger.error(f"Error closing RabbitMQ pools during cleanup: {e}")

        try:
            await close_db()
        except Exception as e:
            logger.error(f"Error closing DB during cleanup: {e}")

    async def _start_consumers(self) -> None:
        """Start all RabbitMQ consumers."""
        # Create consumer instances
        factories = consumer_factories()
        self._consumers = [factories[name]() for name in configured_consumer_names()]

        # Start each consumer
        for consumer in self._consumers:
            try:
                await consumer.start()
                logger.info(f"Started consumer: {consumer.queue_name}")
            except Exception as e:
                logger.error(f"Failed to start consumer {consumer.queue_name}: {e}")
                raise

    async def stop(self) -> None:
        """Stop the worker gracefully (drain in-flight, then close).

        Each consumer cancels its consumer tag (so RabbitMQ stops handing
        it new messages), waits for in-flight tasks to finish (up to the
        drain deadline), then closes its channel. The deadline is tunable
        via BIFROST_DRAIN_DEADLINE_SECONDS (default 300s) and should be
        less than the K8s terminationGracePeriodSeconds with margin for
        connection cleanup.
        """
        if self._stopping:
            return
        self._stopping = True
        logger.info("Stopping Bifrost Worker (graceful drain)...")
        self.running = False

        # Drain consumers in parallel — each cancels its consumer tag, waits on
        # its in-flight tasks, then closes its channel.
        deadline_str = os.environ.get("BIFROST_DRAIN_DEADLINE_SECONDS", "300")
        try:
            drain_deadline = float(deadline_str)
            if drain_deadline <= 0:
                raise ValueError(f"must be positive, got {drain_deadline}")
        except ValueError as e:
            logger.warning(
                f"Invalid BIFROST_DRAIN_DEADLINE_SECONDS={deadline_str!r}: {e}; "
                f"falling back to 300s"
            )
            drain_deadline = 300.0
        results = await asyncio.gather(
            *(self._drain_consumer(consumer, drain_deadline) for consumer in self._consumers),
            return_exceptions=True,
        )
        failed_consumers: list[tuple[object, Exception]] = []
        for consumer, result in zip(self._consumers, results):
            if isinstance(result, Exception):
                logger.error(f"Error draining consumer {consumer.queue_name}: {result}")
                failed_consumers.append((consumer, result))
            else:
                logger.info(f"Drained consumer: {consumer.queue_name}")

        # A workflow consumer can fail its drain after killing a child when
        # PostgreSQL is temporarily unavailable and the attempt cannot be
        # durably surrendered. Retry consumer teardown while the database is
        # still open; closing shared resources and exiting here would discard
        # the exact attempt token retained by the process pool.
        for retry_number in range(1, 3):
            if not failed_consumers:
                break
            retrying = failed_consumers
            failed_consumers = []
            for consumer, _previous_error in retrying:
                try:
                    await consumer.stop()
                    logger.info(
                        "Stopped consumer %s after drain retry %d",
                        consumer.queue_name,
                        retry_number,
                    )
                except Exception as error:
                    logger.error(
                        "Error retrying consumer %s shutdown (%d/2): %s",
                        consumer.queue_name,
                        retry_number,
                        error,
                    )
                    failed_consumers.append((consumer, error))

        if failed_consumers:
            names = ", ".join(str(consumer.queue_name) for consumer, _ in failed_consumers)
            error = RuntimeError(
                "Worker shutdown could not durably stop consumers: " + names
            )
            self._stop_error = error
            self._stopping = False
            self._shutdown_event.set()
            raise error

        # Close RabbitMQ pools (idempotent if already closed).
        await rabbitmq.close()
        logger.info("RabbitMQ connections closed")

        from src.services.agent_runtime.retry_transport import (
            close_ai_retry_http_client,
        )

        await close_ai_retry_http_client()

        # Close database connections.
        await close_db()
        logger.info("Database connections closed")

        self._shutdown_event.set()
        logger.info("Bifrost Worker stopped")

    async def _drain_consumer(self, consumer, deadline: float) -> None:
        """Helper: drain one consumer with the given deadline."""
        await consumer.drain(deadline=deadline)

    def handle_signal(self, signum: int, frame) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating shutdown...")
        self._stop_task = asyncio.create_task(self._stop_from_signal())

    async def _stop_from_signal(self) -> None:
        """Wake ``start`` with a shutdown failure without leaking task errors."""
        try:
            await self.stop()
        except Exception as error:
            self._stop_error = error
            self._shutdown_event.set()


async def main() -> None:
    """Main entry point."""
    worker = Worker()

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: worker.handle_signal(s, None))

    try:
        await worker.start()
    except Exception as e:
        logger.error(f"Worker error: {e}", exc_info=True)
        # Hard-exit bypassing atexit handlers. Python's multiprocessing
        # atexit will otherwise block in waitpid() if any subprocess is
        # still running (e.g., a template child that cleanup failed to
        # stop), leaving PID 1 hung and the container "healthy" to k8s
        # despite the worker being dead. Force exit here so kubelet sees
        # the container terminate and restarts it.
        os._exit(1)
