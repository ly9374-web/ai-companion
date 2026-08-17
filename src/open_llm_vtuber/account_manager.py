"""Local account profiles backed by directories below ``chat_history``."""

from __future__ import annotations

import json
import base64
import hashlib
import hmac
import os
import re
import secrets
import shutil
import tempfile
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

from loguru import logger

from .config_manager.utils import read_yaml


CHAT_HISTORY_ROOT = Path("chat_history")
DEFAULT_ACCOUNT_NAME = "Jason"
MAX_ACCOUNT_NAME_LENGTH = 50
MIN_PASSWORD_LENGTH = 3
MAX_PASSWORD_LENGTH = 128
_INVALID_ACCOUNT_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_ACCOUNT_LOCK = threading.RLock()
_ACCOUNT_MARKER_NAME = ".account.json"
_LAYOUT_MARKER_NAME = ".account-layout-v2.json"
_MIGRATION_CONFLICT_DIR = ".migration-conflicts"
_PASSWORD_ITERATIONS = 210_000
_MAX_PERSISTENT_SESSIONS = 10
_CONVERSATION_STARTERS_FEATURE = "conversation_starters_v1"
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

LONG_TERM_RELATIONSHIP_DEFAULT = {
    "long_term_relationship": "暂无",
}
SHORT_TERM_RELATIONSHIP_DEFAULT = {
    "short_term_relationship": (
        "这是你第一次见到这位用户，你很高兴如果和他聊的开心，"
        "就能认识一位新朋友。和用户提到这是你们第一次见"
    ),
}


class InvalidAccountName(ValueError):
    """Raised when an account name cannot safely be used as a directory."""


class AccountAlreadyExists(ValueError):
    """Raised when registration would create a case-insensitive duplicate."""


class InvalidPassword(ValueError):
    """Raised when a new password does not meet the local account rules."""


class AuthenticationFailed(ValueError):
    """Raised when an account password or persistent session is invalid."""


def normalize_password(value: object) -> str:
    """Validate a password without altering any of its characters."""
    if not isinstance(value, str):
        raise InvalidPassword("密码无效")
    if len(value) < MIN_PASSWORD_LENGTH:
        raise InvalidPassword(f"密码至少需要 {MIN_PASSWORD_LENGTH} 位")
    if len(value) > MAX_PASSWORD_LENGTH:
        raise InvalidPassword(f"密码不能超过 {MAX_PASSWORD_LENGTH} 位")
    return value


def _password_record(password: str) -> dict[str, object]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS
    )
    return {
        "algorithm": "pbkdf2-sha256",
        "iterations": _PASSWORD_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
    }


def _password_matches(password: str, record: object) -> bool:
    if not isinstance(record, dict) or record.get("algorithm") != "pbkdf2-sha256":
        return False
    try:
        iterations = int(record["iterations"])
        salt = base64.b64decode(str(record["salt"]), validate=True)
        expected = base64.b64decode(str(record["hash"]), validate=True)
    except (KeyError, TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual, expected)


def _session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_account_name(value: object) -> str:
    """Return a safe NFC display name while preserving the chosen casing."""
    if not isinstance(value, str):
        raise InvalidAccountName("账号名称无效")
    name = unicodedata.normalize("NFC", value).strip()
    if not name or len(name) > MAX_ACCOUNT_NAME_LENGTH:
        raise InvalidAccountName("账号名称长度必须为 1–50 个字符")
    if (
        name in {".", ".."}
        or name.startswith(".")
        or _INVALID_ACCOUNT_CHARACTERS.search(name)
    ):
        raise InvalidAccountName("账号名称包含不支持的字符")
    if name.endswith((" ", ".")):
        raise InvalidAccountName("账号名称不能以空格或句点结尾")
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise InvalidAccountName("账号名称与系统保留名称冲突")
    return name


def account_key(value: str) -> str:
    """Return the case-insensitive identity key for an account name."""
    return unicodedata.normalize("NFC", value).casefold()


def get_account_history_root(account_name: str) -> Path:
    """Return the validated directory for one canonical account name."""
    return CHAT_HISTORY_ROOT / normalize_account_name(account_name)


def get_character_conf_uids() -> list[str]:
    """Read every configured role UID, with the default role first."""
    config_paths = [Path("conf.yaml")]
    characters_dir = Path("characters")
    if characters_dir.is_dir():
        config_paths.extend(sorted(characters_dir.rglob("*.yaml")))

    conf_uids: list[str] = []
    for config_path in config_paths:
        try:
            payload = read_yaml(str(config_path)) or {}
            conf_uid = payload.get("character_config", {}).get("conf_uid")
        except Exception as exc:
            logger.warning("Cannot read character UID from {}: {}", config_path, exc)
            continue
        if isinstance(conf_uid, str) and conf_uid and conf_uid not in conf_uids:
            conf_uids.append(conf_uid)
    return conf_uids


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """Atomically replace a small JSON metadata file."""
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


def _write_json_if_missing(path: Path, payload: dict[str, object]) -> None:
    if path.exists():
        return
    _write_json(path, payload)


def _read_account_marker(account_name: str) -> dict[str, object]:
    marker_path = get_account_history_root(account_name) / _ACCOUNT_MARKER_NAME
    with marker_path.open("r", encoding="utf-8") as marker_file:
        payload = json.load(marker_file)
    if not isinstance(payload, dict):
        raise ValueError("Invalid account metadata")
    return payload


def _write_account_marker(account_name: str, payload: dict[str, object]) -> None:
    marker_path = get_account_history_root(account_name) / _ACCOUNT_MARKER_NAME
    _write_json(marker_path, payload)


def has_conversation_starters(account_name: str) -> bool:
    """Return the persisted opt-in assigned only during eligible registration."""
    account = resolve_account_name(account_name)
    if account is None:
        return False
    with _ACCOUNT_LOCK:
        marker = _read_account_marker(account)
        features = marker.get("features")
        return bool(
            isinstance(features, dict)
            and features.get(_CONVERSATION_STARTERS_FEATURE) is True
        )


def ensure_character_profile(account_name: str, conf_uid: str) -> Path:
    """Create missing default files for one account/role without overwriting data."""
    canonical_account = normalize_account_name(account_name)
    if not isinstance(conf_uid, str) or not conf_uid.strip():
        raise ValueError("conf_uid cannot be empty")
    if Path(conf_uid).name != conf_uid or conf_uid in {".", ".."}:
        raise ValueError("Invalid character UID")

    role_dir = CHAT_HISTORY_ROOT / canonical_account / conf_uid
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "full_history").mkdir(exist_ok=True)
    (role_dir / "long_term_memory.md").touch(exist_ok=True)
    _write_json_if_missing(
        role_dir / "long_term_relationship.md",
        LONG_TERM_RELATIONSHIP_DEFAULT,
    )
    _write_json_if_missing(
        role_dir / "short_term_relationship.md",
        SHORT_TERM_RELATIONSHIP_DEFAULT,
    )
    return role_dir


def ensure_account_profile(account_name: str) -> str:
    """Ensure every currently configured role exists for an account."""
    canonical_account = normalize_account_name(account_name)
    account_dir = CHAT_HISTORY_ROOT / canonical_account
    account_dir.mkdir(parents=True, exist_ok=True)
    _write_json_if_missing(
        account_dir / _ACCOUNT_MARKER_NAME,
        {"version": 1, "account": canonical_account},
    )
    for conf_uid in get_character_conf_uids():
        ensure_character_profile(canonical_account, conf_uid)
    return canonical_account


def _migrate_legacy_jason_data() -> None:
    """Move the former role-first layout below the default Jason account once."""
    CHAT_HISTORY_ROOT.mkdir(parents=True, exist_ok=True)
    layout_marker = CHAT_HISTORY_ROOT / _LAYOUT_MARKER_NAME
    if layout_marker.is_file():
        return

    role_uids = get_character_conf_uids()
    role_keys = {account_key(conf_uid): conf_uid for conf_uid in role_uids}
    legacy_role_dirs: list[Path] = []
    for entry in CHAT_HISTORY_ROOT.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if account_key(entry.name) not in role_keys:
            continue

        # A legacy role directory stores memory files/full_history directly.
        # A profile created by the old account bug contains role directories one
        # level below it and must never be silently moved into Jason.
        contains_nested_role = any(
            child.is_dir() and account_key(child.name) in role_keys
            for child in entry.iterdir()
        )
        if not contains_nested_role:
            legacy_role_dirs.append(entry)

    jason_dir = CHAT_HISTORY_ROOT / DEFAULT_ACCOUNT_NAME
    if legacy_role_dirs:
        jason_dir.mkdir(parents=True, exist_ok=True)
    for source in legacy_role_dirs:
        canonical_uid = role_keys[account_key(source.name)]
        destination = jason_dir / canonical_uid
        if destination.exists():
            conflict_root = CHAT_HISTORY_ROOT / _MIGRATION_CONFLICT_DIR
            conflict_root.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            preserved_path = conflict_root / f"{source.name}-{timestamp}"
            shutil.move(str(source), str(preserved_path))
            logger.warning(
                "Preserved conflicting legacy history {} at {}; active data remains {}",
                source,
                preserved_path,
                destination,
            )
            continue
        shutil.move(str(source), str(destination))
        logger.info("Migrated legacy history {} to {}", source, destination)

    _write_json_if_missing(layout_marker, {"version": 2})


def _adopt_existing_account_profiles() -> None:
    """Add markers to account folders created before markers were introduced."""
    role_keys = {account_key(conf_uid) for conf_uid in get_character_conf_uids()}
    for entry in CHAT_HISTORY_ROOT.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if account_key(entry.name) in role_keys:
            continue
        marker = entry / _ACCOUNT_MARKER_NAME
        if marker.is_file():
            continue
        looks_like_account = any(
            child.is_dir()
            and account_key(child.name) in role_keys
            and (
                (child / "full_history").is_dir()
                or (child / "long_term_memory.md").is_file()
            )
            for child in entry.iterdir()
        )
        if looks_like_account:
            _write_json_if_missing(
                marker,
                {"version": 1, "account": entry.name},
            )
            logger.info("Adopted existing account profile {}", entry)


def _prepare_account_layout() -> None:
    _migrate_legacy_jason_data()
    _adopt_existing_account_profiles()


def _account_directories() -> list[Path]:
    if not CHAT_HISTORY_ROOT.is_dir():
        return []
    return [
        entry
        for entry in CHAT_HISTORY_ROOT.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(".")
        and (entry / _ACCOUNT_MARKER_NAME).is_file()
    ]


def resolve_account_name(account_name: object) -> str | None:
    """Resolve an existing account using Unicode-aware case-insensitive matching."""
    try:
        requested = normalize_account_name(account_name)
    except InvalidAccountName:
        return None
    with _ACCOUNT_LOCK:
        _prepare_account_layout()
        requested_key = account_key(requested)
        for account_dir in _account_directories():
            if account_key(account_dir.name) == requested_key:
                ensure_account_profile(account_dir.name)
                return account_dir.name
        if requested_key == account_key(DEFAULT_ACCOUNT_NAME):
            ensure_account_profile(DEFAULT_ACCOUNT_NAME)
            return DEFAULT_ACCOUNT_NAME
    return None


def register_account(account_name: object, password: object) -> str:
    """Create a new account and all current role profiles."""
    requested = normalize_account_name(account_name)
    validated_password = normalize_password(password)
    with _ACCOUNT_LOCK:
        _prepare_account_layout()
        requested_key = account_key(requested)
        reserved_role_keys = {
            account_key(conf_uid) for conf_uid in get_character_conf_uids()
        }
        if requested_key in reserved_role_keys:
            raise InvalidAccountName("账号名称与角色标识冲突")
        if requested_key == account_key(DEFAULT_ACCOUNT_NAME):
            ensure_account_profile(DEFAULT_ACCOUNT_NAME)
            raise AccountAlreadyExists("账号已存在")
        if any(
            account_key(account_dir.name) == requested_key
            for account_dir in _account_directories()
        ):
            raise AccountAlreadyExists("账号已存在")

        account_dir = CHAT_HISTORY_ROOT / requested
        if account_dir.exists():
            raise AccountAlreadyExists("账号名称已被占用")
        created_account_dir = False
        try:
            account_dir.mkdir(parents=True, exist_ok=False)
            created_account_dir = True
            ensure_account_profile(requested)
            marker = _read_account_marker(requested)
            marker.update(
                {
                    "version": 2,
                    "account": requested,
                    "password": _password_record(validated_password),
                    "sessions": [],
                    "features": {
                        _CONVERSATION_STARTERS_FEATURE: requested_key.endswith("cs")
                    },
                }
            )
            _write_account_marker(requested, marker)
        except Exception:
            if created_account_dir and account_dir.exists():
                shutil.rmtree(account_dir)
            raise
    return requested


def authenticate_account(account_name: object, password: object) -> str:
    """Validate an account password and return its canonical display name."""
    if not isinstance(password, str):
        raise AuthenticationFailed("账号或密码错误")
    account = resolve_account_name(account_name)
    if account is None:
        raise AuthenticationFailed("账号或密码错误")

    with _ACCOUNT_LOCK:
        marker = _read_account_marker(account)
        password_data = marker.get("password")
        # The original local profile predates passwords. Give only the built-in
        # Jason account its documented initial password during the migration.
        if password_data is None and account_key(account) == account_key(DEFAULT_ACCOUNT_NAME):
            password_data = _password_record("123")
            marker.update({"version": 2, "password": password_data, "sessions": []})
            _write_account_marker(account, marker)
        if not _password_matches(password, password_data):
            raise AuthenticationFailed("账号或密码错误")
    return account


def create_persistent_session(account_name: str) -> str:
    """Issue a persistent opaque token while storing only its digest."""
    account = resolve_account_name(account_name)
    if account is None:
        raise AuthenticationFailed("账号或密码错误")
    token = secrets.token_urlsafe(32)
    with _ACCOUNT_LOCK:
        marker = _read_account_marker(account)
        sessions = marker.get("sessions")
        if not isinstance(sessions, list):
            sessions = []
        valid_sessions = [value for value in sessions if isinstance(value, str)]
        valid_sessions.append(_session_digest(token))
        marker["sessions"] = valid_sessions[-_MAX_PERSISTENT_SESSIONS:]
        marker["version"] = 2
        _write_account_marker(account, marker)
    return token


def resolve_authenticated_session(account_name: object, token: object) -> str | None:
    """Resolve an account only when its persistent session token is valid."""
    if not isinstance(token, str) or not token or len(token) > 256:
        return None
    account = resolve_account_name(account_name)
    if account is None:
        return None
    token_digest = _session_digest(token)
    with _ACCOUNT_LOCK:
        marker = _read_account_marker(account)
        sessions = marker.get("sessions")
        if not isinstance(sessions, list):
            return None
        if any(
            isinstance(value, str) and hmac.compare_digest(value, token_digest)
            for value in sessions
        ):
            return account
    return None


def revoke_persistent_session(account_name: object, token: object) -> None:
    """Remove one persistent login token if it exists."""
    if not isinstance(token, str) or not token:
        return
    account = resolve_account_name(account_name)
    if account is None:
        return
    token_digest = _session_digest(token)
    with _ACCOUNT_LOCK:
        marker = _read_account_marker(account)
        sessions = marker.get("sessions")
        if not isinstance(sessions, list):
            return
        marker["sessions"] = [
            value
            for value in sessions
            if not (isinstance(value, str) and hmac.compare_digest(value, token_digest))
        ]
        _write_account_marker(account, marker)
