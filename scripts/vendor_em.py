"""Vendor the Execution Market data the parity tests compare the SDK against.

Execution Market is a PRIVATE repository and this one is PUBLIC, so its files
are NOT copied. This script reads them from a commit of an EM clone
(``git show``, never the working tree), parses them with ``ast`` (never
imported) and writes into ``vendor/em/`` only what this repository's tests
consume (``scripts/sync_networks.py``, ``scripts/sync_enums.py`` and
``tests/test_retry.py``):

  - ``networks.json`` — from ``NETWORK_CONFIG``
    (``mcp_server/integrations/x402/network_registry.py``), per network:
    ``chain_id``, ``network_type``, per token ``address``/``name``/``decimals``,
    and ``escrow``/``operator`` reduced to whether they are set (the SDK only
    exposes ``has_escrow``/``has_operator``). Every other key (RPC URLs,
    deployment addresses, internal notes...) stays in EM.
  - ``enums.json`` — the members of the enums ``sync_enums.TARGETS`` renders,
    from ``mcp_server/models.py``.
  - ``retry.json`` — the retry decision table (``RETRY_TABLES``: status ->
    retry, exception -> retry, the tx-hash bodies never retried) from EM's
    ``cli/tests/test_retry.py``, where EM's CI proves its two other client
    implementations (sdk/python, the CLI) against it; exceptions by their
    dotted name. EM's code is not copied: ``tests/test_retry.py`` holds the
    SDK to the same table.
  - ``SOURCE`` — the commit and its date (``committed``, UTC), the sha256 of
    each upstream file at that commit and of each vendored file.
    ``tests/test_vendor_em.py`` checks the vendored hashes on every run, CI
    included; only ``--check``, which needs the EM clone, can confirm the
    commit, its date and the upstream hashes.

``--check`` answers two questions, each on its own line, and exits 1 if
either is no:

  1. Is ``vendor/em/`` exactly what SOURCE's commit yields? (the pin)
  2. Does EM's newer ``--upstream`` ref (default ``origin/main``: fetch the
     clone first) yield the same vendored bytes? A newer commit is not drift
     by itself — most EM commits change nothing the SDK mirrors — so this
     compares the bytes ``vendor/em/`` would get, not the commit or the
     upstream module's sha256. A ref that does not contain SOURCE's commit
     (a clone that was not fetched) is no answer: it says so and exits 1,
     instead of proposing to vendor an older commit.

It prints file names and commits, never EM data: EM is private, and the
output of a scheduled run ends up in a log. ``--public-log`` is for a log
anyone can read (``.github/workflows/em-drift.yml``, the weekly run: this
repository is public, so is its Actions log) and prints less: the vendored
file names and SOURCE's commit, both already published here, and nothing it
learnt from the clone — not the newer ref's commit or date, not an error
message or a traceback (a ``SyntaxError`` quotes the line of EM it failed
on): a failure is named by its exception class alone.
``scripts/upstream-check.plist.template`` is the same check scheduled on a
maintainer's machine, not installed.

Usage (from the repository root, after ``git fetch`` in the EM clone)::

    python scripts/vendor_em.py --em-repo ../execution-market               # vendor origin/main
    python scripts/vendor_em.py --em-repo ../execution-market --commit <sha>
    python scripts/vendor_em.py --em-repo ../execution-market --check       # exit 1 if vendor/em/ is not SOURCE's commit, or origin/main moved past it
    python scripts/vendor_em.py --em-repo ../execution-market --check --upstream <ref>
    python scripts/vendor_em.py --em-repo ../execution-market --check --public-log  # for a public log

then ``python scripts/sync_networks.py && python scripts/sync_enums.py &&
ruff format .`` to carry a change into the SDK (a change of ``retry.json`` is
a change of the retry policy: ``tests/test_retry.py`` names it).
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
SDK_ROOT = SCRIPT_DIR.parent
VENDOR_DIR = SDK_ROOT / "vendor" / "em"

REPO = "UltravioletaDAO/execution-market"

#: The ref ``--check`` compares the pin against: EM's main, once fetched.
DEFAULT_UPSTREAM = "origin/main"

#: Vendored file -> the EM module it is derived from (POSIX, as git names it).
UPSTREAM: dict[str, str] = {
    "networks.json": "mcp_server/integrations/x402/network_registry.py",
    "enums.json": "mcp_server/models.py",
    "retry.json": "cli/tests/test_retry.py",
}

#: The only keys copied out of NETWORK_CONFIG. ESCROW_FLAGS become booleans.
NETWORK_KEYS = ("chain_id", "network_type", "tokens", "escrow", "operator")
TOKEN_KEYS = ("address", "name", "decimals")
ESCROW_FLAGS = ("escrow", "operator")
#: The decision table of EM's retry equivalence suite, by its names there.
RETRY_TABLES = ("STATUS_TABLE", "EXCEPTION_TABLE", "TX_HASH_BODIES")

SOURCE_HEADER = (
    "# Derived from Execution Market (private) at `commit`, dated `committed` (UTC):",
    "# NOT a copy of its files, only the fields the tests read (scripts/vendor_em.py",
    "# says which). tests/test_vendor_em.py checks each vendored sha256; `<file> from`",
    "# is the sha256 of the upstream file at `commit` (scripts/vendor_em.py --check).",
    "# To update: python scripts/vendor_em.py --em-repo <EM clone> [--commit <sha>],",
    "# then python scripts/sync_networks.py && python scripts/sync_enums.py && ruff format .",
)


def parse_network_config(source: str) -> dict[str, Any]:
    """``NETWORK_CONFIG`` out of the backend module's text, without importing
    it (importing pulls the x402 package, which reads env vars at import)."""
    tree = ast.parse(source)
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target == "NETWORK_CONFIG" and node.value is not None:
            return ast.literal_eval(node.value)
    raise SystemExit(f"NETWORK_CONFIG not found in {UPSTREAM['networks.json']}")


def parse_enums(source: str) -> dict[str, dict[str, str]]:
    """Every ``class X(str, Enum)`` out of the backend module's text, without
    importing it (the backend package pulls env-dependent imports)."""
    tree = ast.parse(source)
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


def _table(node: ast.expr, name: str) -> Any:
    """A literal, with a name (``httpx.ConnectError``) as its dotted text."""
    if isinstance(node, (ast.Name, ast.Attribute)):
        return ast.unparse(node)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_table(element, name) for element in node.elts]
    if isinstance(node, ast.Dict) and None not in node.keys:
        return {
            _table(key, name): _table(value, name)
            for key, value in zip(node.keys, node.values)
        }
    try:
        return ast.literal_eval(node)
    except ValueError:
        raise SystemExit(f"{name} in {UPSTREAM['retry.json']} is not plain data")


def parse_retry_tables(source: str) -> dict[str, Any]:
    """``RETRY_TABLES`` out of EM's retry suite, without importing it (it
    imports EM's sdk/python and CLI), in ``RETRY_TABLES`` order."""
    tables: dict[str, Any] = {}
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in RETRY_TABLES
        ):
            name = node.targets[0].id
            tables[name] = _table(node.value, name)
    missing = [name for name in RETRY_TABLES if name not in tables]
    if missing:
        raise SystemExit(f"{missing} not found in {UPSTREAM['retry.json']}")
    return {name: tables[name] for name in RETRY_TABLES}


def enum_names() -> tuple[str, ...]:
    """The enums the SDK renders: every one ``sync_enums.TARGETS`` names."""
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from sync_enums import TARGETS
    finally:
        sys.path.pop(0)
    return tuple(dict.fromkeys(e for t in TARGETS.values() for e in t.enums))


def project_networks(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """NETWORK_CONFIG reduced to NETWORK_KEYS / TOKEN_KEYS, as upstream has
    them (a key upstream omits stays omitted), escrow flags as booleans."""
    projected: dict[str, dict[str, Any]] = {}
    for name, cfg in config.items():
        net: dict[str, Any] = {}
        for key in NETWORK_KEYS:
            if key not in cfg:
                continue
            if key in ESCROW_FLAGS:
                net[key] = bool(cfg[key])
            elif key == "tokens":
                net[key] = {
                    symbol: {k: token[k] for k in TOKEN_KEYS if k in token}
                    for symbol, token in cfg[key].items()
                }
            else:
                net[key] = cfg[key]
        projected[name] = net
    return projected


def project_enums(enums: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    missing = [name for name in enum_names() if name not in enums]
    if missing:
        raise SystemExit(f"{missing} not found in {UPSTREAM['enums.json']}")
    return {name: enums[name] for name in enum_names()}


def render_json(data: Any) -> bytes:
    """ASCII, LF, trailing newline: the same bytes on every OS."""
    return (json.dumps(data, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_source(
    commit: str,
    committed: str,
    upstream: dict[str, bytes],
    vendored: dict[str, bytes],
) -> bytes:
    lines = [
        *SOURCE_HEADER,
        f"repo: {REPO}",
        f"commit: {commit}",
        f"committed: {committed}",
    ]
    for name, path in UPSTREAM.items():
        lines.append(f"{name}: sha256={sha256(vendored[name])}")
        lines.append(f"{name} from: {path} sha256={sha256(upstream[name])}")
    return ("\n".join(lines) + "\n").encode("ascii")


def parse_source(text: str) -> dict[str, str]:
    return dict(
        line.split(": ", 1)
        for line in text.splitlines()
        if line and not line.startswith("#")
    )


def _git(em_repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(em_repo), *args], check=True, capture_output=True
        ).stdout
    except FileNotFoundError:
        raise SystemExit("git not found on PATH: the EM clone is read with git")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(f"git {' '.join(args)} failed in {em_repo}: {stderr}")


def resolve_commit(em_repo: Path, ref: str) -> str:
    """The full sha of ``ref`` in the EM clone (a ref moves; SOURCE must not)."""
    return _git(em_repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()


def build(em_repo: Path, commit: str) -> dict[str, bytes]:
    """Every file of ``vendor/em/`` (name -> bytes) for ``commit``."""
    upstream = {
        name: _git(em_repo, "show", f"{commit}:{path}")
        for name, path in UPSTREAM.items()
    }
    networks = parse_network_config(upstream["networks.json"].decode("utf-8"))
    enums = parse_enums(upstream["enums.json"].decode("utf-8"))
    files = {
        "networks.json": render_json(project_networks(networks)),
        "enums.json": render_json(project_enums(enums)),
        "retry.json": render_json(
            parse_retry_tables(upstream["retry.json"].decode("utf-8"))
        ),
    }
    files["SOURCE"] = render_source(
        commit, committed_at(em_repo, commit), upstream, files
    )
    return files


def check(em_repo: Path, vendor_dir: Path = VENDOR_DIR) -> list[str]:
    """What in ``vendor_dir`` differs from a fresh build at SOURCE's commit."""
    commit = parse_source((vendor_dir / "SOURCE").read_text(encoding="utf-8"))["commit"]
    expected = build(em_repo, resolve_commit(em_repo, commit))
    return [
        name
        for name, data in expected.items()
        if not (vendor_dir / name).is_file() or (vendor_dir / name).read_bytes() != data
    ]


def check_upstream(
    em_repo: Path, ref: str, vendor_dir: Path = VENDOR_DIR
) -> tuple[str, list[str]]:
    """``(sha of ref, vendored files a re-vendor at ref would change)``.

    Compared on the bytes of the data files, not on the commit or the
    upstream module's sha256: a commit that touches only what is never
    copied (an RPC URL, a comment, another module) yields the same bytes and
    is not drift. SOURCE is left out — its commit line differs for every
    newer ref by construction.
    """
    commit = resolve_commit(em_repo, ref)
    expected = build(em_repo, commit)
    return commit, [
        name
        for name in UPSTREAM
        if not (vendor_dir / name).is_file()
        or (vendor_dir / name).read_bytes() != expected[name]
    ]


def contains(em_repo: Path, ancestor: str, commit: str) -> bool:
    """Whether ``commit`` has ``ancestor`` in its history."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(em_repo),
                "merge-base",
                "--is-ancestor",
                ancestor,
                commit,
            ],
            capture_output=True,
        )
    except FileNotFoundError:
        raise SystemExit("git not found on PATH: the EM clone is read with git")
    if proc.returncode not in (0, 1):
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(f"git merge-base --is-ancestor failed in {em_repo}: {stderr}")
    return proc.returncode == 0


def committed_at(em_repo: Path, commit: str) -> str:
    """The commit's committer date, UTC, ISO 8601 (``2026-10-12T12:35:30+00:00``):
    SOURCE's ``committed`` and how fresh a compared ref was. From the epoch
    (``%ct``), not ``%cI``: git writes a UTC ``%cI`` as ``Z`` or as ``+00:00``
    depending on its version, and SOURCE's bytes must not depend on it."""
    epoch = _git(em_repo, "log", "-1", "--format=%ct", commit).decode().strip()
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()


def run_check(
    em_repo: Path,
    ref: str,
    say: Callable[[str], None],
    public: bool = False,
    vendor_dir: Path = VENDOR_DIR,
) -> int:
    """``--check``: both questions, a line each (and a ``Fix:`` line under a
    no) through ``say``; 0 is two yeses. ``public`` leaves out what only the
    clone knows (``ref``'s commit and date) and the clone's path."""
    pinned = parse_source((vendor_dir / "SOURCE").read_text(encoding="utf-8"))
    clone = "<EM clone>" if public else em_repo
    status = 0
    drifted = check(em_repo, vendor_dir)
    if drifted:
        say(f"DRIFT — vendor/em/ is not what SOURCE's commit yields: {drifted}")
        say(
            f"Fix: python scripts/vendor_em.py --em-repo {clone} "
            f"--commit {pinned['commit']} (same pin), "
            "or --commit <newer sha> to move it"
        )
        status = 1
    else:
        say(
            f"OK — vendor/em/ is exactly what SOURCE's commit "
            f"({pinned['commit'][:12]}) yields"
        )

    head = resolve_commit(em_repo, ref)
    seen = ref
    if not public:
        seen = f"{ref} = {head[:12]}, committed {committed_at(em_repo, head)}"
    if not contains(em_repo, pinned["commit"], head):
        say(
            f"STALE — {seen} does not contain SOURCE's commit "
            f"({pinned['commit'][:12]}): fetch the EM clone and run again"
        )
        return 1
    _, ahead = check_upstream(em_repo, head, vendor_dir)
    if ahead:
        say(f"UPSTREAM DRIFT — {seen}: a re-vendor would change {ahead}")
        commit = "<the sha --check names on a clone>" if public else head
        say(
            f"Fix: python scripts/vendor_em.py --em-repo {clone} --commit {commit}, "
            "then python scripts/sync_networks.py && python scripts/sync_enums.py "
            "&& ruff format . (the parity tests name what the SDK must follow)"
        )
        status = 1
    else:
        say(f"OK — {seen}: same vendored bytes, nothing to re-vendor")
    return status


def public_check(em_repo: Path, ref: str, vendor_dir: Path = VENDOR_DIR) -> int:
    """``--check --public-log``: ``run_check(public=True)``, and its lines are
    all that reaches the output. Anything else written while it runs (a
    warning about EM's code, say) is dropped, and a failure is named by its
    exception class, not its message: a ``SyntaxError`` quotes the EM line it
    failed on, and git's stderr is not this script's to vouch for."""
    out = sys.stdout

    def say(line: str) -> None:
        print(line, file=out, flush=True)

    dropped = io.StringIO()
    try:
        with contextlib.redirect_stdout(dropped), contextlib.redirect_stderr(dropped):
            return run_check(em_repo, ref, say, public=True, vendor_dir=vendor_dir)
    except (Exception, SystemExit) as exc:
        say(
            f"ERROR — the check did not finish ({type(exc).__name__}); its message "
            "is left out of a public log, it may quote Execution Market. Run it "
            "on a clone: python scripts/vendor_em.py --em-repo <EM clone> --check"
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--em-repo", type=Path, required=True, help="EM clone")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--commit", help="ref to vendor (default: origin/main)")
    mode.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if vendor/em/ is not what SOURCE's commit yields, or if "
        "the --upstream ref yields other bytes",
    )
    parser.add_argument(
        "--upstream",
        metavar="REF",
        help=f"with --check: the newer EM ref to compare (default {DEFAULT_UPSTREAM}"
        "; fetch the clone first)",
    )
    parser.add_argument(
        "--public-log",
        action="store_true",
        help="with --check: print only what is already public (vendored file "
        "names, SOURCE's commit), for a log anyone can read",
    )
    args = parser.parse_args()
    if args.upstream is not None and not args.check:
        parser.error("--upstream is only meaningful with --check")
    if args.public_log and not args.check:
        parser.error("--public-log is only meaningful with --check")

    if args.check:
        ref = args.upstream or DEFAULT_UPSTREAM
        if args.public_log:
            return public_check(args.em_repo, ref)
        return run_check(args.em_repo, ref, print)

    commit = resolve_commit(args.em_repo, args.commit or "origin/main")
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    for name, data in build(args.em_repo, commit).items():
        (VENDOR_DIR / name).write_bytes(data)
    print(
        f"Vendored {REPO}@{commit[:12]} into vendor/em/. Now run: "
        "python scripts/sync_networks.py && python scripts/sync_enums.py "
        "&& ruff format ."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
