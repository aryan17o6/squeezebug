"""Small buggy functions used as shrinking targets in the test-suite."""

from typing import Any


def first_negative(xs: list[int]) -> int:
    return [x for x in xs if x < 0][0]  # IndexError when none negative


def lookup(config: dict[str, Any]) -> str:
    if config.get("mode") == "strict":
        return config["db"]["host"]  # KeyError when db/host missing
    return "default"


def two_bugs(data: dict[str, Any]) -> int:
    if "a" in data:
        raise ValueError("bug A")
    if "b" in data:
        raise ValueError("bug B")
    return 0


def parse_age(text: str) -> int:
    age = int(text.strip())
    if age > 150:
        raise ValueError(f"age too large: {age}")
    return age


def mutating(xs: list[int]) -> None:
    xs.append(1)
    if len(xs) > 3:
        raise RuntimeError("too long")


def ok(x: Any) -> None:
    return None


def kw_target(a: int, *, verbose: bool = False, tags: tuple[str, ...] = ()) -> None:
    if verbose and a > 10:
        raise AssertionError("verbose big")
