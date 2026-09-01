"""Small, failure-isolated hooks for removable project-root features."""

from __future__ import annotations

import importlib.util
import json
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

from loguru import logger


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPTIONAL_FEATURE_DESCRIPTOR = "optional-feature.json"
DEFAULT_EXPRESSION_DIR = "expression"


class OptionalFeatureConfigurationError(RuntimeError):
    """Raised when an installed optional feature is incomplete or ambiguous."""


def _safe_relative_file(feature_dir: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise OptionalFeatureConfigurationError(
            f"Optional feature field {field!r} must be a non-empty path"
        )
    candidate = (feature_dir / value).resolve()
    if feature_dir.resolve() not in candidate.parents or not candidate.is_file():
        raise OptionalFeatureConfigurationError(
            f"Optional feature field {field!r} points to a missing file: {value}"
        )
    return candidate


def get_expression_feature_dir(expression_dir: str | None = None) -> Path:
    """Resolve one character's expression feature folder under PROJECT_ROOT.

    Only a bare directory name is accepted so the resolved path always stays a
    direct child of the project root. Invalid or empty values fall back to the
    legacy ``expression`` directory and degrade to "unavailable".
    """
    name = (expression_dir or "").strip() or DEFAULT_EXPRESSION_DIR
    if Path(name).name != name or name in (".", ".."):
        name = DEFAULT_EXPRESSION_DIR
    return PROJECT_ROOT / name


def get_optional_feature() -> tuple[Path, dict[str, Any]] | None:
    """Return the one installed project feature, or ``None`` when intentionally absent.

    A present but broken descriptor is an installation error.  Silently treating
    that state as "feature removed" could unexpectedly change account policy.
    """
    descriptor_paths = sorted(
        PROJECT_ROOT.glob(f"*/{OPTIONAL_FEATURE_DESCRIPTOR}"),
        key=lambda path: path.as_posix(),
    )
    if not descriptor_paths:
        return None
    if len(descriptor_paths) != 1:
        locations = ", ".join(path.parent.name for path in descriptor_paths)
        raise OptionalFeatureConfigurationError(
            f"Expected one optional feature, found {len(descriptor_paths)}: {locations}"
        )

    descriptor_path = descriptor_paths[0]
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise OptionalFeatureConfigurationError(
            f"Cannot read optional feature descriptor {descriptor_path}"
        ) from exc
    if not isinstance(descriptor, dict):
        raise OptionalFeatureConfigurationError(
            f"Optional feature descriptor must contain an object: {descriptor_path}"
        )
    feature_id = descriptor.get("id")
    if not isinstance(feature_id, str) or not feature_id.strip():
        raise OptionalFeatureConfigurationError("Optional feature id is missing")
    _safe_relative_file(descriptor_path.parent, descriptor.get("backend_entry"), "backend_entry")
    _safe_relative_file(descriptor_path.parent, descriptor.get("frontend_entry"), "frontend_entry")
    return descriptor_path.parent, descriptor


@lru_cache(maxsize=1)
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
    except OptionalFeatureConfigurationError:
        raise
    except Exception as exc:
        raise OptionalFeatureConfigurationError(
            f"Optional feature backend failed to load: {backend_path}"
        ) from exc


def validate_optional_feature() -> None:
    """Fail early when an installed optional feature cannot be used safely."""
    feature = get_optional_feature()
    module = _load_optional_backend()
    if feature is not None:
        _, descriptor = feature
        required_hooks = descriptor.get("backend_required_hooks", [])
        if not isinstance(required_hooks, list) or not all(
            isinstance(name, str) and name.strip() for name in required_hooks
        ):
            raise OptionalFeatureConfigurationError(
                "backend_required_hooks must be a list of non-empty names"
            )
        missing_hooks = [
            name for name in required_hooks if not callable(getattr(module, name, None))
        ]
        if missing_hooks:
            raise OptionalFeatureConfigurationError(
                "Optional feature backend is missing required hooks: "
                + ", ".join(missing_hooks)
            )
    get_optional_static_mounts()


def _call_optional(name: str, default: Any, *args: Any) -> Any:
    module = _load_optional_backend()
    if module is None:
        return default
    callback = getattr(module, name, None)
    if not callable(callback):
        return default
    return callback(*args)


def get_optional_registration_features(account_name: str) -> dict[str, bool]:
    result = _call_optional("registration_features", {}, account_name)
    if not isinstance(result, dict):
        return {}
    return {
        str(key): value
        for key, value in result.items()
        if isinstance(key, str) and isinstance(value, bool)
    }


def get_optional_public_account_features(
    account_name: str,
    persisted_features: dict[str, bool] | None = None,
) -> dict[str, bool]:
    result = _call_optional(
        "public_account_features",
        {},
        account_name,
        dict(persisted_features or {}),
    )
    if not isinstance(result, dict):
        return {}
    return {
        str(key): value
        for key, value in result.items()
        if isinstance(key, str) and isinstance(value, bool)
    }


def get_optional_account_policy(account_name: str) -> dict[str, bool]:
    result = _call_optional("account_policy", {}, account_name)
    if not isinstance(result, dict):
        return {}
    return {
        str(key): value
        for key, value in result.items()
        if isinstance(key, str) and isinstance(value, bool)
    }


def optional_account_can_access_character(account_name: str, conf_uid: object) -> bool:
    result = _call_optional(
        "account_can_access_character",
        True,
        account_name,
        conf_uid,
    )
    return result if isinstance(result, bool) else True


def process_optional_text_input(data: dict, context: Any) -> dict[str, Any] | None:
    result = _call_optional("process_text_input", None, data, context)
    return result if isinstance(result, dict) else None


def get_optional_new_history_messages(context: Any) -> list[dict[str, Any]]:
    result = _call_optional("new_history_messages", [], context)
    if not isinstance(result, list):
        return []
    return [item for item in result if isinstance(item, dict)]


async def run_optional_character_switch_action(context: Any) -> dict[str, Any] | None:
    module = _load_optional_backend()
    if module is None:
        return None
    callback = getattr(module, "after_character_switch", None)
    if not callable(callback):
        return None
    result = await callback(context)
    return result if isinstance(result, dict) else None


def augment_optional_tool_status(
    tool_name: str,
    is_error: bool,
    metadata: dict[str, Any],
    status: dict[str, Any],
) -> dict[str, Any]:
    result = _call_optional(
        "augment_tool_status",
        status,
        tool_name,
        is_error,
        metadata,
        dict(status),
    )
    return result if isinstance(result, dict) else status


def get_optional_static_mounts() -> list[dict[str, Any]]:
    feature = get_optional_feature()
    if feature is None:
        return []
    feature_dir, descriptor = feature
    mounts = descriptor.get("static_mounts", [])
    if not isinstance(mounts, list):
        raise OptionalFeatureConfigurationError("static_mounts must be a list")
    resolved: list[dict[str, Any]] = []
    for mount in mounts:
        if not isinstance(mount, dict):
            raise OptionalFeatureConfigurationError("Invalid optional static mount")
        route = mount.get("route")
        directory = mount.get("directory")
        if not isinstance(route, str) or not route.startswith("/"):
            raise OptionalFeatureConfigurationError("Optional static mount route is invalid")
        if not isinstance(directory, str) or not directory:
            raise OptionalFeatureConfigurationError("Optional static mount directory is invalid")
        directory_path = (feature_dir / directory).resolve()
        if feature_dir.resolve() not in directory_path.parents or not directory_path.is_dir():
            raise OptionalFeatureConfigurationError(
                f"Optional static directory is missing: {directory}"
            )
        resolved.append(
            {"route": route, "directory": directory_path, "html": mount.get("html") is True}
        )
    return resolved


def collect_optional_analysis_data(
    optional_contexts: Any,
    context: Any,
) -> dict[str, Any]:
    """Return validated analysis-only data without exposing it to the chat model."""
    try:
        result = _call_optional(
            "collect_analysis_data",
            {},
            optional_contexts,
            context,
        )
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        logger.warning("Optional analysis data was ignored: {}", exc)
        return {}


def build_optional_request_context(optional_contexts: Any, context: Any = None) -> str:
    """Build one request's optional context; failures always degrade to empty."""
    try:
        result = _call_optional(
            "build_request_context",
            "",
            optional_contexts,
            context,
        )
        return result if isinstance(result, str) else ""
    except Exception as exc:
        logger.warning("Optional feature context was ignored: {}", exc)
        return ""


def get_expression_manifest(
    expression_dir: str | None = None,
) -> dict[str, Any] | None:
    """Read and validate the removable expression feature manifest."""
    feature_dir = get_expression_feature_dir(expression_dir)
    manifest_path = feature_dir / "manifest.json"
    backend_path = feature_dir / "backend_filter.py"
    try:
        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, ValueError, TypeError):
        return None

    if not isinstance(manifest, dict) or manifest.get("enabled") is not True:
        return None

    entry = manifest.get("frontend_entry")
    emotions = manifest.get("emotions")
    if not isinstance(entry, str) or not isinstance(emotions, dict):
        return None
    if not (feature_dir / entry).is_file():
        return None
    if not backend_path.is_file():
        return None

    valid_emotions = {
        emotion: filename
        for emotion, filename in emotions.items()
        if isinstance(emotion, str)
        and isinstance(filename, str)
        and Path(filename).name == filename
        and filename.lower().endswith(".png")
        and (feature_dir / filename).is_file()
    }
    if not valid_emotions:
        return None

    return {**manifest, "emotions": valid_emotions}


def expression_feature_available(expression_dir: str | None = None) -> bool:
    return get_expression_manifest(expression_dir) is not None


def _load_expression_backend(expression_dir: str | None = None) -> ModuleType | None:
    if not expression_feature_available(expression_dir):
        return None
    backend_path = get_expression_feature_dir(expression_dir) / "backend_filter.py"
    try:
        spec = importlib.util.spec_from_file_location(
            "optional_static_expression_filter",
            backend_path,
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        logger.warning("Optional expression backend is unavailable: {}", exc)
        return None


def process_expression_output(
    display_text: str,
    tts_text: str,
    expression_dir: str | None = None,
) -> dict[str, Any]:
    """Filter one completed reply; failures preserve the original output."""
    fallback = {
        "display_text": display_text,
        "tts_text": tts_text,
        "emotion": None,
    }
    module = _load_expression_backend(expression_dir)
    if module is None:
        logger.warning(
            "情绪协议检查：表情目录 {} 不可用，无法解析情绪尾标",
            (expression_dir or "").strip() or DEFAULT_EXPRESSION_DIR,
        )
        return fallback
    processor = getattr(module, "process_output", None)
    if not callable(processor):
        logger.warning(
            "情绪协议检查：表情目录 {} 缺少可用的 process_output",
            (expression_dir or "").strip() or DEFAULT_EXPRESSION_DIR,
        )
        return fallback
    try:
        result = processor(display_text, tts_text)
        if not isinstance(result, dict):
            return fallback
        cleaned_display = result.get("display_text")
        cleaned_tts = result.get("tts_text")
        emotion = result.get("emotion")
        raw_emotion = result.get("raw_emotion")
        protocol_detected = result.get("protocol_detected")
        manifest = get_expression_manifest(expression_dir)
        if (
            not isinstance(cleaned_display, str)
            or not isinstance(cleaned_tts, str)
            or not isinstance(emotion, (str, type(None)))
            or not isinstance(raw_emotion, (str, type(None)))
            or not isinstance(protocol_detected, bool)
            or (
                emotion is not None
                and emotion not in (manifest or {}).get("emotions", {})
            )
        ):
            logger.warning(
                "情绪协议检查：表情目录 {} 返回了无效的解析结果",
                (expression_dir or "").strip() or DEFAULT_EXPRESSION_DIR,
            )
            return fallback

        directory_name = (expression_dir or "").strip() or DEFAULT_EXPRESSION_DIR
        if not protocol_detected:
            logger.warning(
                "情绪协议检查：表情目录 {}，模型回复末尾未检测到“当前我的情绪为：xx”",
                directory_name,
            )
        elif emotion is None:
            logger.warning(
                "情绪协议检查：表情目录 {}，原始标签={}，未匹配可用表情图片",
                directory_name,
                raw_emotion,
            )
        else:
            image_filename = (manifest or {}).get("emotions", {}).get(emotion)
            image_path = get_expression_feature_dir(expression_dir) / image_filename
            try:
                logged_image_path = image_path.relative_to(PROJECT_ROOT).as_posix()
            except ValueError:
                logged_image_path = image_path.as_posix()
            logger.info(
                "情绪协议检查：表情目录 {}，原始标签={}，最终情绪={}，图片={}，文件存在={}",
                directory_name,
                raw_emotion,
                emotion,
                logged_image_path,
                image_path.is_file(),
            )
        return {
            "display_text": cleaned_display,
            "tts_text": cleaned_tts,
            "emotion": emotion,
        }
    except Exception as exc:
        logger.warning("Optional expression output was ignored: {}", exc)
        return fallback
