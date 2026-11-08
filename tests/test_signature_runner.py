from squeezebug import CallInput, FailureSignature, MatchMode
from squeezebug.runner import run_once, still_fails

import targets


def _sig(func, *args):
    out = run_once(func, CallInput(list(args)))
    assert out.signature is not None
    return out.signature


def test_signature_roundtrip() -> None:
    sig = _sig(targets.first_negative, [1])
    assert sig.exc_type == "IndexError"
    assert sig.function == "first_negative"
    assert FailureSignature.from_dict(sig.to_dict()) == sig
    assert "IndexError" in sig.short() and "targets.py" in sig.short()


def test_match_modes() -> None:
    a = _sig(targets.two_bugs, {"a": 1})
    b = _sig(targets.two_bugs, {"b": 1})
    assert a.matches(b, MatchMode.TYPE)
    assert not a.matches(b, MatchMode.LOCATION)
    assert a.matches(a, MatchMode.MESSAGE)


def test_run_once_success_and_quiet(capsys) -> None:
    def noisy(x: int) -> int:
        print("hello from target")
        return x

    out = run_once(noisy, CallInput([1]))
    assert not out.failed
    assert "hello from target" not in capsys.readouterr().out


def test_timeout_is_enforced() -> None:
    def spin(n: int) -> None:
        while True:
            n += 1

    out = run_once(spin, CallInput([0]), timeout=0.2)
    assert out.timed_out and out.failed


def test_still_fails() -> None:
    target = _sig(targets.first_negative, [1, 2])
    assert still_fails(targets.first_negative, CallInput([[]]), target, MatchMode.LOCATION)
    assert not still_fails(targets.first_negative, CallInput([[-1]]), target, MatchMode.LOCATION)
