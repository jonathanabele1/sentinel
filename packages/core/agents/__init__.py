"""LLM-backed agents.

Each agent is a thin module that defines:
  - Pydantic input/output models describing its contract.
  - A Step subclass that calls the LLM client (usually via
    complete_structured) to produce a typed output.

Today: diff_analyzer. Weeks 4: security_reviewer, correctness_reviewer,
testing_reviewer, consolidator.
"""
