"""Account/character-scoped relationship score updated from completed chat turns."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger

from .chat_history_manager import get_character_history_dir
from .conversation_state_manager import read_state, write_state


DEFAULT_SCORE = 3
UPDATE_INTERVAL = 5
STATE_KEY = "current_relationship_score"
SCORE_FILE_NAME = "current_relationship_score.md"
ScoreCallback = Callable[[list[dict[str, str]]], Awaitable[str]]


class CurrentRelationshipScoreManager:
    def __init__(
        self,
        history_root: str | Path = "chat_history",
        update_interval: int = UPDATE_INTERVAL,
    ) -> None:
        if update_interval < 1:
            raise ValueError("update_interval must be at least 1")
        self.history_root = Path(history_root)
        self.update_interval = update_interval
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, conf_uid: str) -> asyncio.Lock:
        if conf_uid not in self._locks:
            self._locks[conf_uid] = asyncio.Lock()
        return self._locks[conf_uid]

    def _score_path(self, conf_uid: str) -> Path:
        return get_character_history_dir(conf_uid, self.history_root) / SCORE_FILE_NAME

    @staticmethod
    def _normalize(value: object) -> str:
        return " ".join(str(value).strip().split())

    def read_score(self, conf_uid: str) -> float:
        path = self._score_path(conf_uid)
        if not path.exists():
            return DEFAULT_SCORE
        try:
            score = float(path.read_text(encoding="utf-8").strip())
            if not math.isfinite(score) or not 0 <= score <= 100:
                raise ValueError("Score is outside 0–100")
            return score
        except (OSError, ValueError) as exc:
            logger.error("Invalid relationship score file {}: {}", path, exc)
            return DEFAULT_SCORE

    @staticmethod
    def parse_rating(raw_output: str) -> int:
        try:
            value = json.loads(raw_output.strip())
        except (AttributeError, json.JSONDecodeError) as exc:
            raise ValueError("Relationship rating must be exact JSON") from exc
        if not isinstance(value, dict) or set(value) != {"score"}:
            raise ValueError("Relationship rating must contain only score")
        rating = value["score"]
        if isinstance(rating, bool) or not isinstance(rating, int) or not -5 <= rating <= 5:
            raise ValueError("Relationship rating must be an integer from -5 to 5")
        return rating

    @staticmethod
    def apply_rating(score: float, rating: int) -> float:
        if rating >= 0:
            updated = score + rating * (100 - score) / 200
        else:
            updated = score + rating
        return max(0.0, min(100.0, updated))

    def _read_state(self, conf_uid: str) -> dict:
        return read_state(conf_uid, STATE_KEY, self.history_root) or {}

    def _save_state(self, conf_uid: str, state: dict) -> bool:
        return write_state(conf_uid, STATE_KEY, state, self.history_root)

    def _write_score(self, conf_uid: str, score: float) -> None:
        path = self._score_path(conf_uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(f"{score!r}\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    async def record_turn(
        self,
        conf_uid: str,
        history_uid: str,
        user_content: str,
        assistant_content: str,
    ) -> bool:
        if not conf_uid or not history_uid:
            return False
        user = self._normalize(user_content)
        assistant = self._normalize(assistant_content)
        if not user or not assistant:
            return False
        async with self._lock(conf_uid):
            state = self._read_state(conf_uid)
            pending = state.get("pending_turns", [])
            if not isinstance(pending, list):
                pending = []
            pending.append({
                "history_uid": history_uid,
                "user": user,
                "assistant": assistant,
            })
            state["pending_turns"] = pending
            if not self._save_state(conf_uid, state):
                return False
            return len(pending) >= self.update_interval

    async def discard_pending_turn(
        self,
        conf_uid: str,
        history_uid: str,
        turn: dict[str, str],
    ) -> bool:
        target = {
            "user": self._normalize(turn.get("user", "")),
            "assistant": self._normalize(turn.get("assistant", "")),
        }
        async with self._lock(conf_uid):
            state = self._read_state(conf_uid)
            if not self._finish_pending_commit(conf_uid, state):
                return False
            pending = state.get("pending_turns", [])
            if not isinstance(pending, list):
                return False
            for index in range(len(pending) - 1, -1, -1):
                candidate = pending[index]
                if (
                    isinstance(candidate, dict)
                    and candidate.get("history_uid") == history_uid
                    and all(candidate.get(key) == value for key, value in target.items())
                ):
                    pending.pop(index)
                    state["pending_turns"] = pending
                    return self._save_state(conf_uid, state)
        return False

    def _finish_pending_commit(self, conf_uid: str, state: dict) -> bool:
        """Complete a score-file write interrupted after its checkpoint was saved."""
        commit = state.get("pending_commit")
        if not isinstance(commit, dict):
            return True
        try:
            score = float(commit["score"])
            count = int(commit["count"])
            if not math.isfinite(score) or not 0 <= score <= 100:
                raise ValueError("Invalid pending score")
            self._write_score(conf_uid, score)
            pending = state.get("pending_turns", [])
            state["pending_turns"] = pending[count:] if isinstance(pending, list) else []
            state.pop("pending_commit", None)
            return self._save_state(conf_uid, state)
        except (KeyError, OSError, ValueError) as exc:
            logger.error("Unable to finish relationship score update: {}", exc)
            return False

    async def summarize_pending_update(
        self,
        conf_uid: str,
        history_uid: str,
        summarize: ScoreCallback,
    ) -> bool:
        async with self._lock(conf_uid):
            processed = False
            while True:
                state = self._read_state(conf_uid)
                if not self._finish_pending_commit(conf_uid, state):
                    return processed
                pending = state.get("pending_turns", [])
                if not isinstance(pending, list) or len(pending) < self.update_interval:
                    return processed
                turns = [
                    {"user": turn["user"], "assistant": turn["assistant"]}
                    for turn in pending[: self.update_interval]
                ]
                try:
                    rating = self.parse_rating(await summarize(turns))
                    updated = self.apply_rating(self.read_score(conf_uid), rating)
                    state["pending_commit"] = {
                        "score": updated,
                        "count": self.update_interval,
                    }
                    if not self._save_state(conf_uid, state):
                        return processed
                    if not self._finish_pending_commit(conf_uid, state):
                        return processed
                except Exception as exc:
                    logger.error("Relationship score update failed: {}", exc)
                    return processed
                processed = True
                logger.info("Updated current relationship score to {}", updated)
