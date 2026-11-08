"""Command-line interface: ``squeezebug shrink | replay | list``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional, Sequence

from . import __version__
from .capture import DEFAULT_STORE, decode_call, read_record
from .codegen import TestStyle, render_test
from .llm import LLMError, explain, get_provider
from .loader import TargetError, load_target
from .runner import CallInput
from .shrink import NotFailingError, Shrinker, ShrinkResult
from .signature import FailureSignature, MatchMode


def _input_from_json(path: str, as_arg: bool) -> CallInput:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not as_arg and isinstance(data, dict) and set(data) <= {"args", "kwargs"} and data:
        return CallInput(list(data.get("args", [])), dict(data.get("kwargs", {})))
    return CallInput([data], {})


def _print_summary(result: ShrinkResult, out: Any) -> None:
    sig = result.final_signature
    print(f"failure   : {result.signature.short()}", file=out)
    print(
        f"shrunk    : size {result.original_size} -> {result.minimized_size} "
        f"({result.reduction:.1%} smaller), {result.accepted_steps} steps, "
        f"{result.calls} calls, {result.seconds:.2f}s",
        file=out,
    )
    if result.budget_exhausted:
        print("note      : call budget exhausted; result may not be fully minimal (raise --max-calls)", file=out)
    if sig.message != result.signature.message:
        print(f"message   : now {sig.message!r} (was {result.signature.message!r})", file=out)
    print("minimal   :", file=out)
    print(f"  args   = {result.minimized.args!r}", file=out)
    print(f"  kwargs = {result.minimized.kwargs!r}", file=out)


def _finish(
    args: argparse.Namespace,
    result: ShrinkResult,
    func: Any,
    module: str,
    qualname: str,
    traceback_text: str = "",
) -> int:
    explanation = ""
    if args.explain:
        provider = get_provider(args.provider)
        if provider is None:
            print("explain   : no LLM provider available (set GEMINI_API_KEY or run Ollama); skipped", file=sys.stderr)
        else:
            try:
                explanation = explain(result, func, provider, traceback_text)
            except LLMError as exc:
                print(f"explain   : {provider.name} failed: {exc}; skipped", file=sys.stderr)
    if args.json:
        payload = {
            "signature": result.signature.to_dict(),
            "original_size": result.original_size,
            "minimized_size": result.minimized_size,
            "calls": result.calls,
            "budget_exhausted": result.budget_exhausted,
            "minimized": result.minimized.as_tree(),
            "explanation": explanation,
        }
        print(json.dumps(payload, indent=2, default=repr))
    else:
        _print_summary(result, sys.stdout)
        if explanation:
            print("\nexplanation (LLM, verify before trusting):\n" + explanation)
    if args.out:
        source = render_test(
            result, module=module, qualname=qualname, style=TestStyle(args.style), explanation=explanation
        )
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(source)
        if not args.json:
            print(f"\nwrote regression test -> {args.out}")
    return 0


def _make_shrinker(args: argparse.Namespace, func: Any) -> Shrinker:
    return Shrinker(func, mode=MatchMode(args.match), max_calls=args.max_calls, timeout=args.timeout or None)


def cmd_shrink(args: argparse.Namespace) -> int:
    """Shrink ``TARGET`` called on the JSON input file."""
    target = load_target(args.target)
    call = _input_from_json(args.input, args.as_arg)
    result = _make_shrinker(args, target.func).shrink(call)
    return _finish(args, result, target.func, target.module, target.qualname)


def cmd_replay(args: argparse.Namespace) -> int:
    """Shrink a call recorded by ``@squeezebug.capture``."""
    record = read_record(args.record)
    target = load_target(args.target or record["target"])
    call = decode_call(record["call"], allow_pickle=args.trust_pickle)
    expected = FailureSignature.from_dict(record["signature"])
    result = _make_shrinker(args, target.func).shrink(call, expected)
    return _finish(args, result, target.func, target.module, target.qualname, record.get("traceback", ""))


def cmd_list(args: argparse.Namespace) -> int:
    """List captured failure records in the store."""
    if not os.path.isdir(args.store):
        print(f"no records ({args.store} does not exist)")
        return 0
    rows = 0
    for name in sorted(os.listdir(args.store)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(args.store, name)
        try:
            rec = read_record(path)
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        sig = FailureSignature.from_dict(rec["signature"])
        print(f"{path}  {rec['target']}  {sig.short()}  [{rec.get('created', '?')}]")
        rows += 1
    if not rows:
        print("no records")
    return 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--match", choices=[m.value for m in MatchMode], default="location",
                   help="what counts as the same failure (default: location = same type and line)")
    p.add_argument("--max-calls", type=int, default=2000, help="maximum target invocations (default 2000)")
    p.add_argument("--timeout", type=float, default=2.0, help="per-call timeout in seconds, 0 disables (default 2)")
    p.add_argument("-o", "--out", help="write a pytest regression test to this path")
    p.add_argument("--style", choices=[s.value for s in TestStyle], default="xfail", help="generated test style")
    p.add_argument("--explain", action="store_true", help="ask an LLM to explain the minimal failure")
    p.add_argument("--provider", choices=["auto", "gemini", "ollama", "none"], default="auto")
    p.add_argument("--json", action="store_true", help="machine-readable output")


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="squeezebug",
        description="Shrink a real failing input to the smallest one that fails the same way, "
        "and turn it into a pytest regression test.",
    )
    parser.add_argument("--version", action="version", version=f"squeezebug {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_shrink = sub.add_parser("shrink", help="shrink TARGET called on a JSON input")
    p_shrink.add_argument("target", help="module:function or path/to/file.py:function")
    p_shrink.add_argument("--input", "-i", required=True,
                          help='JSON file: {"args": [...], "kwargs": {...}} or a single value')
    p_shrink.add_argument("--as-arg", action="store_true",
                          help="always pass the whole JSON document as the single positional argument")
    _add_common(p_shrink)
    p_shrink.set_defaults(handler=cmd_shrink)

    p_replay = sub.add_parser("replay", help="shrink a call recorded by @squeezebug.capture")
    p_replay.add_argument("record", help="path to a .squeezebug/<id>.json record")
    p_replay.add_argument("--target", help="override the recorded module:function")
    p_replay.add_argument("--trust-pickle", action="store_true", help="allow loading pickle-encoded records")
    _add_common(p_replay)
    p_replay.set_defaults(handler=cmd_replay)

    p_list = sub.add_parser("list", help="list captured failures")
    p_list.add_argument("--store", default=DEFAULT_STORE)
    p_list.set_defaults(handler=cmd_list)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the ``squeezebug`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (TargetError, NotFailingError, ValueError, OSError) as exc:
        print(f"squeezebug: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
