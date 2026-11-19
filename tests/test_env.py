"""
Tests for the ``.env`` loader.

The loader is the only place a credential enters the project, so the rules
here are about precedence and about not surprising the user: a value in the
shell must win, a missing file must be harmless, and a malformed line must
not take the run down.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ar_reconciliation.config import _parse_env_file, load_env


@pytest.fixture(autouse=True)
def clean_environment():
    """Keep the developer's own environment out of the test."""
    saved = {
        key: os.environ.pop(key, None)
        for key in ("OPENAI_API_KEY", "AR_AGENT_MODEL", "OPENAI_BASE_URL", "TEST_ONLY")
    }
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def write_env(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


class TestParsing:
    def test_reads_a_simple_assignment(self, tmp_path):
        path = write_env(tmp_path, "OPENAI_API_KEY=sk-abc123\n")
        assert _parse_env_file(path) == {"OPENAI_API_KEY": "sk-abc123"}

    def test_skips_comments_and_blank_lines(self, tmp_path):
        path = write_env(
            tmp_path,
            "# a comment\n\nOPENAI_API_KEY=sk-abc123\n\n# another\n",
        )
        assert _parse_env_file(path) == {"OPENAI_API_KEY": "sk-abc123"}

    def test_accepts_the_export_prefix(self, tmp_path):
        path = write_env(tmp_path, "export OPENAI_API_KEY=sk-abc123\n")
        assert _parse_env_file(path) == {"OPENAI_API_KEY": "sk-abc123"}

    def test_strips_matching_quotes(self, tmp_path):
        path = write_env(
            tmp_path,
            'A="double"\nB=\'single\'\n',
        )
        parsed = _parse_env_file(path)
        assert parsed == {"A": "double", "B": "single"}

    def test_keeps_a_value_that_contains_an_equals_sign(self, tmp_path):
        path = write_env(tmp_path, "BASE_URL=https://x.test/v1?a=1&b=2\n")
        assert _parse_env_file(path)["BASE_URL"] == "https://x.test/v1?a=1&b=2"

    def test_ignores_a_line_without_an_equals_sign(self, tmp_path):
        path = write_env(tmp_path, "not an assignment\nOPENAI_API_KEY=sk-abc123\n")
        assert _parse_env_file(path) == {"OPENAI_API_KEY": "sk-abc123"}

    def test_a_missing_file_yields_nothing(self, tmp_path):
        assert _parse_env_file(tmp_path / "absent.env") == {}

    def test_surrounding_whitespace_is_trimmed(self, tmp_path):
        path = write_env(tmp_path, "  OPENAI_API_KEY  =  sk-abc123  \n")
        assert _parse_env_file(path) == {"OPENAI_API_KEY": "sk-abc123"}


class TestLoading:
    def test_the_file_populates_the_environment(self, tmp_path):
        path = write_env(tmp_path, "OPENAI_API_KEY=sk-from-file\n")
        load_env(path)
        assert os.environ["OPENAI_API_KEY"] == "sk-from-file"

    def test_a_missing_file_is_harmless(self, tmp_path):
        assert load_env(tmp_path / "absent.env") == {}
        assert "OPENAI_API_KEY" not in os.environ

    def test_the_shell_wins_over_the_file(self, tmp_path):
        """An explicit export is a deliberate override."""
        os.environ["OPENAI_API_KEY"] = "sk-from-shell"
        path = write_env(tmp_path, "OPENAI_API_KEY=sk-from-file\n")
        load_env(path)
        assert os.environ["OPENAI_API_KEY"] == "sk-from-shell"

    def test_only_the_new_values_are_reported(self, tmp_path):
        os.environ["OPENAI_API_KEY"] = "sk-from-shell"
        path = write_env(tmp_path, "OPENAI_API_KEY=sk-from-file\nTEST_ONLY=new\n")
        loaded = load_env(path)
        assert loaded == {"TEST_ONLY": "new"}

    def test_the_example_template_is_parsable(self):
        """The shipped .env.example must not break the loader."""
        example = Path(__file__).resolve().parent.parent / ".env.example"
        assert example.exists(), ".env.example is missing"
        parsed = _parse_env_file(example)
        assert "OPENAI_API_KEY" in parsed
        assert parsed["OPENAI_API_KEY"].startswith("sk-")

    def test_the_example_template_never_holds_a_real_key(self):
        """The committed template must not carry a usable credential."""
        example = Path(__file__).resolve().parent.parent / ".env.example"
        for key, value in _parse_env_file(example).items():
            if "KEY" in key or "SECRET" in key or "TOKEN" in key:
                assert "replace" in value.lower(), (
                    f"{key} in .env.example looks like a real credential"
                )
