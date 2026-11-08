"""Python-API demo: shrink a failing call and print the generated test.

Run from the repository root:  python examples/demo.py
"""

import json
from pathlib import Path

from orders import import_order

from squeezebug import Shrinker, CallInput, render_test

payload = json.loads(Path(__file__).with_name("big_order.json").read_text())
result = Shrinker(import_order).shrink(CallInput([payload]))
print(f"{result.signature.short()}")
print(f"size {result.original_size} -> {result.minimized_size} in {result.calls} calls\n")
print(render_test(result, module="orders", qualname="import_order"))
