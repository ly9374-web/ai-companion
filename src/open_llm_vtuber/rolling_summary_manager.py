"""Latest per-history summary of chat outside the direct context window."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger

from .chat_history_manager import get_metadata, update_metadate


ROLLING_SUMMARY_METADATA_KEY = "rolling_summary"
RollingSummaryCallback = Callable[[list[dict[str, str]]], Awaitable[str]]


class RollingSummaryManager:
    def __init__(self, history_root: str | Path = "chat_history") -> None:
        self.history_root = Path(history_root)
        self._history_locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _get_history_lock(self, conf_uid: str, history_uid: str) -> asyncio.Lock:
        key = (conf_uid, history_uid)
        lock = self._history_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._history_locks[key] = lock
        return lock

    def _read_metadata(self, conf_uid: str, history_uid: str) -> dict:
        if "history_root" in inspect.signature(get_metadata).parameters:
            return get_metadata(
                conf_uid,
                history_uid,
                history_root=self.history_root,
            )
        return get_metadata(conf_uid, history_uid)

    def _write_metadata(
        self,
        conf_uid: str,
        history_uid: str,
        metadata: dict,
    ) -> bool:
        if "history_root" in inspect.signature(update_metadate).parameters:
            return update_metadate(
                conf_uid,
                history_uid,
                metadata,
                history_root=self.history_root,
            )
        return update_metadate(conf_uid, history_uid, metadata)

    @staticmethod
    def parse_summary(raw_output: str) -> str:
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise ValueError("Rolling summary is empty")
        text = raw_output.strip()
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced:
            text = fenced.group(1).strip()
        payload = json.loads(text)
        if not isinstance(payload, dict) or set(payload) != {"rolling_summary"}:
            raise ValueError("Rolling summary must contain only rolling_summary")
        raw_summary = payload["rolling_summary"]
        if not isinstance(raw_summary, str):
            raise ValueError("rolling_summary must be a string")
        summary = " ".join(raw_summary.strip().split())
        if not summary:
            raise ValueError("Rolling summary cannot be empty")
        if len(summary) > 100:
            raise ValueError("Rolling summary exceeds 100 characters")
        return summary

    @staticmethod
    def select_turns(
        messages: list[dict],
        context_turns: int,
    ) -> tuple[list[dict[str, str]], int, int]:
        turns: list[dict[str, str]] = []
        pending_user: str | None = None
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "human":
                pending_user = content.strip()
            elif role == "ai" and pending_user is not None:
                turns.append({"user": pending_user, "assistant": content.strip()})
                pending_user = None

        summary_end = len(turns) - max(1, int(context_turns))
        if summary_end < 1:
            return [], 0, 0
        start_index = max(0, summary_end - 30)
        return turns[start_index:summary_end], start_index + 1, summary_end

    def read_injection(
        self,
        conf_uid: str,
        history_uid: str,
        completed_turns: int,
        context_turns: int,
    ) -> str:
        state = self._read_metadata(conf_uid, history_uid).get(
            ROLLING_SUMMARY_METADATA_KEY, {}
        )
        if not isinstance(state, dict):
            return ""
        text = state.get("text")
        end_turn = state.get("end_turn")
        if not isinstance(text, str) or not text.strip():
            return ""
        if isinstance(end_turn, bool) or not isinstance(end_turn, int):
            return ""
        earliest_direct_turn = max(1, completed_turns - context_turns + 1)
        if end_turn >= earliest_direct_turn:
            return ""
        return text.strip()

    async def generate_and_store(
        self,
        conf_uid: str,
        history_uid: str,
        turns: list[dict[str, str]],
        start_turn: int,
        end_turn: int,
        generated_at_turn: int,
        summarize: RollingSummaryCallback,
        force: bool = False,
    ) -> bool:
        if not turns:
            return False
        async with self._get_history_lock(conf_uid, history_uid):
            return await self._generate_and_store_unlocked(
                conf_uid,
                history_uid,
                turns,
                start_turn,
                end_turn,
                generated_at_turn,
                summarize,
                force,
            )

    async def _generate_and_store_unlocked(
        self,
        conf_uid: str,
        history_uid: str,
        turns: list[dict[str, str]],
        start_turn: int,
        end_turn: int,
        generated_at_turn: int,
        summarize: RollingSummaryCallback,
        force: bool,
    ) -> bool:
        existing_state = self._read_metadata(conf_uid, history_uid).get(
            ROLLING_SUMMARY_METADATA_KEY, {}
        )
        existing_turn = (
            existing_state.get("generated_at_turn", 0)
            if isinstance(existing_state, dict)
            else 0
        )
        if (
            not force
            and not isinstance(existing_turn, bool)
            and isinstance(existing_turn, int)
            and existing_turn >= generated_at_turn
        ):
            return False

        summary: str | None = None
        last_error: Exception | None = None
        for _ in range(3):
            try:
                summary = self.parse_summary(await summarize(turns))
                break
            except Exception as exc:
                last_error = exc
        if summary is None:
            logger.error("Rolling summary failed after 3 attempts: {}", last_error)
            return False
        existing_state = self._read_metadata(conf_uid, history_uid).get(
            ROLLING_SUMMARY_METADATA_KEY, {}
        )
        existing_turn = (
            existing_state.get("generated_at_turn", 0)
            if isinstance(existing_state, dict)
            else 0
        )
        if (
            not force
            and not isinstance(existing_turn, bool)
            and isinstance(existing_turn, int)
            and existing_turn > generated_at_turn
        ):
            logger.info(
                "Discarding stale rolling summary for completed turn {}",
                generated_at_turn,
            )
            return False
        saved = self._write_metadata(
            conf_uid,
            history_uid,
            {
                ROLLING_SUMMARY_METADATA_KEY: {
                    "text": summary,
                    "start_turn": start_turn,
                    "end_turn": end_turn,
                    "generated_at_turn": generated_at_turn,
                }
            },
        )
        if saved:
            logger.info(
                "Rolling summary saved for turns {}-{} at completed turn {}",
                start_turn,
                end_turn,
                generated_at_turn,
            )
        return saved


rolling_summary_manager = RollingSummaryManager()
