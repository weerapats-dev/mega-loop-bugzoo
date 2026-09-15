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
from datetime import date

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


# ── chains: related defects, for multi-bug groups ───────────────────────────
#
# The scenarios above are one defect per trace, which is what keeps coverage checkable
# and what makes every bug a group of one. A chain is the opposite: several defects in
# one trace, in a relationship the analyst can confirm three ways — span nesting or order,
# co-failure counts, and a call in ``tools.py``. The relation judge discounts a pair that
# co-failed fewer than five times, so each chain emits its co-failing case six times, plus
# cases where a downstream defect fires ALONE, which is what separates a bug of its own
# from a symptom that should merge into its cause.

CHAINS: dict[str, Callable[[trace.Tracer], int]] = {}

TODAY = date(2026, 9, 15)
CO_FAIL_TRACES = 6
SOLO_TRACES = 2


def chain(name: str) -> Callable[[Callable], Callable]:
    def register(fn: Callable) -> Callable:
        CHAINS[name] = fn
        return fn

    return register


def _refund_trace(tracer: trace.Tracer, order: dict) -> None:
    question = f"can I still get a refund on an order placed {order['placed']}?"
    answer = tools.answer_refund_question(order, TODAY)
    with span(tracer, "answer_refund_question", _agent(question, answer)):
        usage_input = {"order": order, "today": TODAY.isoformat()}
        usage = tools.refund_window_usage(order, TODAY)
        with span(
            tracer, "refund_window_usage", tool_attrs("refund_window_usage", usage_input, usage)
        ):
            with span(
                tracer,
                "parse_order_date",
                tool_attrs("parse_order_date", order["placed"], ""),
            ) as sp:
                try:
                    parsed = tools.parse_order_date(order["placed"])
                    sp.set_attribute("output.value", parsed.isoformat())
                except ValueError as exc:
                    fail(sp, f"ValueError: {exc}")


@chain("sequential_refund")
def sequential_refund(tracer: trace.Tracer) -> int:
    """parse_order_date → refund_window_usage → answer_refund_question, nested.

    Expected group: Sequential, three bugs. Solo cases: a missing window (usage fails
    with the date intact) and a future-dated order (only the answer fails).
    """
    cases = [
        ({"placed": "03/09/2026", "window_days": 30}, CO_FAIL_TRACES),
        ({"placed": "2026-09-01"}, SOLO_TRACES),
        ({"placed": "2026-09-20", "window_days": 30}, SOLO_TRACES),
    ]
    for order, times in cases:
        for _ in range(times):
            _refund_trace(tracer, order)
    return sum(times for _, times in cases)


def _quote_trace(tracer: trace.Tracer, region: str, currency: str) -> None:
    amount = 100.0
    question = f"how much is a 100 USD order shipped to {region}, in {currency}?"
    answer = tools.quote_international_order(region, currency, amount)
    with span(tracer, "quote_international_order", _agent(question, answer)):
        profile = None
        with span(tracer, "load_tax_profile", tool_attrs("load_tax_profile", region, "")) as sp:
            try:
                profile = tools.load_tax_profile(region)
                sp.set_attribute("output.value", json.dumps(profile))
            except KeyError as exc:
                fail(sp, f"KeyError: {exc}")
        vat_line = tools.format_vat_line(profile)
        with span(
            tracer, "format_vat_line", tool_attrs("format_vat_line", profile, vat_line)
        ):
            pass
        convert_input = {"amount_usd": amount, "profile": profile, "currency": currency}
        with span(
            tracer, "convert_to_local", tool_attrs("convert_to_local", convert_input, "")
        ) as sp:
            try:
                sp.set_attribute(
                    "output.value", str(tools.convert_to_local(amount, profile, currency))
                )
            except (KeyError, TypeError) as exc:
                fail(sp, f"{type(exc).__name__}: {exc}")


@chain("parallel_quote")
def parallel_quote(tracer: trace.Tracer) -> int:
    """load_tax_profile feeds two siblings that break independently of each other.

    Expected group: Parallel, three bugs — format_vat_line and convert_to_local both
    depend on load_tax_profile, neither on the other. Solo cases: a region whose rate is
    unset (only the VAT line fails) and an unsupported currency (only conversion fails).
    """
    cases = [
        (("BR", "GBP"), CO_FAIL_TRACES),
        (("HK", "GBP"), SOLO_TRACES),
        (("US", "EUR"), SOLO_TRACES),
    ]
    for (region, currency), times in cases:
        for _ in range(times):
            _quote_trace(tracer, region, currency)
    return sum(times for _, times in cases)


def _tracking_trace(tracer: trace.Tracer, tracking_id: str) -> None:
    question = f"where is my parcel {tracking_id}?"
    answer = tools.answer_tracking_question(tracking_id)
    with span(tracer, "answer_tracking_question", _agent(question, answer)):
        with span(tracer, "track_shipment", tool_attrs("track_shipment", tracking_id, "")) as sp:
            try:
                sp.set_attribute("output.value", tools.track_shipment(tracking_id))
            except IndexError as exc:
                fail(sp, f"IndexError: {exc}")


@chain("conflict_tracking")
def conflict_tracking(tracer: trace.Tracer) -> int:
    """Two unrelated defects in track_shipment, never in the same trace.

    Expected group: Conflict, two bugs — the fixes edit one function, which groups them
    by fingerprint without any dependency between them.
    """
    cases = [("1Z999", 3), ("XX1234567890", 3)]
    for tracking_id, times in cases:
        for _ in range(times):
            _tracking_trace(tracer, tracking_id)
    return sum(times for _, times in cases)
