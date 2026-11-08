# squeezebug: brief

**What it is:** a Python CLI and library. It takes the real input that crashed a function, such as an 80 KB production JSON payload, and automatically shrinks it to the smallest call that still fails the same way. Then it writes a pytest regression test.

**Problem:** bug reports come with huge, noisy inputs. Cutting them down to a minimal repro by hand is slow, and a minimal repro is exactly what an AI assistant needs.

**Who it helps:** backend and data engineers, maintainers triaging issues, and teams handing bugs to coding agents.

**Novelty:** existing reducers work on text files and need a hand-written script (shrinkray, picire), or they only shrink self-generated inputs (Hypothesis, fuzzers). squeezebug reduces your own Python objects structurally and keeps the same failure signature (exception type plus raise line), so it never drifts to a different bug. It also captures crashes in place with `@capture` and emits a committed test.

**Stack:** pure Python 3.10+, standard library only. Gemini or Ollama explanations are optional.

**Run:** `pip install -e .`, then `squeezebug shrink examples/orders.py:import_order -i examples/big_order.json -o tests/test_repro.py`

**Next:** a pytest plugin, an MCP tool for coding agents, PII redaction, parallel probing, and a PyPI release.
