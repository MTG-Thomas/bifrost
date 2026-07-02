"""OpenTelemetry configuration helpers."""

import logging
import os

logger = logging.getLogger(__name__)

_configured_services: set[str] = set()
_trace_provider = None
_meter_provider = None


def configure_opentelemetry(service_name: str, *, span_processor: str = "batch") -> None:
    """Configure OTLP trace and metric export when an endpoint is provided."""
    global _meter_provider, _trace_provider

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
    _trace_provider = trace_provider

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint),
        export_interval_millis=15_000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    _meter_provider = meter_provider

    _configured_services.add(service_name)
    logger.info("OpenTelemetry trace and metric export configured for %s", service_name)


def flush_opentelemetry(*, timeout_millis: int = 5_000) -> None:
    """Force any configured OpenTelemetry providers to export buffered data."""
    for provider_name, provider in (
        ("metric", _meter_provider),
        ("trace", _trace_provider),
    ):
        if provider is None:
            continue

        force_flush = getattr(provider, "force_flush", None)
        if force_flush is None:
            continue

        try:
            force_flush(timeout_millis=timeout_millis)
        except TypeError:
            force_flush()
        except Exception as exc:
            logger.warning("OpenTelemetry %s flush failed: %s", provider_name, exc)
