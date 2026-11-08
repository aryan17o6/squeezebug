"""Resolve ``module:function`` / ``path/to/file.py:function`` targets."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable


class TargetError(ValueError):
    """The target string could not be resolved to a callable."""


@dataclass(frozen=True)
class Target:
    """A resolved callable plus how to import it again from generated code.

    Attributes:
        func: The callable itself.
        module: Importable module name (``"shop.orders"``).
        qualname: Attribute path inside the module (``"Cart.total"`` or ``"parse"``).
        spec: The original target string.
    """

    func: Callable[..., Any]
    module: str
    qualname: str
    spec: str


def _resolve_attr(obj: Any, qualname: str, spec: str) -> Any:
    for part in qualname.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise TargetError(f"{spec!r}: no attribute {part!r}") from exc
    return obj


def load_target(spec: str) -> Target:
    """Load a callable from ``"pkg.module:func"`` or ``"file.py:func"``.

    For file paths the file's directory is put on ``sys.path`` so that the
    generated regression test can import the module by its stem.

    Args:
        spec: Target specification.

    Returns:
        The resolved :class:`Target`.

    Raises:
        TargetError: If the module or attribute cannot be found or is not callable.
    """
    if ":" not in spec:
        raise TargetError(f"{spec!r}: expected 'module:function' or 'file.py:function'")
    module_part, qualname = spec.rsplit(":", 1)
    if module_part.endswith(".py") or os.sep in module_part or "/" in module_part:
        path = os.path.abspath(module_part)
        if not os.path.isfile(path):
            raise TargetError(f"{spec!r}: file not found: {path}")
        directory = os.path.dirname(path)
        if directory not in sys.path:
            sys.path.insert(0, directory)
        module_name = os.path.splitext(os.path.basename(path))[0]
        module = importlib.import_module(module_name)
    else:
        if os.getcwd() not in sys.path:
            sys.path.insert(0, os.getcwd())
        module_name = module_part
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise TargetError(f"{spec!r}: cannot import {module_name!r}: {exc}") from exc
    func = _resolve_attr(module, qualname, spec)
    # Shrink the undecorated function so @capture does not re-record every probe.
    func = getattr(func, "__squeezebug_wrapped__", func)
    if not callable(func):
        raise TargetError(f"{spec!r}: {qualname!r} is not callable")
    return Target(func=func, module=module_name, qualname=qualname, spec=spec)


def describe_callable(func: Callable[..., Any]) -> tuple[str, str]:
    """Return ``(module, qualname)`` for a function object, for later re-import."""
    module = getattr(func, "__module__", None) or "__main__"
    qualname = getattr(func, "__qualname__", None) or getattr(func, "__name__", "func")
    if module == "__main__":
        main = sys.modules.get("__main__")
        main_file = getattr(main, "__file__", None)
        if main_file:
            module = os.path.splitext(os.path.basename(main_file))[0]
    return module, qualname
