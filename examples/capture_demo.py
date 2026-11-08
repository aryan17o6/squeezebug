"""Record a crash in "production" with @capture, then shrink it from the CLI.

    python examples/capture_demo.py            # crashes once, writes .squeezebug/<id>.json
    squeezebug list
    squeezebug replay .squeezebug/<id>.json -o tests/test_order_repro.py
"""

import json
from pathlib import Path

from orders import import_order

from squeezebug import capture

handle = capture(import_order)

payload = json.loads(Path(__file__).with_name("big_order.json").read_text())
try:
    handle(payload)
except TypeError as exc:
    print(f"crashed as expected: {exc!r}; record written to .squeezebug/")
