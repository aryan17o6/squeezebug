# squeezebug

**Take the real input that crashed your Python function (say an 80 KB production payload) and shrink it to the smallest call that still fails the same way. You get back a pytest regression test you can commit.**

```text
$ squeezebug shrink examples/orders.py:import_order -i examples/big_order.json -o tests/test_order_repro.py
failure   : TypeError("unsupported operand type(s) for /: 'str' and 'int'") at orders.py:19 in _line_total()
shrunk    : size 43884 -> 55 (99.9% smaller), 25 steps, 132 calls, 0.30s
minimal   :
  args   = [{'id': '2', 'coupon': '5', 'items': [{'price': 0.0, 'discount': ''}]}]
  kwargs = {}

wrote regression test -> tests/test_order_repro.py
```

That is real output from the bundled example, a 400-item order (83 KB of JSON) with one bad field. The timing was measured once in a Linux sandbox, so yours will differ.

---

## Why it exists

A bug report rarely arrives as a tidy test case. It comes as a 2,000-line JSON body, a pickled object from a worker, or a config dict that crashed a job at 3 a.m. Before anyone can fix the bug (you, a teammate, or an AI coding assistant), someone has to cut that input down to its *essence* by hand: delete half the keys, re-run, put some back, repeat.

That manual loop is exactly what **delta debugging** automates. But the tools that implement it either:

- reduce **text files** and need you to write an "interestingness" shell script (C-Reduce, picire, shrinkray), or
- shrink only inputs **they generated themselves** (Hypothesis and other property-based testers, fuzzers), or
- exist mainly as **teaching code** (the `DeltaDebugger` in *The Debugging Book*).

squeezebug shrinks **your own real Python objects** (dicts, lists, strings, numbers) passed to **your own function**. It only accepts a smaller input if it fails in **the same way as the original**, and it writes the result as a **pytest file**.

A minimal repro is also the best thing to hand an LLM. It costs fewer tokens, has less noise, and gives the model less room to guess. So squeezebug can optionally ask Gemini or a local Ollama model to explain the minimal failure. Everything else works offline, with no API key.

## Features

- **Structure-aware shrinking.** Drops dict keys and list items in halving chunks (ddmin order), empties containers, shortens strings, moves numbers toward 0, and flips `True` to `False`, all recursively.
- **Failure signatures, so it never wanders off to another bug.** A candidate counts only if it raises the same exception type **at the same file and line** (`--match location`, the default). You can loosen this to type only (`--match type`) or tighten it to the exact message too (`--match message`).
- **Fixed arity.** The number of positional arguments never changes. Keyword arguments are dropped when the call still fails without them.
- **Safe execution.** Every probe gets a deep copy of the input, so a function that mutates its arguments can't corrupt the search. The target's stdout and stderr are suppressed, and on POSIX each probe has a per-call timeout (`--timeout`). A hard call budget (`--max-calls`) caps the whole run.
- **`@capture` decorator.** Records the arguments and signature of a crashing call into `.squeezebug/<id>.json` at the moment it happens, then re-raises the exception unchanged. Replay it later with `squeezebug replay`.
- **Regression-test generation.** By default the test is marked `xfail(strict=True)`: it passes while the bug exists and **turns red as soon as you fix it**, which reminds you to remove the marker and assert the correct result. Use `--style raises` to pin the exception instead. Inputs that aren't Python literals are embedded as a pickle, flagged with a comment.
- **Optional LLM explanation.** `--explain` with Gemini (`GEMINI_API_KEY`) or local Ollama. Without either, you get a notice and nothing breaks.
- **Zero runtime dependencies.** Standard library only. Python 3.10+.

## Install

From a clone of this repository:

```bash
pip install -e .            # or: pip install -e ".[dev]" to also get pytest
```

It is not published on PyPI yet.

## Quickstart

### 1. You already have the failing input as JSON

```bash
# input.json is either {"args": [...], "kwargs": {...}} or any single JSON value
squeezebug shrink mypkg.importer:import_order -i input.json -o tests/test_import_repro.py
squeezebug shrink path/to/script.py:parse -i payload.json --as-arg   # whole document = one argument
```

### 2. Catch it where it happens

```python
from squeezebug import capture

@capture                      # or @capture(store="var/squeezebug")
def import_order(order: dict) -> float:
    ...
```

```bash
squeezebug list
squeezebug replay .squeezebug/36a59723bf.json -o tests/test_order_repro.py
```

Set `SQUEEZEBUG_DISABLE=1` to turn recording off without removing the decorator. Records hold real input data, so treat them as sensitive. `.squeezebug/` is in `.gitignore`.

### 3. From Python

```python
from squeezebug import Shrinker, CallInput, render_test, shrink_call

result = shrink_call(import_order, payload)           # options are prefixed squeeze_*
print(result.minimized.args, result.reduction)

result = Shrinker(import_order, mode="type", max_calls=500).shrink(CallInput([payload]))
print(render_test(result, module="orders", qualname="import_order"))
```

### Optional: ask a model why it fails

```bash
export GEMINI_API_KEY=...                      # model: SQUEEZEBUG_GEMINI_MODEL (default gemini-2.5-flash)
squeezebug shrink orders.py:import_order -i big_order.json --explain
# or run Ollama locally; model: SQUEEZEBUG_OLLAMA_MODEL (default llama3.2), host: OLLAMA_HOST
squeezebug shrink orders.py:import_order -i big_order.json --explain --provider ollama
```

The prompt contains the function's source, the **minimized** arguments and the failure. The explanation is printed and, with `-o`, saved as a comment in the test, labelled as LLM output to verify.

## Example: the generated test

```python
"""Regression test generated by squeezebug for orders:import_order.

Original failure: TypeError("unsupported operand type(s) for /: 'str' and 'int'") at orders.py:19 in _line_total()
Input shrunk from size 43884 to 55 (99.9% smaller) in 132 calls, match mode 'location'.
"""

import pytest
from orders import import_order


@pytest.mark.xfail(raises=TypeError, strict=True, reason='TypeError(...) at orders.py:19 in _line_total()')  # reason abbreviated here
def test_import_order_squeezebug_7bcc3e() -> None:
    # Once the bug is fixed this xfail turns into a failure: remove the marker
    # and assert the correct result instead.
    import_order({'id': '2', 'coupon': '5', 'items': [{'price': 0.0, 'discount': ''}]})
```

The 400 items, the customer block, the fees and the notes are all gone. What's left is the actual trigger: an order that **has a coupon**, with **one non-gift-card item** whose **`discount` is a string**. Those three conditions together were hard to spot in the original payload.

Try it yourself:

```bash
python examples/make_payload.py     # regenerates examples/big_order.json (seeded)
python examples/demo.py             # Python API version
python examples/capture_demo.py     # @capture version, then `squeezebug list`
```

## How it works

1. **Signature.** squeezebug runs the original call once and fingerprints the exception: its class, plus the file, line and function of the innermost traceback frame (`FailureSignature`).
2. **Tree.** The call becomes a tree, `{"args": [...], "kwargs": {...}}`.
3. **Greedy hierarchical reduction.** It walks the tree from the outermost nodes inward. For each node it proposes simpler replacements, simplest first: an empty container, the container minus a chunk of its items (chunk sizes halving as in Zeller and Hildebrandt's *ddmin*), a shorter or lower-cased string, a number closer to 0, `False`. A replacement is accepted only if it is **strictly smaller** (by `squeezebug.size`) and the call **still matches the signature**. After each accepted step it restarts from the top, because the tree has changed.
4. **Termination.** Size strictly decreases with every accepted step, so the loop terminates. It stops when a full pass changes nothing (the result is then 1-minimal for these transformations) or when the call budget runs out (reported as `budget_exhausted`).
5. **Codegen.** If the minimized tree survives `ast.literal_eval(repr(...))`, it is inlined. Otherwise it is pickled. The pytest module is rendered from that.

Why the signature matters: take `config["db"]["host"]`. Dropping either `db` or `host` raises `KeyError` on the **same line**, so location mode correctly drops both. `--match message` keeps `"db": {}` because the message (`'host'`) has to match. And if removing a key exposes a *different* `raise` further down, that candidate is rejected, so you get a repro of the bug you started with, not some other bug.

## Limitations (honest list)

- **It runs your code, many times.** Only point it at functions that are safe to call repeatedly: no payments, no emails, no writes to production databases. Mock or sandbox those first.
- Timeouts use `SIGALRM`, so they only apply on POSIX and in the main thread. Elsewhere `--timeout` is ignored.
- It only reduces built-in containers and scalars. Custom objects are kept as they are, though their contents in dicts and lists still shrink.
- Greedy reduction finds a *local* minimum, not the globally smallest input.
- Nondeterministic failures (random, time, network) can make shrinking stall or give different results between runs.
- Pickle records are never loaded unless you pass `--trust-pickle` / `allow_pickle=True`.

## Comparison with nearby tools

This survey was done in November 2026 by searching GitHub and PyPI. If we missed a tool that does the same thing, please open an issue.

| Tool | What it shrinks | Starting input | Preserves "same failure" | Emits pytest |
|---|---|---|---|---|
| **squeezebug** | Python objects passed to a function | **your real failing input** | type + location (+ message), built in | **yes** |
| [Hypothesis](https://hypothesis.readthedocs.io/) | values it generated | its own generated data | yes (internally) | no (prints a falsifying example) |
| [shrinkray](https://pypi.org/project/shrinkray/), [picire](https://pypi.org/project/picire/), C-Reduce | files / text | your file | you write an interestingness script | no |
| [ddmin-python](https://github.com/andrewchambers/ddmin-python), [shrinkplz](https://pypi.org/project/shrinkplz/) | flat lists / lines of a file | your data | you write the predicate | no |
| [The Debugging Book `DeltaDebugger`](https://www.debuggingbook.org/html/DeltaDebugger.html) | function arguments | your call | same exception | no; it is educational code inside a large textbook package |
| [nightman](https://github.com/Falcon305/nightman) | fuzz-generated inputs | inputs it invents | yes | yes |
| [crashmin](https://pypi.org/project/crashmin/) | HTTP requests (cURL) | your request | user-defined | no |
| [llmdebug](https://pypi.org/project/llmdebug/), [safedump](https://pypi.org/project/safedump/) | none; they **capture** locals and tracebacks for debugging | your crash | n/a | no |
| [pytest-replay](https://pypi.org/project/pytest-replay/) | none; replays test **order** | a test run | n/a | n/a |

squeezebug is the bridge between capture tools (which record a crash) and reducers (which need a format and a script). It records the call, reduces the Python structure itself, and hands you a committed test.

## Roadmap

- `pytest` plugin: `--squeezebug` shrinks the arguments of failing parametrized tests automatically.
- Smarter reductions: hoist a nested container into its parent's place, normalise strings toward `"a"`, shrink inside dataclasses and pydantic models.
- Parallel probing (process pool) for slow targets, with real per-call isolation.
- Record redaction hooks (`@capture(redact=...)`) for PII.
- Output formats for issue trackers (Markdown repro block) and an MCP tool, so coding agents can request a minimal repro before they attempt a fix.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT © 2026 Aryan Ojha
