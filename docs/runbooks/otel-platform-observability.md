# Bifrost OpenTelemetry Platform Observability

This is the operating plan for using OpenTelemetry as Bifrost platform
telemetry, not just trace decoration.

## Goal

Every workflow execution should answer these questions from one trace:

- Did the API accept and enqueue the execution?
- Did a worker pick it up?
- Did worker setup, module/cache loading, or the workflow body consume the time?
- Did it fail because of platform infrastructure, workflow code, or a dependency?
- Which namespace, service, and image version produced the span?

## Current Trace Shape

The first useful trace path is:

1. `bifrost.workflow.enqueue` from the API process.
2. `bifrost.worker.execute` from the worker process.
3. `bifrost.workflow.execute` from the execution engine.

The shared join key is `bifrost.execution.id`. Use `bifrost.workflow.name`,
`bifrost.workflow.function`, and `bifrost.execution.organization_id` for
filtering, not for high-cardinality aggregation.

## Consumption Model

Keep the collector as the ingestion boundary and add a real trace backend behind
it. The recommended progression is:

1. **Debug exporter only** while proving span content in PoC.
2. **Grafana Tempo** for durable trace storage once spans are useful.
3. **Grafana dashboards** that link OpenCost namespace spend to trace examples.
4. **Log correlation** by adding trace IDs to structured logs after the trace
   shape is stable.

Tempo is the right next backend because it is cheaper to operate than a heavy
APM stack, works cleanly behind the OpenTelemetry Collector, and gives us a
direct path to Grafana panels and trace search. Jaeger remains useful for local
developer debugging, but it should not be the durable cluster store.

## Day-To-Day Use

For a failed or slow execution:

1. Start with the execution ID from Bifrost history.
2. Search traces by `bifrost.execution.id`.
3. Compare span durations:
   - API enqueue high: Redis/RabbitMQ publish path.
   - Gap before worker span: queue depth, KEDA scale-out, or worker availability.
   - Worker span high before workflow span: context read, module cache, auth, or
     setup.
   - Workflow span high: user workflow, SDK calls, integrations, or data
     provider cache misses.
4. If the trace points to platform capacity, compare with OpenCost namespace
   allocation and worker scaling history.
5. If the trace points to policy drift, check Kyverno reports for the namespace
   before changing manifests.

## Attribute Rules

Allowed high-cardinality fields:

- `bifrost.execution.id`
- `bifrost.workflow.id`

Allowed filter fields:

- `bifrost.workflow.name`
- `bifrost.workflow.function`
- `bifrost.execution.organization_id`
- `bifrost.execution.status`
- `bifrost.execution.error_type`
- `bifrost.worker.is_script`
- `bifrost.worker.has_file_path`

Do not emit secrets, parameters, request bodies, ticket content, log lines, or
raw results as span attributes.

## Next Instrumentation Slices

1. Add queue wait timing by carrying an enqueue timestamp into the worker
   context.
2. Split worker setup into child spans for context read, module cache load, and
   workflow function load.
3. Add integration-call spans with provider, status, duration, and sanitized
   error type.
4. Add data-provider cache spans for hit/miss, load duration, and write duration.
5. Add trace ID to execution logs so Bifrost history can deep-link to Tempo.

## Maturity Gate

Do not make OTel mandatory for production startup. Runtime behavior must remain
unchanged when `OTEL_EXPORTER_OTLP_ENDPOINT` is absent. Treat telemetry export as
best-effort until the collector, backend, retention, and dashboards are proven
in the AKS lane.
