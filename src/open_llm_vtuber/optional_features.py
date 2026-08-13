"""Small, failure-isolated hooks for removable project-root features."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from loguru import logger


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPTIONAL_FEATURE_DESCRIPTOR = "optional-feature.json"
EXPRESSION_FEATURE_DIR = PROJECT_ROOT / "expression"
EXPRESSION_MANIFEST_PATH = EXPRESSION_FEATURE_DIR / "manifest.json"
EXPRESSION_BACKEND_PATH = EXPRESSION_FEATURE_DIR / "backend_filter.py"


def get_optional_feature() -> tuple[Path, dict[str, Any]] | None:
    """Find one removable project-root feature without knowing its name."""
    for descriptor_path in PROJECT_ROOT.glob(f"*/{OPTIONAL_FEATURE_DESCRIPTOR}"):
        try:
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(descriptor, dict):
            continue
        backend_entry = descriptor.get("backend_entry")
        frontend_entry = descriptor.get("frontend_entry")
        if (
            isinstance(backend_entry, str)
            and isinstance(frontend_entry, str)
            and (descriptor_path.parent / backend_entry).is_file()
            and (descriptor_path.parent / frontend_entry).is_file()
        ):
            return descriptor_path.parent, descriptor
    return None


def _load_optional_backend() -> ModuleType | None:
    feature = get_optional_feature()
    if feature is None:
        return None
    feature_dir, descriptor = feature
    backend_path = feature_dir / descriptor["backend_entry"]
    try:
        spec = importlib.util.spec_from_file_location(
            "project_optional_feature_backend",
            backend_path,
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        logger.warning("Optional feature backend is unavailable: {}", exc)
        return None


def build_optional_request_context(optional_contexts: Any) -> str:
    """Build one request's optional context; failures always degrade to empty."""
    module = _load_optional_backend()
    if module is None:
        return ""
    builder = getattr(module, "build_request_context", None)
    if not callable(builder):
        return ""
    try:
        result = builder(optional_contexts)
        return result if isinstance(result, str) else ""
    except Exception as exc:
        logger.warning("Optional feature context was ignored: {}", exc)
        return ""


def get_expression_manifest() -> dict[str, Any] | None:
    """Read and validate the removable expression feature manifest."""
    try:
        with EXPRESSION_MANIFEST_PATH.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, ValueError, TypeError):
        return None

    if not isinstance(manifest, dict) or manifest.get("enabled") is not True:
        return None

    entry = manifest.get("frontend_entry")
    emotions = manifest.get("emotions")
    if not isinstance(entry, str) or not isinstance(emotions, dict):
        return None
    if not (EXPRESSION_FEATURE_DIR / entry).is_file():
        return None
    if not EXPRESSION_BACKEND_PATH.is_file():
        return None

    valid_emotions = {
        emotion: filename
        for emotion, filename in emotions.items()
        if isinstance(emotion, str)
        and isinstance(filename, str)
        and Path(filename).name == filename
        and filename.lower().endswith(".png")
        and (EXPRESSION_FEATURE_DIR / filename).is_file()
    }
    if not valid_emotions:
        return None

    return {**manifest, "emotions": valid_emotions}


def expression_feature_available() -> bool:
    return get_expression_manifest() is not None


def _load_expression_backend() -> ModuleType | None:
    if not expression_feature_available():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "optional_static_expression_filter",
            EXPRESSION_BACKEND_PATH,
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        logger.warning("Optional expression backend is unavailable: {}", exc)
        return None


def process_expression_output(display_text: str, tts_text: str) -> dict[str, Any]:
    """Filter one completed reply; failures preserve the original output."""
    fallback = {
        "display_text": display_text,
        "tts_text": tts_text,
        "emotion": None,
    }
    module = _load_expression_backend()
    if module is None:
        return fallback
    processor = getattr(module, "process_output", None)
    if not callable(processor):
        return fallback
    try:
        result = processor(display_text, tts_text)
        if not isinstance(result, dict):
            return fallback
        cleaned_display = result.get("display_text")
        cleaned_tts = result.get("tts_text")
        emotion = result.get("emotion")
        manifest = get_expression_manifest()
        if (
            not isinstance(cleaned_display, str)
            or not isinstance(cleaned_tts, str)
            or not isinstance(emotion, (str, type(None)))
            or (
                emotion is not None
                and emotion not in (manifest or {}).get("emotions", {})
            )
        ):
            return fallback
        return {
            "display_text": cleaned_display,
            "tts_text": cleaned_tts,
            "emotion": emotion,
        }
    except Exception as exc:
        logger.warning("Optional expression output was ignored: {}", exc)
        return fallback
