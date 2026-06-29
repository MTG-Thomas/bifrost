"""OpenTelemetry configuration helpers."""

import logging
import os

logger = logging.getLogger(__name__)

_configured_services: set[str] = set()


def configure_opentelemetry(service_name: str, *, span_processor: str = "batch") -> None:
    """Configure OTLP trace and metric export when an endpoint is provided."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    if service_name in _configured_services:
        return

    try:
        from opentelemetry import metrics
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError as exc:
        logger.warning("OpenTelemetry export unavailable for %s: %s", service_name, exc)
        return

    resource = Resource.create({"service.name": service_name})
    trace_provider = TracerProvider(resource=resource)
    trace_exporter = OTLPSpanExporter(endpoint=endpoint)

    if span_processor == "simple":
        trace_provider.add_span_processor(SimpleSpanProcessor(trace_exporter))
    else:
        trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))

    trace.set_tracer_provider(trace_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint),
        export_interval_millis=15_000,
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))

    _configured_services.add(service_name)
    logger.info("OpenTelemetry trace and metric export configured for %s", service_name)
