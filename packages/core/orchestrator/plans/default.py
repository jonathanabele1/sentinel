"""The default review plan.

Week 3 shape:
    fetch_diff → analyze_diff

`build_default_plan(...)` is a factory because the Steps now take
dependencies (LLM client, GitHub diff client) which can't be resolved at
module import time. The webhook handler calls this factory each request
to compose a fresh Plan from the shared singletons.

The Plan itself is still immutable once constructed; only the construction
is now parameterised. Adding a registry of plans (Week 4+) will live here.

NoopStep is still available under packages.core.orchestrator.steps.noop
for tests and as a reference implementation. It was removed from this
plan once real steps replaced its scaffolding role.
"""

from __future__ import annotations

from packages.core.agents.diff_analyzer import AnalyzeDiffStep
from packages.core.github.diff import GitHubDiffClient
from packages.core.llm import LLMClient
from packages.core.orchestrator.plan import Plan
from packages.core.orchestrator.steps.fetch_diff import FetchDiffStep

DEFAULT_PLAN_NAME = "default"


def build_default_plan(
    *,
    diff_client: GitHubDiffClient,
    llm_client: LLMClient,
) -> Plan:
    """Construct the default review plan with the given clients."""
    return Plan(
        name=DEFAULT_PLAN_NAME,
        steps=(
            FetchDiffStep(diff_client=diff_client),
            AnalyzeDiffStep(llm_client=llm_client),
        ),
    )
