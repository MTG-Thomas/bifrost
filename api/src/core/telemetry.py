"""OpenTelemetry setup for Bifrost runtime services."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_CONFIGURED = False


def configure_opentelemetry(service_name: str, *, span_processor: str = "batch") -> None:
    """Configure OTLP trace export when the runtime has an OTLP endpoint."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.info("OpenTelemetry disabled: OTEL_EXPORTER_OTLP_ENDPOINT is not set")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError as exc:
        logger.warning("OpenTelemetry disabled: missing runtime package: %s", exc)
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint)
    if span_processor == "simple":
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _CONFIGURED = True
    logger.info("OpenTelemetry trace export configured for %s", service_name)
