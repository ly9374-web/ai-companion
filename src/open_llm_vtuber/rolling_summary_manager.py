"""Recursive per-history summary updated after each x normal turns."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger

from .chat_history_manager import (
    extract_normal_turns,
    get_history,
    get_metadata,
    update_metadate,
)


ROLLING_SUMMARY_METADATA_KEY = "rolling_summary"
RollingSummaryCallback = Callable[[list[dict[str, str]], str], Awaitable[str]]


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

    def read_injection(
        self,
        conf_uid: str,
        history_uid: str,
    ) -> str:
        state = self._read_metadata(conf_uid, history_uid).get(
            ROLLING_SUMMARY_METADATA_KEY, {}
        )
        if not isinstance(state, dict):
            return ""
        text = state.get("text")
        if not isinstance(text, str) or not text.strip():
            return ""
        return text.strip()

    @staticmethod
    def _turn_payloads(messages: list[dict]) -> list[dict[str, str]]:
        return [
            {
                "user": str(turn["user"].get("content", "")).strip(),
                "assistant": str(turn["assistant"].get("content", "")).strip(),
            }
            for turn in extract_normal_turns(messages)
        ]

    async def generate_next_batch(
        self,
        conf_uid: str,
        history_uid: str,
        batch_size: int,
        summarize: RollingSummaryCallback,
        force: bool = False,
    ) -> bool:
        if batch_size < 1:
            return False
        async with self._get_history_lock(conf_uid, history_uid):
            return await self._generate_next_batch_unlocked(
                conf_uid,
                history_uid,
                batch_size,
                summarize,
                force,
            )

    async def _generate_next_batch_unlocked(
        self,
        conf_uid: str,
        history_uid: str,
        batch_size: int,
        summarize: RollingSummaryCallback,
        force: bool,
    ) -> bool:
        existing_state = self._read_metadata(conf_uid, history_uid).get(
            ROLLING_SUMMARY_METADATA_KEY, {}
        )
        if not isinstance(existing_state, dict):
            existing_state = {}
        summarized_turns = existing_state.get(
            "summarized_normal_turns",
            existing_state.get("end_turn", 0),
        )
        if isinstance(summarized_turns, bool) or not isinstance(
            summarized_turns, int
        ):
            summarized_turns = 0
        summarized_turns = max(0, summarized_turns)
        all_turns = self._turn_payloads(
            get_history(conf_uid, history_uid, self.history_root)
        )
        pending_count = len(all_turns) - summarized_turns
        if pending_count <= 0 or (not force and pending_count < batch_size):
            return False
        take_count = min(batch_size, pending_count) if force else batch_size
        start_turn = summarized_turns + 1
        end_turn = summarized_turns + take_count
        turns = all_turns[summarized_turns:end_turn]
        previous_summary = existing_state.get("text", "")
        if not isinstance(previous_summary, str):
            previous_summary = ""

        summary: str | None = None
        last_error: Exception | None = None
        for _ in range(3):
            try:
                summary = self.parse_summary(
                    await summarize(turns, previous_summary.strip())
                )
                break
            except Exception as exc:
                last_error = exc
        if summary is None:
            logger.error("Rolling summary failed after 3 attempts: {}", last_error)
            return False
        saved = self._write_metadata(
            conf_uid,
            history_uid,
            {
                ROLLING_SUMMARY_METADATA_KEY: {
                    "text": summary,
                    "start_turn": start_turn,
                    "end_turn": end_turn,
                    "generated_at_turn": end_turn,
                    "summarized_normal_turns": end_turn,
                    "batch_size": batch_size,
                }
            },
        )
        if saved:
            logger.info(
                "Rolling summary saved for turns {}-{} at completed turn {}",
                start_turn,
                end_turn,
                end_turn,
            )
        return saved

    async def generate_ready_batches(
        self,
        conf_uid: str,
        history_uid: str,
        batch_size: int,
        summarize: RollingSummaryCallback,
    ) -> bool:
        """Serially consume every complete x-turn batch currently available."""
        generated = False
        while await self.generate_next_batch(
            conf_uid,
            history_uid,
            batch_size,
            summarize,
        ):
            generated = True
        return generated


rolling_summary_manager = RollingSummaryManager()
