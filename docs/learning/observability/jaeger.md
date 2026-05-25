# Jaeger and distributed tracing

## What it is

Jaeger is a **distributed tracing backend**: an open-source service that collects "traces" emitted by your application and lets you browse them in a UI. It was originally built at Uber, donated to the Cloud Native Computing Foundation (CNCF), and is now the default open-source choice for tracing.

To understand what Jaeger does, you first need the concept of a **trace**. To understand traces, you need to know how they differ from logs and metrics.

## The three pillars of observability

| | Logs | Metrics | Traces |
| --- | --- | --- | --- |
| **Shape** | Append-only timestamped events | Numbers over time | Tree of timed operations within one request |
| **Question they answer** | What happened? | How much? How often? How fast on average? | Where did time go in *this specific* request? |
| **Example** | "User 42 logged in at 14:03:22" | "p95 latency = 230ms; 5xx rate = 0.4%" | "PR 482 review took 12.8s; 9.4s was waiting on Anthropic" |
| **Storage cost** | High (every event) | Low (aggregated) | Moderate (sampled or every-Nth) |
| **Backend used by Sentinel** | structlog → stdout | Prometheus | **Jaeger** |

Logs and metrics tell you about *the system over time*. A trace tells you about *one specific request*, end to end. When a single PR review is slow, traces show you exactly where the time went. When the system overall is slow, metrics aggregate across many requests to show the trend.

## Spans and traces

A **trace** is a tree of spans representing one logical request through your system.

A **span** is a single timed operation: it has a start time, an end time, a name, and a set of attributes (key-value pairs). Spans nest: a parent span can have child spans for the sub-operations it kicked off.

Here's a trace for a Sentinel PR review, viewed in the Jaeger UI:

```
review_run (12.8s, run_id=7a3f, pr=acme/api#482)
├── fetch_diff (412ms)
│   └── github.api.get_diff (380ms)
├── analyze_diff (2.1s)
│   └── anthropic.messages.create (1.9s, tokens_in=1820, tokens_out=340, cost_cents=1)
├── security_review (3.9s)
│   └── anthropic.messages.create (3.6s, tokens_in=2210, tokens_out=580)
├── correctness_review (3.2s, parallel)
├── testing_review (2.8s, parallel)
├── consolidate (18ms)
└── post_comments (620ms)
```

In the actual UI you'd see this as a Gantt-chart-style waterfall: time on the X axis, spans as horizontal bars, depth shown by indentation. Click any bar and you see its attributes (model name, token count, cost cents, retry count, error message if any).

Questions this makes easy:

- "Why was that one PR slow?" Look at the trace. Probably one specific LLM call was the long pole.
- "Did the security reviewer retry?" Span has a `retry_count` attribute; you can see it.
- "What did it cost us to review this PR?" Sum the `cost_cents` attributes on the LLM spans.
- "Where in the pipeline did it fail?" The failing span is red; its parents are red until the root.

## How a span gets from your code into Jaeger

Three pieces of machinery work together:

1. **OpenTelemetry SDK in your application code** creates spans. In Sentinel, the FastAPI app and the httpx client are auto-instrumented (one line each in `apps/api/main.py`); custom spans are added around steps and LLM calls.
2. **OTLP (OpenTelemetry Protocol)** ships those spans over the network. Sentinel uses gRPC on port 4317.
3. **Jaeger collector** receives the spans, stores them (in memory for the all-in-one dev image, in Cassandra/Elasticsearch in real deployments), and serves them on the UI port 16686.

```
[Sentinel API]            [Jaeger]
                 OTLP gRPC
your code        ─────────▶  collector  ─▶  storage  ─▶  UI (port 16686)
  └ otel sdk      port 4317
```

In Sentinel's Compose file:

```yaml
jaeger:
  image: jaegertracing/all-in-one:1.62
  environment:
    COLLECTOR_OTLP_ENABLED: "true"
  ports:
    - "16686:16686"  # browser UI
    - "4317:4317"    # OTLP gRPC ingest
    - "4318:4318"    # OTLP HTTP ingest (alternative)
```

The `all-in-one` image bundles the collector, the storage backend (in-memory), and the UI into a single process. It's not production-grade (data is lost on restart), but it's perfect for local development.

## Sentinel's OTel setup, briefly

`packages/core/observability/tracing.py` configures the SDK at app startup:

```python
def configure_tracing(*, service_name: str, otlp_endpoint: str, env: str) -> trace.Tracer:
    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "sentinel",
        "deployment.environment": env,
    })
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer("sentinel")
```

What this does:

- Creates a `Resource` describing this service (name, environment).
- Creates a `TracerProvider`, the SDK's central object.
- Creates an OTLP exporter pointing at `http://localhost:4317` (the Jaeger container) and wraps it in a `BatchSpanProcessor` that batches spans before shipping (efficient).
- Installs the provider globally so any code in the process can call `trace.get_tracer(...)` and get a working tracer.

Then `apps/api/main.py` auto-instruments FastAPI and httpx, which means every incoming request becomes a span automatically, and every outgoing HTTP call becomes a child span automatically. The orchestrator (Week 2) will add custom spans for each step it runs.

## OTel baggage and correlation IDs

A trace has a **trace ID** that's the same across every span in the tree. But you also want to attach contextual data (like a `request_id` or `run_id`) that flows down to every child span and even to downstream services you call.

That's **baggage**. Sentinel's `RequestIdMiddleware` (`apps/api/middleware.py`) sets the `request_id` into OTel baggage at the very start of the request:

```python
ctx = baggage.set_baggage("request_id", request_id)
context.attach(ctx)
```

Every span created later in that request automatically has the `request_id` available, and OTel's HTTP instrumentation propagates it as a header on outgoing calls so downstream services see the same ID. That's how you correlate a single request across logs, metrics, and traces.

## In production, you usually don't run Jaeger yourself

Local dev: Jaeger all-in-one in a container. Easy.

Production: you have several choices, and the choice doesn't affect your application code because OTel is standardised on the producer side. You change one env var (`OTEL_EXPORTER_OTLP_ENDPOINT`) and your spans go somewhere else.

| Option | What it is | When to pick it |
| --- | --- | --- |
| **Self-hosted Jaeger (production mode)** | Real Jaeger backed by Cassandra or Elasticsearch | You want full control, you have ops capacity |
| **Grafana Tempo** | Tracing backend that integrates with Grafana / Loki / Prometheus | You already run Grafana; you want the unified experience |
| **AWS X-Ray** | AWS's hosted tracing | You're all-in on AWS |
| **Datadog APM** | Tracing as part of Datadog's broader platform | Polished, expensive; common at well-funded startups |
| **Honeycomb** | High-cardinality analytics over traces | You want to ask weird questions; preferred by some platform teams |
| **Lightstep / ServiceNow Cloud Observability** | Similar space, commercial | Enterprise contracts |
| **Grafana Cloud Tempo** | Hosted Tempo | You want Grafana but not the ops |

Sentinel's `.env.example` configures the OTLP endpoint via env, so swapping is a one-line change.

## Why we use it

1. **You cannot debug an AI system without traces.** "Why was this PR review slow?" is a question metrics can't answer (they aggregate) and logs make tedious (you'd grep across many lines). A trace shows you the answer in one screen.
2. **Cost visibility per request.** Token counts and dollar costs as span attributes let you see exactly how expensive each individual request was, broken down by step. The cost dashboard (Grafana, Week 6) aggregates this across many runs.
3. **Reliability debugging.** When the security reviewer times out, the trace shows it as a red span with the error attached. You see the retry count, the latency before failure, and which parent step it killed.
4. **Onboarding signal.** Showing a trace in a portfolio demo communicates more about reliability engineering in 10 seconds than any amount of prose.

## TL;DR

Jaeger is a tracing backend. Your code creates spans via OpenTelemetry; spans are shipped over OTLP (gRPC on port 4317) to Jaeger; you browse the resulting traces in a UI on port 16686. A trace is a tree of timed operations representing one request through your system, distinct from logs (events) and metrics (aggregates). For local dev Sentinel uses Jaeger's all-in-one container; in production you'd point the OTLP exporter at Tempo, X-Ray, Datadog, Honeycomb, or hosted Jaeger by changing one env var.

## Interview-style questions

1. What does a trace tell you that logs and metrics don't? Give a concrete debugging scenario where only a trace would help.
2. Walk through the journey of a single span from the application code into the Jaeger UI. Name the protocol, the port, and the SDK component that does each step.
3. Why is "service.name" set as a Resource attribute on the TracerProvider rather than on every span individually?
4. What is OTel baggage? How does Sentinel use it for correlation IDs?
5. The `all-in-one` Jaeger image is fine for local dev but a bad choice in production. Name two reasons.
6. Your CTO wants to switch from Jaeger to Datadog APM. What needs to change in the Sentinel codebase? What doesn't?
