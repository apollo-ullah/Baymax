"""Phoenix connection / client setup.

Configuration helpers plus a lightweight OpenTelemetry tracer that can export
spans to Phoenix when a collector endpoint is configured. Safe to import and use
even when Phoenix is not running.
"""

import os

from dotenv import load_dotenv

load_dotenv()

PHOENIX_COLLECTOR_ENDPOINT = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
PHOENIX_PROJECT_NAME = os.getenv("PHOENIX_PROJECT_NAME")

DEFAULT_SERVICE_NAME = "agent-observability-demo"

_TRACER_PROVIDER_INITIALIZED = False


def get_project_name():
    """Return the configured Phoenix project name (or None if unset)."""
    return PHOENIX_PROJECT_NAME


def is_phoenix_configured():
    """Return True if a Phoenix collector endpoint is configured."""
    return bool(PHOENIX_COLLECTOR_ENDPOINT)


def print_phoenix_status():
    """Print whether Phoenix is configured, with project/endpoint details."""
    if is_phoenix_configured():
        print("[Phoenix] configured")
        print(f"project={PHOENIX_PROJECT_NAME}")
        print(f"endpoint={PHOENIX_COLLECTOR_ENDPOINT}")
    else:
        print("[Phoenix] not configured")


def get_tracer():
    """Return an OpenTelemetry tracer, exporting to Phoenix if configured.

    Sets up an OTLP HTTP span exporter pointed at the Phoenix collector when
    PHOENIX_COLLECTOR_ENDPOINT is set. Never crashes if Phoenix is unavailable or
    exporter setup fails - a tracer is always returned.
    """
    global _TRACER_PROVIDER_INITIALIZED

    from opentelemetry import trace

    if is_phoenix_configured() and not _TRACER_PROVIDER_INITIALIZED:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            service_name = PHOENIX_PROJECT_NAME or DEFAULT_SERVICE_NAME
            resource = Resource.create({"service.name": service_name})

            provider = TracerProvider(resource=resource)
            endpoint = f"{PHOENIX_COLLECTOR_ENDPOINT.rstrip('/')}/v1/traces"
            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))

            trace.set_tracer_provider(provider)
            _TRACER_PROVIDER_INITIALIZED = True
        except Exception as exc:  # noqa: BLE001 - stay resilient if setup fails
            print(f"[Phoenix] warning: could not set up span exporter ({exc})")

    return trace.get_tracer("arize-track")
