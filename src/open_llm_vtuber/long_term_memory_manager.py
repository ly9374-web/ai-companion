"""Persistent long-term memory for single-user conversations."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from loguru import logger
from prompts import prompt_builder

from .chat_history_manager import (
    get_character_history_dir,
    get_metadata,
    update_metadate,
)


SUMMARY_INTERVAL = 3
MAX_LONG_TERM_MEMORIES: int | None = None
LONG_TERM_MEMORY_METADATA_KEY = "long_term_memory"


@dataclass(frozen=True)
class LongTermMemory:
    name: str
    content: str


SummaryCallback = Callable[
    [list[dict[str, str]], list[LongTermMemory]], Awaitable[str]
]


class LongTermMemoryManager:
    """Track turns and maintain one long-term memory file per character."""

    def __init__(
        self,
        memory_path: str | Path | None = None,
        history_root: str | Path = "chat_history",
        summary_interval: int = SUMMARY_INTERVAL,
        max_memories: int | None = MAX_LONG_TERM_MEMORIES,
    ) -> None:
        if summary_interval < 1:
            raise ValueError("summary_interval must be at least 1")
        if max_memories is not None and max_memories < 1:
            raise ValueError("max_memories must be at least 1")

        self.memory_path = Path(memory_path) if memory_path is not None else None
        self.history_root = Path(history_root)
        self.summary_interval = summary_interval
        self.max_memories = max_memories
        self._history_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._memory_locks: dict[str, asyncio.Lock] = {}

    def _get_history_lock(self, conf_uid: str, history_uid: str) -> asyncio.Lock:
        key = (conf_uid, history_uid)
        lock = self._history_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._history_locks[key] = lock
        return lock

    def _get_memory_path(self, conf_uid: str) -> Path:
        if self.memory_path is not None:
            return self.memory_path
        return get_character_history_dir(conf_uid, self.history_root) / (
            "long_term_memory.md"
        )

    def _get_memory_lock(self, conf_uid: str) -> asyncio.Lock:
        key = conf_uid if self.memory_path is None else "__fixed__"
        lock = self._memory_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._memory_locks[key] = lock
        return lock

    @staticmethod
    def _normalize_text(value: object) -> str:
        return " ".join(str(value).strip().split())

    @classmethod
    def parse_summary(cls, raw_output: str) -> list[LongTermMemory]:
        """Parse and strictly validate the JSON returned by the summarizer."""
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise ValueError("Long-term memory summary is empty")

        text = raw_output.strip()
        fenced_match = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL
        )
        if fenced_match:
            text = fenced_match.group(1).strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            object_start = text.find("{")
            if object_start < 0:
                raise ValueError("Long-term memory summary does not contain JSON")
            try:
                payload, _ = json.JSONDecoder().raw_decode(text[object_start:])
            except json.JSONDecodeError as exc:
                raise ValueError("Long-term memory summary contains invalid JSON") from exc

        if not isinstance(payload, dict):
            raise ValueError("Long-term memory summary must be a JSON object")

        items = payload.get("长期记忆")
        if not isinstance(items, list):
            raise ValueError("长期记忆 must be an array")

        memories: list[LongTermMemory] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Each long-term memory entry must be an object")
            name = cls._normalize_text(item.get("记忆命名", ""))
            content = cls._normalize_text(item.get("记忆内容", ""))
            if not name:
                raise ValueError("记忆命名 cannot be empty")
            if len(name) >= 8:
                raise ValueError("记忆命名 must contain fewer than 8 characters")
            if not content:
                raise ValueError("记忆内容 cannot be empty")
            memories.append(LongTermMemory(name=name, content=content))

        return memories

    def _read_memories_unlocked(self, memory_path: Path) -> list[LongTermMemory]:
        if not memory_path.exists():
            return []

        try:
            text = memory_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to read long-term memory file: {}", exc)
            return []

        memories: list[LongTermMemory] = []
        blocks = re.split(r"^\s*---+\s*$", text, flags=re.MULTILINE)
        for block in blocks:
            name_match = re.search(r"^记忆命名：\s*(.+)$", block, flags=re.MULTILINE)
            content_match = re.search(
                r"^记忆内容：\s*(.+)$", block, flags=re.MULTILINE
            )
            if not name_match or not content_match:
                continue
            name = self._normalize_text(name_match.group(1))
            content = self._normalize_text(content_match.group(1))
            if name and content:
                memories.append(LongTermMemory(name=name, content=content))
        if self.max_memories is None:
            return memories
        return memories[: self.max_memories]

    def _write_memories_unlocked(
        self,
        memory_path: Path,
        memories: Iterable[LongTermMemory],
    ) -> None:
        normalized = list(memories)
        if self.max_memories is not None:
            normalized = normalized[: self.max_memories]
        lines: list[str] = []
        for memory in normalized:
            lines.extend(
                [
                    f"记忆命名：{memory.name}",
                    f"记忆内容：{memory.content}",
                    "---",
                ]
            )
        content = "\n".join(lines).rstrip()
        if content:
            content += "\n"

        memory_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: str | None = None
        try:
            file_descriptor, temp_path = tempfile.mkstemp(
                prefix=f".{memory_path.name}.",
                suffix=".tmp",
                dir=memory_path.parent,
            )
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as temp_file:
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, memory_path)
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    async def read_memories(self, conf_uid: str = "") -> list[LongTermMemory]:
        memory_path = self._get_memory_path(conf_uid)
        async with self._get_memory_lock(conf_uid):
            return self._read_memories_unlocked(memory_path)

    async def store_memories(
        self,
        new_memories: Iterable[LongTermMemory],
        conf_uid: str = "",
    ) -> list[LongTermMemory]:
        """Prepend new memories and update entries with matching names."""
        memory_path = self._get_memory_path(conf_uid)
        async with self._get_memory_lock(conf_uid):
            existing_memories = self._read_memories_unlocked(memory_path)

            # Keep the summarizer's order for the new batch, while using the
            # final value if it accidentally emits the same name more than once.
            deduplicated_new: list[LongTermMemory] = []
            seen_new_names: set[str] = set()
            for memory in reversed(list(new_memories)):
                if memory.name in seen_new_names:
                    continue
                seen_new_names.add(memory.name)
                deduplicated_new.append(memory)
            deduplicated_new.reverse()

            existing_memories = [
                memory
                for memory in existing_memories
                if memory.name not in seen_new_names
            ]
            memories = deduplicated_new + existing_memories
            if self.max_memories is not None:
                memories = memories[: self.max_memories]
            self._write_memories_unlocked(memory_path, memories)
            return memories

    @staticmethod
    def _get_state(conf_uid: str, history_uid: str) -> dict:
        metadata = get_metadata(conf_uid, history_uid)
        state = metadata.get(LONG_TERM_MEMORY_METADATA_KEY, {})
        if not isinstance(state, dict):
            return {}
        return state.copy()

    @staticmethod
    def _save_state(conf_uid: str, history_uid: str, state: dict) -> bool:
        return update_metadate(
            conf_uid,
            history_uid,
            {LONG_TERM_MEMORY_METADATA_KEY: state},
        )

    async def consume_injection(
        self,
        conf_uid: str,
        history_uid: str,
        turn_number: int | None = None,
    ) -> str:
        """Return memory on turn one and every three completed turns thereafter.

        New conversations immediately receive the existing memory file so that
        memories accumulated in previous conversations carry over.
        """
        if not conf_uid or not history_uid:
            return ""

        if turn_number is not None:
            if turn_number < 1:
                return ""
            if turn_number != 1 and (turn_number - 1) % self.summary_interval != 0:
                return ""
            memories = await self.read_memories(conf_uid)
        else:
            async with self._get_history_lock(conf_uid, history_uid):
                state = self._get_state(conf_uid, history_uid)
                is_new_history = not state  # first turn of a new conversation

                if not is_new_history and not state.get("inject_next_turn", False):
                    return ""

                memories = await self.read_memories(conf_uid)
                state["inject_next_turn"] = False
                if not self._save_state(conf_uid, history_uid, state):
                    logger.error(
                        "Failed to persist long-term memory injection state for history {}",
                        history_uid,
                    )
                    return ""

        if not memories:
            return ""

        return prompt_builder.build_memory_injection(
            (memory.name, memory.content) for memory in memories
        )

    async def record_completed_turn(
        self,
        conf_uid: str,
        history_uid: str,
        user_content: str,
        assistant_content: str,
        summarize: SummaryCallback,
    ) -> bool:
        """Record one new single-chat turn and summarize each new group of three."""
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
            state.setdefault("inject_next_turn", False)
            if not self._save_state(conf_uid, history_uid, state):
                logger.error(
                    "Failed to persist long-term memory turn for history {}",
                    history_uid,
                )
                return False

            if len(pending_turns) < self.summary_interval:
                return False

            summary_turns = pending_turns[: self.summary_interval]
            existing_memories = await self.read_memories(conf_uid)
            try:
                raw_output = await summarize(summary_turns, existing_memories)
                new_memories = self.parse_summary(raw_output)
                await self.store_memories(new_memories, conf_uid)
            except Exception as exc:
                logger.error(
                    "Long-term memory summarization failed for history {}: {}",
                    history_uid,
                    exc,
                )
                return False

            memory_output = json.dumps(
                {
                    "长期记忆": [
                        {"记忆命名": memory.name, "记忆内容": memory.content}
                        for memory in new_memories
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
            logger.info(
                "Long-term memory output for history {}:\n{}",
                history_uid,
                memory_output,
            )

            state["pending_turns"] = pending_turns[self.summary_interval :]
            state["inject_next_turn"] = True
            if not self._save_state(conf_uid, history_uid, state):
                logger.error(
                    "Long-term memory was saved but its history checkpoint failed for {}",
                    history_uid,
                )
                return False

            logger.info(
                "Long-term memory processed {} turns and saved {} entries",
                self.summary_interval,
                len(new_memories),
            )
            return True


long_term_memory_manager = LongTermMemoryManager()
