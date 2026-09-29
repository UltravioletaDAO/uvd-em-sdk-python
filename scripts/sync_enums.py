"""Regenerate the client enum snapshots from the backend models.

Single source of truth: ``mcp_server/models.py`` (``TaskStatus``,
``TaskCategory``, ``EvidenceType``, ``TargetExecutorType``). This script
rewrites the generated region of each client SDK from it, so the snapshots
can never drift silently again (F3-6) — the same pattern as
``scripts/sync_networks.py`` for the network registry. ``sdk/python``
shipped a 5-member ``TaskCategory`` against a 21-category backend and
crashed with ``ValueError`` on 16 of them; this is the fix that keeps it
fixed. Per-SDK parity tests (``tests/test_enums_sync.py`` in each SDK)
enforce equality on every pytest run.

Targets:
  - ``uvd_em_sdk/models.py``            (canonical client stack)
  - ``sdk/python/execution_market/types.py`` (OWS signer + legacy shim)

Client extras: values the backend API emits but its enum predates
(``TaskStatus.ASSIGNING``, ADR-003 async assign) are declared ONCE in
``CLIENT_EXTRAS`` below, rendered into every target, and guarded: if the
backend enum ever gains the value, the script refuses to run until the
extra is retired.

``DisputeReason`` is NOT synced — it mirrors migration 004, not models.py.

Usage (from the repository root)::

    python scripts/sync_enums.py            # rewrite both regions
    python scripts/sync_enums.py --check    # exit 1 on drift (CI)
    python scripts/sync_enums.py --backend path/to/models.py

Run ``ruff format .`` after a rewrite (house rule).
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SDK_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SDK_ROOT.parent
DEFAULT_BACKEND = REPO_ROOT / "mcp_server" / "models.py"

BEGIN_MARKER = "# --- BEGIN GENERATED: ENUMS (uvd-em-sdk/scripts/sync_enums.py) ---"
END_MARKER = "# --- END GENERATED: ENUMS ---"


@dataclass(frozen=True)
class ExtraMember:
    """A client-side enum member the backend enum does not (yet) declare."""

    name: str
    value: str
    comment: tuple[str, ...]


@dataclass(frozen=True)
class Target:
    """A client file whose generated region this script owns."""

    path: Path
    enums: tuple[str, ...]


TARGETS: dict[str, Target] = {
    "plugin": Target(
        path=SDK_ROOT / "uvd_em_sdk" / "models.py",
        enums=("TaskStatus", "TaskCategory", "EvidenceType", "TargetExecutorType"),
    ),
    "sdk-python": Target(
        path=REPO_ROOT / "sdk" / "python" / "execution_market" / "types.py",
        enums=("TaskStatus", "TaskCategory", "EvidenceType"),
    ),
}

#: Values the live API emits that the backend enum predates. Documented
#: here ONCE; the guard in :func:`expected_members` fails the run the day
#: the backend catches up (retire the extra then).
CLIENT_EXTRAS: dict[str, tuple[ExtraMember, ...]] = {
    "TaskStatus": (
        ExtraMember(
            name="ASSIGNING",
            value="assigning",
            comment=(
                "ADR-003 async assign: intermediate state while the escrow lock",
                "runs. The API emits it (202 + poll) but the backend enum",
                "predates async assign — clients must keep parsing it.",
            ),
        ),
    ),
}

ENUM_DOCS: dict[str, str] = {
    "TaskStatus": "Task lifecycle states (synced from ``mcp_server/models.py``).",
    "TaskCategory": "Task categories (synced from ``mcp_server/models.py``).",
    "EvidenceType": "Evidence types (synced from ``mcp_server/models.py``).",
    "TargetExecutorType": (
        "Who can execute (synced from ``mcp_server/models.py``); ``any`` = wildcard."
    ),
}


def load_backend_enums(backend_path: Path) -> dict[str, dict[str, str]]:
    """Parse every ``class X(str, Enum)`` out of the backend module WITHOUT
    importing it (the backend package pulls env-dependent imports)."""
    tree = ast.parse(backend_path.read_text(encoding="utf-8"))
    enums: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        if "Enum" not in bases:
            continue
        members: dict[str, str] = {}
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                members[stmt.targets[0].id] = stmt.value.value
        enums[node.name] = members
    return enums


def expected_members(
    backend_enums: dict[str, dict[str, str]], enum_name: str
) -> dict[str, str]:
    """Backend members + documented client extras, guarding collisions."""
    if enum_name not in backend_enums:
        raise SystemExit(f"{enum_name} not found in the backend models")
    members = dict(backend_enums[enum_name])
    for extra in CLIENT_EXTRAS.get(enum_name, ()):
        if extra.value in members.values() or extra.name in members:
            raise SystemExit(
                f"{enum_name}.{extra.name} now exists in the backend enum — "
                f"retire the CLIENT_EXTRAS entry in {Path(__file__).name}"
            )
        members[extra.name] = extra.value
    return members


def render_region(backend_enums: dict[str, dict[str, str]], target: Target) -> str:
    """Render the generated region content (marker to marker, exclusive).

    Values are rendered with double quotes so ``ruff format`` is a no-op on
    the region — ``--check`` compares TEXT, and must survive a format pass.
    """
    blocks: list[str] = []
    for enum_name in target.enums:
        lines = [f"class {enum_name}(str, Enum):"]
        lines.append(f'    """{ENUM_DOCS[enum_name]}"""')
        lines.append("")
        for name, value in backend_enums[enum_name].items():
            lines.append(f'    {name} = "{value}"')
        for extra in CLIENT_EXTRAS.get(enum_name, ()):
            for comment in extra.comment:
                lines.append(f"    # {comment}")
            lines.append(f'    {extra.name} = "{extra.value}"')
        blocks.append("\n".join(lines))
    return "\n\n\n".join(blocks)


def read_region(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    try:
        rest = text.split(BEGIN_MARKER, 1)[1]
        region = rest.split(END_MARKER, 1)[0]
    except IndexError:
        raise SystemExit(f"Markers not found in {path}")
    return region.strip("\n")


def rewrite_region(path: Path, content: str) -> None:
    text = path.read_text(encoding="utf-8")
    try:
        head, rest = text.split(BEGIN_MARKER, 1)
        _, tail = rest.split(END_MARKER, 1)
    except ValueError:
        raise SystemExit(f"Markers not found in {path}")
    new_text = head + BEGIN_MARKER + "\n" + content + "\n" + END_MARKER + tail
    path.write_text(new_text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare every target against the backend; exit 1 on drift",
    )
    args = parser.parse_args()

    if not args.backend.exists():
        raise SystemExit(f"Backend models not found: {args.backend}")

    backend_enums = load_backend_enums(args.backend)
    # Validate extras (and fail loudly) before touching any file.
    for target in TARGETS.values():
        for enum_name in target.enums:
            expected_members(backend_enums, enum_name)

    drifted: list[str] = []
    for key, target in TARGETS.items():
        expected = render_region(backend_enums, target)
        if args.check:
            if read_region(target.path) != expected:
                drifted.append(f"{key}: {target.path}")
            continue
        rewrite_region(target.path, expected)
        print(f"Rewrote {target.path} ({len(target.enums)} enums)")

    if args.check:
        if drifted:
            print("DRIFT — client enums do not match mcp_server/models.py:")
            for entry in drifted:
                print(f"  {entry}")
            print("Fix: python scripts/sync_enums.py && ruff format .")
            return 1
        print(f"OK — {len(TARGETS)} targets match mcp_server/models.py")
        return 0

    print("Now run: ruff format .")
    return 0


if __name__ == "__main__":
    sys.exit(main())
