"""Version consistency — one source of truth (SDK-51).

The version previously lived in THREE contradictory places (pyproject
0.1.0, __init__ 0.4.0, User-Agent 0.2.0). Now ``__init__.__version__`` is
the only literal: pyproject derives it at build time via
``[tool.hatch.version]`` and the User-Agent derives it at runtime.
"""

import re
from pathlib import Path

try:  # tomllib is stdlib from Python 3.11; the SDK supports >=3.10
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py3.10 fallback
    tomllib = None

import uvd_em_sdk
from uvd_em_sdk import EMClient

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", uvd_em_sdk.__version__)


def test_version_consistency():
    """The 3 historical version locations must agree (single source)."""
    # 1. Runtime User-Agent derives from __init__.__version__
    client = EMClient()
    assert (
        client._default_headers()["User-Agent"]
        == f"uvd-em-sdk/{uvd_em_sdk.__version__}"
    )

    # 2. pyproject.toml has NO literal version — it derives from __init__
    raw = PYPROJECT.read_text(encoding="utf-8")
    if tomllib is not None:
        data = tomllib.loads(raw)
        assert "version" in data["project"]["dynamic"]
        assert "version" not in data["project"]
        assert data["tool"]["hatch"]["version"]["path"] == "uvd_em_sdk/__init__.py"
    else:  # pragma: no cover - py3.10 fallback
        assert 'dynamic = ["version"]' in raw
        assert 'path = "uvd_em_sdk/__init__.py"' in raw


def test_current_version_is_0_9_0():
    """v0.9.0 = the package leaves the Execution Market monorepo under its
    own name and import path, ``uvd_em_sdk`` / ``uvd-em-sdk`` (the 0.8.0
    API, renamed; see CHANGELOG.md)."""
    assert uvd_em_sdk.__version__ == "0.9.0"
