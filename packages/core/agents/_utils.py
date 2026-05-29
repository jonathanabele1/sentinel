"""Small shared utilities for agent modules.

Lives in its own file so the diff analyzer, the reviewer base, and the
shared-prefix builder can all import trim_diff and format_files_summary
without pulling in each other's modules (which would create import cycles).
Keeps these leaf helpers dependency-light.
"""

from __future__ import annotations

from packages.core.github.diff import ChangedFile


def trim_diff(diff_text: str, max_chars: int) -> tuple[str, bool]:
    """Return (possibly-truncated diff, truncated_flag).

    Keep the head, drop the tail with a marker. Most diffs are most
    interesting at the top (the first few files); cutting the tail is the
    least-bad way to fit a char budget without proper tokenisation.
    """
    if len(diff_text) <= max_chars:
        return diff_text, False
    marker = (
        f"\n\n[diff truncated by Sentinel: showing first {max_chars} of {len(diff_text)} chars]"
    )
    return diff_text[: max_chars - len(marker)] + marker, True


def format_files_summary(files: list[ChangedFile]) -> str:
    """Render per-file metadata as a compact bullet list for prompts."""
    if not files:
        return "  (none)"
    return "\n".join(f"  - {f.path} [{f.status}, +{f.additions}/-{f.deletions}]" for f in files)
