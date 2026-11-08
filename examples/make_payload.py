"""Generate examples/big_order.json: a large, realistic-looking failing payload."""

import json
import random
from pathlib import Path

rng = random.Random(7)
items = []
for i in range(400):
    item = {
        "sku": f"SKU-{rng.randint(10000, 99999)}",
        "title": rng.choice(["Blue mug", "Notebook A5", "USB-C cable 2m", "Desk lamp", "Gift card"]),
        "price": round(rng.uniform(1, 250), 2),
        "qty": rng.randint(1, 5),
        "type": "gift_card" if i % 37 == 0 else "physical",
        "discount": rng.choice([0, 5, 10, 20]),
        "meta": {"warehouse": rng.choice(["DEL-1", "BOM-2", "BLR-3"]), "tags": ["sale", "q4"][: rng.randint(0, 2)]},
    }
    items.append(item)
items[263]["discount"] = "15%"  # the needle in the haystack
order = {
    "id": "ORD-2026-118842",
    "customer": {"name": "Test Customer", "email": "test@example.com", "address": {"city": "Delhi", "pin": "110078"}},
    "coupon": "DIWALI15",
    "items": items,
    "fees": [{"kind": "shipping", "amount": 49}, {"kind": "cod", "amount": 25}],
    "notes": "Leave at the gate. " * 20,
}
Path(__file__).with_name("big_order.json").write_text(json.dumps(order, indent=1))
print("wrote big_order.json with", len(items), "items")
