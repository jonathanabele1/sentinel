"""Live smoke test against the real Anthropic API.

Requires ANTHROPIC_API_KEY in the environment. Skipped automatically if
the key isn't set, so this won't fail in environments without credentials
(including CI). Run via `make test-integration` when you want to verify
the full happy path with real model output.

Cost per run: roughly $0.0001 (one cheap Sonnet call with ~500 input
tokens, ~100 output). The cap should never become noticeable, but if
you find yourself running these in a loop, switch the model in the test
to Haiku to drop it further.
"""

from __future__ import annotations

import os

import pytest
from apps.api.config import get_settings
from packages.core.agents.diff_analyzer import DiffAnalysis
from packages.core.llm import LLMClient, complete_structured

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set; live LLM tests skipped",
    ),
]


SAMPLE_DIFF = """\
diff --git a/src/auth.py b/src/auth.py
index 1234..5678 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,7 +10,7 @@ def lookup_user(email: str):
-    query = "SELECT * FROM users WHERE email = ?"
-    return db.execute(query, [email]).fetchone()
+    query = f"SELECT * FROM users WHERE email = '{email}'"
+    return db.execute(query).fetchone()
"""


async def test_diff_analyzer_returns_valid_schema_against_real_api() -> None:
    """End-to-end: real Anthropic call returns a Pydantic-validated DiffAnalysis."""
    settings = get_settings()
    client = LLMClient(api_key=settings.anthropic_api_key)

    parsed, response = await complete_structured(
        client,
        model="claude-sonnet-4-5",
        schema=DiffAnalysis,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Analyze this small diff and call the `diff_analysis` tool.\n\n"
                    f"```diff\n{SAMPLE_DIFF}\n```"
                ),
            }
        ],
        system=(
            "You are a code review assistant. Respond by calling the "
            "diff_analysis tool. Be concise."
        ),
        agent="diff_analyzer_test",
        tool_name="diff_analysis",
    )

    # Schema-level guarantees: validation already succeeded by virtue of
    # returning. We assert plausible content here.
    assert isinstance(parsed, DiffAnalysis)
    assert len(parsed.summary) > 10
    assert parsed.file_count >= 0
    # Cost should be tiny but non-zero (we round up).
    assert response.cost_cents >= 1
    assert response.tokens_in > 0
    assert response.tokens_out > 0

    await client.aclose()
