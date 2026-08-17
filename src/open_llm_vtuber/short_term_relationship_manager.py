"""Persistent short-term relationship summary for single-user conversations."""

from __future__ import annotations

import asyncio
import inspect
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
    get_recent_normal_turns,
    update_metadate,
)
from .conversation_state_manager import read_legacy_states, read_state, write_state


UPDATE_INTERVAL = 6
INJECTION_INTERVAL = 6
CONTEXT_TURN_LIMIT = 15
SHORT_TERM_RELATIONSHIP_METADATA_KEY = "short_term_relationship"
SHORT_TERM_RELATIONSHIP_PLACEHOLDER = json.dumps(
    {
        "short_term_relationship": (
            "你与用户的短期关系尚未形成，目前暂无足够的近期互动。"
        )
    },
    ensure_ascii=False,
    indent=2,
) + "\n"


RelationshipSummaryCallback = Callable[
    [list[dict[str, str]], str, str, str], Awaitable[str]
]


class ShortTermRelationshipManager:
    """Maintain one short-term relationship file per character."""

    def __init__(
        self,
        relationship_path: str | Path | None = None,
        long_term_relationship_path: str | Path | None = None,
        history_root: str | Path = "chat_history",
        update_interval: int = UPDATE_INTERVAL,
        injection_interval: int = INJECTION_INTERVAL,
    ) -> None:
        if update_interval < 1:
            raise ValueError("update_interval must be at least 1")
        if injection_interval < 1:
            raise ValueError("injection_interval must be at least 1")
        self.relationship_path = (
            Path(relationship_path) if relationship_path is not None else None
        )
        self.long_term_relationship_path = (
            Path(long_term_relationship_path)
            if long_term_relationship_path is not None
            else None
        )
        self.history_root = Path(history_root)
        self.update_interval = update_interval
        self.injection_interval = injection_interval
        self._history_locks: dict[str, asyncio.Lock] = {}
        self._relationship_locks: dict[str, asyncio.Lock] = {}
        self._update_locks: dict[str, asyncio.Lock] = {}
        self._summary_locks: dict[str, asyncio.Lock] = {}

    def _get_history_lock(self, conf_uid: str, history_uid: str) -> asyncio.Lock:
        key = conf_uid
        lock = self._history_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._history_locks[key] = lock
        return lock

    def _get_relationship_path(self, conf_uid: str) -> Path:
        if self.relationship_path is not None:
            return self.relationship_path
        return get_character_history_dir(conf_uid, self.history_root) / (
            "short_term_relationship.md"
        )

    def _get_long_term_relationship_path(self, conf_uid: str) -> Path:
        if self.long_term_relationship_path is not None:
            return self.long_term_relationship_path
        return get_character_history_dir(conf_uid, self.history_root) / (
            "long_term_relationship.md"
        )

    def _get_relationship_lock(self, conf_uid: str) -> asyncio.Lock:
        key = conf_uid if self.relationship_path is None else "__fixed__"
        lock = self._relationship_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._relationship_locks[key] = lock
        return lock

    def _get_update_lock(self, conf_uid: str) -> asyncio.Lock:
        key = conf_uid if self.relationship_path is None else "__fixed__"
        lock = self._update_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._update_locks[key] = lock
        return lock

    def _get_summary_lock(self, conf_uid: str, history_uid: str) -> asyncio.Lock:
        key = conf_uid
        lock = self._summary_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._summary_locks[key] = lock
        return lock

    @staticmethod
    def _normalize_text(value: object) -> str:
        return " ".join(str(value).strip().split())

    def parse_summary(self, raw_output: str) -> str:
        """Parse the exact JSON object requested by the short-relationship prompt."""
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise ValueError("Short-term relationship summary is empty")

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
                    "Short-term relationship summary does not contain JSON"
                )
            try:
                payload, _ = json.JSONDecoder().raw_decode(text[object_start:])
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Short-term relationship summary contains invalid JSON"
                ) from exc

        if not isinstance(payload, dict):
            raise ValueError("Short-term relationship summary must be a JSON object")
        if set(payload) != {"short_term_relationship"}:
            raise ValueError(
                "Short-term relationship JSON must contain only "
                "short_term_relationship"
            )

        raw_relationship = payload["short_term_relationship"]
        if not isinstance(raw_relationship, str):
            raise ValueError("short_term_relationship must be a string")
        relationship = self._normalize_text(raw_relationship)
        if not relationship:
            raise ValueError("short_term_relationship cannot be empty")
        return relationship

    def _get_state(self, conf_uid: str, history_uid: str) -> dict:
        if self.relationship_path is not None:
            metadata = (
                get_metadata(
                    conf_uid,
                    history_uid,
                    history_root=self.history_root,
                )
                if "history_root" in inspect.signature(get_metadata).parameters
                else get_metadata(conf_uid, history_uid)
            )
            state = metadata.get(SHORT_TERM_RELATIONSHIP_METADATA_KEY, {})
            return state.copy() if isinstance(state, dict) else {}
        state = read_state(
            conf_uid,
            SHORT_TERM_RELATIONSHIP_METADATA_KEY,
            self.history_root,
        )
        if state is not None:
            return state

        pending_turns: list[dict[str, str]] = []
        user_prompt_count = 0
        for _, legacy_state in read_legacy_states(
            conf_uid,
            SHORT_TERM_RELATIONSHIP_METADATA_KEY,
            self.history_root,
        ):
            legacy_pending = legacy_state.get("pending_turns", [])
            if isinstance(legacy_pending, list):
                pending_turns.extend(
                    turn.copy() for turn in legacy_pending if isinstance(turn, dict)
                )
            legacy_prompts = legacy_state.get("user_prompt_count", 0)
            if not isinstance(legacy_prompts, bool) and isinstance(
                legacy_prompts, int
            ):
                user_prompt_count += max(0, legacy_prompts)
        state = {
            "pending_turns": pending_turns,
            "user_prompt_count": user_prompt_count,
        }
        write_state(
            conf_uid,
            SHORT_TERM_RELATIONSHIP_METADATA_KEY,
            state,
            self.history_root,
        )
        return state.copy()

    def _save_state(self, conf_uid: str, history_uid: str, state: dict) -> bool:
        if self.relationship_path is not None:
            metadata = {SHORT_TERM_RELATIONSHIP_METADATA_KEY: state}
            if "history_root" in inspect.signature(update_metadate).parameters:
                return update_metadate(
                    conf_uid,
                    history_uid,
                    metadata,
                    history_root=self.history_root,
                )
            return update_metadate(conf_uid, history_uid, metadata)
        return write_state(
            conf_uid,
            SHORT_TERM_RELATIONSHIP_METADATA_KEY,
            state,
            self.history_root,
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
            {"short_term_relationship": relationship},
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
        """Return all short relationship file content, or empty if absent."""
        relationship_path = self._get_relationship_path(conf_uid)
        async with self._get_relationship_lock(conf_uid):
            return self._read_text(relationship_path)

    async def read_injection(self, conf_uid: str) -> str:
        """Return the current relationship snapshot or an explicit placeholder."""
        relationship_file = await self.read_relationship_file(conf_uid)
        if relationship_file.strip():
            try:
                self.parse_summary(relationship_file)
            except ValueError as exc:
                logger.error(
                    "Short relationship file is invalid; using placeholder instead: {}",
                    exc,
                )
                relationship_file = SHORT_TERM_RELATIONSHIP_PLACEHOLDER
        else:
            relationship_file = SHORT_TERM_RELATIONSHIP_PLACEHOLDER

        return prompt_builder.build_short_relationship_injection(
            relationship_file
        )

    async def consume_injection(
        self,
        conf_uid: str,
        history_uid: str,
        turn_number: int | None = None,
    ) -> str:
        """Return short relationship on turn one and every six completed turns after it.

        New conversations immediately receive the existing relationship file so
        that relationships built in previous conversations carry over.
        """
        if not conf_uid or not history_uid:
            return ""

        if turn_number is not None:
            if turn_number < 1:
                return ""
            if turn_number != 1 and (turn_number - 1) % self.injection_interval != 0:
                return ""
        else:
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
                state.setdefault("pending_turns", [])
                if not self._save_state(conf_uid, history_uid, state):
                    logger.error(
                        "Failed to persist short relationship injection count for {}",
                        history_uid,
                    )
                    return ""

            if not is_new_history and user_prompt_count % self.injection_interval != 0:
                return ""

        return await self.read_injection(conf_uid)

    async def record_completed_turn(
        self,
        conf_uid: str,
        history_uid: str,
        user_content: str,
        assistant_content: str,
        summarize: RelationshipSummaryCallback,
        browser_time: str = "",
    ) -> bool:
        """Record a turn and rewrite the relationship from up to 15 recent turns."""
        should_summarize = await self.record_turn(
            conf_uid,
            history_uid,
            user_content,
            assistant_content,
        )
        if not should_summarize:
            return False
        return (
            await self.summarize_pending_turns(
                conf_uid,
                history_uid,
                summarize,
                browser_time,
            )
            == "success"
        )

    async def record_turn(
        self,
        conf_uid: str,
        history_uid: str,
        user_content: str,
        assistant_content: str,
    ) -> bool:
        """Persist one completed turn and report whether a batch is ready."""
        if not conf_uid or not history_uid:
            return False

        user_content = self._normalize_text(user_content)
        assistant_content = self._normalize_text(assistant_content)
        if not user_content or not assistant_content:
            return False

        async with self._get_history_lock(conf_uid, history_uid):
            state = self._get_state(conf_uid, history_uid)
            pending_turns = state.get("pending_turns", [])
            if not isinstance(pending_turns, list):
                pending_turns = []
            pending_turns.append(
                {"user": user_content, "assistant": assistant_content}
            )
            state["pending_turns"] = pending_turns
            state.setdefault("user_prompt_count", 0)
            if not self._save_state(conf_uid, history_uid, state):
                logger.error(
                    "Failed to persist short relationship turn for history {}",
                    history_uid,
                )
                return False

            return len(pending_turns) >= self.update_interval

    async def summarize_pending_turns(
        self,
        conf_uid: str,
        history_uid: str,
        summarize: RelationshipSummaryCallback,
        browser_time: str = "",
        require_full_batch: bool = False,
    ) -> str:
        """Rewrite the short relationship from all pending completed turns."""
        if not conf_uid or not history_uid:
            return "empty"

        async with self._get_summary_lock(conf_uid, history_uid):
            return await self._summarize_pending_turns_unlocked(
                conf_uid,
                history_uid,
                summarize,
                browser_time,
                require_full_batch,
            )

    async def _summarize_pending_turns_unlocked(
        self,
        conf_uid: str,
        history_uid: str,
        summarize: RelationshipSummaryCallback,
        browser_time: str,
        require_full_batch: bool,
    ) -> str:
        """Process one pending batch while the per-history summary lock is held."""

        async with self._get_history_lock(conf_uid, history_uid):
            state = self._get_state(conf_uid, history_uid)
            pending_turns = state.get("pending_turns", [])
            if not isinstance(pending_turns, list):
                pending_turns = []
            turns_to_consume = (
                pending_turns[: self.update_interval]
                if require_full_batch
                else pending_turns[:]
            )
            if not turns_to_consume:
                return "empty"
            if require_full_batch and len(turns_to_consume) < self.update_interval:
                return "empty"

        latest_turns = get_recent_normal_turns(
            conf_uid,
            CONTEXT_TURN_LIMIT,
            self.history_root,
        )
        if not latest_turns:
            latest_turns = turns_to_consume[-CONTEXT_TURN_LIMIT:]

        relationship_path = self._get_relationship_path(conf_uid)
        long_term_relationship_path = self._get_long_term_relationship_path(
            conf_uid
        )
        async with self._get_update_lock(conf_uid):
            async with self._get_relationship_lock(conf_uid):
                long_term_relationship_file = self._read_text(
                    long_term_relationship_path
                )
                short_term_relationship_file = self._read_text(
                    relationship_path
                )
            try:
                raw_output = await summarize(
                    latest_turns,
                    long_term_relationship_file,
                    short_term_relationship_file,
                    browser_time,
                )
                relationship = self.parse_summary(raw_output)
                async with self._get_relationship_lock(conf_uid):
                    self._write_relationship_unlocked(
                        relationship_path, relationship
                    )
            except Exception as exc:
                logger.error(
                    "Manual short-term relationship update failed for history {}: {}",
                    history_uid,
                    exc,
                )
                return "error"

        async with self._get_history_lock(conf_uid, history_uid):
            state = self._get_state(conf_uid, history_uid)
            latest_pending = state.get("pending_turns", [])
            if not isinstance(latest_pending, list):
                latest_pending = []
            state["pending_turns"] = latest_pending[len(turns_to_consume) :]
            if not self._save_state(conf_uid, history_uid, state):
                logger.error(
                    "Manual short relationship was saved but its checkpoint failed for {}",
                    history_uid,
                )
                return "error"

        logger.info(
            "Short-term relationship summary used {} recent turns and processed {} pending turns",
            len(latest_turns),
            len(turns_to_consume),
        )
        return "success"


short_term_relationship_manager = ShortTermRelationshipManager()
