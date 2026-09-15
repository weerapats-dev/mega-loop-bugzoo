# mega-loop-bugzoo

Synthetic traces that trip **every** MEGA Loop detector, for a dashboard that has
something in it — bug lists, bug graphs, docs screenshots — and for auto-fix, because
the defects are real functions in this repository rather than invented span payloads.

No app to deploy, no LLM key, no provider spend. One command, a Langfuse project of its
own, and seconds rather than a pipeline run.

## Run it

```bash
cp .env.example .env      # fill in a Langfuse host + keypair
uv sync
uv run python -m bugzoo               # 40 healthy traces + all 16 scenarios
uv run python -m bugzoo --list        # scenario names
uv run python -m bugzoo --only span_error --baseline 0
uv run python -m bugzoo --repeat 3 --chains   # enough traces for the analyst to file bugs, plus the chains
```

The analyst never sees an issue with fewer than two failing traces (MEGA Loop design
95 §3), so a single run of each scenario fills the failures inbox but files no bugs from
it. `--repeat 2` or more is what turns them into bugs.

Use a Langfuse project of its **own**. These are not real runs: mixed in with a real
app's traces, neither set can be trusted afterwards.

## Why the healthy baseline is not optional

Three detectors read a line measured from the project's own history rather than a
constant, and they stay silent below it — the latency pair needs 30 samples of a span
*name*, `no_tool_evidence` needs 30 traces before it trusts the share of runs that
gather evidence, and `system_instruction_dropped` needs a longer instruction on the same
span name to be a subset of. `--baseline 0` is how you make four signals disappear.

## The signals it covers

`span_error` · `tool_loop` · `hallucinated_tool` · `tool_arguments` ·
`tool_arg_violation` · `empty_evidence` · `empty_terminal_answer` ·
`empty_result_answer` · `oversized_tool_output` · `context_growth` ·
`error_in_content` · `internal_repr_in_output` · `pii_in_payload` ·
`span_high_latency` · `trace_high_latency` · `system_instruction_dropped` ·
`no_tool_evidence`

Two more exist in MEGA Loop and are deliberately absent: `low_score` comes from its own
judge rather than from trace content, and `output_schema_violation` is promoted but
never emitted by any detector.

## Chains: groups with more than one bug

Every scenario above is one defect in one trace, so every bug it files is a group of
one. `--chains` adds three trace families whose defects fail together, so the bug
graph has multi-bug groups to draw and auto-fix has a group to open one PR for:

| chain | defects | expected group |
|---|---|---|
| `sequential_refund` | `parse_order_date` → `refund_window_usage` → `answer_refund_question`, nested calls | Sequential, 3 bugs |
| `parallel_quote` | `load_tax_profile` feeds `format_vat_line` and `convert_to_local`, which break independently | Parallel, 3 bugs |
| `conflict_tracking` | two unrelated defects inside `track_shipment` | Conflict, 2 bugs |

MEGA Loop draws a dependency only when three kinds of evidence agree — span nesting or
order, co-failure counts, and a call in this repository — and discounts a pair that
co-failed fewer than five times. So each chain emits its co-failing case six times, plus
cases where the downstream defect fails alone, which is what tells a bug of its own from
a symptom that should merge into its cause. Whether the analyst draws exactly these edges
is its judgement, not a guarantee: expect the shapes above, check the graph.

The analyst reads this repository's default branch, so push changes here before the
analysis run that should see them.

## For auto-fix

Bind the MEGA Loop project to this repository and install the GitHub App on it. Span
names are the function names in `bugzoo/tools.py`, each defect is a real bug in a real
function, and the counterfactual can re-run the failing tool with the input the span
recorded — so a fix has somewhere to land and something to prove.

The identifier in `pii_in_payload` is synthetic and shape-valid. It belongs to nobody.
