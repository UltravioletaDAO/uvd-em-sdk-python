"""The parity tests run in this repository's CI, and they catch drift.

``tests/test_networks_sync.py`` and ``tests/test_enums_sync.py`` used to skip
unless the checkout sat next to Execution Market's ``mcp_server/``, and 32
tests of ``tests/test_retry.py`` unless it sat next to EM's ``sdk/python`` and
``cli/src``: in this repository's own CI, which checks out the SDK alone, they
never ran. Now they compare the SDK against the vendored EM data
(``vendor/em/``), with no skip. This file keeps both halves of that true. It
runs the three modules with pytest, as a process, in a copy of what they read
with nothing next to it (what CI checks out):

  - unchanged, every parity test runs and passes: none skipped, xfailed,
    deselected or gone;
  - with a drift on either side (the vendored EM data moved, or a generated
    region of the SDK was edited by hand), the parity tests that own it fail
    and only those.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]

#: Every parity test, by module, with its number of cases (a parametrized test
#: has one per row). Literal on purpose: a parity test that stops running
#: (renamed, deleted, deselected, a row gone), or a new one, changes this too.
PARITY_TESTS = {
    "test_networks_sync": {
        "test_networks_matches_backend": 1,
        "test_escrow_rule_matches_backend_and": 1,
    },
    "test_enums_sync": {
        "test_enums_match_backend": 1,
        "test_generated_region_matches_renderer": 1,
        "test_client_extras_still_absent_from_backend": 1,
        "test_dispute_reason_not_generated": 1,
    },
    "test_retry": {
        "test_the_decision_table_is_execution_markets": 1,
        "test_a_drift_of_each_table_is_named": 3,
        "test_plugin_sdk_status_table": 11,
        "test_plugin_sdk_exception_table": 2,
        "test_plugin_sdk_tx_hash_guard": 5,
        "test_plugin_sdk_5xx_without_tx_hash_still_retries": 1,
        "test_plugin_sdk_idempotency_key_persists_signature_regenerates": 1,
    },
}

#: What the parity modules read, and what configures pytest for them.
TREE = ("uvd_em_sdk", "scripts", "vendor")
CONFIG = ("pyproject.toml", "pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg")


def _standalone(tmp_path: Path) -> Path:
    """A copy of this checkout's parity inputs in a directory with nothing
    next to it: no ``../mcp_server/``, wherever this checkout lives."""
    root = tmp_path / "ci" / "uvd-em-sdk-python"
    for name in TREE:
        shutil.copytree(
            SDK_ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    (root / "tests").mkdir()
    for name in ("__init__.py", "conftest.py", *(f"{m}.py" for m in PARITY_TESTS)):
        if (SDK_ROOT / "tests" / name).is_file():
            shutil.copyfile(SDK_ROOT / "tests" / name, root / "tests" / name)
    for name in (*CONFIG, "conftest.py"):
        if (SDK_ROOT / name).is_file():
            shutil.copyfile(SDK_ROOT / name, root / name)
    return root


def _run_parity(root: Path) -> tuple[dict[str, str], str]:
    """pytest in ``root``, as a process and as CI runs it (no path: what the
    configuration collects, ``testpaths`` and ``collect_ignore`` included): a
    skip, an xfail or a deselection is an outcome of a pytest run, so it is
    read from one. Returns ``({"module::test[case]": outcome}, output)``;
    outcome is ``passed``, ``skipped`` (xfail included), ``xpassed``,
    ``failure`` or ``error``."""
    report = root.parent / "junit.xml"
    # PYTEST_ADDOPTS is the caller's (-x, -n, --lf...), not this repository's.
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rA"]
        + [f"--junitxml={report}"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = proc.stdout + proc.stderr
    assert report.is_file(), output
    outcomes: dict[str, str] = {}
    for case in ET.parse(report).iter("testcase"):
        module = case.get("classname", "").rsplit(".", 1)[-1]
        verdicts = [c.tag for c in case if c.tag in ("skipped", "failure", "error")]
        outcomes[f"{module}::{case.get('name')}"] = (verdicts or ["passed"])[0]
    # junit writes an XPASS as a pass: a parity test marked xfail that still
    # passes would read as running. The -rA summary names it.
    for nodeid in re.findall(r"^XPASS (\S+)", proc.stdout, flags=re.M):
        path, _, name = nodeid.partition("::")
        outcomes[f"{Path(path).stem}::{name}"] = "xpassed"
    return outcomes, output


def _per_test(outcomes: dict[str, str]) -> dict[str, list[str]]:
    """``{"module::test": [the outcome of each case]}``."""
    grouped: dict[str, list[str]] = {}
    for case, outcome in sorted(outcomes.items()):
        grouped.setdefault(case.split("[", 1)[0], []).append(outcome)
    return grouped


def _expected(red: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """Every case of every parity test passed, but those of ``red`` failed."""
    return {
        f"{m}::{t}": ["failure" if f"{m}::{t}" in red else "passed"] * cases
        for m, tests in PARITY_TESTS.items()
        for t, cases in tests.items()
    }


def test_the_parity_tests_run_and_pass_in_a_standalone_checkout(tmp_path):
    """The skips this repository's CI had were keyed on ``../mcp_server/``
    and on ``../sdk/python`` + ``../cli/src``; the copy has none, so those
    skips fire here as they did in CI, wherever this checkout lives. Any
    other skip, xfail or deselection fails this too."""
    outcomes, output = _run_parity(_standalone(tmp_path))

    assert _per_test(outcomes) == _expected(), output


# ---------------------------------------------------------------------------
# A drift on either side turns them red
# ---------------------------------------------------------------------------


def _edit_json(path: Path, change) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def _edit_region(path: Path, begin: str, pattern: str, repl) -> None:
    """One substitution inside the generated region of ``path`` (after its
    BEGIN marker); fails if the pattern is gone, so the drift is real."""
    text = path.read_text(encoding="utf-8")
    head, marker, region = text.partition(begin)
    assert marker, f"{begin!r} not in {path}"
    region, count = re.subn(pattern, repl, region, count=1, flags=re.M)
    assert count == 1, f"{pattern!r} not in the generated region of {path}"
    path.write_text(head + marker + region, encoding="utf-8", newline="\n")


def _networks_json(root: Path) -> Path:
    return root / "vendor" / "em" / "networks.json"


def _enums_json(root: Path) -> Path:
    return root / "vendor" / "em" / "enums.json"


def _a_token_address_moves_upstream(root: Path) -> None:
    def change(networks):
        net = next(iter(networks.values()))
        token = next(iter(net["tokens"].values()))
        token["address"] += "0"

    _edit_json(_networks_json(root), change)


def _a_task_status_is_added_upstream(root: Path) -> None:
    _edit_json(
        _enums_json(root), lambda enums: enums["TaskStatus"].update(DRIFTED="drifted")
    )


def _an_operator_is_removed_upstream(root: Path) -> None:
    def change(networks):
        net = next(
            c for c in networks.values() if c.get("escrow") and c.get("operator")
        )
        net["operator"] = False

    _edit_json(_networks_json(root), change)


def _a_task_category_is_removed_upstream(root: Path) -> None:
    _edit_json(_enums_json(root), lambda enums: enums["TaskCategory"].popitem())


def _a_retry_status_flips_upstream(root: Path) -> None:
    def change(retry):
        row = retry["STATUS_TABLE"][0]
        row[1] = not row[1]

    _edit_json(root / "vendor" / "em" / "retry.json", change)


def _the_sdk_regions_are_edited_by_hand(root: Path) -> None:
    _edit_region(
        root / "uvd_em_sdk" / "networks.py",
        "# --- BEGIN GENERATED: NETWORKS",
        r"chain_id=(\d+),",
        lambda m: f"chain_id={int(m[1]) + 1},",
    )
    _edit_region(
        root / "uvd_em_sdk" / "models.py",
        "# --- BEGIN GENERATED: ENUMS",
        r'^(    [A-Z_]+ = ")([a-z_]+)"$',
        r'\1\2_edited"',
    )


@pytest.mark.parametrize(
    "drift, red",
    [
        pytest.param(
            (_a_token_address_moves_upstream, _a_task_status_is_added_upstream),
            {
                "test_networks_sync::test_networks_matches_backend",
                "test_enums_sync::test_enums_match_backend",
                "test_enums_sync::test_generated_region_matches_renderer",
            },
            id="vendor-em-a-token-address-and-a-new-task-status",
        ),
        pytest.param(
            (_an_operator_is_removed_upstream, _a_task_category_is_removed_upstream),
            {
                "test_networks_sync::test_networks_matches_backend",
                "test_networks_sync::test_escrow_rule_matches_backend_and",
                "test_enums_sync::test_enums_match_backend",
                "test_enums_sync::test_generated_region_matches_renderer",
            },
            id="vendor-em-an-operator-and-a-task-category-removed",
        ),
        pytest.param(
            (_a_retry_status_flips_upstream,),
            {"test_retry::test_the_decision_table_is_execution_markets"},
            id="vendor-em-a-retry-status-flips",
        ),
        pytest.param(
            (_the_sdk_regions_are_edited_by_hand,),
            {
                "test_networks_sync::test_networks_matches_backend",
                "test_enums_sync::test_enums_match_backend",
                "test_enums_sync::test_generated_region_matches_renderer",
            },
            id="sdk-generated-regions-edited-by-hand",
        ),
    ],
)
def test_a_drift_fails_the_parity_tests_that_own_it(tmp_path, drift, red):
    root = _standalone(tmp_path)
    for edit in drift:
        edit(root)

    outcomes, output = _run_parity(root)

    assert _per_test(outcomes) == _expected(frozenset(red)), output
