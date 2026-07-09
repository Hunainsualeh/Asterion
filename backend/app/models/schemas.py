"""Pydantic request/response models for the API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class StartProjectRequest(BaseModel):
    idea: str = Field(..., min_length=1, description="The raw request: a question, task, or project idea.")
    # "auto" classifies intent and picks the lane; the explicit values force it.
    lane: Literal["auto", "project", "task"] = "auto"
    # "research" forces the deep-research flow regardless of classification.
    mode: Literal["auto", "research"] = "auto"
    # Reference to a staged upload batch whose extracted text grounds the run.
    attachment_batch_id: str = ""
    # A resolved response-style directive (from Settings); "" = default tone.
    tone: str = ""
    # The user's IANA timezone (from the browser) so the Task Agent can resolve
    # "tomorrow at 9am" to the right absolute instant. Defaults to UTC.
    timezone: str = "UTC"


class StartProjectResponse(BaseModel):
    project_id: str
    status: str
    lane: str = "project"
    intent: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    action: Literal["approve", "reject"]
    feedback: str = ""
    # Echo of the interrupt being answered; a mismatch means the UI was stale.
    interrupt_id: str = ""


class ManualTestRequest(BaseModel):
    result: Literal["pass", "fail"]
    feedback: str = ""
    interrupt_id: str = ""


class AnswerRequest(BaseModel):
    """Generic free-text resume (e.g. answering scope questions)."""
    feedback: str = ""
    interrupt_id: str = ""


class MessageRequest(BaseModel):
    """A follow-up chat message sent to an existing project."""
    message: str = Field(..., min_length=1)
    mode: Literal["auto", "research"] = "auto"
    attachment_batch_id: str = ""
    tone: str = ""
    timezone: str = "UTC"


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class ProjectSummary(BaseModel):
    project_id: str
    idea: str = ""
    title: str = ""
    summary: str = ""
    status: str = ""
    lane: str = "project"
    intent: dict[str, Any] = Field(default_factory=dict)
    pending_gate: str | None = None
    running: bool = False
    stage: dict[str, Any] | None = None


class ProjectDetail(ProjectSummary):
    interrupt: dict[str, Any] | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    dag: dict[str, Any] | None = None


class SandboxRunRequest(BaseModel):
    command: str = Field(..., min_length=1)
    timeout_s: int = Field(default=120, ge=1, le=1800)
    background: bool = False


class FileWriteRequest(BaseModel):
    path: str = Field(..., min_length=1)
    content: str = ""
