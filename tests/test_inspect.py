from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

import skills_dump


def _run(argv, monkeypatch) -> dict:
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    skills_dump.main(argv)
    return json.loads(buf.getvalue().strip())


def test_inspect_rejects_invalid_name(monkeypatch):
    out = _run(["--mode", "inspect", "--name", "../etc/passwd"], monkeypatch)
    assert out["error"] == "invalid_name"
    assert out["skill"] is None


def test_inspect_rejects_command_chars(monkeypatch):
    out = _run(["--mode", "inspect", "--name", "writer; rm -rf /"], monkeypatch)
    assert out["error"] == "invalid_name"


def test_inspect_rejects_too_long(monkeypatch):
    out = _run(["--mode", "inspect", "--name", "a" * 201], monkeypatch)
    assert out["error"] == "invalid_name"


def test_inspect_accepts_valid_short_name(monkeypatch):
    out = _run(["--mode", "inspect", "--name", "writer"], monkeypatch)
    assert out["skill"] is None
    assert out["error"] == "hermes_unavailable"


def test_inspect_accepts_valid_slash_path(monkeypatch):
    out = _run(["--mode", "inspect", "--name", "anthropics/skills/skill-creator"], monkeypatch)
    assert out["skill"] is None
    assert out["error"] == "hermes_unavailable"


def test_inspect_accepts_name_with_spaces(monkeypatch):
    """Real Hermes skill names like 'MD5 Tool' / 'GitHub PR Reviewer'
    contain spaces. Earlier regex `[A-Za-z0-9_./\\-]` rejected them
    and the iOS detail sheet showed `invalid_name`. Plugin >=0.1.3
    accepts them."""
    out = _run(["--mode", "inspect", "--name", "MD5 Tool"], monkeypatch)
    # Name passes validation; plugin then routes to Hermes (unavailable
    # in tests) and surfaces that as the only error.
    assert out["error"] == "hermes_unavailable"


def test_inspect_accepts_common_punctuation(monkeypatch):
    """Real-world skill names include parens, plus, ampersand, comma."""
    for name in ["Skill (beta)", "C++ Linter", "A & B", "X, Y, Z", "It's Fine"]:
        out = _run(["--mode", "inspect", "--name", name], monkeypatch)
        assert out["error"] == "hermes_unavailable", f"rejected: {name!r}"


def test_inspect_still_rejects_shell_metacharacters(monkeypatch):
    """The relaxed regex must still keep dangerous chars out."""
    for bad in [
        "name|cat /etc/passwd",
        "name>/tmp/x",
        "name<input",
        "name`whoami`",
        "name$(whoami)",
        "name\\nwith newline",
        "name\\with backslash",
    ]:
        out = _run(["--mode", "inspect", "--name", bad], monkeypatch)
        assert out["error"] == "invalid_name", f"accepted dangerous name: {bad!r}"


def test_unknown_mode_rejected_by_argparse(monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        _run(["--mode", "wat"], monkeypatch)
    assert excinfo.value.code == 2


def test_envelope_always_includes_plugin_version(monkeypatch):
    out = _run(["--mode", "inspect", "--name", ""], monkeypatch)
    assert out["plugin_version"] == "0.1.3"

    out = _run(["--mode", "hub"], monkeypatch)
    assert out["plugin_version"] == "0.1.3"
