"""Concrete Step implementations.

Each Step is a typed unit of work the engine knows how to execute. Steps
live in their own files (or grouped files by domain like `reviewers.py`)
and are imported into Plans declaratively.
"""
