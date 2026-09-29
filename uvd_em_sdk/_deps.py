"""The one hard dependency with a floor: uvd-x402-sdk >= 0.93.0.

``uvd_em_sdk.escrow_signing`` re-exports ``uvd_x402_sdk.escrow_signing``,
whose public API (``__all__``) is stable from 0.93.0 on; older releases lack
names this package re-exports (``ESCROW_PAYMENT_INFO_TYPEHASH``,
``VERIFIED_USDC_DOMAINS``, ...) and sign without the typehash check. pip
enforces the requirement at install time; :func:`require_uvd_x402_sdk`
enforces the floor at import time too (this module runs it when
``uvd_em_sdk`` is imported), for environments pip did not resolve
(``--no-deps``, a vendored or pre-installed SDK), so an old SDK fails here
with a message that names the fix instead of later with an ``ImportError``
of one missing name.

Only the floor is checked at runtime. The ceiling (``<0.94``) is the
install-time pin: it keeps the next minor out until this package's suite has
run against it.
"""

from __future__ import annotations

import re

#: What ``pyproject.toml`` declares; ``tests/test_dependencies.py`` pins both.
UVD_X402_SDK_REQUIREMENT = "uvd-x402-sdk[wallet]>=0.93.0,<0.94"
UVD_X402_SDK_MIN_VERSION = (0, 93, 0)

_RELEASE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_release(version: str) -> tuple[int, int, int]:
    """``"0.93.0"`` -> ``(0, 93, 0)``; pre/post/local suffixes are ignored."""
    match = _RELEASE.match(version.strip())
    if match is None:
        raise ValueError(f"not a release version: {version!r}")
    major, minor, micro = match.groups()
    return int(major), int(minor), int(micro or 0)


def require_uvd_x402_sdk(found: str | None = None) -> str:
    """Return the installed uvd-x402-sdk version, or raise ``ImportError``.

    Args:
        found: The version to check; defaults to ``uvd_x402_sdk.__version__``
            (the SDK that is actually imported, not whatever metadata says).
    """
    if found is None:
        import uvd_x402_sdk

        found = str(getattr(uvd_x402_sdk, "__version__", "0"))
    try:
        release = parse_release(found)
    except ValueError:
        release = (0, 0, 0)
    if release < UVD_X402_SDK_MIN_VERSION:
        raise ImportError(
            f"uvd-em-sdk needs {UVD_X402_SDK_REQUIREMENT} (escrow signing is "
            f"uvd_x402_sdk.escrow_signing, stable from 0.93.0); found "
            f"uvd-x402-sdk {found}. Fix: pip install '{UVD_X402_SDK_REQUIREMENT}'"
        )
    return found


# The import-time check: ``uvd_em_sdk/__init__.py`` imports this module first.
require_uvd_x402_sdk()
