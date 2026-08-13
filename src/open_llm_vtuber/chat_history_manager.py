import os
import re
import json
import uuid
import tempfile
import threading
from pathlib import Path
from datetime import datetime
from typing import Literal, List, TypedDict, Optional
from loguru import logger


FULL_HISTORY_DIR_NAME = "full_history"
_HISTORY_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_HISTORY_LOCKS_GUARD = threading.Lock()


def _get_history_lock(conf_uid: str, history_uid: str) -> threading.RLock:
    key = (conf_uid, history_uid)
    with _HISTORY_LOCKS_GUARD:
        lock = _HISTORY_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _HISTORY_LOCKS[key] = lock
        return lock


def _write_history_atomic(filepath: str, history_data: list[dict]) -> None:
    """Replace one history JSON only after the complete new file is durable."""
    directory = os.path.dirname(filepath)
    temp_path: str | None = None
    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(filepath)}.",
            suffix=".tmp",
            dir=directory,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(history_data, temp_file, ensure_ascii=False, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, filepath)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


class HistoryMessage(TypedDict):
    role: Literal["human", "ai"]
    timestamp: str
    content: str
    # Optional display information for the message
    name: Optional[str]
    avatar: Optional[str]
    # Hidden model-only context. The frontend must not display this field.
    context_injections: Optional[dict[str, str]]


def _is_safe_filename(filename: str) -> bool:
    """Validate filename for safety and allowed characters"""
    if not filename or len(filename) > 255:
        return False

    # Allow alphanumeric, hyphen, underscore, and common unicode characters
    # Block any filesystem special characters, control characters, and path separators
    pattern = re.compile(r"^[\w\-_\u0020-\u007E\u00A0-\uFFFF]+$")
    return bool(pattern.match(filename))


def _sanitize_path_component(component: str) -> str:
    """Sanitize and validate a path component"""
    # Remove any path components, get just the basename
    sanitized = os.path.basename(component.strip())

    if not _is_safe_filename(sanitized):
        raise ValueError(f"Invalid characters in path component: {component}")

    return sanitized


def _ensure_conf_dir(conf_uid: str) -> str:
    """Ensure the directory for a specific conf exists and return its path"""
    if not conf_uid:
        raise ValueError("conf_uid cannot be empty")

    safe_conf_uid = _sanitize_path_component(conf_uid)
    base_dir = os.path.join("chat_history", safe_conf_uid)
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def get_character_history_dir(
    conf_uid: str,
    history_root: str | Path = "chat_history",
    create: bool = False,
) -> Path:
    """Return the safe per-character history directory for a conf UID."""
    if not conf_uid:
        raise ValueError("conf_uid cannot be empty")
    safe_conf_uid = _sanitize_path_component(conf_uid)
    if safe_conf_uid in {".", ".."}:
        raise ValueError(f"Invalid path component: {conf_uid}")
    directory = Path(history_root) / safe_conf_uid
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def get_full_history_dir(
    conf_uid: str,
    history_root: str | Path = "chat_history",
    create: bool = False,
) -> Path:
    """Return the directory that contains complete conversation JSON files."""
    directory = (
        get_character_history_dir(conf_uid, history_root, create=create)
        / FULL_HISTORY_DIR_NAME
    )
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def _ensure_full_history_dir(conf_uid: str) -> str:
    """Create full_history and migrate legacy JSON files into it."""
    conf_dir = Path(_ensure_conf_dir(conf_uid))
    history_dir = conf_dir / FULL_HISTORY_DIR_NAME
    history_dir.mkdir(parents=True, exist_ok=True)

    for legacy_path in conf_dir.glob("*.json"):
        destination = history_dir / legacy_path.name
        if destination.exists():
            logger.warning(
                "Cannot migrate legacy history because destination exists: {}",
                destination,
            )
            continue
        try:
            legacy_path.replace(destination)
            logger.info("Migrated history file to {}", destination)
        except FileNotFoundError:
            # Another request may have migrated the same file concurrently.
            continue
        except OSError as exc:
            logger.error("Failed to migrate history file {}: {}", legacy_path, exc)

    return str(history_dir)


def _get_safe_history_path(conf_uid: str, history_uid: str) -> str:
    """Get sanitized path for history file"""
    safe_conf_uid = _sanitize_path_component(conf_uid)
    safe_history_uid = _sanitize_path_component(history_uid)
    base_dir = _ensure_full_history_dir(safe_conf_uid)
    full_path = os.path.normpath(os.path.join(base_dir, f"{safe_history_uid}.json"))
    if not full_path.startswith(base_dir):
        raise ValueError("Invalid path: Path traversal detected")
    return full_path


def create_new_history(conf_uid: str) -> str:
    """Create a new history file with a unique ID and return the history_uid"""
    if not conf_uid:
        logger.warning("No conf_uid provided")
        return ""

    # Use uuid.uuid4().hex to generate a UUID without hyphens
    # New format: UUID_YYYY-MM-DD_HH-MM-SS
    history_uid = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex}"
    history_dir = _ensure_full_history_dir(conf_uid)

    # Create history file with empty metadata
    try:
        filepath = os.path.join(history_dir, f"{history_uid}.json")
        initial_data = [
            {
                "role": "metadata",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        ]
        with _get_history_lock(conf_uid, history_uid):
            _write_history_atomic(filepath, initial_data)
    except Exception as e:
        logger.error(f"Failed to create new history file: {e}")
        return ""

    logger.debug(f"Created new history file with empty metadata: {filepath}")
    return history_uid


def store_message(
    conf_uid: str,
    history_uid: str,
    role: Literal["human", "ai"],
    content: str,
    name: str | None = None,
    avatar: str | None = None,
    context_injections: dict[str, str] | None = None,
):
    """Store a message in a specific history file

    Args:
        conf_uid: Configuration unique identifier
        history_uid: History unique identifier
        role: Message role ("human" or "ai")
        content: Message content
        name: Optional display name (default None)
        avatar: Optional avatar URL (default None)
        context_injections: Optional model-only context snapshots
    """
    if not conf_uid or not history_uid:
        if not conf_uid:
            logger.warning("Missing conf_uid")
        if not history_uid:
            logger.warning("Missing history_uid")
        return

    filepath = _get_safe_history_path(conf_uid, history_uid)
    logger.debug(f"Storing {role} message to {filepath}")

    with _get_history_lock(conf_uid, history_uid):
        history_data = []
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    history_data = json.load(f)
            except Exception:
                logger.error(f"Failed to load history file: {filepath}")

        now_str = datetime.now().isoformat(timespec="seconds")
        new_item = {
            "role": role,
            "timestamp": now_str,
            "content": content,
        }
        if name is not None:
            new_item["name"] = name
        if avatar is not None:
            new_item["avatar"] = avatar
        if context_injections:
            new_item["context_injections"] = {
                key: value
                for key, value in context_injections.items()
                if isinstance(value, str) and value.strip()
            }
        history_data.append(new_item)
        _write_history_atomic(filepath, history_data)
    logger.debug(f"Successfully stored {role} message")


def get_metadata(conf_uid: str, history_uid: str) -> dict:
    """Get metadata from history file"""
    if not conf_uid or not history_uid:
        return {}

    filepath = _get_safe_history_path(conf_uid, history_uid)
    if not os.path.exists(filepath):
        return {}

    try:
        with _get_history_lock(conf_uid, history_uid):
            with open(filepath, "r", encoding="utf-8") as f:
                history_data = json.load(f)

        if history_data and history_data[0]["role"] == "metadata":
            return history_data[0]
    except Exception as e:
        logger.error(f"Failed to get metadata: {e}")
    return {}


def update_metadate(conf_uid: str, history_uid: str, metadata: dict) -> bool:
    """Set metadata in history file

    Updates existing metadata with new fields, preserving existing ones.
    If no metadata exists, creates new metadata entry.
    """
    if not conf_uid or not history_uid:
        return False

    filepath = _get_safe_history_path(conf_uid, history_uid)
    if not os.path.exists(filepath):
        return False

    try:
        with _get_history_lock(conf_uid, history_uid):
            if not os.path.exists(filepath):
                return False
            with open(filepath, "r", encoding="utf-8") as f:
                history_data = json.load(f)

            if history_data and history_data[0]["role"] == "metadata":
                history_data[0].update(metadata)
            else:
                new_metadata = {
                    "role": "metadata",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                new_metadata.update(metadata)
                history_data.insert(0, new_metadata)
            _write_history_atomic(filepath, history_data)

        logger.debug(f"Updated metadata for history {history_uid}")
        return True
    except Exception as e:
        logger.error(f"Failed to set metadata: {e}")
    return False


def update_metadata_state(
    conf_uid: str,
    history_uid: str,
    state_key: str,
    updates: dict,
) -> bool:
    """Atomically merge fields into one nested metadata state object."""
    if not conf_uid or not history_uid or not state_key:
        return False

    filepath = _get_safe_history_path(conf_uid, history_uid)
    try:
        with _get_history_lock(conf_uid, history_uid):
            if not os.path.exists(filepath):
                return False
            with open(filepath, "r", encoding="utf-8") as history_file:
                history_data = json.load(history_file)

            if history_data and history_data[0].get("role") == "metadata":
                metadata = history_data[0]
            else:
                metadata = {
                    "role": "metadata",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                history_data.insert(0, metadata)

            existing_state = metadata.get(state_key, {})
            state = existing_state.copy() if isinstance(existing_state, dict) else {}
            state.update(updates)
            metadata[state_key] = state
            _write_history_atomic(filepath, history_data)
        return True
    except Exception as exc:
        logger.error("Failed to update metadata state {}: {}", state_key, exc)
        return False


def get_history(conf_uid: str, history_uid: str) -> List[HistoryMessage]:
    """Read chat history for the given conf_uid and history_uid"""
    if not conf_uid or not history_uid:
        if not conf_uid:
            logger.warning("Missing conf_uid")
        if not history_uid:
            logger.warning("Missing history_uid")
        return []

    filepath = _get_safe_history_path(conf_uid, history_uid)

    if not os.path.exists(filepath):
        logger.warning(f"History file not found: {filepath}")
        return []

    try:
        with _get_history_lock(conf_uid, history_uid):
            with open(filepath, "r", encoding="utf-8") as f:
                history_data = json.load(f)
            # Filter out metadata
            return [msg for msg in history_data if msg["role"] != "metadata"]
    except Exception:
        return []


def delete_history(conf_uid: str, history_uid: str) -> bool:
    """Delete a specific history file"""
    if not conf_uid or not history_uid:
        logger.warning("Missing conf_uid or history_uid")
        return False

    filepath = _get_safe_history_path(conf_uid, history_uid)
    try:
        with _get_history_lock(conf_uid, history_uid):
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.debug(f"Successfully deleted history file: {filepath}")
                return True
    except Exception as e:
        logger.error(f"Failed to delete history file: {e}")
    return False


def get_history_list(conf_uid: str) -> List[dict]:
    """Get list of histories with their latest messages"""
    if not conf_uid:
        return []

    histories = []
    history_dir = _ensure_full_history_dir(conf_uid)
    empty_history_uids = []

    try:
        for filename in os.listdir(history_dir):
            if not filename.endswith(".json"):
                continue

            history_uid = filename[:-5]
            filepath = os.path.join(history_dir, filename)

            try:
                with _get_history_lock(conf_uid, history_uid):
                    with open(filepath, "r", encoding="utf-8") as f:
                        messages = json.load(f)

                    # Filter out metadata for checking if history is empty
                    actual_messages = [
                        msg for msg in messages if msg["role"] != "metadata"
                    ]
                    if not actual_messages:
                        empty_history_uids.append(history_uid)
                        continue

                    latest_message = actual_messages[-1].copy()
                    latest_message.pop("context_injections", None)
                    history_info = {
                        "uid": history_uid,
                        "latest_message": latest_message,
                        "timestamp": (
                            latest_message["timestamp"] if latest_message else None
                        ),
                    }
                    histories.append(history_info)
            except Exception as e:
                logger.error(f"Error reading history file {filename}: {e}")
                continue

        # Clean up empty histories if there are other non-empty ones
        if len(empty_history_uids) > 0 and len(os.listdir(history_dir)) > 1:
            for uid in empty_history_uids:
                try:
                    with _get_history_lock(conf_uid, uid):
                        empty_path = os.path.join(history_dir, f"{uid}.json")
                        if os.path.exists(empty_path):
                            os.remove(empty_path)
                    logger.info(f"Removed empty history file: {uid}")
                except Exception as e:
                    logger.error(f"Failed to remove empty history file {uid}: {e}")

        histories.sort(
            key=lambda x: x["timestamp"] if x["timestamp"] else "", reverse=True
        )
        return histories

    except Exception as e:
        logger.error(f"Error listing histories: {e}")
        return []


def modify_latest_message(
    conf_uid: str,
    history_uid: str,
    role: Literal["human", "ai", "system"],
    new_content: str,
) -> bool:
    """Modify the latest message in a specific history file if it matches the given role"""
    if not conf_uid or not history_uid:
        logger.warning("Missing conf_uid or history_uid")
        return False

    filepath = _get_safe_history_path(conf_uid, history_uid)
    if not os.path.exists(filepath):
        logger.warning(f"History file not found: {filepath}")
        return False

    try:
        with _get_history_lock(conf_uid, history_uid):
            if not os.path.exists(filepath):
                return False
            with open(filepath, "r", encoding="utf-8") as f:
                history_data = json.load(f)

            if not history_data:
                logger.warning("History is empty")
                return False

            latest_message = history_data[-1]
            if latest_message["role"] != role:
                logger.warning(
                    f"Latest message role ({latest_message['role']}) doesn't match requested role ({role})"
                )
                return False

            latest_message["content"] = new_content
            _write_history_atomic(filepath, history_data)

        logger.debug(f"Successfully modified latest {role} message")
        return True

    except Exception as e:
        logger.error(f"Failed to modify latest message: {e}")
        return False


def rename_history_file(
    conf_uid: str, old_history_uid: str, new_history_uid: str
) -> bool:
    """Rename a history file with a new history_uid"""
    if not conf_uid or not old_history_uid or not new_history_uid:
        logger.warning("Missing required parameters for rename")
        return False

    old_filepath = _get_safe_history_path(conf_uid, old_history_uid)
    new_filepath = _get_safe_history_path(conf_uid, new_history_uid)

    old_lock = _get_history_lock(conf_uid, old_history_uid)
    new_lock = _get_history_lock(conf_uid, new_history_uid)
    first_lock, second_lock = sorted((old_lock, new_lock), key=id)
    try:
        with first_lock:
            with second_lock:
                if os.path.exists(old_filepath):
                    os.replace(old_filepath, new_filepath)
                    logger.info(
                        f"Renamed history file from {old_history_uid} to {new_history_uid}"
                    )
                    return True
    except Exception as e:
        logger.error(f"Failed to rename history file: {e}")
    return False
