"""The default review plan.

Plan shape:
    fetch_diff → analyze_diff → ┬─→ security_review    ─┐
                                ├─→ correctness_review ─┼─→ consolidate
                                └─→ testing_review     ─┘

`build_default_plan(...)` is a factory because the Steps take dependencies
(LLM client, GitHub diff client, repo policy) that can't be resolved at
module import time. The webhook handler calls this factory each request to
compose a fresh Plan from the shared singletons and the per-repo policy.

The RepoPolicy supplies two things:
  - ignore_paths → FetchDiffStep (which files to exclude from the diff)
  - thresholds   → ConsolidatorStep (which findings to post)

When no .sentinel.yml is present, RepoPolicy() defaults apply.
"""

from __future__ import annotations

from packages.core.agents.consolidator import ConsolidatorPolicy, ConsolidatorStep
from packages.core.agents.correctness_reviewer import CorrectnessReviewStep
from packages.core.agents.diff_analyzer import AnalyzeDiffStep
from packages.core.agents.security_reviewer import SecurityReviewStep
from packages.core.agents.testing_reviewer import TestingReviewStep
from packages.core.github.diff import GitHubDiffClient
from packages.core.llm import LLMClient
from packages.core.orchestrator.plan import Plan
from packages.core.orchestrator.steps.fetch_diff import FetchDiffStep
from packages.core.policy import RepoPolicy

DEFAULT_PLAN_NAME = "default"


def build_default_plan(
    *,
    diff_client: GitHubDiffClient,
    llm_client: LLMClient,
    policy: RepoPolicy | None = None,
) -> Plan:
    """Construct the default review plan with the given clients and policy.

    The three reviewers run in parallel (they declare depends_on=
    ("analyze_diff",) with no edges to each other). The consolidator runs
    after all three via depends_on=(the three).
    """
    policy = policy or RepoPolicy()
    consolidator_policy = ConsolidatorPolicy(
        posting_threshold_by_reviewer=dict(policy.posting_threshold_by_reviewer),
        min_severity_to_post=policy.min_severity_to_post,
        max_comments_per_pr=policy.max_comments_per_pr,
    )
    return Plan(
        name=DEFAULT_PLAN_NAME,
        steps=(
            FetchDiffStep(diff_client=diff_client, ignore_patterns=policy.ignore_paths),
            AnalyzeDiffStep(llm_client=llm_client),
            SecurityReviewStep(llm_client=llm_client),
            CorrectnessReviewStep(llm_client=llm_client),
            TestingReviewStep(llm_client=llm_client),
            ConsolidatorStep(policy=consolidator_policy),
        ),
    )
