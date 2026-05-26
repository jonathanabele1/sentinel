"""Plan definitions.

Plans are declarative: they're module-level constants that compose Step
instances into an ordered sequence. There's exactly one Plan for now
(`default_review_plan`); Week 4 will add per-repo policy variations.
"""

from packages.core.orchestrator.plans.default import default_review_plan

__all__ = ["default_review_plan"]
