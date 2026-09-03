"""OTLP export into Langfuse, in the shape mega-loop's detectors read.

Langfuse routes by API key, not by a resource attribute, so the export is OTLP/HTTP to
``<host>/api/public/otel/v1/traces`` with a Basic header — the per-backend half of intake
scoping. The attribute vocabulary is OpenInference: ``openinference.span.kind`` and the
flat ``llm.*`` keys are what ``mega_loop.analyze.detect`` matches on, and a span that
spells them differently is invisible to every detector.
"""

from __future__ import annotations

import base64
import json
import os
from contextlib import contextmanager
from collections.abc import Iterator

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

KIND = "openinference.span.kind"


class Config:
    """Langfuse target, read from the environment once."""

    def __init__(self) -> None:
        host = (os.environ.get("LANGFUSE_HOST") or "").rstrip("/")
        public = os.environ.get("LANGFUSE_PUBLIC_KEY") or ""
        secret = os.environ.get("LANGFUSE_SECRET_KEY") or ""
        missing = [
            name
            for name, value in (
                ("LANGFUSE_HOST", host),
                ("LANGFUSE_PUBLIC_KEY", public),
                ("LANGFUSE_SECRET_KEY", secret),
            )
            if not value
        ]
        if missing:
            raise SystemExit(f"missing env: {', '.join(missing)} — copy .env.example to .env")
        self.endpoint = f"{host}/api/public/otel/v1/traces"
        self.auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
        self.service = os.environ.get("BUGZOO_SERVICE_NAME", "mega-loop-bugzoo")


def build_tracer(config: Config) -> tuple[trace.Tracer, TracerProvider]:
    provider = TracerProvider(resource=Resource.create({"service.name": config.service}))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=config.endpoint,
                headers={"Authorization": f"Basic {config.auth}"},
            )
        )
    )
    return provider.get_tracer("bugzoo"), provider


def tool_attrs(name: str, value: object, output: object) -> dict:
    """A TOOL span's attributes. ``output.value`` is what every content detector reads."""
    return {
        KIND: "TOOL",
        "tool.name": name,
        "input.value": _text(value),
        "output.value": _text(output),
    }


def llm_attrs(
    *,
    system: str | None = None,
    user: str | None = None,
    history: list[dict] | None = None,
    output: str = "",
    tool_calls: list[tuple[str, str]] | None = None,
    declared: list[dict] | None = None,
) -> dict:
    """An LLM span's attributes in flat OpenInference keys.

    ``history`` exists so the context-growth detector has message INDICES to count: it
    counts distinct ``llm.input_messages.{i}`` prefixes, not a token total.
    """
    attrs: dict[str, object] = {KIND: "LLM", "gen_ai.request.model": "bugzoo-sim-1"}
    messages: list[dict] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.extend(history or [])
    if user is not None:
        messages.append({"role": "user", "content": user})
    for i, msg in enumerate(messages):
        attrs[f"llm.input_messages.{i}.message.role"] = msg["role"]
        attrs[f"llm.input_messages.{i}.message.content"] = msg["content"]
    attrs["llm.output_messages.0.message.role"] = "assistant"
    attrs["llm.output_messages.0.message.content"] = output
    for j, (fn_name, fn_args) in enumerate(tool_calls or []):
        base = f"llm.output_messages.0.message.tool_calls.{j}.tool_call.function"
        attrs[f"{base}.name"] = fn_name
        attrs[f"{base}.arguments"] = fn_args
    for k, schema in enumerate(declared or []):
        attrs[f"llm.tools.{k}.tool.json_schema"] = json.dumps(schema)
    attrs["gen_ai.usage.input_tokens"] = 120 + 40 * len(messages)
    attrs["gen_ai.usage.output_tokens"] = max(1, len(output) // 4)
    return attrs


@contextmanager
def span(tracer: trace.Tracer, name: str, attrs: dict) -> Iterator[trace.Span]:
    with tracer.start_as_current_span(name, attributes=_flatten(attrs)) as sp:
        yield sp


def fail(sp: trace.Span, message: str) -> None:
    """Mark a span errored the way both mega-loop's error readers see it.

    ``status_message`` is set as well as the status: the detectors treat either as an
    error, and the counterfactual re-run compares its raised exception against this text.
    """
    sp.set_status(trace.Status(trace.StatusCode.ERROR, message))
    sp.set_attribute("status_message", message)


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _flatten(attrs: dict) -> dict:
    return {k: v if isinstance(v, (str, int, float, bool)) else _text(v) for k, v in attrs.items()}
