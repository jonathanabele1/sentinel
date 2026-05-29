"""Consolidator: dedupe, rank, threshold, persist.

Pure Python. No LLM. Takes the union of the three specialist reviewers'
findings, dedupes overlapping ones, ranks by severity * confidence,
applies posting thresholds, persists every finding (posted or not) to
the review_findings table, and returns the ranked list with `posted`
flags set on the ones that cleared the threshold.

Why not an LLM here:
  This step's work is well-specified (dedup, sort, filter). Adding an
  LLM would add cost, latency, and nondeterminism without buying
  judgment we need. The "deterministic consolidator after parallel
  specialists" pattern is the differentiator vs an agents-talk-to-agents
  design that would be brittle, slow, and untestable.

Policy:
  Hard-coded defaults today. A .sentinel.yml policy loader will let
  per-repo configuration override these. The dataclass shape is the
  contract between this step and the policy loader.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from packages.core.models.db import ReviewFinding as ReviewFindingRow
from packages.core.models.domain import ReviewerCategory, ReviewerFindings, ReviewFinding, Severity
from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.step import RetryPolicy, Step

# --- Policy ---


SEVERITY_WEIGHT: dict[Severity, int] = {
    "info": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "critical": 5,
}
"""Numeric weights for severity ranking. Higher = more important."""


@dataclass(frozen=True)
class ConsolidatorPolicy:
    """Decisions the consolidator makes per run.

    Today these are hard-coded defaults. The .sentinel.yml policy loader
    will produce these from per-repo YAML.

    The defaults reflect the "precision over recall" thesis:
      - Posting thresholds default to 0.7+ so noisy findings get filtered.
      - Min severity is "medium" so info/low findings aren't posted.
      - Max comments caps the noise per PR.

    Reviewers learn to ignore noisy bots. The hard part of a good
    reviewer is NOT posting bad comments.
    """

    posting_threshold_by_reviewer: dict[ReviewerCategory, float] = field(
        default_factory=lambda: {
            "security": 0.7,
            "correctness": 0.8,
            "testing": 0.75,
        }
    )
    min_severity_to_post: Severity = "medium"
    max_comments_per_pr: int = 10


# --- Inputs / outputs ---


class ConsolidatorInputs(BaseModel):
    """All findings from all three reviewers, merged.

    Snapshotted to step_executions.inputs JSONB. Lets you re-run the
    consolidator on the exact merged input (letting you tune thresholds
    and replay later without re-running the LLM reviewers).
    """

    findings: list[ReviewFinding]


class ConsolidatedFindings(BaseModel):
    """The consolidator's output.

    `findings` is every finding (deduped and ranked) with `posted` set
    on the ones that cleared the threshold. `posted_count` is a
    convenience denormalisation for the summary comment.
    """

    findings: list[ReviewFinding]
    posted_count: int


# --- Step ---


class ConsolidatorStep(Step[ConsolidatorInputs, ConsolidatedFindings]):
    """Deterministic deduper-ranker-thresholder. No LLM."""

    name = "consolidate"
    input_model = ConsolidatorInputs
    output_model = ConsolidatedFindings
    depends_on = ("security_review", "correctness_review", "testing_review")
    timeout_seconds = 10
    retry_policy = RetryPolicy.no_retry()  # Pure Python; retries don't help.

    def __init__(self, *, policy: ConsolidatorPolicy | None = None) -> None:
        self._policy = policy or ConsolidatorPolicy()

    def build_inputs(self, ctx: StepContext) -> ConsolidatorInputs:
        security = ctx.get_output("security_review", ReviewerFindings)
        correctness = ctx.get_output("correctness_review", ReviewerFindings)
        testing = ctx.get_output("testing_review", ReviewerFindings)
        return ConsolidatorInputs(
            findings=[*security.findings, *correctness.findings, *testing.findings],
        )

    async def execute(self, inputs: ConsolidatorInputs, ctx: StepContext) -> ConsolidatedFindings:
        deduped = _dedupe_findings(inputs.findings)
        ranked = sorted(deduped, key=_finding_rank, reverse=True)
        posted_findings = _apply_thresholds(ranked, self._policy)

        # Flip the posted flag on the chosen subset.
        posted_ids = {f.id for f in posted_findings}
        for f in ranked:
            f.posted = f.id in posted_ids

        await _persist_findings(ctx, ranked)

        ctx.log.info(
            "consolidator.completed",
            total_findings=len(ranked),
            posted=len(posted_findings),
            duplicates_removed=len(inputs.findings) - len(deduped),
            by_severity=_count_by_severity(ranked),
        )

        return ConsolidatedFindings(
            findings=ranked,
            posted_count=len(posted_findings),
        )


# --- Helpers ---


def _finding_rank(f: ReviewFinding) -> float:
    """Composite ranking score: severity weight * confidence.

    Examples:
      critical (5) * 0.95 = 4.75  (top of the list)
      high     (4) * 0.80 = 3.20
      medium   (3) * 0.60 = 1.80
      low      (2) * 0.40 = 0.80  (bottom)
    """
    return SEVERITY_WEIGHT[f.severity] * f.confidence


def _is_duplicate(a: ReviewFinding, b: ReviewFinding) -> bool:
    """Two findings are duplicates if they're about the same code chunk
    with the same problem category.

    Same file + overlapping line range + same category. Two reviewers
    that both flag "sql-injection" on the same function are the canonical
    case: keep one, drop the other.
    """
    if a.file != b.file:
        return False
    if a.category != b.category:
        return False
    # Overlapping line ranges, inclusive on both ends.
    return not (a.line_end < b.line_start or b.line_end < a.line_start)


def _dedupe_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Remove duplicates; keep the highest-confidence representative.

    Stable in the sense that the kept finding's ID survives — useful for
    the consolidator's downstream code that compares pre- vs post-dedup
    finding IDs.
    """
    kept: list[ReviewFinding] = []
    for f in findings:
        # Find an existing duplicate in `kept`.
        dup_index = next(
            (i for i, existing in enumerate(kept) if _is_duplicate(f, existing)),
            None,
        )
        if dup_index is None:
            kept.append(f)
            continue
        # Replace if the new finding is more confident.
        if f.confidence > kept[dup_index].confidence:
            kept[dup_index] = f
    return kept


def _apply_thresholds(
    findings: list[ReviewFinding],
    policy: ConsolidatorPolicy,
) -> list[ReviewFinding]:
    """Return the subset of findings that should be posted.

    Filters by:
      1. Minimum severity (e.g. don't post anything below "medium").
      2. Per-reviewer confidence threshold (e.g. security >= 0.7).
      3. Maximum comments per PR (e.g. cap at 10).

    Assumes findings are pre-sorted by importance (callers do this). The
    cap takes from the top, so the most important findings are kept when
    the cap binds.
    """
    severity_order = list(SEVERITY_WEIGHT.keys())
    min_index = severity_order.index(policy.min_severity_to_post)

    chosen: list[ReviewFinding] = []
    for f in findings:
        if severity_order.index(f.severity) < min_index:
            continue
        threshold = policy.posting_threshold_by_reviewer.get(f.reviewer, 0.5)
        if f.confidence < threshold:
            continue
        chosen.append(f)
        if len(chosen) >= policy.max_comments_per_pr:
            break
    return chosen


async def _persist_findings(ctx: StepContext, findings: list[ReviewFinding]) -> None:
    """Insert one review_findings row per finding (posted or not).

    Below-threshold findings are persisted too: the eval harness and the
    calibration analysis both need access to the full distribution of
    findings to compute precision/recall and Brier scores.
    """
    for f in findings:
        row = ReviewFindingRow(
            id=f.id,
            run_id=ctx.run.id,
            reviewer=f.reviewer,
            file=f.file,
            line_start=f.line_start,
            line_end=f.line_end,
            severity=f.severity,
            category=f.category,
            message=f.message,
            confidence=f.confidence,
            evidence=f.evidence,
            posted=f.posted,
        )
        ctx.session.add(row)
    await ctx.session.commit()


def _count_by_severity(findings: list[ReviewFinding]) -> dict[str, int]:
    """Small breakdown for the structlog event. Useful in dashboards."""
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    return counts
