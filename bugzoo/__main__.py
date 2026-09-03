"""``python -m bugzoo`` — seed a Langfuse project with traces that trip every detector.

No app, no repo, no LLM call. The defects in ``tools.py`` run for real and their spans
carry the OpenInference attributes ``mega_loop.analyze.detect`` matches on, which is all
detection needs. What it deliberately does NOT give you is a bug auto-fix can repair:
nothing here is code-located in a repository MEGA Loop can clone, so those bugs file as
``deferred``. Use this for detection, dashboards and screenshots; use a real
instrumented app for anything that has to end in a pull request.
"""

from __future__ import annotations

import argparse
import sys
import time

from bugzoo.scenarios import SCENARIOS, emit_baseline
from bugzoo.telemetry import Config, build_tracer

# Three detectors read a line measured from the project's own history rather than a
# constant, and none of them will speak below it: the latency pair needs 30 samples of a
# span NAME, `no_tool_evidence` needs 30 traces before it trusts the evidence-gathering
# share. 40 leaves headroom for the anomalies pulling the same averages down.
DEFAULT_BASELINE = 40


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bugzoo", description=__doc__)
    parser.add_argument(
        "--baseline",
        type=int,
        default=DEFAULT_BASELINE,
        help=f"healthy traces to emit first (default {DEFAULT_BASELINE}; 0 to skip — the "
        "latency pair, no_tool_evidence and system_instruction_dropped then stay silent)",
    )
    parser.add_argument(
        "--only",
        action="append",
        metavar="SCENARIO",
        help="emit just this scenario (repeatable). Default: all of them.",
    )
    parser.add_argument("--list", action="store_true", help="list scenario names and exit")
    args = parser.parse_args(argv)

    if args.list:
        for name in sorted(SCENARIOS):
            print(name)
        return 0

    chosen = args.only or sorted(SCENARIOS)
    if unknown := [name for name in chosen if name not in SCENARIOS]:
        print(f"unknown scenario(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"known: {', '.join(sorted(SCENARIOS))}", file=sys.stderr)
        return 2

    config = Config()
    tracer, provider = build_tracer(config)
    print(f"→ {config.endpoint}")

    started = time.monotonic()
    for i in range(args.baseline):
        emit_baseline(tracer, i)
        if (i + 1) % 10 == 0:
            print(f"  baseline {i + 1}/{args.baseline}")
    for name in chosen:
        SCENARIOS[name](tracer)
        print(f"  {name}")

    # Flush before the process exits or the batch processor drops what it is holding —
    # a silent partial seed looks exactly like a detector that failed to fire.
    provider.force_flush()
    provider.shutdown()
    print(
        f"\n✓ {args.baseline} baseline + {len(chosen)} anomaly traces "
        f"in {time.monotonic() - started:.1f}s"
    )
    print("  Langfuse ingests asynchronously — give it a few seconds before querying.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
