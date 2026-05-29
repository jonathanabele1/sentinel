"""Per-repo review policy: ignore patterns + consolidator thresholds.

A RepoPolicy is the parsed form of a repo's `.sentinel.yml`. When the file
is absent, the defaults apply (which already do the right thing: ignore
lockfiles and generated junk, post medium+ findings above per-reviewer
confidence thresholds).

The single most impactful field is `ignore_paths`. Lockfiles, vendored
code, and generated files are enormous, change constantly, and are
worthless to review. Sending them to the LLM reviewers wastes tokens
(a uv.lock alone can be 100k+ chars). Filtering them out before the diff
reaches any model is the biggest cost lever in the whole pipeline.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from typing import Any

import yaml
from pydantic import BaseModel, Field

from packages.core.models.domain import ReviewerCategory, Severity

# Generated / lock / vendored files that should never reach a reviewer.
# Globs match either the full repo-relative path or the basename.
DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    # Lockfiles (the big cost driver)
    "*.lock",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    "go.sum",
    # Minified / built assets
    "*.min.js",
    "*.min.css",
    "*.map",
    # Generated code
    "*.generated.*",
    "*_pb2.py",
    "*_pb2.pyi",
    "*.pb.go",
    # Vendored / build / dependency dirs
    "vendor/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    ".venv/**",
    # Snapshots
    "__snapshots__/**",
    "*.snap",
)


def _default_thresholds() -> dict[ReviewerCategory, float]:
    """Default per-reviewer posting thresholds.

    A named function (rather than an inline lambda) so its return
    annotation tells mypy the dict keys are ReviewerCategory literals,
    not plain str.
    """
    return {"security": 0.7, "correctness": 0.8, "testing": 0.75}


class RepoPolicy(BaseModel):
    """Parsed `.sentinel.yml`. All fields have sensible defaults.

    Defaults reflect the "precision over recall" thesis and the
    "never review generated files" rule.
    """

    ignore_paths: list[str] = Field(
        default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS),
        description="Glob patterns for files to exclude from review entirely.",
    )
    posting_threshold_by_reviewer: dict[ReviewerCategory, float] = Field(
        default_factory=_default_thresholds
    )
    min_severity_to_post: Severity = "medium"
    max_comments_per_pr: int = 10


def path_is_ignored(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    """Return True if `path` matches any ignore glob.

    Each pattern is tested against both the full repo-relative path and the
    basename, so `uv.lock` matches `sub/dir/uv.lock` and `*.lock` matches
    anything ending in .lock anywhere in the tree.
    """
    basename = path.rsplit("/", 1)[-1]
    return any(fnmatch(path, pat) or fnmatch(basename, pat) for pat in patterns)


def filter_unified_diff(
    diff_text: str, patterns: list[str] | tuple[str, ...]
) -> tuple[str, list[str]]:
    """Drop ignored files from a unified diff.

    Splits the diff on `diff --git` boundaries, drops sections whose target
    path matches an ignore pattern, and rejoins. Returns the filtered diff
    plus the list of dropped paths (for transparency in the summary comment).
    """
    if not diff_text.strip():
        return diff_text, []

    # Split keeping each "diff --git ..." header at the start of its section.
    sections = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)

    kept: list[str] = []
    dropped: list[str] = []
    for section in sections:
        if not section.strip():
            continue
        if not section.startswith("diff --git "):
            # Preamble before the first file header; keep it.
            kept.append(section)
            continue
        path = _extract_diff_path(section)
        if path is not None and path_is_ignored(path, patterns):
            dropped.append(path)
        else:
            kept.append(section)

    return "".join(kept), dropped


def load_repo_policy(yaml_text: str | None) -> RepoPolicy:
    """Parse `.sentinel.yml` text into a RepoPolicy.

    Returns defaults when the text is None/empty (no file in the repo) or
    when parsing fails (malformed YAML shouldn't break reviews; we log and
    fall back). Unknown keys are ignored by Pydantic's default behaviour.
    """
    if not yaml_text or not yaml_text.strip():
        return RepoPolicy()
    try:
        data: Any = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return RepoPolicy()
    if not isinstance(data, dict):
        return RepoPolicy()
    # Merge user-provided ignore paths on TOP of the defaults rather than
    # replacing them, so a repo never accidentally un-ignores lockfiles.
    user_ignores = data.get("ignore_paths")
    if isinstance(user_ignores, list):
        data["ignore_paths"] = [*DEFAULT_IGNORE_PATTERNS, *user_ignores]
    return RepoPolicy.model_validate(data)


def _extract_diff_path(section: str) -> str | None:
    """Pull the destination path out of a `diff --git a/X b/Y` header line."""
    first_line = section.split("\n", 1)[0]
    match = re.match(r"diff --git a/(.+?) b/(.+)", first_line)
    if match is None:
        return None
    return match.group(2)
