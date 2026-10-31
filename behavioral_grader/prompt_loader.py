"""Load version-controlled prompts owned by the behavioral grader package."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re


PROMPT_DIRECTORY = Path(__file__).resolve().parent / "prompts"
INCLUDE_RE = re.compile(r"^\{\{include:([a-zA-Z0-9_.-]+\.md)\}\}$", re.MULTILINE)


def _validate_filename(filename: str) -> None:
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError(f"Invalid prompt filename: {filename!r}")


def _load_prompt(filename: str, stack: tuple[str, ...]) -> str:
    _validate_filename(filename)
    if filename in stack:
        chain = " -> ".join((*stack, filename))
        raise RuntimeError(f"Circular prompt include: {chain}")
    path = PROMPT_DIRECTORY / filename
    try:
        prompt = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except OSError as exc:
        raise RuntimeError(f"Cannot load prompt file {path}: {exc}") from exc
    if not prompt.strip():
        raise RuntimeError(f"Prompt file {path} is empty.")

    def include(match: re.Match[str]) -> str:
        return _load_prompt(match.group(1), (*stack, filename)).strip()

    return INCLUDE_RE.sub(include, prompt)


@lru_cache(maxsize=None)
def load_prompt(filename: str) -> str:
    """Return one non-empty grader prompt with safe local includes expanded."""
    return _load_prompt(filename, ())
