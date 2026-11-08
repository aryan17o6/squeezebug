import json
import os
import subprocess
import sys

import pytest

from squeezebug import CallInput, TestStyle, capture, render_test, shrink_call
from squeezebug.capture import decode_call, encode_call, read_record
from squeezebug.cli import main
from squeezebug.codegen import is_literal
from squeezebug.llm import build_prompt, explain, get_provider

import targets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_generated(tmp_path, source: str) -> subprocess.CompletedProcess:
    test_file = tmp_path / "test_repro.py"
    test_file.write_text(source)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.join(ROOT, "tests"), os.path.join(ROOT, "src")]))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )


@pytest.mark.parametrize("style", list(TestStyle))
def test_generated_test_runs_green(tmp_path, style) -> None:
    result = shrink_call(targets.lookup, {"mode": "strict", "db": {"port": 1}, "x": [1, 2]})
    source = render_test(result, module="targets", qualname="lookup", style=style)
    compile(source, "generated", "exec")
    proc = _run_generated(tmp_path, source)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ("1 xfailed" if style is TestStyle.XFAIL else "1 passed") in proc.stdout


def test_non_literal_input_uses_pickle(tmp_path) -> None:
    result = shrink_call(targets.first_negative, [1, 2, 3])
    result.minimized = CallInput([range(3)])  # not a literal -> pickle path
    source = render_test(result, module="targets", qualname="first_negative")
    assert "pickle.loads" in source
    assert not is_literal(range(3))


def test_explanation_is_embedded_as_comment() -> None:
    result = shrink_call(targets.first_negative, [1])
    source = render_test(result, module="targets", qualname="first_negative", explanation="line one\nline two")
    assert "#   line one" in source and "#   line two" in source


def test_encode_decode_roundtrip() -> None:
    call = CallInput([{"a": [1, 2]}], {"k": "v"})
    assert decode_call(encode_call(call)).as_tree() == call.as_tree()
    weird = CallInput([{1, 2}])
    payload = encode_call(weird)
    assert payload["encoding"] == "pickle"
    with pytest.raises(ValueError):
        decode_call(payload)
    assert decode_call(payload, allow_pickle=True).args == [{1, 2}]


def test_capture_records_and_replay_cli(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("SQUEEZEBUG_DISABLE", raising=False)
    store = str(tmp_path / "store")
    wrapped = capture(store=store)(targets.lookup)
    with pytest.raises(KeyError):
        wrapped({"mode": "strict", "db": {"user": "x"}, "noise": list(range(40))})
    files = os.listdir(store)
    assert len(files) == 1
    record = read_record(os.path.join(store, files[0]))
    assert record["target"] == "targets:lookup"
    assert record["signature"]["exc_type"] == "KeyError"

    out_test = tmp_path / "test_out.py"
    code = main(["replay", os.path.join(store, files[0]), "-o", str(out_test), "--provider", "none"])
    assert code == 0
    printed = capsys.readouterr().out
    assert "shrunk" in printed and "wrote regression test" in printed
    assert "from targets import lookup" in out_test.read_text()

    assert main(["list", "--store", store]) == 0
    assert "targets:lookup" in capsys.readouterr().out


def test_capture_can_be_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SQUEEZEBUG_DISABLE", "1")
    store = str(tmp_path / "s")
    with pytest.raises(IndexError):
        capture(store=store)(targets.first_negative)([1])
    assert not os.path.exists(store)


def test_cli_shrink_json(tmp_path, capsys) -> None:
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"mode": "strict", "db": {}, "pad": "x" * 100}))
    code = main(["shrink", os.path.join(ROOT, "tests", "targets.py") + ":lookup", "-i", str(inp), "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["minimized"]["args"] == [{"mode": "strict"}]
    assert data["signature"]["exc_type"] == "KeyError"


def test_cli_errors_are_reported(tmp_path, capsys) -> None:
    inp = tmp_path / "in.json"
    inp.write_text("5")
    assert main(["shrink", "targets:ok", "-i", str(inp)]) == 2
    assert "does not raise" in capsys.readouterr().err
    assert main(["shrink", "nonexistent_mod_xyz:f", "-i", str(inp)]) == 2


def test_explain_with_fake_provider_and_graceful_none(monkeypatch, capsys, tmp_path) -> None:
    class Fake:
        name = "fake"

        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return "The list has no negative numbers."

    result = shrink_call(targets.first_negative, [1, 2])
    fake = Fake()
    assert explain(result, targets.first_negative, fake) == "The list has no negative numbers."
    assert "def first_negative" in fake.prompts[0]
    assert explain(result, targets.first_negative, None) == ""
    assert "Minimal failing call" in build_prompt(result, targets.first_negative)

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert get_provider("gemini") is None
    assert get_provider("none") is None
    inp = tmp_path / "in.json"
    inp.write_text("[1, 2, 3]")
    assert main(["shrink", "targets:first_negative", "-i", str(inp), "--as-arg", "--explain", "--provider", "gemini"]) == 0
    assert "no LLM provider available" in capsys.readouterr().err
