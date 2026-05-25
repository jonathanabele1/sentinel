"""OpenTelemetry tracer setup with OTLP gRPC export."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_tracing(*, service_name: str, otlp_endpoint: str, env: str) -> trace.Tracer:
    """Initialise the global TracerProvider and return a tracer."""
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "sentinel",
            "deployment.environment": env,
        }
    )

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    return trace.get_tracer("sentinel")


def get_tracer(name: str = "sentinel") -> trace.Tracer:
    return trace.get_tracer(name)
