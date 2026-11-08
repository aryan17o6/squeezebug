"""Record failing calls where they happen, so they can be shrunk later.

Decorate a function that sometimes crashes on real-world input::

    from squeezebug import capture

    @capture
    def import_orders(payload: dict) -> int:
        ...

When it raises, the arguments, the failure signature and the traceback are
written to ``.squeezebug/<id>.json`` and the exception is re-raised unchanged.
Then run ``squeezebug replay .squeezebug/<id>.json`` to get a minimal repro.
"""

from __future__ import annotations

import base64
import datetime as _dt
import functools
import hashlib
import json
import os
import pickle
import platform
import traceback
from typing import Any, Callable, Optional, TypeVar, overload

from .loader import describe_callable
from .runner import CallInput
from .signature import FailureSignature

F = TypeVar("F", bound=Callable[..., Any])

DEFAULT_STORE = ".squeezebug"
RECORD_VERSION = 1


def encode_call(call: CallInput) -> dict[str, Any]:
    """Encode a call as JSON when possible, falling back to base64 pickle."""
    tree = call.as_tree()
    try:
        text = json.dumps(tree)
        if json.loads(text) == tree:
            return {"encoding": "json", "data": tree}
    except (TypeError, ValueError):
        pass
    blob = base64.b64encode(pickle.dumps(tree)).decode("ascii")
    return {"encoding": "pickle", "data": blob}


def decode_call(payload: dict[str, Any], *, allow_pickle: bool = False) -> CallInput:
    """Decode the output of :func:`encode_call`.

    Args:
        payload: The ``{"encoding": ..., "data": ...}`` mapping.
        allow_pickle: Pickle can execute code; it is only loaded when the
            caller explicitly trusts the file.

    Raises:
        ValueError: On unknown encodings or untrusted pickle payloads.
    """
    encoding = payload.get("encoding")
    if encoding == "json":
        return CallInput.from_tree(payload["data"])
    if encoding == "pickle":
        if not allow_pickle:
            raise ValueError("record uses pickle encoding; pass allow_pickle=True (--trust-pickle) if you trust it")
        return CallInput.from_tree(pickle.loads(base64.b64decode(payload["data"])))
    raise ValueError(f"unknown encoding {encoding!r}")


def build_record(func: Callable[..., Any], call: CallInput, exc: BaseException) -> dict[str, Any]:
    """Assemble the JSON record describing one failing call."""
    module, qualname = describe_callable(func)
    sig = FailureSignature.from_exception(exc)
    return {
        "squeezebug": RECORD_VERSION,
        "target": f"{module}:{qualname}",
        "module": module,
        "qualname": qualname,
        "signature": sig.to_dict(),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        "call": encode_call(call),
        "python": platform.python_version(),
        "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }


def write_record(record: dict[str, Any], store: str = DEFAULT_STORE) -> str:
    """Write ``record`` into ``store`` and return its path.

    The file name is a short hash of the target and signature, so the same bug
    hit repeatedly overwrites one file instead of flooding the directory.
    """
    os.makedirs(store, exist_ok=True)
    sig = record["signature"]
    digest = hashlib.sha1(
        f"{record['target']}|{sig['exc_type']}|{sig['filename']}|{sig['lineno']}".encode()
    ).hexdigest()[:10]
    path = os.path.join(store, f"{digest}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, default=repr)
    return path


def read_record(path: str) -> dict[str, Any]:
    """Load a record written by :func:`write_record`."""
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    if record.get("squeezebug") != RECORD_VERSION:
        raise ValueError(f"{path}: not a squeezebug record (or unsupported version)")
    return record


@overload
def capture(func: F) -> F: ...
@overload
def capture(*, store: str = DEFAULT_STORE, enabled: Optional[bool] = None) -> Callable[[F], F]: ...


def capture(
    func: Optional[F] = None,
    *,
    store: str = DEFAULT_STORE,
    enabled: Optional[bool] = None,
) -> Any:
    """Decorator that records the arguments of calls that raise.

    Args:
        func: The function to wrap (when used as bare ``@capture``).
        store: Directory to write records into.
        enabled: Force on/off. By default capture is on unless the environment
            variable ``SQUEEZEBUG_DISABLE`` is set.

    Returns:
        The wrapped function; behaviour is unchanged apart from the recording.
    """

    def decorate(f: F) -> F:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return f(*args, **kwargs)
            except Exception as exc:
                active = enabled if enabled is not None else not os.environ.get("SQUEEZEBUG_DISABLE")
                if active:
                    try:
                        record = build_record(f, CallInput(list(args), dict(kwargs)), exc)
                        record["path"] = write_record(record, store)
                    except Exception:  # never let recording mask the real error
                        pass
                raise

        wrapper.__squeezebug_wrapped__ = f  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    if func is not None:
        return decorate(func)
    return decorate
