"""A deliberately buggy order importer used by the squeezebug demo.

The bug: a line item whose ``discount`` is given as a percentage *string*
("15%") instead of a number crashes ``_line_total`` -- but only for items that
are not gift cards, and only when the order has a coupon. Spotting that in a
2,000-line production payload is tedious; squeezebug does it mechanically.
"""

from __future__ import annotations

from typing import Any


def _line_total(item: dict[str, Any], coupon: bool) -> float:
    price = float(item["price"]) * int(item.get("qty", 1))
    if item.get("type") == "gift_card" or not coupon:
        return price
    discount = item.get("discount", 0)
    return price * (1 - discount / 100)  # BUG: discount may be a string like "15%"


def import_order(order: dict[str, Any]) -> float:
    """Validate an order payload and return its total."""
    if not order.get("id"):
        raise ValueError("order without id")
    coupon = bool(order.get("coupon"))
    total = 0.0
    for item in order.get("items", []):
        total += _line_total(item, coupon)
    for fee in order.get("fees", []):
        total += float(fee.get("amount", 0))
    return round(total, 2)
