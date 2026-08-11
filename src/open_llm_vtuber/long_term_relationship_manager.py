"""Persistent long-term relationship summary for single-user conversations."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger
from prompts import prompt_builder

from .chat_history_manager import (
    get_character_history_dir,
    get_metadata,
    update_metadate,
)


UPDATE_INTERVAL = 4
INJECTION_INTERVAL = 5
MAX_RELATIONSHIP_CHARACTERS = 200
LONG_TERM_RELATIONSHIP_METADATA_KEY = "long_term_relationship"


RelationshipSummaryCallback = Callable[[str, str], Awaitable[str]]


class LongTermRelationshipManager:
    """Maintain one long-term relationship file per character."""

    def __init__(
        self,
        relationship_path: str | Path | None = None,
        memory_path: str | Path | None = None,
        history_root: str | Path = "chat_history",
        update_interval: int = UPDATE_INTERVAL,
        injection_interval: int = INJECTION_INTERVAL,
        max_characters: int = MAX_RELATIONSHIP_CHARACTERS,
    ) -> None:
        if update_interval < 1:
            raise ValueError("update_interval must be at least 1")
        if injection_interval < 1:
            raise ValueError("injection_interval must be at least 1")
        if max_characters < 1:
            raise ValueError("max_characters must be at least 1")

        self.relationship_path = (
            Path(relationship_path) if relationship_path is not None else None
        )
        self.memory_path = Path(memory_path) if memory_path is not None else None
        self.history_root = Path(history_root)
        self.update_interval = update_interval
        self.injection_interval = injection_interval
        self.max_characters = max_characters
        self._history_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._relationship_locks: dict[str, asyncio.Lock] = {}

    def _get_history_lock(self, conf_uid: str, history_uid: str) -> asyncio.Lock:
        key = (conf_uid, history_uid)
        lock = self._history_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._history_locks[key] = lock
        return lock

    def _get_relationship_path(self, conf_uid: str) -> Path:
        if self.relationship_path is not None:
            return self.relationship_path
        return get_character_history_dir(conf_uid, self.history_root) / (
            "long_term_relationship.md"
        )

    def _get_memory_path(self, conf_uid: str) -> Path:
        if self.memory_path is not None:
            return self.memory_path
        return get_character_history_dir(conf_uid, self.history_root) / (
            "long_term_memory.md"
        )

    def _get_relationship_lock(self, conf_uid: str) -> asyncio.Lock:
        key = conf_uid if self.relationship_path is None else "__fixed__"
        lock = self._relationship_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._relationship_locks[key] = lock
        return lock

    @staticmethod
    def _normalize_text(value: object) -> str:
        return " ".join(str(value).strip().split())

    def parse_summary(self, raw_output: str) -> str:
        """Parse the model's exact JSON object and validate its text length."""
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise ValueError("Long-term relationship summary is empty")

        text = raw_output.strip()
        fenced_match = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced_match:
            text = fenced_match.group(1).strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            object_start = text.find("{")
            if object_start < 0:
                raise ValueError(
                    "Long-term relationship summary does not contain JSON"
                )
            try:
                payload, _ = json.JSONDecoder().raw_decode(text[object_start:])
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Long-term relationship summary contains invalid JSON"
                ) from exc

        if not isinstance(payload, dict):
            raise ValueError("Long-term relationship summary must be a JSON object")
        if set(payload) != {"long_term_relationship"}:
            raise ValueError(
                "Long-term relationship JSON must contain only "
                "long_term_relationship"
            )

        raw_relationship = payload["long_term_relationship"]
        if not isinstance(raw_relationship, str):
            raise ValueError("long_term_relationship must be a string")
        relationship = self._normalize_text(raw_relationship)
        if not relationship:
            raise ValueError("long_term_relationship cannot be empty")
        if len(relationship) > self.max_characters:
            raise ValueError(
                f"long_term_relationship must not exceed {self.max_characters} characters"
            )
        return relationship

    @staticmethod
    def _get_state(conf_uid: str, history_uid: str) -> dict:
        metadata = get_metadata(conf_uid, history_uid)
        state = metadata.get(LONG_TERM_RELATIONSHIP_METADATA_KEY, {})
        if not isinstance(state, dict):
            return {}
        return state.copy()

    @staticmethod
    def _save_state(conf_uid: str, history_uid: str, state: dict) -> bool:
        return update_metadate(
            conf_uid,
            history_uid,
            {LONG_TERM_RELATIONSHIP_METADATA_KEY: state},
        )

    @staticmethod
    def _read_text(path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to read {}: {}", path, exc)
            return ""

    def _write_relationship_unlocked(
        self,
        relationship_path: Path,
        relationship: str,
    ) -> None:
        content = json.dumps(
            {"long_term_relationship": relationship},
            ensure_ascii=False,
            indent=2,
        ) + "\n"

        relationship_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: str | None = None
        try:
            file_descriptor, temp_path = tempfile.mkstemp(
                prefix=f".{relationship_path.name}.",
                suffix=".tmp",
                dir=relationship_path.parent,
            )
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as temp_file:
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, relationship_path)
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    async def read_relationship_file(self, conf_uid: str = "") -> str:
        """Return all relationship-file content, or an empty string if absent."""
        relationship_path = self._get_relationship_path(conf_uid)
        async with self._get_relationship_lock(conf_uid):
            return self._read_text(relationship_path)

    async def consume_injection(self, conf_uid: str, history_uid: str) -> str:
        """Return relationship context on the first turn of a new conversation
        and every N user prompts thereafter (default: every 5th).

        New conversations immediately receive the existing relationship file so
        that relationships built in previous conversations carry over.
        """
        if not conf_uid or not history_uid:
            return ""

        async with self._get_history_lock(conf_uid, history_uid):
            state = self._get_state(conf_uid, history_uid)
            is_new_history = not state  # first turn of a new conversation

            user_prompt_count = state.get("user_prompt_count", 0)
            if isinstance(user_prompt_count, bool) or not isinstance(
                user_prompt_count, int
            ):
                user_prompt_count = 0
            user_prompt_count += 1
            state["user_prompt_count"] = user_prompt_count
            state.setdefault("pending_update_turns", 0)
            if not self._save_state(conf_uid, history_uid, state):
                logger.error(
                    "Failed to persist relationship injection count for history {}",
                    history_uid,
                )
                return ""

        if not is_new_history and user_prompt_count % self.injection_interval != 0:
            return ""

        relationship_file = await self.read_relationship_file(conf_uid)
        if not relationship_file.strip():
            return ""
        try:
            self.parse_summary(relationship_file)
        except ValueError as exc:
            logger.error("Relationship file is invalid and will not be injected: {}", exc)
            return ""

        return prompt_builder.build_long_relationship_injection(
            relationship_file
        )

    async def record_completed_turn(
        self,
        conf_uid: str,
        history_uid: str,
        summarize: RelationshipSummaryCallback,
    ) -> bool:
        """Count a turn and periodically update this character's file."""
        if not conf_uid or not history_uid:
            return False

        async with self._get_history_lock(conf_uid, history_uid):
            state = self._get_state(conf_uid, history_uid)
            pending_turns = state.get("pending_update_turns", 0)
            if isinstance(pending_turns, bool) or not isinstance(pending_turns, int):
                pending_turns = 0
            pending_turns += 1
            state["pending_update_turns"] = pending_turns
            state.setdefault("user_prompt_count", 0)
            if not self._save_state(conf_uid, history_uid, state):
                logger.error(
                    "Failed to persist relationship turn for history {}",
                    history_uid,
                )
                return False

            if pending_turns < self.update_interval:
                return False

            relationship_path = self._get_relationship_path(conf_uid)
            memory_path = self._get_memory_path(conf_uid)
            async with self._get_relationship_lock(conf_uid):
                memory_file = self._read_text(memory_path)
                relationship_file = self._read_text(relationship_path)
                try:
                    raw_output = await summarize(memory_file, relationship_file)
                    relationship = self.parse_summary(raw_output)
                    self._write_relationship_unlocked(
                        relationship_path, relationship
                    )
                except Exception as exc:
                    logger.error(
                        "Long-term relationship update failed for history {}: {}",
                        history_uid,
                        exc,
                    )
                    return False

            relationship_output = json.dumps(
                {"long_term_relationship": relationship},
                ensure_ascii=False,
                indent=2,
            )
            logger.info(
                "Long-term relationship output for history {}:\n{}",
                history_uid,
                relationship_output,
            )

            state["pending_update_turns"] = pending_turns - self.update_interval
            if not self._save_state(conf_uid, history_uid, state):
                logger.error(
                    "Relationship was saved but its history checkpoint failed for {}",
                    history_uid,
                )
                return False

            logger.info(
                "Long-term relationship processed {} completed turns",
                self.update_interval,
            )
            return True


long_term_relationship_manager = LongTermRelationshipManager()
