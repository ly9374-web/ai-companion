"""Load every runtime prompt from the single prompts.yaml file."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


PROMPT_FILE = Path(__file__).with_name("prompts.yaml")
_KEY_PART = re.compile(r"^[A-Za-z0-9_-]+$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _load_prompt_data() -> dict[str, Any]:
    """Read YAML on every call so edits take effect without process restart."""
    try:
        content = PROMPT_FILE.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Prompt YAML not found: {PROMPT_FILE}") from exc

    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError(f"Prompt YAML root must be a mapping: {PROMPT_FILE}")
    return data


def load_prompt(key: str) -> str:
    """Load a string by a dot-separated YAML key such as runtime.image_notice."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Prompt key cannot be empty")

    parts = key.split(".")
    if any(not _KEY_PART.fullmatch(part) for part in parts):
        raise ValueError(f"Invalid prompt key: {key}")

    value: Any = _load_prompt_data()
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            raise KeyError(f"Prompt key not found: {key}")
        value = value[part]

    if not isinstance(value, str):
        raise TypeError(f"Prompt value must be a string: {key}")
    return value


def render_text(template: str, **values: object) -> str:
    """Replace named placeholders without treating normal JSON braces specially."""
    required = set(_PLACEHOLDER.findall(template))
    missing = sorted(required.difference(values))
    if missing:
        raise ValueError(
            "Missing template value(s) " + ", ".join(missing)
        )

    return _PLACEHOLDER.sub(
        lambda match: str(values[match.group(1)]),
        template,
    )


def render_prompt(key: str, **values: object) -> str:
    """Load and render a YAML prompt with explicit named values."""
    return render_text(load_prompt(key), **values)


def load_persona(persona_name: str) -> str:
    """Load the full character system prompt template from YAML."""
    return load_prompt(f"chat.characters.{persona_name}.system_prompt")


def load_util(util_name: str) -> str:
    return load_prompt(f"utility.{util_name}")


def render_util(util_name: str, **values: object) -> str:
    return render_prompt(f"utility.{util_name}", **values)
