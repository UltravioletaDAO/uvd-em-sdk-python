"""Submissions resource — client.submissions.approve(), .reject(), etc."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .._envelope import unwrap_envelope
from ..models import (
    ApproveParams,
    RejectParams,
    Submission,
    SubmissionList,
    SubmitEvidenceParams,
)

if TYPE_CHECKING:
    from ..client import EMClient


class SubmissionsResource:
    """Operations on submissions.

    Usage::

        subs = await client.submissions.list(task_id="...")
        await client.submissions.approve("sub-uuid")
    """

    def __init__(self, client: EMClient) -> None:
        self._client = client

    async def list(self, task_id: str) -> SubmissionList:
        """List all submissions for a task.

        ``GET /tasks/{task_id}/submissions`` is an owner-scoped read
        (``verify_agent_auth_read``) — it requires auth even though it is a GET.
        ``sign=True`` forces the ERC-8128 signature that GETs skip by default;
        without it the server answers 401/403 (``EMAuthError``).
        """
        data = await self._client._request(
            "GET", f"/tasks/{task_id}/submissions", sign=True
        )
        return SubmissionList.model_validate(data)

    async def get(self, submission_id: str) -> dict[str, Any]:
        """Get a submission's full details, including AI verification.

        ``GET /submissions/{submission_id}`` — workers poll this after
        submitting: Phase A verification returns synchronously at submit,
        Phase B updates ``ai_verification_result`` asynchronously (plus the
        Ring 2 ``arbiter_*`` verdict fields). Verified with the *worker's*
        Supabase JWT (``EMClient(supabase_jwt=...)``) or, for agent executors
        without a JWT, the ERC-8128 signature (``sign=True`` — no-op when only
        a JWT is configured); only the submitting executor (or the task's
        publisher) may read it (401/403 otherwise).
        """
        jwt = self._client._supabase_jwt
        headers = {"Authorization": f"Bearer {jwt}"} if jwt else None
        return await self._client._request(
            "GET", f"/submissions/{submission_id}", headers=headers, sign=True
        )

    async def submit(self, task_id: str, params: SubmitEvidenceParams) -> Submission:
        """Submit evidence for a task (worker operation)."""
        data = await self._client._request(
            "POST",
            f"/tasks/{task_id}/submit",
            json=params.model_dump(exclude_none=True),
        )
        # POST /tasks/{id}/submit returns the SuccessResponse envelope
        # ({success, message, data}); the Submission model is the inner payload.
        return Submission.model_validate(unwrap_envelope(data))

    async def approve(
        self,
        submission_id: str,
        params: ApproveParams | None = None,
    ) -> dict[str, Any]:
        """Approve a submission (releases payment to worker)."""
        body = params.model_dump(exclude_none=True) if params else {}
        return await self._client._request(
            "POST",
            f"/submissions/{submission_id}/approve",
            json=body,
        )

    async def reject(
        self,
        submission_id: str,
        params: RejectParams,
    ) -> dict[str, Any]:
        """Reject a submission."""
        return await self._client._request(
            "POST",
            f"/submissions/{submission_id}/reject",
            json=params.model_dump(exclude_none=True),
        )

    async def request_more_info(
        self,
        submission_id: str,
        notes: str,
    ) -> dict[str, Any]:
        """Request additional information from the worker."""
        return await self._client._request(
            "POST",
            f"/submissions/{submission_id}/request-more-info",
            json={"notes": notes},
        )
