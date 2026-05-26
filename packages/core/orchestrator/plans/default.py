"""The default review plan.

Week 2: a single NoopStep. The plan exists so the webhook handler has
something concrete to call Engine.run() with, and so the whole pipeline
end-to-end (webhook → engine → step → snapshot → comment) is exercised.

Week 3 adds: fetch_diff → analyze_diff before the noop.
Week 4 adds: parallel specialist reviewers + consolidator + post_comments
             (and the plan becomes a DAG).

The `name` field is what gets stored in review_runs.plan_name. If you
change it, existing runs' plan_name will not match, which is fine; it
just means historical runs are tagged with their plan version.
"""

from __future__ import annotations

from packages.core.orchestrator.plan import Plan
from packages.core.orchestrator.steps.noop import NoopStep

default_review_plan = Plan(
    name="default",
    steps=(NoopStep(),),
)
