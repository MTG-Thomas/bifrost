"""CLI-side mirrors for Solution deploy response contracts."""

from uuid import UUID

from pydantic import BaseModel


class SolutionCandidateDeployEnqueued(BaseModel):
    """An accepted deploy job bound to the uploaded candidate bundle."""

    deploy_job_id: UUID
    candidate_id: str
