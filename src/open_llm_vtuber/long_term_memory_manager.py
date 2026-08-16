"""Persistent long-term memory for single-user conversations."""

from __future__ import annotations

import asyncio
import inspect
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
from .memory_rag import RagMemory, memory_rag_store


SUMMARY_INTERVAL = 6
MAX_LONG_TERM_MEMORIES: int | None = None
LONG_TERM_MEMORY_METADATA_KEY = "long_term_memory"


@dataclass(frozen=True)
class LongTermMemory:
    name: str
    content: str
    type: str = "长期记忆"
    reference: str = ""
    count: int = 0

    def to_rag_memory(self) -> RagMemory:
        return RagMemory(
            count=self.count,
            content=self.content,
            type=self.type,
            reference=self.reference or self.name,
        )


SummaryCallback = Callable[[list[dict[str, str]]], Awaitable[str]]


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
        self._summary_locks: dict[tuple[str, str], asyncio.Lock] = {}

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

    def _get_summary_lock(self, conf_uid: str, history_uid: str) -> asyncio.Lock:
        key = (conf_uid, history_uid)
        lock = self._summary_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._summary_locks[key] = lock
        return lock

    def _rag_scope_uid(self, conf_uid: str) -> str:
        if self.history_root == Path("chat_history"):
            return conf_uid
        return f"{self.history_root.as_posix()}::{conf_uid}"

    @staticmethod
    def _normalize_text(value: object) -> str:
        return " ".join(str(value).strip().split())

    @classmethod
    def parse_summary(cls, raw_output: str) -> list[LongTermMemory]:
        """Parse strict --- blocks containing count/content/type/reference."""
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise ValueError("Long-term memory summary is empty")

        text = raw_output.strip()
        lines = text.splitlines()
        blocks: list[list[str]] = []
        current: list[str] | None = None
        for line in lines:
            if line.strip() == "---":
                if current is None:
                    current = []
                else:
                    blocks.append(current)
                    current = None
                continue
            if current is None:
                if line.strip():
                    raise ValueError(
                        "Long-term memory output contains text outside --- blocks"
                    )
                continue
            current.append(line)

        if current is not None:
            raise ValueError("Long-term memory output has an unclosed --- block")
        if not blocks:
            raise ValueError("Long-term memory output does not contain a --- block")

        expected_keys = ["count", "content", "type", "reference"]
        memories: list[LongTermMemory] = []
        for block_index, block in enumerate(blocks, start=1):
            if len(block) != len(expected_keys):
                raise ValueError(
                    f"Long-term memory block {block_index} must contain exactly "
                    "four field lines"
                )

            values: dict[str, str] = {}
            for expected_key, line in zip(expected_keys, block):
                match = re.fullmatch(
                    rf"\s*{expected_key}\s*:\s*(.*?)\s*",
                    line,
                )
                if not match:
                    raise ValueError(
                        f"Long-term memory block {block_index} must use field "
                        f"order count/content/type/reference"
                    )
                values[expected_key] = cls._normalize_text(match.group(1))

            if not any(values.values()):
                if len(blocks) != 1:
                    raise ValueError(
                        "An empty long-term memory block must be the only block"
                    )
                return []
            if not values["content"] or not values["type"] or not values["reference"]:
                raise ValueError(
                    f"Long-term memory block {block_index} may only leave all four "
                    "fields empty; otherwise content/type/reference are required"
                )
            raw_count = values["count"]
            if raw_count and not re.fullmatch(r"\d{3}", raw_count):
                raise ValueError(
                    f"Long-term memory block {block_index} count must be empty or "
                    "a three-digit number"
                )
            count = int(raw_count) if raw_count else 0
            memories.append(
                LongTermMemory(
                    name=values["reference"],
                    content=values["content"],
                    type=values["type"],
                    reference=values["reference"],
                    count=count,
                )
            )

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
        blocks: list[str] = []
        current: list[str] | None = None
        for line in text.splitlines():
            if re.fullmatch(r"\s*---\s*", line):
                if current is None:
                    current = []
                else:
                    block = "\n".join(current).strip()
                    if block:
                        blocks.append(block)
                    current = None
                continue
            if current is not None:
                current.append(line)

        for block in blocks:
            values: dict[str, str] = {}
            active_key: str | None = None
            for line in block.splitlines():
                match = re.match(r"^\s*(count|content|type|reference)\s*:\s*(.*)$", line)
                if match:
                    active_key = match.group(1)
                    values[active_key] = match.group(2).strip()
                elif active_key and line.strip():
                    values[active_key] = f"{values[active_key]}\n{line.strip()}".strip()
            if values.get("content"):
                try:
                    count = int(values.get("count", "0"))
                except ValueError:
                    count = 0
                reference = self._normalize_text(values.get("reference", ""))
                memories.append(
                    LongTermMemory(
                        name=reference or f"记忆{count}",
                        content=self._normalize_text(values["content"]),
                        type=self._normalize_text(values.get("type", "长期记忆")),
                        reference=reference,
                        count=max(0, count),
                    )
                )

        # Read the legacy format once so an older character can migrate on write.
        if not memories:
            for block in re.split(r"^\s*---+\s*$", text, flags=re.MULTILINE):
                name_match = re.search(r"^记忆命名：\s*(.+)$", block, flags=re.MULTILINE)
                content_match = re.search(r"^记忆内容：\s*(.+)$", block, flags=re.MULTILINE)
                if name_match and content_match:
                    name = self._normalize_text(name_match.group(1))
                    content = self._normalize_text(content_match.group(1))
                    if name and content:
                        memories.append(LongTermMemory(name=name, content=content, reference=name))

        used_counts: set[int] = set()
        next_count = 1
        numbered: list[LongTermMemory] = []
        for memory in memories:
            count = memory.count
            if count < 1 or count in used_counts:
                while next_count in used_counts:
                    next_count += 1
                count = next_count
            used_counts.add(count)
            next_count = max(next_count, count + 1)
            numbered.append(
                LongTermMemory(
                    name=memory.name,
                    content=memory.content,
                    type=memory.type,
                    reference=memory.reference,
                    count=count,
                )
            )
        memories = numbered
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
                    "---",
                    f"count:{memory.count:03d}",
                    f"content:{memory.content}",
                    f"type:{memory.type}",
                    f"reference:{memory.reference or memory.name}",
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
        """Upsert summarized memories and synchronize the derived RAG index."""
        memory_path = self._get_memory_path(conf_uid)
        async with self._get_memory_lock(conf_uid):
            existing_memories = self._read_memories_unlocked(memory_path)

            by_count = {memory.count: memory for memory in existing_memories}
            reference_to_count = {
                (memory.reference or memory.name).casefold(): memory.count
                for memory in existing_memories
            }
            next_count = max(by_count, default=0) + 1
            for memory in new_memories:
                count = memory.count
                if count < 1:
                    count = reference_to_count.get(
                        (memory.reference or memory.name).casefold(), 0
                    )
                if count < 1:
                    count = next_count
                    next_count += 1
                normalized_memory = LongTermMemory(
                    name=memory.name,
                    content=memory.content,
                    type=memory.type,
                    reference=memory.reference or memory.name,
                    count=count,
                )
                by_count[count] = normalized_memory
                reference_to_count[normalized_memory.reference.casefold()] = count

            memories = [by_count[count] for count in sorted(by_count)]
            if self.max_memories is not None:
                memories = memories[: self.max_memories]
            self._write_memories_unlocked(memory_path, memories)
        if conf_uid:
            try:
                await asyncio.to_thread(
                    memory_rag_store.sync,
                    self._rag_scope_uid(conf_uid),
                    [memory.to_rag_memory() for memory in memories],
                )
            except Exception as exc:
                logger.error("Failed to update the long-term memory RAG index: {}", exc)
        return memories

    def _get_state(self, conf_uid: str, history_uid: str) -> dict:
        if "history_root" in inspect.signature(get_metadata).parameters:
            metadata = get_metadata(
                conf_uid,
                history_uid,
                history_root=self.history_root,
            )
        else:
            metadata = get_metadata(conf_uid, history_uid)
        state = metadata.get(LONG_TERM_MEMORY_METADATA_KEY, {})
        if not isinstance(state, dict):
            return {}
        return state.copy()

    def _save_state(self, conf_uid: str, history_uid: str, state: dict) -> bool:
        metadata = {LONG_TERM_MEMORY_METADATA_KEY: state}
        if "history_root" in inspect.signature(update_metadate).parameters:
            return update_metadate(
                conf_uid,
                history_uid,
                metadata,
                history_root=self.history_root,
            )
        return update_metadate(conf_uid, history_uid, metadata)

    async def retrieve_injection(
        self,
        conf_uid: str,
        query: str,
        *,
        top_k: int = 5,
        threshold: float = 0.5,
        hybrid_weight: float = 0.5,
    ) -> str:
        """Retrieve content for this request without adding it to chat history."""
        if not conf_uid or not query.strip():
            return ""
        memories = await self.read_memories(conf_uid)
        if not memories:
            return ""
        try:
            retrieved = await asyncio.to_thread(
                memory_rag_store.retrieve,
                self._rag_scope_uid(conf_uid),
                query,
                [memory.to_rag_memory() for memory in memories],
                top_k=max(1, min(20, int(top_k))),
                threshold=max(0.0, min(1.0, float(threshold))),
                hybrid_weight=max(0.0, min(1.0, float(hybrid_weight))),
            )
        except Exception as exc:
            logger.error("Long-term memory retrieval failed: {}", exc)
            return ""
        return prompt_builder.build_memory_injection(
            memory.content for memory in retrieved
        )

    async def record_completed_turn(
        self,
        conf_uid: str,
        history_uid: str,
        user_content: str,
        assistant_content: str,
        summarize: SummaryCallback,
    ) -> bool:
        """Record one new single-chat turn and summarize each new group of six."""
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
        """Persist one completed turn and report whether a new batch is ready."""
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
            if not self._save_state(conf_uid, history_uid, state):
                logger.error(
                    "Failed to persist long-term memory turn for history {}",
                    history_uid,
                )
                return False

            return len(pending_turns) >= self.summary_interval

    async def summarize_pending_turns(
        self,
        conf_uid: str,
        history_uid: str,
        summarize: SummaryCallback,
        require_full_batch: bool = False,
    ) -> str:
        """Summarize all currently pending completed turns on demand."""
        if not conf_uid or not history_uid:
            return "empty"

        async with self._get_summary_lock(conf_uid, history_uid):
            return await self._summarize_pending_turns_unlocked(
                conf_uid,
                history_uid,
                summarize,
                require_full_batch,
            )

    async def _summarize_pending_turns_unlocked(
        self,
        conf_uid: str,
        history_uid: str,
        summarize: SummaryCallback,
        require_full_batch: bool,
    ) -> str:
        """Process one pending batch while the per-history summary lock is held."""

        async with self._get_history_lock(conf_uid, history_uid):
            state = self._get_state(conf_uid, history_uid)
            pending_turns = state.get("pending_turns", [])
            if not isinstance(pending_turns, list):
                pending_turns = []
            summary_turns = (
                pending_turns[: self.summary_interval]
                if require_full_batch
                else pending_turns[:]
            )
            if not summary_turns:
                return "empty"
            if require_full_batch and len(summary_turns) < self.summary_interval:
                return "empty"

        new_memories: list[LongTermMemory] | None = None
        last_error: Exception | None = None
        for _ in range(3):
            try:
                raw_output = await summarize(summary_turns)
                new_memories = self.parse_summary(raw_output)
                break
            except Exception as exc:
                last_error = exc

        if new_memories is None:
            logger.error(
                "Manual long-term memory summary failed for history {}: {}",
                history_uid,
                last_error,
            )
            return "error"

        try:
            await self.store_memories(new_memories, conf_uid)
        except Exception as exc:
            logger.error(
                "Manual long-term memory storage failed for history {}: {}",
                history_uid,
                exc,
            )
            return "error"

        async with self._get_history_lock(conf_uid, history_uid):
            state = self._get_state(conf_uid, history_uid)
            latest_pending = state.get("pending_turns", [])
            if not isinstance(latest_pending, list):
                latest_pending = []
            state["pending_turns"] = latest_pending[len(summary_turns) :]
            if not self._save_state(conf_uid, history_uid, state):
                logger.error(
                    "Manual long-term memory was saved but its checkpoint failed for {}",
                    history_uid,
                )
                return "error"

        logger.info(
            "Long-term memory summary processed {} pending turns",
            len(summary_turns),
        )
        return "success"


long_term_memory_manager = LongTermMemoryManager()
