import pytest

from squeezebug import CallInput, MatchMode, NotFailingError, Shrinker, shrink_call, size
from squeezebug.shrink import get_at, iter_paths, replace_at, simplifications

import targets


def test_size_orders_simpler_values_lower() -> None:
    assert size(0) < size(12345)
    assert size("") < size("abc")
    assert size([]) < size([1])
    assert size({"a": 1}) > size({})
    assert size(True) == size(False) == size(None) == 1


def test_paths_and_replace_are_consistent() -> None:
    tree = {"a": [1, {"b": "x"}], "c": (2, 3)}
    paths = list(iter_paths(tree))
    assert ("a", 1, "b") in paths and ("c", 1) in paths
    new = replace_at(tree, ("a", 1, "b"), "y")
    assert get_at(new, ("a", 1, "b")) == "y"
    assert tree["a"][1]["b"] == "x", "original must not be mutated"
    assert isinstance(replace_at(tree, ("c", 0), 9)["c"], tuple)


def test_simplifications_are_never_larger() -> None:
    for value in [10**9, -7, 3.75, "Hello World", [1, 2, 3, 4, 5], {"a": 1, "b": [2]}, {1, 2, 3}, True, b"xyz"]:
        for cand in simplifications(value):
            assert size(cand) <= size(value)


def test_shrinks_list_to_minimum() -> None:
    result = shrink_call(targets.first_negative, list(range(1, 200)))
    assert result.minimized.args == [[]]
    assert result.minimized_size < result.original_size


def test_keeps_only_what_triggers_bug() -> None:
    config = {
        "mode": "strict",
        "db": {"port": 5432, "user": "admin", "pool": list(range(50))},
        "cache": {"ttl": 600, "servers": ["a", "b", "c"]},
        "feature_flags": {f"f{i}": bool(i % 2) for i in range(30)},
    }
    result = shrink_call(targets.lookup, config)
    (minimal,) = result.minimized.args
    # config["db"]["host"] raises KeyError on the same line whether "db" or "host"
    # is missing, so location mode legitimately drops "db" too.
    assert minimal == {"mode": "strict"}
    strict = shrink_call(targets.lookup, config, squeeze_mode="message")
    assert strict.minimized.args == [{"mode": "strict", "db": {}}]


def test_location_mode_does_not_jump_to_other_bug() -> None:
    data = {"a": 1, "b": 2, "c": list(range(20))}
    # Original failure is "bug A". Removing "a" would reveal bug B (different line) -> rejected.
    result = shrink_call(targets.two_bugs, data)
    assert result.minimized.args == [{"a": 0}]
    assert result.final_signature.message == "bug A"


def test_type_mode_is_looser_than_location() -> None:
    data = {"a": 1, "b": 2}
    loose = shrink_call(targets.two_bugs, data, squeeze_mode="type")
    assert loose.minimized_size <= shrink_call(targets.two_bugs, data).minimized_size


def test_message_mode_preserves_message() -> None:
    result = shrink_call(targets.parse_age, "   987654   ", squeeze_mode=MatchMode.MESSAGE)
    assert result.final_signature.message == "age too large: 987654"
    assert result.minimized.args[0].strip() == "987654"


def test_location_mode_shrinks_numbers() -> None:
    result = shrink_call(targets.parse_age, "   987654   ")
    (text,) = result.minimized.args
    assert int(text) > 150
    assert len(text) == 3  # no 2-char string exceeds 150


def test_input_mutation_does_not_leak() -> None:
    xs = [5, 6, 7]
    result = shrink_call(targets.mutating, xs)
    assert xs == [5, 6, 7]
    assert len(result.minimized.args[0]) == 3


def test_positional_arity_preserved_and_kwargs_dropped() -> None:
    result = shrink_call(targets.kw_target, 99, verbose=True, tags=("x", "y", "z"))
    assert len(result.minimized.args) == 1
    assert result.minimized.kwargs == {"verbose": True}
    assert result.minimized.args[0] == 11 or result.minimized.args[0] > 10


def test_not_failing_input_raises() -> None:
    with pytest.raises(NotFailingError):
        shrink_call(targets.ok, {"a": 1})


def test_budget_is_respected() -> None:
    shrinker = Shrinker(targets.lookup, max_calls=5)
    big = {"mode": "strict", **{f"k{i}": list(range(i)) for i in range(60)}}
    result = shrinker.shrink(CallInput([big]))
    assert result.calls <= 5
    assert result.budget_exhausted


def test_example_order_needle_found() -> None:
    import json
    import os

    from orders import import_order

    path = os.path.join(os.path.dirname(__file__), "..", "examples", "big_order.json")
    with open(path) as fh:
        payload = json.load(fh)
    result = shrink_call(import_order, payload)
    (minimal,) = result.minimized.args
    assert len(minimal["items"]) == 1
    assert isinstance(minimal["items"][0]["discount"], str)
    assert minimal.get("coupon")
    assert result.reduction > 0.99
