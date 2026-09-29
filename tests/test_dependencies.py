"""uvd-x402-sdk is a HARD dependency, >=0.93.0,<0.94 (0.9.0).

Until 0.8.0 it was the optional ``[wallet]`` extra, pinned ``==0.91.2``. Now
``uvd_em_sdk.escrow_signing`` re-exports ``uvd_x402_sdk.escrow_signing``,
whose API is stable from 0.93.0, so the requirement is unconditional and has
a floor. Pinned three ways: in ``pyproject.toml``, in the installed metadata,
and at import time (``uvd_em_sdk/_deps.py``).
"""

import importlib.metadata as md
import subprocess
import sys
from pathlib import Path

import pytest
import uvd_x402_sdk
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from uvd_em_sdk import _deps

try:  # tomllib is stdlib from Python 3.11; the SDK supports >=3.10
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py3.10 fallback
    tomllib = None

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
EXPECTED = SpecifierSet(">=0.93.0,<0.94")


def _x402(requirements):
    return [
        req
        for req in (Requirement(r) for r in requirements)
        if req.name == "uvd-x402-sdk"
    ]


def _assert_the_requirement(req):
    assert req.specifier == EXPECTED, str(req)
    assert "wallet" in req.extras, str(req)  # eth-account: what signs
    assert Version("0.92.0") not in req.specifier
    assert Version("0.93.0") in req.specifier
    assert Version("0.94.0") not in req.specifier


@pytest.mark.skipif(tomllib is None, reason="tomllib needs Python >= 3.11")
def test_pyproject_declares_it_as_a_dependency_not_an_extra():
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    deps = _x402(project["dependencies"])
    assert len(deps) == 1
    _assert_the_requirement(deps[0])
    assert deps[0].marker is None
    for extra, requirements in project.get("optional-dependencies", {}).items():
        assert _x402(requirements) == [], f"extra [{extra}] still carries it"


def test_installed_metadata_requires_it_unconditionally():
    try:
        requirements = md.requires("uvd-em-sdk") or []
    except md.PackageNotFoundError:  # pragma: no cover - not pip-installed
        pytest.skip("uvd-em-sdk is not installed (pip install -e .)")
    unconditional = [req for req in _x402(requirements) if req.marker is None]
    assert len(unconditional) == 1, requirements
    _assert_the_requirement(unconditional[0])


def test_the_sdk_in_this_environment_satisfies_it():
    assert Version(uvd_x402_sdk.__version__) in EXPECTED


def test_the_import_guard_states_the_same_requirement():
    _assert_the_requirement(Requirement(_deps.UVD_X402_SDK_REQUIREMENT))
    assert _deps.UVD_X402_SDK_MIN_VERSION == (0, 93, 0)


@pytest.mark.parametrize("found", ["0.91.2", "0.92.0", "0.92.99", "0.34.0", "?"])
def test_an_older_sdk_is_refused(found):
    with pytest.raises(ImportError, match=r"uvd-x402-sdk\[wallet\]>=0\.93\.0,<0\.94"):
        _deps.require_uvd_x402_sdk(found)


@pytest.mark.parametrize("found", ["0.93.0", "0.93.7", "0.94.0", "1.0.0"])
def test_the_floor_and_above_is_accepted(found):
    assert _deps.require_uvd_x402_sdk(found) == found


def test_importing_the_package_over_an_older_sdk_fails():
    """At import time, in a fresh interpreter: the SDK reports 0.91.2."""
    code = (
        "import uvd_x402_sdk; uvd_x402_sdk.__version__ = '0.91.2'\nimport uvd_em_sdk\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode != 0
    assert "ImportError" in proc.stderr
    assert "found uvd-x402-sdk 0.91.2" in proc.stderr
