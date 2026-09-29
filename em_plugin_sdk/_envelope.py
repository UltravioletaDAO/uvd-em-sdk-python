"""Unwrap the API's generic ``SuccessResponse`` envelope.

Some write endpoints (``POST /tasks/{id}/apply``, ``POST /tasks/{id}/submit``,
...) return the backend's generic ``SuccessResponse`` shape
``{"success": bool, "message": str, "data": {...}}``, while the SDK models
(``Application``, ``Submission``, ...) describe the inner object. Other
endpoints return the object directly (``Task``, ``SubmissionList`` = ``{
"submissions": [...], "count": N}``, health, config). Unwrapping globally in
``_handle_response`` would break those and change the contract of methods that
return the envelope dict verbatim (``approve``/``reject``), so the unwrap is
applied only where a model is parsed against a ``SuccessResponse`` endpoint.
"""

from __future__ import annotations

from typing import Any


def unwrap_envelope(data: Any) -> Any:
    """Return the inner payload of a ``SuccessResponse`` envelope, else *data*.

    Only unwraps when *data* is exactly the envelope shape — a dict with a
    boolean ``success`` key and a dict ``data`` payload. A model object
    (``Task``, ``Application``, ...) has no top-level boolean ``success``, so it
    is returned unchanged; this keeps direct-object endpoints untouched.
    """
    if (
        isinstance(data, dict)
        and isinstance(data.get("success"), bool)
        and isinstance(data.get("data"), dict)
    ):
        return data["data"]
    return data
