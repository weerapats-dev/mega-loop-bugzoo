"""One trace per detector signal, each driven by the real defect in ``tools.py``.

Every scenario is deliberately narrow: a root ``run_agent_loop`` span and the one or
two children the signal needs. A trace that exercises several defects at once files as
one consolidated bug and the coverage claim stops being checkable.

The healthy baseline is not optional. Three detectors read a measured line rather than
a constant — the latency pair needs 30 samples of a span NAME before it has a
threshold at all, ``no_tool_evidence`` needs 30 traces before it trusts the share that
gather evidence, and ``system_instruction_dropped`` needs a longer instruction on the
same span name to be a subset OF. Seed the baseline or those four stay silent forever.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from opentelemetry import trace

from bugzoo import tools
from bugzoo.telemetry import KIND, fail, llm_attrs, span, tool_attrs

SCENARIOS: dict[str, Callable[[trace.Tracer], None]] = {}

GROUNDED_ANSWER = (
    "Refunds are issued to the original payment method within five business days, per "
    "policy REF-001, and hardware carries a twelve month limited warranty."
)

SCHEMAS = [
    {
        "function": {
            "name": "search_docs",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    },
    {
        "function": {
            "name": "lookup_order",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "sort": {"type": "string", "enum": ["asc", "desc"]},
                },
                "required": ["order_id"],
            },
        }
    },
    {
        "function": {
            "name": "query_inventory",
            "parameters": {
                "type": "object",
                "properties": {"sku": {"type": "string"}},
                "required": ["sku"],
            },
        }
    },
]


def scenario(name: str) -> Callable[[Callable], Callable]:
    def register(fn: Callable) -> Callable:
        SCENARIOS[name] = fn
        return fn

    return register


def _agent(question: str, answer: str) -> dict:
    return {KIND: "AGENT", "input.value": question, "output.value": answer}


# ── the healthy corpus ──────────────────────────────────────────────────────


def emit_baseline(tracer: trace.Tracer, index: int) -> None:
    """One clean trace: retrieval, a full-instruction turn, a small report.

    Its span NAMES are what give the latency pair and the instruction floor something
    to measure, so they must match the names the anomalies use.
    """
    question = f"what is the refund window for order A-{index:04d}?"
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        hits = "\n".join(tools.DOCS[:2])
        with span(tracer, "search_docs", tool_attrs("search_docs", question, hits)):
            pass
        with span(tracer, "build_daily_report", tool_attrs("build_daily_report", 5, {"rows": 5})):
            tools.build_daily_report("2026-09-03", rows=5)
        with span(
            tracer,
            "generate_reply",
            llm_attrs(
                system=tools.build_system_prompt(10_000),
                user=question,
                output=GROUNDED_ANSWER,
            ),
        ):
            pass


# ── one scenario per signal ─────────────────────────────────────────────────


@scenario("span_error")
def span_error(tracer: trace.Tracer) -> None:
    """A leaf TOOL span that raised. The parent stays OK — a leaf-only rule means an
    errored parent would hide the child that actually broke."""
    listing = "Price: free shipping included"
    with span(tracer, "run_agent_loop", _agent("what does SKU-00042 cost?", "")):
        with span(tracer, "parse_price", tool_attrs("parse_price", listing, "")) as sp:
            try:
                tools.parse_price(listing)
            except (IndexError, ValueError) as exc:
                fail(sp, f"{type(exc).__name__}: {exc}")


@scenario("tool_loop")
def tool_loop(tracer: trace.Tracer) -> None:
    """An errored AGENT span — the only thing that produces tool_loop."""
    question = "reserve the unavailable part for me"
    with span(tracer, "run_agent_loop", _agent(question, "")) as sp:
        try:
            tools.run_agent_loop(question)
        except RuntimeError as exc:
            fail(sp, f"RuntimeError: {exc}")


@scenario("hallucinated_tool")
def hallucinated_tool(tracer: trace.Tracer) -> None:
    """A tool call whose name is not among the schemas declared in the same trace."""
    question = "search the web for the current shipping surcharge"
    picked = tools.select_tool(question)
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        with span(
            tracer,
            "generate_reply",
            llm_attrs(
                user=question,
                output="",
                declared=SCHEMAS,
                tool_calls=[(picked, json.dumps({"query": question}))],
            ),
        ):
            pass


@scenario("tool_arguments")
def tool_arguments(tracer: trace.Tracer) -> None:
    """A declared tool called without its required field — a shape violation."""
    question = "where is order A-8891?"
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        with span(
            tracer,
            "generate_reply",
            llm_attrs(
                user=question,
                output="",
                declared=SCHEMAS,
                tool_calls=[("lookup_order", tools.build_lookup_args("A-8891"))],
            ),
        ):
            pass


@scenario("tool_arg_violation")
def tool_arg_violation(tracer: trace.Tracer) -> None:
    """The same tool called with a well-formed argument whose VALUE breaks the enum."""
    question = "list my orders newest first"
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        with span(
            tracer,
            "generate_reply",
            llm_attrs(
                user=question,
                output="",
                declared=SCHEMAS,
                tool_calls=[("lookup_order", tools.build_sort_args("A-8891"))],
            ),
        ):
            pass


@scenario("empty_evidence")
def empty_evidence(tracer: trace.Tracer) -> None:
    """Every evidence span came back empty, and the answer is confident anyway."""
    question = "how long is the hardware warranty?"
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        with span(
            tracer,
            "search_docs",
            tool_attrs("search_docs", question, tools.search_docs(question)),
        ):
            pass


@scenario("empty_terminal_answer")
def empty_terminal_answer(tracer: trace.Tracer) -> None:
    """The last generative turn returned no text, and did not error or call a tool."""
    question = "summarise the shipping policy"
    hits = "\n".join(tools.DOCS[:2])
    with span(tracer, "run_agent_loop", _agent(question, "")):
        with span(tracer, "search_docs", tool_attrs("search_docs", question, hits)):
            pass
        with span(
            tracer,
            "generate_reply",
            llm_attrs(user=question, output=tools.compose_answer({"context": hits})),
        ):
            pass


@scenario("empty_result_answer")
def empty_result_answer(tracer: trace.Tracer) -> None:
    """Evidence parsed as an empty result set, and the answer reported that as fact."""
    question = "how many SKU-00042 are on hand?"
    answer = "There are 0 results for that item in the inventory, so none are on hand."
    with span(tracer, "run_agent_loop", _agent(question, answer)):
        with span(
            tracer,
            "query_inventory",
            tool_attrs("query_inventory", "SKU-00042", tools.query_inventory("SKU-00042")),
        ):
            pass


@scenario("oversized_tool_output")
def oversized_tool_output(tracer: trace.Tracer) -> None:
    """A tool payload past the 200 kB line — the whole table, unpaged."""
    question = "which widgets are in the catalog?"
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        with span(
            tracer, "dump_catalog", tool_attrs("dump_catalog", "*", tools.dump_catalog())
        ):
            pass


@scenario("context_growth")
def context_growth(tracer: trace.Tracer) -> None:
    """Prompt history that only ever grows: three turns, the last past 50 messages."""
    question = "and what about international orders?"
    history: list[dict] = []
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        for turn, size in enumerate((12, 34, 58)):
            while len(history) < size:
                history = tools.append_history(
                    history,
                    {"role": "user" if len(history) % 2 == 0 else "assistant",
                     "content": f"turn {len(history)} of the same session"},
                )
            with span(
                tracer,
                "generate_reply",
                llm_attrs(history=history, output=f"acknowledged ({turn})"),
            ):
                pass


@scenario("error_in_content")
def error_in_content(tracer: trace.Tracer) -> None:
    """A span that succeeded while its output leads with an error idiom."""
    question = "what plan is user u-999 on?"
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        with span(
            tracer,
            "fetch_profile",
            tool_attrs("fetch_profile", "u-999", tools.fetch_profile("u-999")),
        ):
            pass


@scenario("internal_repr_in_output")
def internal_repr_in_output(tracer: trace.Tracer) -> None:
    """A sentinel rendered into a value a user reads."""
    question = "what is this month's completion rate?"
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        with span(
            tracer,
            "format_completion_rate",
            tool_attrs(
                "format_completion_rate", {"done": 0, "total": 0},
                tools.format_completion_rate(0, 0),
            ),
        ):
            pass


@scenario("pii_in_payload")
def pii_in_payload(tracer: trace.Tracer) -> None:
    """A national identifier retained in a span payload.

    The value is a synthetic, shape-valid identifier — it belongs to nobody, and the
    detector reports the ATTRIBUTE name, never the value.
    """
    record = {"id": "c-4410", "rrn": "900101-1234567", "tier": "gold"}
    question = "what tier is customer c-4410?"
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        with span(
            tracer,
            "enrich_customer",
            tool_attrs("enrich_customer", record["id"], tools.enrich_customer(record)),
        ):
            pass


@scenario("high_latency")
def high_latency(tracer: trace.Tracer) -> None:
    """One leaf past its measured line, dragging the trace past its own.

    Genuinely slow, not a fabricated timestamp: the N+1 in ``build_daily_report`` is
    what a fix has to remove, and a fabricated duration would leave nothing to fix.
    """
    question = "give me the full daily report"
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        rows = len(tools.CATALOG)
        with span(
            tracer, "build_daily_report", tool_attrs("build_daily_report", rows, {"rows": rows})
        ):
            tools.build_daily_report("2026-09-03", rows=rows)


@scenario("system_instruction_dropped")
def system_instruction_dropped(tracer: trace.Tracer) -> None:
    """The same step, this time under a budget that silently dropped instructions."""
    question = "can I get a refund on order A-2210?"
    with span(tracer, "run_agent_loop", _agent(question, GROUNDED_ANSWER)):
        with span(
            tracer,
            "generate_reply",
            llm_attrs(
                system=tools.build_system_prompt(120),
                user=question,
                output=GROUNDED_ANSWER,
            ),
        ):
            pass


@scenario("no_tool_evidence")
def no_tool_evidence(tracer: trace.Tracer) -> None:
    """A confident answer with no evidence span anywhere in the trace."""
    question = "remind me of the refund window"
    with span(tracer, "run_agent_loop", _agent(question, tools.answer_from_memory(question))):
        with span(
            tracer,
            "generate_reply",
            llm_attrs(user=question, output=tools.answer_from_memory(question)),
        ):
            pass
