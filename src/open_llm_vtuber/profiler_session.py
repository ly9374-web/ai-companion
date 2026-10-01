"""Open-ended profiler role state, protocol handling, and final artifact output."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from prompts import prompt_builder

from .chat_history_manager import get_history


PROFILER_CONF_UID = "profile_analyst_001"
PROFILER_START_COMMAND = "开始侧写"
PROFILER_MINIMUM_RESOLUTION_ROUND = 6
PROFILER_INTERIM_ROUNDS = (1, 3)

_ROUND_LINE_RE = re.compile(
    r"[ \t\r\n]*[“\"]?本轮为第\s*\d+\s*轮[。.!！?？]?[”\"]?\s*",
    re.MULTILINE,
)
_EMOTION_SUFFIX_RE = re.compile(
    r"[ \t\r\n]*[\"“「『]?当前\s*(?:我\s*的\s*情绪|情绪\s*我\s*的|情绪)"
    r"\s*为\s*[:：]?\s*.*?\s*[。.!！?？]?[\"”」』]?\s*$",
    re.DOTALL,
)
_PROBLEM_SOLVED_LINE_RE = re.compile(
    r"(?:^|\n)\s*[“\"]?问题解决[。.!！]?[”\"]?\s*(?=\n|$)",
    re.MULTILINE,
)


GenerateProfiler = Callable[[str], Awaitable[str]]


def is_profiler_character(conf_uid: object) -> bool:
    return isinstance(conf_uid, str) and conf_uid == PROFILER_CONF_UID


def _is_start_command(text: object) -> bool:
    if not isinstance(text, str):
        return False
    normalized = text.strip()
    if not normalized:
        return False
    return normalized.splitlines()[0].strip() == PROFILER_START_COMMAND


def completed_profiler_rounds(messages: list[dict[str, Any]]) -> int:
    """Count USER decisions after the first explicit start command."""
    user_messages = [
        message
        for message in _normal_messages(messages)
        if message.get("role") == "human"
    ]
    start_index = next(
        (
            index
            for index, message in enumerate(user_messages)
            if _is_start_command(message.get("display_content") or message.get("content"))
        ),
        None,
    )
    if start_index is None:
        return 0
    return max(0, len(user_messages) - start_index - 1)


def formal_round_for_turn(
    previous_messages: list[dict[str, Any]],
    input_text: str,
) -> int:
    """Return -1 before start, 0 for opening, then an unbounded round number."""
    if completed_profiler_rounds(previous_messages) == 0:
        already_started = any(
            message.get("role") == "human"
            and _is_start_command(
                message.get("display_content") or message.get("content")
            )
            for message in _normal_messages(previous_messages)
        )
        if not already_started:
            return 0 if _is_start_command(input_text) else -1
    return completed_profiler_rounds(previous_messages) + 1


def has_problem_solved_protocol(text: object) -> bool:
    """Return whether the model emitted the standalone completion marker."""
    return isinstance(text, str) and _PROBLEM_SOLVED_LINE_RE.search(text) is not None


def profiler_session_is_solved(messages: list[dict[str, Any]]) -> bool:
    return any(
        message.get("role") == "ai"
        and has_problem_solved_protocol(message.get("content"))
        for message in _normal_messages(messages)
    )


def apply_hidden_round_protocol(text: str, formal_round: int) -> str:
    """Normalize the hidden round marker while leaving the emotion suffix last."""
    if not isinstance(text, str):
        return text
    problem_solved = (
        formal_round >= PROFILER_MINIMUM_RESOLUTION_ROUND
        and has_problem_solved_protocol(text)
    )
    cleaned = _PROBLEM_SOLVED_LINE_RE.sub("\n", text)
    cleaned = _ROUND_LINE_RE.sub("\n", cleaned).strip()
    if formal_round <= 0:
        return cleaned

    marker = f"本轮为第{formal_round}轮"
    emotion_match = _EMOTION_SUFFIX_RE.search(cleaned)
    if emotion_match is None:
        completion = "\n问题解决" if problem_solved else ""
        return f"{cleaned}{completion}\n{marker}".strip()
    prefix = cleaned[: emotion_match.start()].rstrip()
    emotion_suffix = cleaned[emotion_match.start():].strip()
    if problem_solved:
        prefix = f"{prefix}\n问题解决".strip()
    return f"{prefix}\n{marker}\n{emotion_suffix}".strip()


def strip_character_emotion_protocol(text: str) -> str:
    """Remove the character's rendering protocol from analysis transcripts."""
    if not isinstance(text, str):
        return ""
    return _EMOTION_SUFFIX_RE.sub("", text).strip()


def _normal_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        message
        for message in messages
        if message.get("role") in {"human", "ai"}
        and message.get("debug_mode") is not True
    ]


def build_profiler_analysis_payload(
    *,
    account_name: str,
    history_uid: str,
    history_root: Path,
    final_listening_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one structured, role-labelled analysis payload for DeepSeek."""
    messages = _normal_messages(
        get_history(PROFILER_CONF_UID, history_uid, history_root)
    )
    transcript: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    user_index = 0
    assistant_index = 0

    for message in messages:
        role = message.get("role")
        content = str(message.get("content", "")).strip()
        if role == "human":
            user_index += 1
            transcript.append(
                {
                    "speaker": "USER",
                    "message_index": user_index,
                    "content": content,
                }
            )
            analysis_data = message.get("analysis_data")
            if isinstance(analysis_data, dict) and analysis_data:
                observations.append(
                    {
                        "attached_to_user_message_index": user_index,
                        "heard_previous_ai_message_index": assistant_index,
                        "meaning": prompt_builder.load_runtime_prompt(
                            "profiler_user_observation_meaning"
                        ),
                        "data": analysis_data,
                    }
                )
        elif role == "ai":
            assistant_index += 1
            transcript.append(
                {
                    "speaker": "AI",
                    "message_index": assistant_index,
                    "content": strip_character_emotion_protocol(content),
                }
            )

    if isinstance(final_listening_data, dict) and final_listening_data:
        observations.append(
            {
                "attached_to_ai_message_index": assistant_index,
                "meaning": prompt_builder.load_runtime_prompt(
                    "profiler_final_observation_meaning"
                ),
                "data": final_listening_data,
            }
        )

    return {
        "target_name": account_name,
        "history_uid": history_uid,
        "round_definition": prompt_builder.load_runtime_prompt(
            "profiler_round_definition"
        ),
        "transcript": transcript,
        "sensor_observations": observations,
    }


class ProfilerAnalysisManager:
    """Generate and publish session-scoped thinslice artifacts."""

    async def generate(
        self,
        *,
        account_name: str,
        history_uid: str,
        history_root: Path,
        final_listening_data: dict[str, Any] | None,
        generate: GenerateProfiler,
        interim_round: int | None = None,
    ) -> dict[str, Any]:
        payload = build_profiler_analysis_payload(
            account_name=account_name,
            history_uid=history_uid,
            history_root=history_root,
            final_listening_data=final_listening_data,
        )
        source_messages = _normal_messages(
            get_history(PROFILER_CONF_UID, history_uid, history_root)
        )
        if interim_round is None:
            if (
                completed_profiler_rounds(source_messages)
                < PROFILER_MINIMUM_RESOLUTION_ROUND
                or not profiler_session_is_solved(source_messages)
            ):
                raise RuntimeError("危机尚未自然解决，不能生成分析")

        user_prompt = prompt_builder.build_profiler_thinslice_input(
            account_name,
            payload,
        )
        output = (await generate(user_prompt)).strip()
        if not output:
            raise RuntimeError("侧写模型返回了空内容")

        session_dir = (
            Path(history_root)
            / PROFILER_CONF_UID
            / "profiler_sessions"
            / history_uid
        )
        session_dir.mkdir(parents=True, exist_ok=True)
        if interim_round is None:
            user_prompt_path = session_dir / "thinslice_user_prompt.md"
            user_prompt_path.write_text(user_prompt + "\n", encoding="utf-8")
            (session_dir / "analysis_input.md").write_text(
                user_prompt + "\n", encoding="utf-8"
            )
            report_path = session_dir / "thinslice.md"
        else:
            user_prompt_path = (
                session_dir / f"thinslice_round{interim_round}_user_prompt.md"
            )
            user_prompt_path.write_text(user_prompt + "\n", encoding="utf-8")
            report_path = session_dir / f"thinslice_round{interim_round}.md"
        report_path.write_text(output.rstrip() + "\n", encoding="utf-8")
        result = {
            "status": "success",
            "path": report_path.as_posix(),
            "user_prompt_path": user_prompt_path.as_posix(),
            "content": output,
        }
        if interim_round is not None:
            result["round"] = interim_round
        return result
