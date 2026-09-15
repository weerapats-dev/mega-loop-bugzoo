"""The planted defects. Every function here is a real tool with a real bug.

Span names in ``scenarios.py`` are these function names verbatim. mega-loop's analyst
builds a node_map (span name -> {file, symbol}) and verifies it by finding the
definition in this repo; a span named anything else is never code-located, so its bug
files as ``deferred`` and auto-fix has nothing to scope to.

One bug per function, and each is reachable from the recorded span input alone — the
counterfactual re-runs the owning tool with that input and checks the error cleared.
"""

from __future__ import annotations

import json
from datetime import date
import time

DOCS = [
    "refund policy: refunds are issued to the original payment method within 5 business days",
    "shipping policy: orders over 50 USD ship free within the contiguous states",
    "warranty policy: hardware carries a 12 month limited warranty from the delivery date",
]

INVENTORY = [
    {"sku": "SKU-00042", "name": "Widget", "on_hand": 7},
    {"sku": "SKU-00043", "name": "Gadget", "on_hand": 0},
]

PROFILES = {"u-100": "Ada Lovelace, plan=pro, since=2021"}

CATALOG = [
    {
        "sku": f"SKU-{i:05d}",
        "name": f"Widget {i}",
        "blurb": "A dependable widget for everyday use, sold in packs of twelve.",
        "tags": ["widget", "everyday", "pack-of-twelve"],
    }
    for i in range(1200)
]

DECLARED_TOOLS = ("search_docs", "query_inventory", "lookup_order", "fetch_profile")

FULL_SYSTEM_PROMPT = "\n".join(
    (
        "You are a support agent for an online hardware store.",
        "Always ground every claim in a retrieved document; never answer from memory.",
        "Quote the policy id you relied on at the end of the answer.",
        "Refuse to state a refund amount you did not read from the order record.",
        "Never reveal a customer's national identifier, even when it appears in a record.",
        "If retrieval returns nothing, say so plainly instead of guessing.",
    )
)


def parse_price(text: str) -> float:
    """``"Price: 12.50 USD"`` -> ``12.5``.

    Assumes the price is always the second whitespace-separated token, so any listing
    that words its price ("free", "call for pricing") raises instead of returning None.
    """
    return float(text.split()[1])


def plan_next_step(query: str) -> str:
    """The next step the agent should take, or ``"done"``."""
    return "search" if "unavailable" in query else "done"


def run_agent_loop(query: str, max_steps: int = 10) -> str:
    """Plan/act loop.

    No progress check: a step that returns the same plan as the last one still counts
    as work, so an unsatisfiable query burns the whole budget and raises.
    """
    for _ in range(max_steps):
        if plan_next_step(query) == "done":
            return "done"
    raise RuntimeError(f"max iterations ({max_steps}) exceeded without an answer")


def select_tool(query: str) -> str:
    """The tool to call for QUERY.

    Returns a name that was never declared to the model, so the call reaches no
    implementation and the turn is wasted.
    """
    if "web" in query:
        return "search_web_v2"
    return "search_docs"


def build_lookup_args(order_id: str) -> str:
    """JSON arguments for ``lookup_order``.

    Emits the key in camelCase; the declared schema requires ``order_id``, so the
    required argument is missing on every call.
    """
    return json.dumps({"orderId": order_id})


def build_sort_args(order_id: str, direction: str = "descending") -> str:
    """JSON arguments for ``lookup_order`` with a sort direction.

    Passes the human word through unmapped; the schema's enum is ``asc``/``desc``.
    """
    return json.dumps({"order_id": order_id, "sort": direction})


def search_docs(query: str) -> str:
    """Policy-document search.

    Filters with ``startswith`` against the raw question, so a natural-language query
    matches nothing and the caller gets an empty string rather than no-hits.
    """
    return "\n".join(doc for doc in DOCS if doc.startswith(query))


def compose_answer(context: dict) -> str:
    """Render the final answer from CONTEXT.

    Reads only ``summary``; a context assembled under any other key renders empty and
    the turn ends with no text at all.
    """
    return context.get("summary", "")


def query_inventory(sku: str) -> list[dict]:
    """Inventory rows for SKU.

    Lowercases the needle but not the haystack, so the comparison never matches and
    every lookup reports an empty result set.
    """
    return [row for row in INVENTORY if row["sku"] == sku.lower()]


def dump_catalog() -> str:
    """The catalog as JSON — no page size and no projection, so the whole table
    travels on every call."""
    return json.dumps(CATALOG)


def append_history(history: list[dict], message: dict) -> list[dict]:
    """Append a turn to the prompt history.

    Never trims and never summarizes, so the prompt grows for the life of the session.
    """
    return [*history, message]


def fetch_profile(user_id: str) -> str:
    """Profile lookup.

    Returns the failure as a STRING, so the span stays OK and the model reads the
    error message as if it were profile data.
    """
    if user_id not in PROFILES:
        return f"Error: user {user_id} not found"
    return PROFILES[user_id]


def format_completion_rate(done: int, total: int) -> str:
    """Render a completion percentage.

    Guards the division but not the rendering, so an empty period interpolates the
    sentinel straight into text a user reads.
    """
    rate = round(done / total * 100, 1) if total else None
    return f"{rate}%"


def enrich_customer(record: dict) -> dict:
    """Attach the customer's identity to the trace payload.

    Copies the whole record including the national identifier, which the caller never
    needed and which is now retained by every downstream observer.
    """
    return {"customer_id": record["id"], "rrn": record["rrn"], "tier": record["tier"]}


def _fetch_row_detail(sku: str) -> dict:
    """One round trip per row — the per-call cost the report multiplies."""
    time.sleep(0.01)
    return {"sku": sku, "sold": len(sku) % 7}


def build_daily_report(day: str, rows: int) -> dict:
    """The day's sales report.

    Fetches each row's detail individually (N+1); the cost is linear in the catalog,
    and the catalog is not small.
    """
    details = [_fetch_row_detail(item["sku"]) for item in CATALOG[:rows]]
    return {"day": day, "rows": len(details), "sold": sum(d["sold"] for d in details)}


def build_system_prompt(budget_chars: int) -> str:
    """Assemble the system prompt under a character budget.

    Drops whole instructions from the end to fit, and says nothing about it — the model
    simply stops being told the rules that did not fit.
    """
    kept: list[str] = []
    for line in FULL_SYSTEM_PROMPT.split("\n"):
        if sum(len(x) for x in kept) + len(line) > budget_chars:
            break
        kept.append(line)
    return "\n".join(kept)


def answer_from_memory(question: str) -> str:
    """Answer without retrieval when the question 'looks familiar'.

    Familiarity is a substring test against a keyword list, so ordinary questions match
    and the agent answers confidently having gathered no evidence at all.
    """
    if any(word in question for word in ("refund", "shipping", "warranty")):
        return (
            "Refunds are returned to the original payment method within five business "
            "days, and orders above fifty dollars ship free within the contiguous states."
        )
    return ""


# ── chains: defects that fail together ──────────────────────────────────────
#
# Everything above fails alone, on purpose. These fail in a known relationship, so the
# analyst has a dependency to find and the bug graph has a multi-bug group to draw. Each
# defect is still its own function with its own fix; what links them is a call.


def parse_order_date(text: str) -> date:
    """Order placement date.

    Assumes ISO ``YYYY-MM-DD``; the storefront writes ``MM/DD/YYYY``, so every order
    placed through it raises instead of parsing.
    """
    year, month, day = text.split("-")
    return date(int(year), int(month), int(day))


def refund_window_usage(order: dict, today: date) -> str:
    """How much of the refund window has elapsed, as a percentage.

    Swallows an unreadable date and a missing window alike, then formats the number it
    never computed — the caller receives ``"None%"`` rather than an error.
    """
    try:
        elapsed = (today - parse_order_date(order["placed"])).days
        used = round(elapsed / order["window_days"] * 100)
    except (ValueError, KeyError):
        used = None
    return f"{used}%"


def answer_refund_question(order: dict, today: date) -> str:
    """Tell the customer whether a refund is still possible.

    Answers only when the usage reads as a plain number, so anything else — including a
    negative usage from an order dated in the future — ends the turn with no reply at all.
    """
    usage = refund_window_usage(order, today)
    if not usage.rstrip("%").isdigit():
        return ""
    return f"You have used {usage} of your refund window."


TAX_PROFILES = {"US": {"vat": 0}, "UK": {"vat": 20}, "HK": {"vat": None}}
FX_RATES = {"USD": 1.0, "GBP": 0.79}


def load_tax_profile(region: str) -> dict:
    """Tax profile for REGION.

    Indexes the table directly, so a region the storefront sells to before the tax table
    lists it raises instead of falling back to a default profile.
    """
    return TAX_PROFILES[region]


def format_vat_line(profile: dict | None) -> str:
    """The VAT line on a quote.

    Formats the rate whether or not there is one, so a missing profile or rate reaches the
    customer as ``"VAT None%"``.
    """
    rate = profile.get("vat") if profile else None
    return f"VAT {rate}%"


def convert_to_local(amount_usd: float, profile: dict | None, currency: str) -> float:
    """The quote in the customer's currency, VAT included.

    Reads the profile without checking it exists and the rate table without a fallback,
    so either gap raises.
    """
    vat = profile["vat"] or 0
    return round(amount_usd * FX_RATES[currency] * (1 + vat / 100), 2)


def quote_international_order(region: str, currency: str, amount_usd: float) -> str:
    """Quote an order for a region and currency."""
    try:
        profile = load_tax_profile(region)
    except KeyError:
        profile = None
    vat_line = format_vat_line(profile)
    try:
        total = convert_to_local(amount_usd, profile, currency)
    except (KeyError, TypeError):
        return f"We could not price this order yet. {vat_line}"
    return f"Total {total} {currency}. {vat_line}"


CARRIER_PREFIXES = {"1Z": "UPS", "94": "USPS"}
DELIVERY_PROGRESS = {"UPS": 60, "USPS": 35}


def track_shipment(tracking_id: str) -> str:
    """Delivery progress for TRACKING_ID.

    Two unrelated defects in one function: the check digit is read at a fixed index, so a
    short legacy id raises; and a carrier with no progress feed is formatted anyway, so the
    customer reads ``"None% delivered"``.
    """
    check_digit = tracking_id[11]
    carrier = CARRIER_PREFIXES.get(tracking_id[:2], "unknown carrier")
    progress = DELIVERY_PROGRESS.get(carrier)
    return f"{carrier} shipment {progress}% delivered (check {check_digit})"


def answer_tracking_question(tracking_id: str) -> str:
    """Tell the customer where their parcel is."""
    try:
        return track_shipment(tracking_id)
    except IndexError:
        return "We could not find that tracking number."
