"""Account/character-scoped checkpoints shared by all chat histories."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from loguru import logger

from .chat_history_manager import get_character_history_dir, get_full_history_dir


STATE_FILE_NAME = "conversation_state.json"
STATE_VERSION = 1
_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _get_lock(history_root: str | Path, conf_uid: str) -> threading.RLock:
    key = (str(Path(history_root)), conf_uid)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _state_path(conf_uid: str, history_root: str | Path) -> Path:
    return (
        get_character_history_dir(conf_uid, history_root, create=True)
        / STATE_FILE_NAME
    )


def _read_payload_unlocked(path: Path) -> dict:
    if not path.exists():
        return {"version": STATE_VERSION}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read shared conversation state {}: {}", path, exc)
        return {"version": STATE_VERSION}
    if not isinstance(payload, dict):
        return {"version": STATE_VERSION}
    payload["version"] = STATE_VERSION
    return payload


def _write_payload_unlocked(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def read_state(
    conf_uid: str,
    state_key: str,
    history_root: str | Path = "chat_history",
) -> dict | None:
    """Return a copied shared state, or None before its first migration."""
    path = _state_path(conf_uid, history_root)
    with _get_lock(history_root, conf_uid):
        state = _read_payload_unlocked(path).get(state_key)
    return state.copy() if isinstance(state, dict) else None


def write_state(
    conf_uid: str,
    state_key: str,
    state: dict,
    history_root: str | Path = "chat_history",
) -> bool:
    """Atomically replace one shared checkpoint without touching other keys."""
    path = _state_path(conf_uid, history_root)
    try:
        with _get_lock(history_root, conf_uid):
            payload = _read_payload_unlocked(path)
            payload[state_key] = state.copy()
            payload["version"] = STATE_VERSION
            _write_payload_unlocked(path, payload)
        return True
    except Exception as exc:
        logger.error("Failed to write shared conversation state {}: {}", state_key, exc)
        return False


def reserve_interval_event(
    conf_uid: str,
    state_key: str,
    interval: int,
    *,
    force: bool = False,
    history_root: str | Path = "chat_history",
) -> tuple[int, bool]:
    """Atomically advance a shared prompt counter and reserve an interval event."""
    if interval < 1:
        raise ValueError("interval must be at least 1")
    path = _state_path(conf_uid, history_root)
    with _get_lock(history_root, conf_uid):
        payload = _read_payload_unlocked(path)
        state = payload.get(state_key, {})
        if not isinstance(state, dict):
            state = {}
        prompt_count = state.get("normal_prompt_count", 0)
        if isinstance(prompt_count, bool) or not isinstance(prompt_count, int):
            prompt_count = 0
        prompt_count += 1
        last_event = state.get("last_event_prompt")
        if isinstance(last_event, bool) or not isinstance(last_event, int):
            last_event = None
        should_run = (
            force
            or last_event is None
            or prompt_count - last_event >= interval
        )
        state["normal_prompt_count"] = prompt_count
        if should_run:
            state["last_event_prompt"] = prompt_count
        payload[state_key] = state
        payload["version"] = STATE_VERSION
        _write_payload_unlocked(path, payload)
    return prompt_count, should_run


def read_legacy_states(
    conf_uid: str,
    state_key: str,
    history_root: str | Path = "chat_history",
) -> list[tuple[str, dict]]:
    """Read per-history states in chronological history-file order."""
    history_dir = get_full_history_dir(conf_uid, history_root)
    if not history_dir.exists():
        return []
    states: list[tuple[str, dict]] = []
    for path in sorted(history_dir.glob("*.json"), key=lambda item: item.name):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list) or not payload:
            continue
        metadata = payload[0]
        if not isinstance(metadata, dict) or metadata.get("role") != "metadata":
            continue
        state = metadata.get(state_key)
        if isinstance(state, dict) and state:
            states.append((path.stem, state.copy()))
    return states
