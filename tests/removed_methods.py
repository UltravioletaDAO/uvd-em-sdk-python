"""The flat client methods deleted in the Phase 2 Stripe refactor (SDK-54).

ONE source, two consumers:

* ``tests/test_readme_quickstart.py`` — keeps them out of the packaged README,
  which is the PyPI long_description and cannot be replaced once released.
* ``tests/ci/test_docs_site_sdk_examples.py`` (repo root, not shipped) — keeps
  them out of ``docs-site/``, which publishes ``docs.execution.market``.

This module ships inside the sdist so the README test stays runnable from an
unpacked release; the repo-root guard loads it by path.

Adding a name here is the whole fix for a regression: both guards pick it up.
"""

# Substrings, matched literally against the raw markdown. `client.` is part of
# the entry on purpose — bare `get_task` would hit `client.tasks.get_task_...`
# and the prose around it.
REMOVED_FLAT_METHODS = [
    "client.list_tasks",
    "client.publish_task",
    "client.get_task",
    "client.cancel_task",
    "client.apply_to_task",
    "client.submit_evidence",
    "client.list_submissions",
    "client.get_submission",
    "client.approve_submission",
    "client.reject_submission",
    "client.get_executor",
    "client.register_worker",
    "client.leaderboard",
    # Con `client.` delante a proposito: `guides/task-lifecycle.md` define su
    # PROPIA corrutina `wait_for_completion(task_id)` sobre la REST API, que no
    # tiene nada que ver con el metodo borrado. La entrada suelta la marcaba
    # como error y obligaba a editar una pagina ajena para callar el guardia.
    "client.wait_for_completion",
    "workers.get(",  # SDK-43: route never existed
]
