"""The vendored Execution Market data (``vendor/em/``) and its extractor.

``tests/test_networks_sync.py`` and ``tests/test_enums_sync.py`` compare the
SDK against ``vendor/em/``; this file checks that ``vendor/em/`` is what
``vendor/em/SOURCE`` says (one sha256 per file), that it carries only the
fields the SDK reads (EM is private, this repository is public), and that
``scripts/vendor_em.py`` derives it from a COMMIT of an EM clone. The
extractor runs against a throwaway git repository with a fake backend: no
network, no EM clone needed.
"""

import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]
VENDOR = SDK_ROOT / "vendor" / "em"
VENDOR_SCRIPT = SDK_ROOT / "scripts" / "vendor_em.py"


def _load_vendor_module():
    spec = importlib.util.spec_from_file_location("vendor_em", VENDOR_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vendor = _load_vendor_module()


def _source() -> dict:
    return vendor.parse_source((VENDOR / "SOURCE").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The vendored copy is what SOURCE says
# ---------------------------------------------------------------------------


def test_source_names_the_repo_a_full_commit_and_its_utc_date():
    fields = _source()
    assert fields["repo"] == "UltravioletaDAO/execution-market"
    assert re.fullmatch(r"[0-9a-f]{40}", fields["commit"])
    committed = datetime.fromisoformat(fields["committed"])
    assert committed.utcoffset() == timedelta(0)
    assert committed.isoformat() == fields["committed"]  # one spelling, not `Z`


def test_each_vendored_file_has_the_sha256_of_source():
    fields = _source()
    on_disk = sorted(p.name for p in VENDOR.iterdir() if p.name != "SOURCE")
    assert on_disk == sorted(vendor.UPSTREAM), (
        f"vendor/em/ holds {on_disk}; SOURCE hashes {sorted(vendor.UPSTREAM)}"
    )
    for name in vendor.UPSTREAM:
        digest = hashlib.sha256((VENDOR / name).read_bytes()).hexdigest()
        assert fields[name] == f"sha256={digest}", (
            f"vendor/em/{name} is not the file SOURCE hashes — re-vendor with "
            "python scripts/vendor_em.py --em-repo <EM clone>"
        )


def test_source_names_the_upstream_module_of_each_file():
    fields = _source()
    for name, path in vendor.UPSTREAM.items():
        upstream_path, digest = fields[f"{name} from"].split(" sha256=")
        assert upstream_path == path
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_vendored_copy_holds_only_the_fields_the_sdk_reads():
    """Literal allowlists on purpose: widening what is copied out of a
    private repository has to change this test too."""
    networks = json.loads((VENDOR / "networks.json").read_text(encoding="utf-8"))
    assert networks
    for name, net in networks.items():
        assert set(net) <= {"chain_id", "network_type", "tokens", "escrow", "operator"}
        for flag in ("escrow", "operator"):
            assert isinstance(net.get(flag, False), bool), (name, flag)
        for symbol, token in net["tokens"].items():
            assert set(token) <= {"address", "name", "decimals"}, (name, symbol)
    enums = json.loads((VENDOR / "enums.json").read_text(encoding="utf-8"))
    assert list(enums) == [
        "TaskStatus",
        "TaskCategory",
        "EvidenceType",
        "TargetExecutorType",
    ]
    retry = json.loads((VENDOR / "retry.json").read_text(encoding="utf-8"))
    assert list(retry) == ["STATUS_TABLE", "EXCEPTION_TABLE", "TX_HASH_BODIES"]
    for status, should_retry in retry["STATUS_TABLE"]:
        assert type(status) is int and type(should_retry) is bool, status
    for name, should_retry in retry["EXCEPTION_TABLE"]:
        assert re.fullmatch(r"httpx\.[A-Za-z]+", name), name
        assert type(should_retry) is bool, name
    assert all(type(body) is dict for body in retry["TX_HASH_BODIES"])


def _no_git_env() -> dict:
    """os.environ without GIT_*: a hook (pre-push runs this suite) exports
    GIT_DIR & co, and git would act on that repository instead."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def test_vendored_files_keep_their_bytes_on_checkout():
    """The sha256 is of the bytes: a CRLF checkout (core.autocrlf on
    Windows) would break it, so git must not touch their line endings."""
    rules = (SDK_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "vendor/em/** -text" in rules
    if not (SDK_ROOT / ".git").exists():
        return  # an sdist: no git to ask, the rule above is all there is
    # What git will actually apply: a later line (`*.json text eol=crlf`)
    # must not override the rule.
    paths = [f"vendor/em/{name}" for name in (*vendor.UPSTREAM, "SOURCE")]
    applied = subprocess.run(
        ["git", "check-attr", "text", "eol", "--", *paths],
        cwd=SDK_ROOT,
        env=_no_git_env(),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert applied == [
        line
        for path in paths
        for line in (f"{path}: text: unset", f"{path}: eol: unspecified")
    ]


# ---------------------------------------------------------------------------
# scripts/vendor_em.py against a fake EM clone
# ---------------------------------------------------------------------------

FAKE_REGISTRY = '''"""Fake backend registry: only the shape matters."""

from typing import Any, Dict

NETWORK_CONFIG: Dict[str, Dict[str, Any]] = {
    "base": {
        "chain_id": 8453,
        "network_type": "evm",
        "rpc_url": "https://rpc.example.invalid/KEY",
        "tokens": {
            "USDC": {"address": "0xA", "name": "USD Coin", "version": "2", "decimals": 6},
            "EURC": {"address": "0xB", "name": "EURC \u20ac", "version": "2"},
        },
        "escrow": "0xE",
        "deployer": "0xF",
        "operator": "0x0",
        "infra": {"collector": "0xC"},
        "internal_note": "not for the public repository",
    },
    "solana": {
        "network_type": "svm",
        "tokens": {"USDC": {"address": "So1", "name": "USD Coin", "decimals": 6}},
    },
    "base-sepolia": {
        "chain_id": 84532,
        "network_type": "evm",
        "tokens": {"USDC": {"address": "0xT", "name": "USDC", "decimals": 6}},
        "escrow": "0xE2",
        "operator": None,
    },
}
'''

FAKE_MODELS = '''"""Fake backend models: parsed, never imported."""

from enum import Enum

import module_that_does_not_exist  # importing this file would fail


class TaskStatus(str, Enum):
    PUBLISHED = "published"
    ACCEPTED = "accepted"


class TaskCategory(str, Enum):
    SIMPLE_ACTION = "simple_action"


class EvidenceType(str, Enum):
    PHOTO = "photo"


class TargetExecutorType(str, Enum):
    HUMAN = "human"
    ANY = "any"


class ApplicationStatus(str, Enum):
    PENDING = "pending"
'''

FAKE_RETRY = '''"""Fake EM retry suite: parsed, never imported."""

import httpx
from execution_market._signer import with_backoff  # importing this would fail

REPO_ROOT = "not a table"

STATUS_TABLE = [
    # (status, should_retry)
    (400, False),
    (503, True),
]

EXCEPTION_TABLE = [
    (httpx.ConnectError, True),
]

TX_HASH_BODIES = [
    {"transaction": {"hash": "0xabc"}},
    {"txHash": "0xabc"},
]


def test_cli_status_table():
    assert with_backoff
'''

EXPECTED_NETWORKS = {
    "base": {
        "chain_id": 8453,
        "network_type": "evm",
        "tokens": {
            "USDC": {"address": "0xA", "name": "USD Coin", "decimals": 6},
            "EURC": {"address": "0xB", "name": "EURC \u20ac"},
        },
        "escrow": True,
        "operator": True,
    },
    "solana": {
        "network_type": "svm",
        "tokens": {"USDC": {"address": "So1", "name": "USD Coin", "decimals": 6}},
    },
    "base-sepolia": {
        "chain_id": 84532,
        "network_type": "evm",
        "tokens": {"USDC": {"address": "0xT", "name": "USDC", "decimals": 6}},
        "escrow": True,
        "operator": False,
    },
}

EXPECTED_ENUMS = {
    "TaskStatus": {"PUBLISHED": "published", "ACCEPTED": "accepted"},
    "TaskCategory": {"SIMPLE_ACTION": "simple_action"},
    "EvidenceType": {"PHOTO": "photo"},
    "TargetExecutorType": {"HUMAN": "human", "ANY": "any"},
}

EXPECTED_RETRY = {
    "STATUS_TABLE": [[400, False], [503, True]],
    "EXCEPTION_TABLE": [["httpx.ConnectError", True]],
    "TX_HASH_BODIES": [{"transaction": {"hash": "0xabc"}}, {"txHash": "0xabc"}],
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def em_clone(tmp_path, monkeypatch):
    """A git repository shaped like EM, one commit. Returns (path, sha)."""
    # A hook (pre-push runs this suite) exports GIT_DIR & co: git would act
    # on THIS repository instead of the throwaway one (see _no_git_env).
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    # Fixed dates, the committer's after the author's and off UTC: SOURCE's
    # `committed` is the committer date, in UTC.
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00+0200")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-02T03:04:05+0200")
    repo = tmp_path / "em"
    registry = repo / "mcp_server" / "integrations" / "x402" / "network_registry.py"
    registry.parent.mkdir(parents=True)
    registry.write_bytes(FAKE_REGISTRY.encode("utf-8"))
    (repo / "mcp_server" / "models.py").write_bytes(FAKE_MODELS.encode("utf-8"))
    (repo / "cli" / "tests").mkdir(parents=True)
    (repo / "cli" / "tests" / "test_retry.py").write_bytes(FAKE_RETRY.encode("utf-8"))
    (tmp_path / "no-hooks").mkdir()
    _git(repo, "init", "-q")
    _git(repo, "add", "mcp_server", "cli")
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        f"core.hooksPath={tmp_path / 'no-hooks'}",
        "commit",
        "-q",
        "-m",
        "fake backend",
    )
    return repo, _git(repo, "rev-parse", "HEAD")


def test_build_copies_only_the_allowlisted_network_fields(em_clone):
    repo, sha = em_clone
    networks = json.loads(vendor.build(repo, sha)["networks.json"])
    assert networks == EXPECTED_NETWORKS
    assert list(networks) == ["base", "solana", "base-sepolia"]  # backend order


def test_build_copies_only_the_enums_the_sdk_renders(em_clone):
    repo, sha = em_clone
    enums = json.loads(vendor.build(repo, sha)["enums.json"])
    assert enums == EXPECTED_ENUMS
    assert list(enums["TaskStatus"]) == ["PUBLISHED", "ACCEPTED"]  # backend order


def test_build_copies_only_the_retry_decision_table(em_clone):
    repo, sha = em_clone
    retry = json.loads(vendor.build(repo, sha)["retry.json"])
    assert retry == EXPECTED_RETRY
    assert list(retry) == ["STATUS_TABLE", "EXCEPTION_TABLE", "TX_HASH_BODIES"]


def test_source_dates_the_commit_with_its_committer_date_in_utc(em_clone):
    """The committer date (2026-01-02T03:04:05+0200), not the author's, and in
    UTC: git writes a UTC ``%cI`` as ``Z`` or ``+00:00`` depending on its
    version, and --check compares SOURCE's bytes across machines."""
    repo, sha = em_clone
    fields = vendor.parse_source(vendor.build(repo, sha)["SOURCE"].decode())
    assert fields["committed"] == "2026-01-02T01:04:05+00:00"


def test_build_reads_the_commit_not_the_working_tree(em_clone):
    repo, sha = em_clone
    models = repo / "mcp_server" / "models.py"
    committed = models.read_bytes()
    models.write_bytes(committed.replace(b'"photo"', b'"uncommitted"'))
    files = vendor.build(repo, sha)
    assert json.loads(files["enums.json"])["EvidenceType"] == {"PHOTO": "photo"}
    fields = vendor.parse_source(files["SOURCE"].decode())
    assert fields["enums.json from"] == (
        f"mcp_server/models.py sha256={hashlib.sha256(committed).hexdigest()}"
    )


def test_source_pins_the_full_commit_and_each_hash(em_clone):
    repo, sha = em_clone
    files = vendor.build(repo, vendor.resolve_commit(repo, "HEAD"))
    fields = vendor.parse_source(files["SOURCE"].decode())
    assert fields["repo"] == "UltravioletaDAO/execution-market"
    assert fields["commit"] == sha and len(sha) == 40
    for name in vendor.UPSTREAM:
        assert fields[name] == f"sha256={hashlib.sha256(files[name]).hexdigest()}"
    assert fields["networks.json from"].startswith(
        "mcp_server/integrations/x402/network_registry.py sha256="
    )


def test_rendered_files_are_ascii_lf_and_end_in_a_newline(em_clone):
    repo, sha = em_clone
    for name, data in vendor.build(repo, sha).items():
        assert data.endswith(b"\n") and b"\r" not in data, name
        data.decode("ascii")


def test_check_passes_on_a_fresh_vendor_and_names_each_edit(em_clone, tmp_path):
    repo, sha = em_clone
    out = tmp_path / "vendor"
    out.mkdir()
    for name, data in vendor.build(repo, sha).items():
        (out / name).write_bytes(data)
    assert vendor.check(repo, out) == []

    source = out / "SOURCE"
    pinned = source.read_bytes()
    source.write_bytes(pinned.replace(b"models.py sha256=", b"models.py sha256=0"))
    assert vendor.check(repo, out) == ["SOURCE"]
    source.write_bytes(pinned)

    networks = out / "networks.json"
    networks.write_bytes(networks.read_bytes().replace(b"0xA", b"0xa"))
    (out / "enums.json").unlink()
    assert vendor.check(repo, out) == ["networks.json", "enums.json"]


def test_a_backend_without_an_enum_the_sdk_renders_is_refused():
    with pytest.raises(SystemExit, match="TargetExecutorType"):
        vendor.project_enums(
            {k: v for k, v in EXPECTED_ENUMS.items() if k != "TargetExecutorType"}
        )


def test_a_retry_suite_without_its_table_is_refused():
    with pytest.raises(SystemExit, match="EXCEPTION_TABLE"):
        vendor.parse_retry_tables("STATUS_TABLE = []\nTX_HASH_BODIES = []\n")


def test_a_retry_table_that_is_not_plain_data_is_refused():
    """A table built by code is not data: refused, not evaluated."""
    with pytest.raises(SystemExit, match="STATUS_TABLE"):
        vendor.parse_retry_tables(
            "STATUS_TABLE = build()\nEXCEPTION_TABLE = []\nTX_HASH_BODIES = []\n"
        )


def test_a_backend_without_network_config_is_refused():
    with pytest.raises(SystemExit, match="NETWORK_CONFIG not found"):
        vendor.parse_network_config("OTHER_CONFIG = {}\n")


def test_an_unknown_commit_is_refused(em_clone):
    repo, _ = em_clone
    with pytest.raises(SystemExit, match="git rev-parse"):
        vendor.resolve_commit(repo, "0" * 40)


def test_a_missing_git_is_named(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty directory: no git
    with pytest.raises(SystemExit, match="git not found"):
        vendor.resolve_commit(tmp_path, "HEAD")


def test_check_does_not_take_a_commit(tmp_path):
    """--check always rebuilds at SOURCE's commit; a --commit next to it
    would be silently ignored, so it is refused."""
    result = subprocess.run(
        [sys.executable, str(VENDOR_SCRIPT), "--em-repo", str(tmp_path)]
        + ["--check", "--commit", "HEAD"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "not allowed with" in result.stderr


# ---------------------------------------------------------------------------
# --check against Execution Market's newer commits (the periodic job)
# ---------------------------------------------------------------------------


def _commit(repo: Path, tmp_path: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        f"core.hooksPath={tmp_path / 'no-hooks'}",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _fetched(repo: Path, sha: str) -> None:
    """What ``git fetch origin`` leaves behind: ``origin/main`` at ``sha``."""
    _git(repo, "update-ref", "refs/remotes/origin/main", sha)


@pytest.fixture
def sdk_tree(em_clone, tmp_path):
    """This repository's two scripts in a throwaway SDK tree whose
    ``vendor/em/`` is vendored at the fake clone's first commit: SOURCE is
    intact, and ``--check`` runs as a maintainer runs it, as a process."""
    repo, sha = em_clone
    root = tmp_path / "sdk"
    (root / "scripts").mkdir(parents=True)
    for name in ("vendor_em.py", "sync_enums.py"):
        (root / "scripts" / name).write_bytes(
            (SDK_ROOT / "scripts" / name).read_bytes()
        )
    (root / "vendor" / "em").mkdir(parents=True)
    for name, data in vendor.build(repo, sha).items():
        (root / "vendor" / "em" / name).write_bytes(data)
    return root


def _check(
    root: Path, repo: Path, *extra: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "vendor_em.py")]
        + ["--em-repo", str(repo), "--check", *extra],
        capture_output=True,
        text=True,
        env={**_no_git_env(), **(env or {})},
    )


REGISTRY = Path("mcp_server/integrations/x402/network_registry.py")


def test_check_flags_an_upstream_that_changed_what_the_sdk_mirrors(
    em_clone, sdk_tree, tmp_path
):
    """SOURCE intact, EM's registry changed on origin/main: drift. Checking
    only SOURCE's commit, --check said OK here and stayed blind to EM."""
    repo, pinned = em_clone
    registry = repo / REGISTRY
    registry.write_bytes(registry.read_bytes().replace(b'"0xA"', b'"0xA2"'))
    head = _commit(repo, tmp_path, "the USDC address changes upstream")
    _fetched(repo, head)

    result = _check(sdk_tree, repo)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "networks.json" in result.stdout
    assert "enums.json" not in result.stdout
    assert head[:12] in result.stdout
    assert f"OK — vendor/em/ is exactly what SOURCE's commit ({pinned[:12]})" in (
        result.stdout
    )


def test_check_flags_an_enum_member_added_upstream(em_clone, sdk_tree, tmp_path):
    repo, _ = em_clone
    models = repo / "mcp_server" / "models.py"
    models.write_bytes(
        models.read_bytes().replace(
            b'    ACCEPTED = "accepted"\n',
            b'    ACCEPTED = "accepted"\n    EXPIRED = "expired"\n',
        )
    )
    _fetched(repo, _commit(repo, tmp_path, "a new task status"))

    result = _check(sdk_tree, repo)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "enums.json" in result.stdout
    assert "networks.json" not in result.stdout


def test_check_flags_a_retry_policy_changed_upstream(em_clone, sdk_tree, tmp_path):
    repo, _ = em_clone
    suite = repo / "cli" / "tests" / "test_retry.py"
    suite.write_bytes(suite.read_bytes().replace(b"(503, True)", b"(503, False)"))
    _fetched(repo, _commit(repo, tmp_path, "503 is no longer retried"))

    result = _check(sdk_tree, repo)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "a re-vendor would change ['retry.json']" in result.stdout


def test_check_is_green_when_upstream_yields_the_same_bytes(
    em_clone, sdk_tree, tmp_path
):
    """A newer EM commit is not drift by itself: EM commits every day and
    most commits change nothing the SDK mirrors. Here the registry changes a
    field that is never copied (the RPC URL) and a later commit touches
    another module: the vendored bytes are the same, so it is green."""
    repo, pinned = em_clone
    registry = repo / REGISTRY
    registry.write_bytes(
        registry.read_bytes().replace(b"rpc.example.invalid", b"rpc2.example.invalid")
    )
    _commit(repo, tmp_path, "an RPC URL changes")
    (repo / "mcp_server" / "other.py").write_bytes(b"X = 1\n")
    head = _commit(repo, tmp_path, "another module")
    _fetched(repo, head)

    result = _check(sdk_tree, repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert head[:12] in result.stdout
    assert head != pinned


def test_check_reads_the_ref_it_is_given(em_clone, sdk_tree, tmp_path):
    """--upstream names the ref; origin/main is only the default."""
    repo, pinned = em_clone
    _fetched(repo, pinned)
    registry = repo / REGISTRY
    registry.write_bytes(registry.read_bytes().replace(b'"0xA"', b'"0xA2"'))
    head = _commit(repo, tmp_path, "the USDC address changes upstream")

    assert _check(sdk_tree, repo).returncode == 0
    flagged = _check(sdk_tree, repo, "--upstream", head)
    assert flagged.returncode == 1, flagged.stdout + flagged.stderr
    assert "networks.json" in flagged.stdout


def test_check_names_a_vendored_file_that_is_gone_on_both_lines(em_clone, sdk_tree):
    repo, pinned = em_clone
    _fetched(repo, pinned)
    (sdk_tree / "vendor" / "em" / "enums.json").unlink()

    result = _check(sdk_tree, repo)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "DRIFT — vendor/em/ is not what SOURCE's commit yields: ['enums.json']" in (
        result.stdout
    )
    assert "a re-vendor would change ['enums.json']" in result.stdout


def test_check_against_a_ref_behind_source_is_not_drift(em_clone, sdk_tree, tmp_path):
    """A clone that was not fetched answers with an OLDER commit than the
    pin. That is no answer, and certainly not "re-vendor the older one"."""
    repo, older = em_clone
    registry = repo / REGISTRY
    registry.write_bytes(registry.read_bytes().replace(b'"0xA"', b'"0xA2"'))
    newer = _commit(repo, tmp_path, "EM moves on")
    for name, data in vendor.build(repo, newer).items():  # SOURCE pins `newer`
        (sdk_tree / "vendor" / "em" / name).write_bytes(data)
    _fetched(repo, older)  # origin/main never fetched past `older`

    result = _check(sdk_tree, repo)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"STALE — origin/main = {older[:12]}" in result.stdout
    assert "--commit" not in result.stdout
    assert "UPSTREAM DRIFT" not in result.stdout


def test_check_without_the_upstream_ref_is_not_green(em_clone, sdk_tree):
    """A clone that was never fetched has no origin/main: that is no answer,
    so it must not read as one."""
    repo, _ = em_clone

    result = _check(sdk_tree, repo)

    assert result.returncode != 0
    assert "origin/main" in result.stderr


def test_check_prints_no_execution_market_content(em_clone, sdk_tree, tmp_path):
    """EM is private: what --check prints (a log, a job's output) names
    files and commits, never the data that changed."""
    repo, _ = em_clone
    registry = repo / REGISTRY
    registry.write_bytes(registry.read_bytes().replace(b'"0xA"', b'"0xPRIVATEADDRESS"'))
    _fetched(repo, _commit(repo, tmp_path, "the USDC address changes upstream"))

    result = _check(sdk_tree, repo)

    assert result.returncode == 1
    assert "PRIVATEADDRESS" not in result.stdout + result.stderr


def test_upstream_is_only_meaningful_with_check(tmp_path):
    result = subprocess.run(
        [sys.executable, str(VENDOR_SCRIPT), "--em-repo", str(tmp_path)]
        + ["--upstream", "origin/main"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--upstream" in result.stderr


# ---------------------------------------------------------------------------
# The scheduled run (scripts/upstream-check.plist.template), not installed
# ---------------------------------------------------------------------------

JOB_TEMPLATE = SDK_ROOT / "scripts" / "upstream-check.plist.template"
JOB_PLACEHOLDERS = {"EM_CLONE", "PYTHON", "SDK_CHECKOUT", "HOME", "EVERY", "LOG"}


def _job(**values: str) -> dict:
    import plistlib

    text = JOB_TEMPLATE.read_text(encoding="utf-8")
    for name, value in values.items():
        text = text.replace("{{" + name + "}}", value)
    return plistlib.loads(text.encode("utf-8"))


def test_the_job_template_has_only_its_placeholders():
    """Nothing of one machine (a home path, a clone path) and nothing
    secret: what varies is a placeholder, and the set is fixed."""
    text = JOB_TEMPLATE.read_text(encoding="utf-8")
    assert set(re.findall(r"\{\{([A-Z_]+)\}\}", text)) == JOB_PLACEHOLDERS
    assert "/Users/" not in text and "C:\\" not in text
    plist = re.sub(r"<!--.*?-->", "", text, flags=re.S)  # the prose may say "secret"
    assert not re.search(r"token|secret|password|ghp_|github_pat_", plist, re.I)
    # No variable through which a credential could ride along.
    env = _job(EVERY="60")["EnvironmentVariables"]
    assert set(env) == {"HOME", "PATH"}


def test_the_job_fetches_then_checks_upstream_and_stops_if_the_fetch_fails():
    job = _job(
        EM_CLONE="/em",
        PYTHON="/py",
        SDK_CHECKOUT="/sdk",
        HOME="/home",
        EVERY="86400",
        LOG="/log",
    )
    shell, flag, command = job["ProgramArguments"]
    assert (shell, flag) == ("/bin/sh", "-c")
    clean, pull, fetch, check = command.split(" && ")
    # This checkout first: on main, without local edits (a stale checkout
    # reports drift main already fixed; an edited one checks the edits).
    assert clean == 'git -C "/sdk" diff --quiet HEAD --'
    assert pull == 'git -C "/sdk" pull --ff-only --quiet origin main'
    # The refspec is explicit: a clone configured without main still updates
    # origin/main, the ref the check compares.
    assert fetch == (
        'git -C "/em" fetch --quiet origin +refs/heads/main:refs/remotes/origin/main'
    )
    assert check == (
        'exec "/py" scripts/vendor_em.py --em-repo "/em" --check --upstream origin/main'
    )
    assert job["WorkingDirectory"] == "/sdk"
    assert job["StartInterval"] == 86400
    assert job["StandardOutPath"] == job["StandardErrorPath"] == "/log"


def test_the_job_runs_the_script_it_names(tmp_path):
    """The template calls flags the script accepts (argparse exits 2 on an
    unknown one): run the check part with a clone that does not exist."""
    job = _job(
        EM_CLONE=str(tmp_path / "no-clone"),
        PYTHON=sys.executable,
        SDK_CHECKOUT=str(SDK_ROOT),
        HOME=str(tmp_path),
        EVERY="60",
        LOG=str(tmp_path / "log"),
    )
    argv = shlex.split(job["ProgramArguments"][2].split(" && ")[-1])[1:]
    result = subprocess.run(
        argv, cwd=job["WorkingDirectory"], capture_output=True, text=True
    )
    assert result.returncode == 1, result.stderr
    assert "git rev-parse" in result.stderr


# ---------------------------------------------------------------------------
# --check --public-log: the weekly run's log is as public as this repository
# ---------------------------------------------------------------------------

_NAMES = r"\[(?:'(?:networks\.json|enums\.json|retry\.json|SOURCE)'(?:, )?)+\]"
#: Every line --public-log may print. Literal on purpose: what a public log
#: says about a private repository widens only by changing this too.
PUBLIC_LINES = (
    r"OK — vendor/em/ is exactly what SOURCE's commit \([0-9a-f]{12}\) yields",
    rf"DRIFT — vendor/em/ is not what SOURCE's commit yields: {_NAMES}",
    r"Fix: python scripts/vendor_em\.py --em-repo <EM clone> --commit [0-9a-f]{40}"
    r" \(same pin\), or --commit <newer sha> to move it",
    r"STALE — origin/main does not contain SOURCE's commit \([0-9a-f]{12}\):"
    r" fetch the EM clone and run again",
    rf"UPSTREAM DRIFT — origin/main: a re-vendor would change {_NAMES}",
    r"Fix: python scripts/vendor_em\.py --em-repo <EM clone> --commit <the sha"
    r" --check names on a clone>, then python scripts/sync_networks\.py && python"
    r" scripts/sync_enums\.py && ruff format \. \(the parity tests name what the"
    r" SDK must follow\)",
    r"OK — origin/main: same vendored bytes, nothing to re-vendor",
    r"ERROR — the check did not finish \([A-Za-z]+\); its message is left out of"
    r" a public log, it may quote Execution Market\. Run it on a clone: python"
    r" scripts/vendor_em\.py --em-repo <EM clone> --check",
    r"NETWORK — the check did not finish: Execution Market could not be reached"
    rf" \((?:{'|'.join(map(re.escape, vendor.TRANSPORT_CAUSES))})\); run it again",
)


def _public(result: subprocess.CompletedProcess, pinned: str) -> list[str]:
    """The lines of a --public-log run: nothing on stderr, each line one of
    PUBLIC_LINES, and the only commit named is SOURCE's (public already)."""
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    for line in lines:
        assert any(re.fullmatch(p, line) for p in PUBLIC_LINES), line
    for sha in re.findall(r"[0-9a-f]{7,}", result.stdout):
        assert pinned.startswith(sha), f"{sha} is not SOURCE's commit"
    return lines


def test_public_log_names_the_drift_not_the_commit_nor_its_data(
    em_clone, sdk_tree, tmp_path
):
    repo, pinned = em_clone
    registry = repo / REGISTRY
    registry.write_bytes(registry.read_bytes().replace(b'"0xA"', b'"0xPRIVATEADDRESS"'))
    head = _commit(repo, tmp_path, "the USDC address changes upstream")
    _fetched(repo, head)

    plain = _check(sdk_tree, repo)
    result = _check(sdk_tree, repo, "--public-log")

    assert plain.returncode == result.returncode == 1
    assert f"{head[:12]}, committed " in plain.stdout  # on a clone, it says
    lines = _public(result, pinned)
    assert lines[:2] == [
        f"OK — vendor/em/ is exactly what SOURCE's commit ({pinned[:12]}) yields",
        "UPSTREAM DRIFT — origin/main: a re-vendor would change ['networks.json']",
    ]
    assert lines[2].startswith("Fix: ") and len(lines) == 3
    assert "PRIVATEADDRESS" not in result.stdout


def test_public_log_is_green_when_upstream_yields_the_same_bytes(
    em_clone, sdk_tree, tmp_path
):
    repo, pinned = em_clone
    registry = repo / REGISTRY
    registry.write_bytes(
        registry.read_bytes().replace(b"rpc.example.invalid", b"rpc2.example.invalid")
    )
    _fetched(repo, _commit(repo, tmp_path, "an RPC URL changes"))

    result = _check(sdk_tree, repo, "--public-log")

    assert result.returncode == 0, result.stdout
    assert _public(result, pinned) == [
        f"OK — vendor/em/ is exactly what SOURCE's commit ({pinned[:12]}) yields",
        "OK — origin/main: same vendored bytes, nothing to re-vendor",
    ]


def test_public_log_names_a_pin_drift_without_the_clone(em_clone, sdk_tree):
    repo, pinned = em_clone
    _fetched(repo, pinned)
    (sdk_tree / "vendor" / "em" / "enums.json").unlink()

    result = _check(sdk_tree, repo, "--public-log")

    assert result.returncode == 1
    lines = _public(result, pinned)
    assert lines[0] == (
        "DRIFT — vendor/em/ is not what SOURCE's commit yields: ['enums.json']"
    )
    assert f"--commit {pinned} (same pin)" in lines[1]
    assert lines[2] == (
        "UPSTREAM DRIFT — origin/main: a re-vendor would change ['enums.json']"
    )
    assert str(repo) not in result.stdout


def test_public_log_on_a_stale_clone_names_only_the_pin(em_clone, sdk_tree, tmp_path):
    repo, older = em_clone
    registry = repo / REGISTRY
    registry.write_bytes(registry.read_bytes().replace(b'"0xA"', b'"0xA2"'))
    newer = _commit(repo, tmp_path, "EM moves on")
    for name, data in vendor.build(repo, newer).items():  # SOURCE pins `newer`
        (sdk_tree / "vendor" / "em" / name).write_bytes(data)
    _fetched(repo, older)

    result = _check(sdk_tree, repo, "--public-log")

    assert result.returncode == 1
    assert _public(result, newer)[1] == (
        f"STALE — origin/main does not contain SOURCE's commit ({newer[:12]}):"
        " fetch the EM clone and run again"
    )


def test_public_log_does_not_quote_the_em_line_it_failed_on(
    em_clone, sdk_tree, tmp_path
):
    """A SyntaxError's traceback prints the line it failed on: on a clone
    that is fine, in a public log it is EM's code."""
    repo, pinned = em_clone
    models = repo / "mcp_server" / "models.py"
    models.write_bytes(models.read_bytes() + b"PRIVATE_CANARY = (\n")
    _fetched(repo, _commit(repo, tmp_path, "a module that does not parse"))

    plain = _check(sdk_tree, repo)
    result = _check(sdk_tree, repo, "--public-log")

    assert "PRIVATE_CANARY" in plain.stderr  # the hazard
    assert result.returncode == 1
    assert _public(result, pinned)[1].startswith(
        "ERROR — the check did not finish (SyntaxError); "
    )
    assert "PRIVATE_CANARY" not in result.stdout


def test_public_log_does_not_relay_git_errors(em_clone, sdk_tree):
    """A clone without origin/main: git's stderr is not this script's to
    vouch for, so --public-log names the failure and leaves the message."""
    repo, pinned = em_clone

    plain = _check(sdk_tree, repo)
    result = _check(sdk_tree, repo, "--public-log")

    assert "git rev-parse" in plain.stderr
    assert result.returncode == 1
    assert _public(result, pinned)[1].startswith(
        "ERROR — the check did not finish (GitFailed); "
    )


def test_public_log_drops_whatever_else_the_check_writes(
    em_clone, sdk_tree, capsys, monkeypatch
):
    """Only run_check's lines reach the output: a print or a warning written
    along the way (today none; tomorrow a library's) is dropped."""
    repo, pinned = em_clone
    _fetched(repo, pinned)
    build = vendor.build

    def noisy(em_repo, commit):
        print("PRIVATE_CANARY on stdout")
        print("PRIVATE_CANARY on stderr", file=sys.stderr)
        return build(em_repo, commit)

    monkeypatch.setattr(vendor, "build", noisy)

    status = vendor.public_check(repo, "origin/main", sdk_tree / "vendor" / "em")

    out, err = capsys.readouterr()
    assert status == 0
    assert err == ""
    assert out.splitlines() == [
        f"OK — vendor/em/ is exactly what SOURCE's commit ({pinned[:12]}) yields",
        "OK — origin/main: same vendored bytes, nothing to re-vendor",
    ]


def test_public_log_is_only_meaningful_with_check(tmp_path):
    result = subprocess.run(
        [sys.executable, str(VENDOR_SCRIPT), "--em-repo", str(tmp_path)]
        + ["--public-log"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--public-log" in result.stderr


posix = pytest.mark.skipif(
    sys.platform == "win32", reason="a sh script stands in for ssh or git"
)
GITHUB_EM = "git@github.com:UltravioletaDAO/execution-market.git"


def _fake_ssh(tmp_path: Path, *said: str) -> str:
    """A GIT_SSH_COMMAND that never connects: it says ``said`` and a canary
    on stderr and fails as ssh does (255). The network stays closed."""
    script = tmp_path / "fake-ssh"
    lines = "".join(
        f"echo {shlex.quote(line)} >&2\n" for line in (*said, "PRIVATE_CANARY")
    )
    script.write_text(f"#!/bin/sh\n{lines}exit 255\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


@pytest.fixture
def partial_clone(em_clone, tmp_path):
    """What the weekly run reads: the fake EM's main without blobs (git show
    fetches them), with GitHub's EM as its remote, so that fetch goes through
    GIT_SSH_COMMAND: a test's fake ssh, never the network."""
    repo, sha = em_clone
    _git(repo, "branch", "-M", "main")
    _git(repo, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "partial"
    _git(
        tmp_path,
        "clone",
        "-q",
        "--no-checkout",
        "--filter=blob:none",
        repo.as_uri(),
        str(clone),
    )
    _git(clone, "remote", "set-url", "origin", GITHUB_EM)
    return clone, sha


@posix
def test_public_log_names_a_network_failure_not_drift(
    partial_clone, sdk_tree, tmp_path
):
    """EM cannot be reached when git show fetches what it reads: the check
    did not run. That is NETWORK and 75, not ERROR and 1, so the weekly run
    warns instead of reading as drift. Plain --check is unchanged."""
    clone, pinned = partial_clone
    ssh = {
        "GIT_SSH_COMMAND": _fake_ssh(
            tmp_path, "ssh: connect to host github.com port 22: Connection timed out"
        )
    }

    plain = _check(sdk_tree, clone, env=ssh)
    result = _check(sdk_tree, clone, "--public-log", env=ssh)

    assert plain.returncode == 1 and "Connection timed out" in plain.stderr
    assert result.returncode == vendor.EXIT_NETWORK == 75
    assert _public(result, pinned) == [
        "NETWORK — the check did not finish: Execution Market could not be reached"
        " (Connection timed out); run it again"
    ]


@posix
def test_public_log_keeps_a_drift_found_before_the_network_red(
    em_clone, sdk_tree, tmp_path
):
    """The blobs come in two rounds: SOURCE's commit, then origin/main. A
    DRIFT the first round found stays red (1) when the second cannot reach
    EM: NETWORK is not drift only while no drift was seen."""
    repo, pinned = em_clone
    enums = sdk_tree / "vendor" / "em" / "enums.json"
    enums.write_bytes(enums.read_bytes() + b" ")  # vendor/em/ edited by hand
    registry = repo / REGISTRY
    registry.write_bytes(registry.read_bytes().replace(b'"0xA"', b'"0xA2"'))
    _commit(repo, tmp_path, "EM's main moves: origin/main needs a new blob")
    _git(repo, "branch", "-M", "main")
    _git(repo, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "partial"
    _git(
        tmp_path,
        "clone",
        "-q",
        "--no-checkout",
        "--filter=blob:none",
        repo.as_uri(),
        str(clone),
    )
    for path in vendor.UPSTREAM.values():  # the first round reaches EM
        _git(clone, "show", f"{pinned}:{path}")
    _git(clone, "remote", "set-url", "origin", GITHUB_EM)
    ssh = {"GIT_SSH_COMMAND": _fake_ssh(tmp_path, "send disconnect: Broken pipe")}

    result = _check(sdk_tree, clone, "--public-log", env=ssh)

    assert result.returncode == 1
    lines = _public(result, pinned)
    assert lines[0] == (
        "DRIFT — vendor/em/ is not what SOURCE's commit yields: ['enums.json']"
    )
    assert lines[2] == (
        "NETWORK — the check did not finish: Execution Market could not be reached"
        " (Broken pipe); run it again"
    )


@posix
@pytest.mark.parametrize(
    "said",
    [
        # A refused key wins over the connection closing after it.
        (
            "git@github.com: Permission denied (publickey).",
            "Connection closed by 192.0.2.1 port 22",
        ),
        # GitHub answered (a SOURCE commit EM no longer has, measured against
        # GitHub): that is STALE's business, never NETWORK, even if git then
        # says the remote hung up. Any answer, not only upload-pack's.
        (
            f"fatal: remote error: upload-pack: not our ref {'0' * 40}",
            "fatal: the remote end hung up unexpectedly",
        ),
        (
            "fatal: remote error: access denied or repository not exported",
            "fatal: the remote end hung up unexpectedly",
        ),
    ],
)
def test_public_log_keeps_an_answer_from_github_an_error(
    partial_clone, sdk_tree, tmp_path, said
):
    """Only a GitHub that could not be reached is NETWORK: a refused key, or
    any answer from the server, is someone's to fix, and stays red."""
    clone, pinned = partial_clone
    ssh = {"GIT_SSH_COMMAND": _fake_ssh(tmp_path, *said)}

    result = _check(sdk_tree, clone, "--public-log", env=ssh)

    assert result.returncode == 1
    assert _public(result, pinned)[0].startswith(
        "ERROR — the check did not finish (GitFailed); "
    )


# ---------------------------------------------------------------------------
# The weekly run (.github/workflows/em-drift.yml): EM is private, its log is not
# ---------------------------------------------------------------------------

WORKFLOW = SDK_ROOT / ".github" / "workflows" / "em-drift.yml"
CLONE_STEP = "Clone Execution Market's main (read-only deploy key)"
CHECK_STEP = "Check vendor/em/ against it"
#: https://api.github.com/meta, .ssh_keys
GITHUB_HOST_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
)


def _workflow_text() -> str:
    if not WORKFLOW.exists() and not (SDK_ROOT / ".git").exists():
        pytest.skip("an sdist: .github/ is the repository's, not the package's")
    return WORKFLOW.read_text(encoding="utf-8")


def _yaml(text: str) -> dict:
    import yaml  # [dev]: only the workflow tests need it, the rest runs without

    return yaml.safe_load(text)


def _workflow() -> dict:
    return _yaml(_workflow_text())


def _drift_job() -> dict:
    (job,) = _workflow()["jobs"].values()
    return job


def _steps() -> dict:
    return {step.get("name", step.get("uses")): step for step in _drift_job()["steps"]}


def test_the_weekly_run_has_no_trigger_a_fork_can_fire():
    """No pull_request (nor push, workflow_run...): only this repository's
    schedule and its maintainers start a job that holds the key."""
    workflow = _workflow()
    on = workflow.get("on", workflow.get(True))  # YAML 1.1 reads `on` as true
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert on["workflow_dispatch"] is None  # no inputs: nothing reaches a script
    (entry,) = on["schedule"]
    minute, hour, day, month, weekday = entry["cron"].split()
    assert (day, month) == ("*", "*") and weekday.isdigit(), entry  # weekly


def test_the_weekly_run_only_reads_this_repository():
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    job = _drift_job()
    assert "permissions" not in job
    # This repository by id (gh api repos/UltravioletaDAO/uvd-em-sdk-python
    # --jq .id): a fork has no key, and a rename must not skip every run.
    assert job["if"] == "github.repository_id == '1393883838'"
    # The key is a secret of this environment, whose deployment branches are
    # main alone (docs/em-drift-check.md): no PR, branch or other workflow.
    assert job["environment"] == "em-drift"
    assert job["runs-on"] == "ubuntu-latest"  # $RUNNER_TEMP emptied after the job


def test_the_key_reaches_the_clone_step_alone():
    assert re.findall(r"secrets\.\w+", _workflow_text()) == ["secrets.EM_DEPLOY_KEY"]
    steps = _steps()
    assert steps[CLONE_STEP]["env"]["EM_DEPLOY_KEY"] == "${{ secrets.EM_DEPLOY_KEY }}"
    for name, step in steps.items():
        # An expression in `run` is spliced into the script before it runs.
        assert "${{" not in step.get("run", ""), name


def test_nothing_of_execution_market_leaves_the_runner():
    """ci.yml's two pinned actions and nothing else: no actions/checkout of
    EM (it prints the commit it checks out and, with the fetch-depth: 0 the
    check needs, every branch and tag), no cache (a fork's PR restores the
    default branch's), no artifact."""
    actions = [step for step in _drift_job()["steps"] if "uses" in step]
    ci = _yaml((WORKFLOW.parent / "ci.yml").read_text(encoding="utf-8"))
    ci_job = ci["jobs"]["ci"]
    assert [step["uses"] for step in actions] == [
        step["uses"] for step in ci_job["steps"] if "uses" in step
    ]
    assert [step["with"] for step in actions] == [
        {"persist-credentials": False},
        {"python-version": "3.11"},
    ]
    assert list(_steps())[2:] == [CLONE_STEP, CHECK_STEP]


def test_a_network_failure_skips_the_check_and_is_not_drift():
    """The clone step names a failure from vendor_em.py's two lists, access
    (red) then transport (a warning); a transport cause skips the check step,
    which would only fail for want of a clone, and the check step turns
    vendor_em.py's NETWORK exit into a warning."""
    steps = _steps()
    loops = [
        shlex.split(line.strip()[len("for known in ") : -len("; do")])
        for line in steps[CLONE_STEP]["run"].splitlines()
        if line.strip().startswith("for known in ")
    ]
    assert loops == [list(vendor.ACCESS_CAUSES), list(vendor.TRANSPORT_CAUSES)]
    assert f'if ! grep -qF "{vendor.SERVER_ANSWERED}" ' in steps[CLONE_STEP]["run"]
    assert steps[CLONE_STEP]["env"]["EM_REMOTE"] == GITHUB_EM  # a typo: NETWORK
    for name in (CLONE_STEP, CHECK_STEP):  # the causes are English words
        assert steps[name]["env"]["LC_ALL"] == "C", name
    assert steps[CLONE_STEP]["id"] == "clone"
    assert steps[CHECK_STEP]["if"] == "steps.clone.outputs.network != 'true'"
    assert (
        f'if [ "$status" -eq {vendor.EXIT_NETWORK} ]; then'
        in (steps[CHECK_STEP]["run"])
    )


bash_steps = pytest.mark.skipif(
    sys.platform == "win32", reason="the steps are bash, for ubuntu-latest"
)


def _run_step(name: str, cwd: Path, temp: Path, **env: str):
    """A step's script as the runner runs it (``bash -e``), with ``python``
    this interpreter and the step's env; what the runner fills in
    (``${{ ... }}``) comes from ``env``. The step summary and the step's
    outputs go to ``summary.md`` and ``output`` next to ``temp``."""
    step = _steps()[name]
    script = temp.parent / f"step-{len(name)}.sh"
    script.write_text(
        f'python() {{ {shlex.quote(sys.executable)} "$@"; }}\n' + step["run"],
        encoding="utf-8",
    )
    step_env = {
        key: str(value)
        for key, value in step.get("env", {}).items()
        if "${{" not in str(value)
    }
    return subprocess.run(
        ["bash", "-e", str(script)],
        cwd=cwd,
        env={
            **_no_git_env(),
            **step_env,
            "RUNNER_TEMP": str(temp),
            "GITHUB_STEP_SUMMARY": str(temp.parent / "summary.md"),
            "GITHUB_OUTPUT": str(temp.parent / "output"),
            **env,
        },
        capture_output=True,
        text=True,
    )


@pytest.fixture
def em_remote(em_clone, tmp_path):
    """The fake EM as GitHub serves it: ``main``, plus a branch and a tag the
    run must not fetch, the branch being the remote's HEAD (so a clone that
    does not ask for main gets it), and ``--filter`` honoured (a local
    repository ignores it unless told). Returns (path, sha, URL, $RUNNER_TEMP)."""
    repo, sha = em_clone
    _git(repo, "branch", "-M", "main")
    _git(repo, "branch", "SECRETBRANCH")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/SECRETBRANCH")
    _git(repo, "tag", "SECRETTAG")
    _git(repo, "config", "uploadpack.allowFilter", "true")
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    return repo, sha, repo.as_uri(), temp


@bash_steps
def test_the_weekly_run_does_not_run_without_its_key(em_remote):
    repo, _, remote, temp = em_remote

    result = _run_step(CLONE_STEP, SDK_ROOT, temp, EM_DEPLOY_KEY="", EM_REMOTE=remote)

    assert result.returncode == 1
    assert result.stdout == (
        "::error::Secret EM_DEPLOY_KEY of environment em-drift is not set, so the"
        " check did not run (docs/em-drift-check.md)\n"
    )
    assert list(temp.iterdir()) == []  # no key file, no clone


@bash_steps
def test_the_weekly_run_clones_main_alone_quietly_and_checks_it(
    em_remote, sdk_tree, tmp_path
):
    repo, pinned, remote, temp = em_remote
    summary = tmp_path / "summary.md"

    clone = _run_step(
        CLONE_STEP, sdk_tree, temp, EM_DEPLOY_KEY="not a key", EM_REMOTE=remote
    )

    assert clone.returncode == 0, clone.stderr
    assert clone.stdout == clone.stderr == ""
    assert (temp / "clone.err").read_text(encoding="utf-8") == ""  # --quiet
    assert not (tmp_path / "output").exists()  # the check step runs
    em = temp / "em"
    assert _git(em, "for-each-ref", "--format=%(refname)").splitlines() == [
        "refs/heads/main",
        "refs/remotes/origin/main",
    ]
    objects = _git(em, "rev-list", "--objects", "--missing=print", "--all")
    assert any(line.startswith("?") for line in objects.splitlines())  # no blobs
    assert (temp / "em_key").read_text(encoding="utf-8") == "not a key\n"
    assert stat.S_IMODE((temp / "em_key").stat().st_mode) == 0o600
    assert (temp / "known_hosts").read_text(encoding="utf-8") == (
        f"github.com {GITHUB_HOST_KEY}\n"
    )
    assert _git(em, "config", "core.sshCommand") == (
        f"ssh -i '{temp}/em_key' -o IdentitiesOnly=yes -o BatchMode=yes"
        f" -o StrictHostKeyChecking=yes -o UserKnownHostsFile='{temp}/known_hosts'"
        # A connection that stalls is NETWORK ("not responding"), not a job
        # killed at its timeout.
        " -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
    )

    check = _run_step(CHECK_STEP, sdk_tree, temp, GITHUB_STEP_SUMMARY=str(summary))

    assert check.returncode == 0, check.stdout + check.stderr
    assert _public(check, pinned) == [
        f"OK — vendor/em/ is exactly what SOURCE's commit ({pinned[:12]}) yields",
        "OK — origin/main: same vendored bytes, nothing to re-vendor",
    ]
    assert summary.read_text(encoding="utf-8") == f"```\n{check.stdout}```\n"


@bash_steps
def test_the_weekly_run_is_red_on_drift_and_publishes_none_of_it(
    em_remote, sdk_tree, tmp_path
):
    repo, pinned, remote, temp = em_remote
    _git(repo, "checkout", "-q", "main")
    registry = repo / REGISTRY
    registry.write_bytes(registry.read_bytes().replace(b'"0xA"', b'"0xPRIVATEADDRESS"'))
    head = _commit(repo, tmp_path, "the USDC address changes upstream")
    summary = tmp_path / "summary.md"

    clone = _run_step(
        CLONE_STEP, sdk_tree, temp, EM_DEPLOY_KEY="not a key", EM_REMOTE=remote
    )
    check = _run_step(CHECK_STEP, sdk_tree, temp, GITHUB_STEP_SUMMARY=str(summary))

    assert clone.returncode == 0, clone.stderr
    assert check.returncode == 1, check.stdout + check.stderr
    assert _public(check, pinned)[1] == (
        "UPSTREAM DRIFT — origin/main: a re-vendor would change ['networks.json']"
    )
    published = clone.stdout + clone.stderr + check.stdout + summary.read_text()
    assert "PRIVATEADDRESS" not in published and head[:7] not in published


#: What git and OpenSSH print, and the cause each one is named by. Literal
#: on purpose: a cause added to, or dropped from, vendor_em.py's lists (and
#: the workflow's) changes this table too.
_ACCESS = [
    ("git@github.com: Permission denied (publickey).", "Permission denied (publickey)"),
    ("ERROR: Repository not found.", "Repository not found"),
    ("Host key verification failed.", "Host key verification failed"),
]
_TRANSPORT = [
    (
        "ssh: Could not resolve hostname github.com: Temporary failure in name"
        " resolution",
        "Could not resolve hostname",
    ),
    (
        "ssh: connect to host github.com port 22: Connection timed out",
        "Connection timed out",
    ),
    (
        "ssh: connect to host github.com port 22: Operation timed out",
        "Operation timed out",
    ),
    (
        "ssh: connect to host github.com port 22: Connection refused",
        "Connection refused",
    ),
    ("kex_exchange_identification: read: Connection reset by peer", "Connection reset"),
    ("Connection closed by 140.82.112.3 port 22", "Connection closed"),
    ("Connection to github.com closed by remote host.", "closed by remote host"),
    (
        "ssh: connect to host github.com port 22: Network is unreachable",
        "Network is unreachable",
    ),
    ("ssh: connect to host github.com port 22: No route to host", "No route to host"),
    ("Timeout, server github.com not responding.", "not responding"),
    ("client_loop: send disconnect: Broken pipe", "Broken pipe"),
    ("fatal: early EOF", "early EOF"),
    (
        "fatal: the remote end hung up unexpectedly",
        "the remote end hung up unexpectedly",
    ),
]


def test_each_cause_is_named_by_a_real_message_and_only_one():
    assert [cause for _, cause in _ACCESS] == list(vendor.ACCESS_CAUSES)
    assert [cause for _, cause in _TRANSPORT] == list(vendor.TRANSPORT_CAUSES)
    causes = (*vendor.ACCESS_CAUSES, *vendor.TRANSPORT_CAUSES)
    for said, cause in _ACCESS + _TRANSPORT:
        assert [c for c in causes if c in said] == [cause], said


@bash_steps
@pytest.mark.parametrize(
    "said, red, named",
    [((said,), True, named) for said, named in _ACCESS]
    + [((said,), False, cause) for said, cause in _TRANSPORT]
    + [
        # An access cause wins: a refused key is red even if git then says
        # the remote hung up.
        (
            (_ACCESS[0][0], "fatal: the remote end hung up unexpectedly"),
            True,
            "Permission denied (publickey)",
        ),
        # A server that answered was reached: red, whatever git adds.
        (
            (
                f"fatal: remote error: upload-pack: not our ref {'0' * 40}",
                "fatal: the remote end hung up unexpectedly",
            ),
            True,
            "unknown cause",
        ),
        (("fatal: something else",), True, "unknown cause"),
    ],
)
def test_a_failed_clone_is_named_not_quoted(em_remote, tmp_path, said, red, named):
    """git's and ssh's words do not reach the log (a server message can carry
    a repository name): a known cause is named, the rest is "unknown". An
    access cause or an unknown one is red; a transport cause is a warning, a
    NETWORK line in the summary and network=true, which skips the check."""
    _, _, remote, temp = em_remote
    fake = tmp_path / "bin"
    fake.mkdir()
    lines = "".join(f"echo {shlex.quote(line)} >&2\n" for line in said)
    (fake / "git").write_text(
        f"#!/bin/sh\n{lines}echo 'remote: PRIVATE_CANARY' >&2\nexit 128\n",
        encoding="utf-8",
    )
    (fake / "git").chmod(0o755)

    result = _run_step(
        CLONE_STEP,
        SDK_ROOT,
        temp,
        EM_DEPLOY_KEY="not a key",
        EM_REMOTE=remote,
        PATH=f"{fake}{os.pathsep}{os.environ['PATH']}",
    )

    assert result.stderr == ""
    summary, output = tmp_path / "summary.md", tmp_path / "output"
    if red:
        assert result.returncode == 1
        assert result.stdout == (
            f"::error::The clone of Execution Market's main failed ({named}), so the"
            " check did not run (docs/em-drift-check.md)\n"
        )
        assert not summary.exists() and not output.exists()
    else:
        line = (
            "NETWORK — the check did not run: the clone of Execution Market's main"
            f" failed ({named}); not drift (docs/em-drift-check.md)"
        )
        assert result.returncode == 0
        assert result.stdout == f"::warning::{line}\n"
        assert summary.read_text(encoding="utf-8") == f"{line}\n"
        assert output.read_text(encoding="utf-8") == "network=true\n"


@bash_steps
def test_a_clone_that_cannot_reach_github_is_a_warning(em_remote, tmp_path):
    """The same with git itself, against the workflow's own remote, and an
    ssh that stands in for a network that is down (GIT_SSH_COMMAND wins over
    the step's core.sshCommand)."""
    _, _, _, temp = em_remote
    ssh = _fake_ssh(
        tmp_path, "ssh: connect to host github.com port 22: Connection timed out"
    )

    result = _run_step(
        CLONE_STEP, SDK_ROOT, temp, EM_DEPLOY_KEY="not a key", GIT_SSH_COMMAND=ssh
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == (
        "::warning::NETWORK — the check did not run: the clone of Execution"
        " Market's main failed (Connection timed out); not drift"
        " (docs/em-drift-check.md)\n"
    )
    assert "PRIVATE_CANARY" not in result.stdout + result.stderr
    assert (tmp_path / "output").read_text(encoding="utf-8") == "network=true\n"


@bash_steps
def test_the_check_step_warns_when_em_cannot_be_reached_mid_check(
    em_remote, sdk_tree, tmp_path
):
    """The clone worked, then git show cannot fetch what it reads: NETWORK
    from vendor_em.py (75) becomes a warning and a green step, not drift."""
    _, pinned, remote, temp = em_remote
    clone = _run_step(
        CLONE_STEP, sdk_tree, temp, EM_DEPLOY_KEY="not a key", EM_REMOTE=remote
    )
    assert clone.returncode == 0, clone.stderr
    _git(temp / "em", "remote", "set-url", "origin", GITHUB_EM)

    check = _run_step(
        CHECK_STEP,
        sdk_tree,
        temp,
        GIT_SSH_COMMAND=_fake_ssh(tmp_path, "Connection reset by peer"),
    )

    network = (
        "NETWORK — the check did not finish: Execution Market could not be reached"
        " (Connection reset); run it again"
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert check.stdout == (
        f"{network}\n::warning::NETWORK — the check did not finish: Execution"
        " Market could not be reached; not drift (docs/em-drift-check.md)\n"
    )
    assert check.stderr == "" and "PRIVATE_CANARY" not in check.stdout
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == (
        f"```\n{network}\n```\n"
    )


@bash_steps
def test_a_real_failed_clone_is_not_quoted(em_remote, tmp_path):
    """The same with git itself: its message names the remote, kept out."""
    _, _, _, temp = em_remote
    missing = (tmp_path / "PRIVATE_CANARY").as_uri()

    result = _run_step(
        CLONE_STEP, SDK_ROOT, temp, EM_DEPLOY_KEY="not a key", EM_REMOTE=missing
    )

    assert result.returncode == 1
    assert "PRIVATE_CANARY" not in result.stdout + result.stderr
    assert "(unknown cause)" in result.stdout
