"""OpenTelemetry configuration helpers."""

import logging
import os

logger = logging.getLogger(__name__)

_configured_services: set[str] = set()


def configure_opentelemetry(service_name: str, *, span_processor: str = "batch") -> None:
    """Configure OTLP trace export when an endpoint is provided."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    if service_name in _configured_services:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError as exc:
        logger.warning("OpenTelemetry trace export unavailable for %s: %s", service_name, exc)
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint)

    if span_processor == "simple":
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _configured_services.add(service_name)
    logger.info("OpenTelemetry trace export configured for %s", service_name)
