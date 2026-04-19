"""
Bifrost Worker - Background Worker Service
"""

import asyncio
import logging
import os
import signal

from src.config import get_settings
from src.core.database import init_db, close_db
from src.jobs.rabbitmq import rabbitmq
from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.jobs.consumers.package_install import PackageInstallConsumer
from src.jobs.consumers.agent_run import AgentRunConsumer
from src.observability.otel import configure_tracing, get_tracer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logging.getLogger("aiormq").setLevel(logging.WARNING)
logging.getLogger("aio_pika").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("aiobotocore").setLevel(logging.WARNING)
logging.getLogger("s3transfer").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logging.getLogger("src.services.execution").setLevel(logging.DEBUG)
logging.getLogger("bifrost").setLevel(logging.DEBUG)
logging.getLogger("src.jobs.consumers.workflow_execution").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)


class Worker:

    def __init__(self):
        self.settings = get_settings()
        self.running = False
        self._shutdown_event = asyncio.Event()
        self._consumers: list = []

    async def start(self) -> None:
        self.running = True
        logger.info("Starting Bifrost Worker...")
        logger.info(f"Environment: {self.settings.environment}")

        # Initialize tracing
        configure_tracing("bifrost-worker", self.settings.environment)

        with tracer.start_as_current_span("worker.start"):
            try:
                logger.info("Initializing database connection...")
                await init_db()
                logger.info("Database connection established")

                logger.info("Starting RabbitMQ consumers...")
                await self._start_consumers()
            except Exception:
                logger.error("Startup failed; tearing down partially-started worker")
                await self._cleanup_after_failed_start()
                raise

        logger.info("Bifrost Worker started")
        logger.info("Waiting for messages... (Ctrl+C to stop)")

        await self._shutdown_event.wait()

    async def _start_consumers(self) -> None:
        self._consumers = [
            WorkflowExecutionConsumer(),
            PackageInstallConsumer(),
            AgentRunConsumer(),
        ]

        for consumer in self._consumers:
            try:
                with tracer.start_as_current_span(f"consumer.start.{consumer.queue_name}"):
                    await consumer.start()
                logger.info(f"Started consumer: {consumer.queue_name}")
            except Exception as e:
                logger.error(f"Failed to start consumer {consumer.queue_name}: {e}")
                raise

    async def stop(self) -> None:
        logger.info("Stopping Bifrost Worker...")
        self.running = False

        for consumer in self._consumers:
            try:
                with tracer.start_as_current_span(f"consumer.stop.{consumer.queue_name}"):
                    await consumer.stop()
                logger.info(f"Stopped consumer: {consumer.queue_name}")
            except Exception as e:
                logger.error(f"Error stopping consumer {consumer.queue_name}: {e}")

        await rabbitmq.close()
        logger.info("RabbitMQ connections closed")

        await close_db()
        logger.info("Database connections closed")

        self._shutdown_event.set()
        logger.info("Bifrost Worker stopped")

    def handle_signal(self, signum: int, frame) -> None:
        logger.info(f"Received signal {signum}, initiating shutdown...")
        asyncio.create_task(self.stop())


async def main() -> None:
    worker = Worker()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: worker.handle_signal(s, None))

    try:
        await worker.start()
    except Exception as e:
        logger.error(f"Worker error: {e}", exc_info=True)
        os._exit(1)


if __name__ == "__main__":
    asyncio.run(main())
