"""Phoenix tracing setup for the job-hunt agent."""

from __future__ import annotations

from typing import Any

from openinference.instrumentation.google_adk import GoogleADKInstrumentor
from phoenix.otel import register

DEFAULT_PROJECT_NAME = "job-hunt-agent"
DEFAULT_PROTOCOL = "http/protobuf"

_tracer_provider: Any | None = None


def configure_phoenix_tracing(
    project_name: str = DEFAULT_PROJECT_NAME,
    protocol: str = DEFAULT_PROTOCOL,
) -> Any:
    """Configure Phoenix/OpenInference tracing once and return the tracer provider."""
    global _tracer_provider

    if _tracer_provider is not None:
        return _tracer_provider

    _tracer_provider = register(
        project_name=project_name,
        auto_instrument=False,
        protocol=protocol,
    )
    GoogleADKInstrumentor().instrument(tracer_provider=_tracer_provider)
    return _tracer_provider

