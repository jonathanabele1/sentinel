"""LLM-backed agents.

Each agent is a thin module that defines:
  - Pydantic input/output models describing its contract.
  - A Step subclass that calls the LLM client (usually via
    complete_structured) to produce a typed output.

Agents: diff_analyzer, security_reviewer, correctness_reviewer,
testing_reviewer, consolidator.
"""
