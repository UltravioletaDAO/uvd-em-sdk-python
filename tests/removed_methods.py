"""The flat client methods deleted in the Phase 2 Stripe refactor (SDK-54).

The list, and who reads it:

* ``tests/test_readme_quickstart.py`` — keeps them out of the packaged README,
  which is the PyPI long_description and cannot be replaced once released.
* Execution Market's ``tests/ci/test_docs_site_sdk_examples.py`` — keeps them
  out of ``docs-site/``, which publishes ``docs.execution.market``. It used to
  load this file by path, when this package lived in that repository; an
  installed wheel carries no tests, so it now keeps a copy of the list.

This module ships inside the sdist so the README test stays runnable from an
unpacked release.

Adding a name here fixes the README guard; Execution Market's copy has to be
updated by hand (its guard resolves every docs example against the installed
SDK anyway, so a method removed from now on fails there without the list).
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
    # Prefixed with `client.` on purpose: a docs guide defines its OWN
    # `wait_for_completion(task_id)` coroutine over the REST API, unrelated to
    # the removed method. The bare entry flagged it as an error and forced an
    # edit to someone else's page just to silence the guard.
    "client.wait_for_completion",
    "workers.get(",  # SDK-43: route never existed
]
