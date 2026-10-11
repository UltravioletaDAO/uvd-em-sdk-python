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
import subprocess
import sys
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


def test_source_names_the_repo_and_a_full_commit():
    fields = _source()
    assert fields["repo"] == "UltravioletaDAO/execution-market"
    assert re.fullmatch(r"[0-9a-f]{40}", fields["commit"])


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
    repo = tmp_path / "em"
    registry = repo / "mcp_server" / "integrations" / "x402" / "network_registry.py"
    registry.parent.mkdir(parents=True)
    registry.write_bytes(FAKE_REGISTRY.encode("utf-8"))
    (repo / "mcp_server" / "models.py").write_bytes(FAKE_MODELS.encode("utf-8"))
    (tmp_path / "no-hooks").mkdir()
    _git(repo, "init", "-q")
    _git(repo, "add", "mcp_server")
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


def _check(root: Path, repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "vendor_em.py")]
        + ["--em-repo", str(repo), "--check", *extra],
        capture_output=True,
        text=True,
        env=_no_git_env(),
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
