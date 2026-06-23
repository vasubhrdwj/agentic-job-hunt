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

    _load_dotenv_if_available()
    _tracer_provider = register(
        project_name=project_name,
        batch=True,
        auto_instrument=False,
        protocol=protocol,
    )
    GoogleADKInstrumentor().instrument(tracer_provider=_tracer_provider)
    _instrument_google_genai_if_available(_tracer_provider)
    return _tracer_provider


def flush_phoenix_tracing(timeout_millis: int = 15_000) -> bool:
    """Flush queued spans when a short-lived CLI process is about to exit."""
    if _tracer_provider is None:
        return True
    return bool(_tracer_provider.force_flush(timeout_millis=timeout_millis))


def _instrument_google_genai_if_available(tracer_provider: Any) -> None:
    """Add direct google-genai spans when the optional package is installed."""
    try:
        from openinference.instrumentation.google_genai import GoogleGenAIInstrumentor
    except ImportError:
        return
    GoogleGenAIInstrumentor().instrument(tracer_provider=tracer_provider)


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()
