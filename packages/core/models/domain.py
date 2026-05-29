"""Domain models shared across agents and the orchestrator.

Pydantic classes that cross multiple boundaries: produced by LLM-backed
reviewer agents, validated on the way in, snapshotted as JSONB on
step_executions.outputs, persisted to the review_findings table.

Kept here (rather than in any single agent module) because multiple
agents and the consolidator all share these types. Owning them in one
place keeps the contract obvious.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

# --- Enumerations ---

Severity = Literal["info", "low", "medium", "high", "critical"]
"""Severity scale for a finding. Drives ranking + posting thresholds.

Conventions (loosely):
    info     — informational note; almost never blocking
    low      — minor issue, worth mentioning, not urgent
    medium   — should be fixed before merge in most cases
    high     — should block merge; reviewer attention required
    critical — security / data-loss class issue; must fix
"""

ReviewerCategory = Literal["security", "correctness", "testing"]
"""Which specialist reviewer produced a finding."""

FileCategory = Literal[
    "source",
    "test",
    "config",
    "docs",
    "infra",
    "build",
    "ci",
    "data",
    "other",
]
"""Buckets a changed file can fall into. Produced by the diff analyzer."""


# --- Models ---


class DiffAnalysis(BaseModel):
    """Structured summary of what changed in a PR.

    Produced by the diff analyzer agent and consumed by the specialist
    reviewers (as high-level context) and the webhook summary renderer.
    Used both as the analyzer step's output schema (snapshotted to
    step_executions.outputs as JSONB) AND as the JSON schema given to the
    LLM via Anthropic tool use. The same Pydantic class enforces the shape
    on both sides of the LLM boundary.

    Lives here rather than in diff_analyzer.py because it crosses several
    boundaries (analyzer output, reviewer input, webhook rendering) and the
    shared-prefix builder needs it without importing the agent module.
    """

    summary: str = Field(
        description=(
            "One-paragraph plain-English description of what this PR changes. "
            "Aim for 2-4 sentences. Mention the touched components and the user-facing effect."
        ),
    )
    categories: list[FileCategory] = Field(
        description=(
            "Which buckets of file the PR touches. Pick all that apply. "
            "Examples: 'source' for application code, 'test' for tests, 'config' for "
            "configuration files, 'infra' for Dockerfiles/k8s/Terraform, 'ci' for "
            "GitHub Actions workflows, 'docs' for markdown, 'build' for pyproject/lockfiles."
        ),
        default_factory=list,
    )
    risk_hints: list[str] = Field(
        description=(
            "Short bullet-form notes about anything worth flagging to a human reviewer: "
            "missing tests, new external integrations, schema changes, secrets handling, "
            "scope creep, etc. Two to six items is a healthy size. Empty list is fine "
            "for low-risk PRs."
        ),
        default_factory=list,
    )
    file_count: int = Field(
        ge=0,
        description="Number of files touched (use the count from the files input).",
    )
    primary_language: str | None = Field(
        default=None,
        description=(
            "The dominant programming language in this PR, lowercased "
            "(e.g. 'python', 'typescript', 'go'). None if it's mixed or unclear."
        ),
    )


class ReviewFinding(BaseModel):
    """A single issue surfaced by a specialist reviewer.

    Used in three places:
      1. Reviewer agent output: list[ReviewFinding].
      2. Consolidator input/output: same shape, with `posted` flag set.
      3. review_findings table: one row per finding, snapshotted as JSONB
         for any structured fields plus dedicated columns for the ones we
         want to index.

    The `id` field defaults to a fresh UUID4 so an agent can create
    findings without knowing about DB persistence; the consolidator
    preserves IDs across the dedupe step.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    reviewer: ReviewerCategory = Field(
        description=(
            "Which specialist reviewer found this. Fixed at the call site by "
            "the agent; not chosen by the model."
        ),
    )
    file: str = Field(
        description="Repository-relative path of the file the finding is about.",
    )
    line_start: int = Field(
        ge=1,
        description="First line of the affected code (1-indexed).",
    )
    line_end: int = Field(
        ge=1,
        description=(
            "Last line of the affected code (1-indexed, inclusive). "
            "Equal to line_start for a single-line finding."
        ),
    )
    severity: Severity = Field(
        description=(
            "How serious this finding is. See domain.Severity for the scale. "
            "'critical' is for security or data-loss class issues only."
        ),
    )
    category: str = Field(
        description=(
            "Short tag describing the type of issue: 'sql-injection', "
            "'missing-error-handling', 'no-test-coverage', etc. Free-form "
            "for now; the consolidator uses it for dedupe."
        ),
    )
    message: str = Field(
        description=(
            "What's wrong, in 1-2 sentences. This is what gets posted as "
            "the body of the inline comment. Be specific and actionable."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How confident the reviewer is that this finding is a real "
            "issue (0.0 to 1.0). Used with severity to rank and threshold."
        ),
    )
    evidence: str = Field(
        description=(
            "The specific code snippet or reasoning that supports the "
            "finding. Quote a few lines of the diff or explain the chain "
            "of reasoning. Brief; reviewers read this to decide whether to "
            "trust the finding."
        ),
    )
    posted: bool = Field(
        default=False,
        description=(
            "Whether this finding was posted as an inline comment on the "
            "PR. Set by the consolidator; reviewers always emit False."
        ),
    )


class ReviewerFindings(BaseModel):
    """The reviewer step's OUTPUT type (post-construction).

    This is what reviewers return and what the consolidator reads. It holds
    full ReviewFinding objects (with id/reviewer/posted set by our code).
    It is NOT the schema handed to the LLM — see ModelFindings for that.
    """

    findings: list[ReviewFinding] = Field(default_factory=list)


# --- LLM-facing schemas ---
#
# The model fills in ONLY the fields it should decide. id, reviewer, and
# posted are set by Sentinel (auto-generated id, reviewer forced per-agent,
# posted decided by the consolidator). Keeping those out of the model-facing
# schema removes a class of validation failures: the model can't get a field
# wrong if it never sees the field. Each validation failure used to trigger a
# full retry that re-sent the entire diff, multiplying token cost.


class ModelFinding(BaseModel):
    """A single finding as the LLM produces it. No id/reviewer/posted."""

    file: str = Field(
        description="Repository-relative path of the file the finding is about.",
    )
    line_start: int = Field(ge=1, description="First line of the affected code (1-indexed).")
    line_end: int = Field(
        ge=1,
        description=(
            "Last line of the affected code (1-indexed, inclusive). Equal to "
            "line_start for a single-line finding."
        ),
    )
    severity: Severity = Field(
        description=(
            "How serious: info | low | medium | high | critical. "
            "'critical' is for security or data-loss class issues only."
        ),
    )
    category: str = Field(
        description=(
            "Short tag for the issue type: 'sql-injection', "
            "'missing-error-handling', 'no-test-coverage', etc."
        ),
    )
    message: str = Field(
        description=(
            "What's wrong, in 1-2 sentences. This is the inline comment body. "
            "Be specific and actionable."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How confident you are this is a real issue, 0.0 to 1.0. A "
            "0.9-confidence finding should be right 90% of the time."
        ),
    )
    evidence: str = Field(
        description=(
            "The specific code snippet or reasoning that supports the finding. "
            "Quote a few lines or explain the chain of reasoning. Brief."
        ),
    )


class ModelFindings(BaseModel):
    """The schema handed to the LLM via tool use.

    Reviewer agents call complete_structured(schema=ModelFindings). The model
    fills `findings`. Empty list is the correct answer for low-risk PRs.
    """

    findings: list[ModelFinding] = Field(
        default_factory=list,
        description=(
            "All issues you identified. Empty list is correct for low-risk "
            "PRs; do not invent findings to fill the list."
        ),
    )
