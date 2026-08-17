"""Persistent long-term memory for single-user conversations."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from loguru import logger
from prompts import prompt_builder

from .chat_history_manager import get_character_history_dir, get_metadata, update_metadate
from .conversation_state_manager import read_legacy_states, read_state, write_state
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
    created_at: str = ""
    updated_at: str = ""
    source_batch_id: str = ""

    def to_rag_memory(self) -> RagMemory:
        return RagMemory(
            count=self.count,
            content=self.content,
            type=self.type,
            reference=self.reference or self.name,
            created_at=self.created_at,
            updated_at=self.updated_at,
            source_batch_id=self.source_batch_id,
        )


SummaryCallback = Callable[[list[dict[str, str]]], Awaitable[str]]
ReconcileCallback = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class MemoryReconcileOperation:
    new_memory_index: int
    action: str
    target_count: int | None
    memory: LongTermMemory | None
    reason: str = ""


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
        self._history_locks: dict[str, asyncio.Lock] = {}
        self._memory_locks: dict[str, asyncio.Lock] = {}
        self._summary_locks: dict[str, asyncio.Lock] = {}
        self._consolidation_locks: dict[str, asyncio.Lock] = {}

    def _get_history_lock(self, conf_uid: str, history_uid: str) -> asyncio.Lock:
        key = conf_uid
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
        key = conf_uid
        lock = self._summary_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._summary_locks[key] = lock
        return lock

    def _get_consolidation_lock(self, conf_uid: str) -> asyncio.Lock:
        key = conf_uid if self.memory_path is None else "__fixed__"
        lock = self._consolidation_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._consolidation_locks[key] = lock
        return lock

    def _rag_scope_uid(self, conf_uid: str) -> str:
        if self.history_root == Path("chat_history"):
            return conf_uid
        return f"{self.history_root.as_posix()}::{conf_uid}"

    @staticmethod
    def _normalize_text(value: object) -> str:
        return " ".join(str(value).strip().split())

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

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
                match = re.match(
                    r"^\s*(count|content|type|reference|created_at|updated_at|source_batch_id)\s*:\s*(.*)$",
                    line,
                )
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
                        created_at=self._normalize_text(
                            values.get("created_at", "")
                        ),
                        updated_at=self._normalize_text(
                            values.get("updated_at", "")
                        ),
                        source_batch_id=self._normalize_text(
                            values.get("source_batch_id", "")
                        ),
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
                    created_at=memory.created_at,
                    updated_at=memory.updated_at,
                    source_batch_id=memory.source_batch_id,
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
                    f"created_at:{memory.created_at}",
                    f"updated_at:{memory.updated_at}",
                    f"source_batch_id:{memory.source_batch_id}",
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
            now = self._now_iso()
            for memory in new_memories:
                count = memory.count
                if count < 1:
                    count = reference_to_count.get(
                        (memory.reference or memory.name).casefold(), 0
                    )
                if count < 1:
                    count = next_count
                    next_count += 1
                existing_memory = by_count.get(count)
                normalized_memory = LongTermMemory(
                    name=memory.name,
                    content=memory.content,
                    type=memory.type,
                    reference=memory.reference or memory.name,
                    count=count,
                    created_at=(
                        memory.created_at
                        or (existing_memory.created_at if existing_memory else "")
                        or now
                    ),
                    updated_at=memory.updated_at or now,
                    source_batch_id=(
                        memory.source_batch_id
                        or (
                            existing_memory.source_batch_id
                            if existing_memory
                            else ""
                        )
                    ),
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
        if self.memory_path is not None:
            metadata = (
                get_metadata(
                    conf_uid,
                    history_uid,
                    history_root=self.history_root,
                )
                if "history_root" in inspect.signature(get_metadata).parameters
                else get_metadata(conf_uid, history_uid)
            )
            state = metadata.get(LONG_TERM_MEMORY_METADATA_KEY, {})
            return state.copy() if isinstance(state, dict) else {}
        state = read_state(
            conf_uid,
            LONG_TERM_MEMORY_METADATA_KEY,
            self.history_root,
        )
        if state is not None:
            return state

        pending_turns: list[dict[str, str]] = []
        next_sequence = 1
        applied_batch_ids = {
            memory.source_batch_id
            for memory in self._read_memories_unlocked(
                self._get_memory_path(conf_uid)
            )
            if memory.source_batch_id
        }
        for _, legacy_state in read_legacy_states(
            conf_uid,
            LONG_TERM_MEMORY_METADATA_KEY,
            self.history_root,
        ):
            legacy_pending = legacy_state.get("pending_turns", [])
            if isinstance(legacy_pending, list):
                active_batch = legacy_state.get("active_batch", {})
                consumed_turns = 0
                if (
                    isinstance(active_batch, dict)
                    and active_batch.get("id") in applied_batch_ids
                ):
                    active_count = active_batch.get("turn_count", 0)
                    if not isinstance(active_count, bool) and isinstance(
                        active_count, int
                    ):
                        consumed_turns = max(0, active_count)
                pending_turns.extend(
                    turn.copy()
                    for turn in legacy_pending[consumed_turns:]
                    if isinstance(turn, dict)
                )
            legacy_sequence = legacy_state.get("next_batch_sequence", 1)
            if (
                not isinstance(legacy_sequence, bool)
                and isinstance(legacy_sequence, int)
            ):
                next_sequence = max(next_sequence, legacy_sequence)
        state = {
            "pending_turns": pending_turns,
            "next_batch_sequence": next_sequence,
        }
        write_state(
            conf_uid,
            LONG_TERM_MEMORY_METADATA_KEY,
            state,
            self.history_root,
        )
        return state.copy()

    def _save_state(self, conf_uid: str, history_uid: str, state: dict) -> bool:
        if self.memory_path is not None:
            metadata = {LONG_TERM_MEMORY_METADATA_KEY: state}
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
            LONG_TERM_MEMORY_METADATA_KEY,
            state,
            self.history_root,
        )

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

    async def _retrieve_reconciliation_candidates(
        self,
        conf_uid: str,
        new_memories: list[LongTermMemory],
        existing_memories: list[LongTermMemory],
    ) -> list[list[LongTermMemory]]:
        if not new_memories:
            return []
        if not existing_memories:
            return [[] for _ in new_memories]
        queries = [memory.to_rag_memory().search_text for memory in new_memories]
        retrieved_groups = await asyncio.to_thread(
            memory_rag_store.retrieve_many,
            self._rag_scope_uid(conf_uid),
            queries,
            [memory.to_rag_memory() for memory in existing_memories],
            top_k=3,
            threshold=0.1,
            hybrid_weight=0.5,
        )
        return [
            [
                LongTermMemory(
                    name=memory.reference,
                    content=memory.content,
                    type=memory.type,
                    reference=memory.reference,
                    count=memory.count,
                    created_at=memory.created_at,
                    updated_at=memory.updated_at,
                    source_batch_id=memory.source_batch_id,
                )
                for memory in group
            ]
            for group in retrieved_groups
        ]

    @classmethod
    def parse_reconciliation(
        cls,
        raw_output: str,
        new_memories: list[LongTermMemory],
        retrieved_groups: list[list[LongTermMemory]],
    ) -> list[MemoryReconcileOperation]:
        """Parse and validate one complete reconciliation response."""
        try:
            payload = json.loads(raw_output)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Long-term memory reconciliation is not valid JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {"operations"}:
            raise ValueError("Reconciliation output must only contain operations")
        raw_operations = payload["operations"]
        if not isinstance(raw_operations, list):
            raise ValueError("Reconciliation operations must be a list")
        if len(raw_operations) != len(new_memories):
            raise ValueError(
                "Reconciliation must return exactly one operation per new memory"
            )

        operations: list[MemoryReconcileOperation] = []
        seen_indices: set[int] = set()
        rewritten_counts: set[int] = set()
        expected_operation_keys = {
            "new_memory_index",
            "action",
            "target_count",
            "memory",
            "reason",
        }
        expected_memory_keys = {"content", "type", "reference"}
        for raw_operation in raw_operations:
            if not isinstance(raw_operation, dict) or set(raw_operation) != expected_operation_keys:
                raise ValueError("Reconciliation operation has invalid fields")
            index = raw_operation["new_memory_index"]
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("new_memory_index must be an integer")
            if index < 0 or index >= len(new_memories) or index in seen_indices:
                raise ValueError("new_memory_index is missing, duplicated, or out of range")
            seen_indices.add(index)

            action = raw_operation["action"]
            if action not in {"add", "rewrite", "noop"}:
                raise ValueError("action must be add, rewrite, or noop")
            target_count = raw_operation["target_count"]
            if target_count is not None and (
                isinstance(target_count, bool) or not isinstance(target_count, int)
            ):
                raise ValueError("target_count must be an integer or null")
            candidate_counts = {
                memory.count for memory in retrieved_groups[index]
            }
            raw_memory = raw_operation["memory"]
            raw_reason = raw_operation["reason"]
            if not isinstance(raw_reason, str):
                raise ValueError("reason must be a string")
            reason = cls._normalize_text(raw_reason)

            operation_memory: LongTermMemory | None = None
            if action == "noop":
                if target_count not in candidate_counts or raw_memory is not None:
                    raise ValueError(
                        "noop must target a retrieved memory and set memory to null"
                    )
            else:
                if not isinstance(raw_memory, dict) or set(raw_memory) != expected_memory_keys:
                    raise ValueError("add and rewrite require a complete memory object")
                if not all(
                    isinstance(raw_memory[key], str) for key in expected_memory_keys
                ):
                    raise ValueError("Reconciled memory fields must be strings")
                content = cls._normalize_text(raw_memory["content"])
                memory_type = cls._normalize_text(raw_memory["type"])
                reference = cls._normalize_text(raw_memory["reference"])
                if not content or not memory_type or not reference:
                    raise ValueError("Reconciled memory fields cannot be empty")
                if action == "add":
                    if target_count is not None:
                        raise ValueError("add must set target_count to null")
                    original = new_memories[index]
                    operation_memory = LongTermMemory(
                        name=original.reference or original.name,
                        content=original.content,
                        type=original.type,
                        reference=original.reference or original.name,
                    )
                else:
                    if target_count not in candidate_counts:
                        raise ValueError(
                            "rewrite target_count must come from retrieved candidates"
                        )
                    if target_count in rewritten_counts:
                        raise ValueError(
                            "The same existing memory cannot be rewritten twice in one batch"
                        )
                    rewritten_counts.add(target_count)
                    operation_memory = LongTermMemory(
                        name=reference,
                        content=content,
                        type=memory_type,
                        reference=reference,
                        count=target_count,
                    )

            operations.append(
                MemoryReconcileOperation(
                    new_memory_index=index,
                    action=action,
                    target_count=target_count,
                    memory=operation_memory,
                    reason=reason,
                )
            )

        return sorted(operations, key=lambda operation: operation.new_memory_index)

    async def _apply_reconciliation(
        self,
        conf_uid: str,
        operations: list[MemoryReconcileOperation],
        source_batch_id: str,
    ) -> list[LongTermMemory]:
        """Atomically apply validated operations and rebuild the derived index."""
        memory_path = self._get_memory_path(conf_uid)
        async with self._get_memory_lock(conf_uid):
            existing_memories = self._read_memories_unlocked(memory_path)
            by_count = {memory.count: memory for memory in existing_memories}
            next_count = max(by_count, default=0) + 1
            now = self._now_iso()

            for operation in operations:
                if operation.action == "noop":
                    continue
                if operation.memory is None:
                    raise ValueError("Validated write operation is missing memory")
                if operation.action == "add":
                    count = next_count
                    next_count += 1
                    created_at = now
                else:
                    count = operation.target_count or 0
                    existing = by_count.get(count)
                    if existing is None:
                        raise ValueError(
                            f"Rewrite target memory {count:03d} no longer exists"
                        )
                    created_at = existing.created_at or now
                memory = operation.memory
                by_count[count] = LongTermMemory(
                    name=memory.reference or memory.name,
                    content=memory.content,
                    type=memory.type,
                    reference=memory.reference or memory.name,
                    count=count,
                    created_at=created_at,
                    updated_at=now,
                    source_batch_id=source_batch_id,
                )

            memories = [by_count[count] for count in sorted(by_count)]
            if self.max_memories is not None:
                memories = memories[: self.max_memories]
            self._write_memories_unlocked(memory_path, memories)

        if conf_uid:
            await asyncio.to_thread(
                memory_rag_store.sync,
                self._rag_scope_uid(conf_uid),
                [memory.to_rag_memory() for memory in memories],
            )
        return memories

    @staticmethod
    def _make_source_batch_id(
        conf_uid: str,
        turns: list[dict[str, str]],
        sequence: int,
    ) -> str:
        serialized = json.dumps(
            {
                "conf_uid": conf_uid,
                "turns": turns,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]
        return f"{conf_uid}:long_term_memory:{sequence}:{digest}"

    @classmethod
    def _deduplicate_new_memories(
        cls,
        memories: list[LongTermMemory],
    ) -> list[LongTermMemory]:
        unique: list[LongTermMemory] = []
        seen: set[str] = set()
        for memory in memories:
            key = cls._normalize_text(memory.content).casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(memory)
        return unique

    @staticmethod
    def _build_reconciliation_input(
        new_memories: list[LongTermMemory],
        retrieved_groups: list[list[LongTermMemory]],
        source_batch_id: str,
        current_time: str,
    ) -> dict[str, Any]:
        return {
            "current_time": current_time,
            "source_batch_id": source_batch_id,
            "new_memories": [
                {
                    "new_memory_index": index,
                    "memory": {
                        "content": memory.content,
                        "type": memory.type,
                        "reference": memory.reference or memory.name,
                    },
                    "retrieved_existing_memories": [
                        {
                            "count": existing.count,
                            "content": existing.content,
                            "type": existing.type,
                            "reference": existing.reference or existing.name,
                            "created_at": existing.created_at,
                            "updated_at": existing.updated_at,
                        }
                        for existing in retrieved_groups[index]
                    ],
                }
                for index, memory in enumerate(new_memories)
            ],
        }

    async def record_completed_turn(
        self,
        conf_uid: str,
        history_uid: str,
        user_content: str,
        assistant_content: str,
        summarize: SummaryCallback,
        reconcile: ReconcileCallback | None = None,
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
                reconcile=reconcile,
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
        reconcile: ReconcileCallback | None = None,
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
                reconcile,
            )

    async def _summarize_pending_turns_unlocked(
        self,
        conf_uid: str,
        history_uid: str,
        summarize: SummaryCallback,
        require_full_batch: bool,
        reconcile: ReconcileCallback | None,
    ) -> str:
        """Process one pending batch while the per-history summary lock is held."""

        async with self._get_history_lock(conf_uid, history_uid):
            state = self._get_state(conf_uid, history_uid)
            pending_turns = state.get("pending_turns", [])
            if not isinstance(pending_turns, list):
                pending_turns = []
            active_batch = state.get("active_batch", {})
            if not isinstance(active_batch, dict):
                active_batch = {}
            summary_turns: list[dict[str, str]]
            source_batch_id: str
            if isinstance(active_batch, dict) and active_batch.get("id"):
                active_turn_count = active_batch.get("turn_count", 0)
                active_sequence = active_batch.get("sequence", 0)
                if (
                    isinstance(active_turn_count, bool)
                    or not isinstance(active_turn_count, int)
                    or active_turn_count < 1
                    or isinstance(active_sequence, bool)
                    or not isinstance(active_sequence, int)
                    or active_sequence < 1
                ):
                    active_turn_count = 0
                summary_turns = pending_turns[:active_turn_count]
                expected_batch_id = self._make_source_batch_id(
                    conf_uid,
                    summary_turns,
                    active_sequence if isinstance(active_sequence, int) else 0,
                )
                if (
                    not summary_turns
                    or expected_batch_id != active_batch.get("id")
                ):
                    active_batch = {}
            if not active_batch:
                summary_turns = (
                    pending_turns[: self.summary_interval]
                    if require_full_batch
                    else pending_turns[:]
                )
                if not summary_turns:
                    return "empty"
                if require_full_batch and len(summary_turns) < self.summary_interval:
                    return "empty"
                next_sequence = state.get("next_batch_sequence", 1)
                if (
                    isinstance(next_sequence, bool)
                    or not isinstance(next_sequence, int)
                    or next_sequence < 1
                ):
                    next_sequence = 1
                source_batch_id = self._make_source_batch_id(
                    conf_uid,
                    summary_turns,
                    next_sequence,
                )
                state["active_batch"] = {
                    "id": source_batch_id,
                    "sequence": next_sequence,
                    "turn_count": len(summary_turns),
                }
                state["next_batch_sequence"] = next_sequence + 1
                if not self._save_state(conf_uid, history_uid, state):
                    logger.error(
                        "Failed to reserve long-term memory batch for history {}",
                        history_uid,
                    )
                    return "error"
            else:
                source_batch_id = str(active_batch["id"])

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

        new_memories = self._deduplicate_new_memories(new_memories)
        if new_memories:
            try:
                if reconcile is None:
                    await self.store_memories(new_memories, conf_uid)
                else:
                    async with self._get_consolidation_lock(conf_uid):
                        existing_memories = await self.read_memories(conf_uid)
                        batch_already_applied = any(
                            memory.source_batch_id == source_batch_id
                            for memory in existing_memories
                        )
                        if batch_already_applied:
                            await asyncio.to_thread(
                                memory_rag_store.sync,
                                self._rag_scope_uid(conf_uid),
                                [
                                    memory.to_rag_memory()
                                    for memory in existing_memories
                                ],
                            )
                        else:
                            retrieved_groups = (
                                await self._retrieve_reconciliation_candidates(
                                    conf_uid,
                                    new_memories,
                                    existing_memories,
                                )
                            )
                            reconciliation_input = (
                                self._build_reconciliation_input(
                                    new_memories,
                                    retrieved_groups,
                                    source_batch_id,
                                    self._now_iso(),
                                )
                            )
                            operations: list[MemoryReconcileOperation] | None = None
                            reconcile_error: Exception | None = None
                            for _ in range(3):
                                try:
                                    raw_reconciliation = await reconcile(
                                        reconciliation_input
                                    )
                                    operations = self.parse_reconciliation(
                                        raw_reconciliation,
                                        new_memories,
                                        retrieved_groups,
                                    )
                                    break
                                except Exception as exc:
                                    reconcile_error = exc
                            if operations is None:
                                raise ValueError(
                                    "Long-term memory reconciliation failed"
                                ) from reconcile_error
                            await self._apply_reconciliation(
                                conf_uid,
                                operations,
                                source_batch_id,
                            )
            except Exception as exc:
                logger.error(
                    "Manual long-term memory reconciliation/storage failed for history {}: {}",
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
            active_batch = state.get("active_batch", {})
            if (
                isinstance(active_batch, dict)
                and active_batch.get("id") == source_batch_id
            ):
                state.pop("active_batch", None)
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
