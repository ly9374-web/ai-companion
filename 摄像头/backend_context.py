"""Validate and render request-scoped camera emotion context."""

from __future__ import annotations

import asyncio
import os
import unicodedata
from pathlib import Path
from typing import Any

from loguru import logger
from prompts import prompt_builder


SUPPORTED_EMOTIONS = {
    "neutral",
    "happy",
    "sad",
    "angry",
    "surprise",
}
EMOTION_LABELS_ZH = {
    "neutral": "中性",
    "happy": "开心",
    "sad": "悲伤",
    "angry": "愤怒",
    "surprise": "惊讶",
}

# 心率拼接阈值：窗口均值高于该值时才往 user prompt 里追加提示。
HEART_RATE_PROMPT_THRESHOLD = 90
HEART_RATE_MIN_BPM = 30
HEART_RATE_MAX_BPM = 220
# 窗口内最少有效心率样本数，不足则视为偶发值，不拼接。
HEART_RATE_MINIMUM_SAMPLES = 3
MAXIMUM_EMOTION_GROUPS = 2
MAXIMUM_LISTENING_SEGMENTS = 100
MAXIMUM_LISTENING_TEXT_LENGTH = 1000
MAXIMUM_PROMPT_LISTENING_SEGMENTS = 3
MAXIMUM_DURATION_MS = 86_400_000
DURATION_ROUNDING_MS = 100
CS_SUFFIX = "cs"
CONVERSATION_STARTERS_FEATURE = "conversation_starters_v1"
STANDARD_ACCOUNT_ONLY_CHARACTER_UIDS = frozenset({"莉娅"})
GREETING_AUDIO_DIR = "greeting_audio"
GREETING_AUDIO_FILENAME = "welcome_message.wav"
GREETING_AUDIO_LOCKS: dict[str, asyncio.Lock] = {}
PROFILER_CONF_UID = "profile_analyst_001"
CONVERSATION_STARTERS = {
    "english": ("我想练英语", "camera_starter_english"),
    "work": ("我想聊工作", "camera_starter_work"),
    "relationships": ("我想聊关系", "camera_starter_relationships"),
    "school": ("我想聊学校", "camera_starter_school"),
    "psychology": ("我想学心理学", "camera_starter_psychology"),
    "story": ("给我讲个故事", "camera_starter_story"),
}


def _account_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", value).strip().casefold()


def _is_cs_account(account_name: object) -> bool:
    return _account_key(account_name).endswith(CS_SUFFIX)


def registration_features(account_name: str) -> dict[str, bool]:
    """Persist compatibility metadata while this feature is installed."""
    return {CONVERSATION_STARTERS_FEATURE: _is_cs_account(account_name)}


def public_account_features(
    account_name: str,
    _persisted_features: dict[str, bool],
) -> dict[str, bool]:
    enabled = _is_cs_account(account_name)
    return {"csMode": enabled, "conversationStarters": enabled}


def account_policy(account_name: str) -> dict[str, bool]:
    return {"isolated_conversation_context": _is_cs_account(account_name)}


def account_can_access_character(account_name: str, conf_uid: object) -> bool:
    if not isinstance(conf_uid, str):
        return True
    if _account_key(conf_uid) == _account_key(PROFILER_CONF_UID):
        return _is_cs_account(account_name)
    if not _is_cs_account(account_name):
        return True
    return _account_key(conf_uid) not in {
        _account_key(value) for value in STANDARD_ACCOUNT_ONLY_CHARACTER_UIDS
    }


def process_text_input(data: dict, context: Any) -> dict[str, Any] | None:
    """Resolve one CS quick-start request without exposing hidden prompts."""
    topic = data.get("quick_start_topic")
    if topic is None:
        return None
    if not _is_cs_account(getattr(context, "account_name", "")):
        raise ValueError("Conversation starters are not enabled for this account")
    starter = CONVERSATION_STARTERS.get(topic) if isinstance(topic, str) else None
    if starter is None:
        raise ValueError("Unsupported conversation starter")
    label, prompt_key = starter
    return {
        "user_input": prompt_builder.load_runtime_prompt(prompt_key),
        "metadata": {
            "history_display_text": label,
            "skip_english_suffix": True,
        },
        "english_mode": topic == "english",
    }


def new_history_messages(context: Any) -> list[dict[str, Any]]:
    if not _is_cs_account(getattr(context, "account_name", "")):
        return []
    character = getattr(context, "character_config", None)
    if character is None:
        return []
    return [
        {
            "role": "ai",
            "content": character.welcome_message,
            "name": character.character_name,
            "avatar": character.avatar,
        }
    ]


def _is_valid_wav(path: Path) -> bool:
    try:
        with path.open("rb") as audio_file:
            header = audio_file.read(12)
    except OSError:
        return False
    return (
        len(header) == 12
        and header[:4] == b"RIFF"
        and header[8:12] == b"WAVE"
    )


async def _get_or_create_character_greeting(context: Any) -> Path:
    voice = context.get_current_tts_voice()
    greeting_filename = (
        f"welcome_message_{voice}.wav" if voice else GREETING_AUDIO_FILENAME
    )
    target_path = (
        context.history_root
        / context.character_config.conf_uid
        / GREETING_AUDIO_DIR
        / greeting_filename
    )
    lock_key = str(target_path.resolve())
    lock = GREETING_AUDIO_LOCKS.setdefault(lock_key, asyncio.Lock())

    async with lock:
        if _is_valid_wav(target_path):
            return target_path
        welcome_message = context.character_config.welcome_message.strip()
        if not welcome_message:
            raise ValueError("The active character has no welcome message")
        if context.tts_engine is None:
            raise RuntimeError("The active character has no TTS engine")

        generated_path: Path | None = None
        try:
            generated_path = Path(
                await context.tts_engine.async_generate_audio(
                    text=welcome_message,
                    file_name_no_ext=(
                        f"welcome_{context.character_config.conf_uid}_{context.client_uid}"
                    ),
                )
            )
            if not _is_valid_wav(generated_path):
                raise ValueError("TTS generated an invalid welcome-message WAV")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(generated_path, target_path)
            generated_path = None
            logger.info(
                "Persisted optional character greeting for account={} character={} at {}",
                context.account_name,
                context.character_config.conf_uid,
                target_path,
            )
            return target_path
        finally:
            if generated_path is not None and generated_path.exists():
                context.tts_engine.remove_file(str(generated_path), verbose=False)


async def after_character_switch(context: Any) -> dict[str, Any] | None:
    if not _is_cs_account(getattr(context, "account_name", "")):
        return None
    try:
        from src.open_llm_vtuber.utils.stream_audio import prepare_audio_payload

        audio_path = await _get_or_create_character_greeting(context)
        return prepare_audio_payload(audio_path=str(audio_path))
    except Exception as exc:
        logger.warning(
            "Unable to prepare optional character greeting for account={} character={}: {}",
            context.account_name,
            context.character_config.conf_uid,
            exc,
        )
        return None


def augment_tool_status(
    tool_name: str,
    is_error: bool,
    metadata: dict[str, Any],
    status: dict[str, Any],
) -> dict[str, Any]:
    """Attach live browser-view metadata only while this feature is installed."""
    if tool_name == "stagehand_navigate" and not is_error:
        live_view_data = metadata.get("liveViewData", {})
        if isinstance(live_view_data, dict) and live_view_data:
            status["browser_view"] = live_view_data
    return status


def _render_emotion_sentence(aggregate: dict[str, Any]) -> str:
    """Return the validated one-turn expression sentence, or an empty string."""
    valid_duration_ms = aggregate.get("valid_duration_ms")
    if (
        isinstance(valid_duration_ms, bool)
        or not isinstance(valid_duration_ms, int)
        or not 0 <= valid_duration_ms <= MAXIMUM_DURATION_MS
    ):
        return ""

    raw_groups = aggregate.get("emotion_durations")
    if raw_groups == []:
        # 中性只作为整轮没有达标非中性表情时的回退结果，不拼接毫秒。
        return (
            "你看到用户回复你时的表情为：中性"
            if aggregate.get("emotions") == ["neutral"]
            else ""
        )
    if not isinstance(raw_groups, list) or not 1 <= len(raw_groups) <= MAXIMUM_EMOTION_GROUPS:
        return ""

    rendered_groups: list[str] = []
    seen_groups: set[tuple[str, ...]] = set()
    duration_total = 0
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            return ""
        raw_emotions = raw_group.get("emotions")
        if not isinstance(raw_emotions, list) or not 1 <= len(raw_emotions) <= 2:
            return ""

        emotions: list[str] = []
        for raw_emotion in raw_emotions:
            if not isinstance(raw_emotion, str):
                return ""
            emotion = raw_emotion.strip().lower()
            if emotion not in SUPPORTED_EMOTIONS or emotion == "neutral" or emotion in emotions:
                return ""
            emotions.append(emotion)

        group_key = tuple(sorted(emotions))
        if group_key in seen_groups:
            return ""
        seen_groups.add(group_key)

        duration_ms = raw_group.get("duration_ms")
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or not DURATION_ROUNDING_MS <= duration_ms <= MAXIMUM_DURATION_MS
            or duration_ms % DURATION_ROUNDING_MS != 0
        ):
            return ""
        duration_total += duration_ms
        if duration_total > MAXIMUM_DURATION_MS:
            return ""

        labels = [EMOTION_LABELS_ZH[emotion] for emotion in emotions]
        rendered_groups.append(f"{duration_ms}毫秒{'或'.join(labels)}")

    if duration_total != valid_duration_ms:
        return ""
    return f"你看到用户回复你时的表情为：{'，'.join(rendered_groups)}"


def _render_heart_rate_sentence(aggregate: dict[str, Any]) -> str:
    """Return the high-heart-rate sentence, or an empty string.

    Heart-rate validation is independent of the emotion sentence: an invalid
    or missing heart rate must not suppress the expression sentence, and vice
    versa.
    """
    heart_rate = aggregate.get("heart_rate")
    if not isinstance(heart_rate, dict):
        return ""

    avg_bpm = heart_rate.get("avg_bpm")
    sample_count = heart_rate.get("sample_count")
    if isinstance(avg_bpm, bool) or not isinstance(avg_bpm, (int, float)):
        return ""
    if isinstance(sample_count, bool) or not isinstance(sample_count, int):
        return ""
    # NaN 参与比较结果为 False，会被该范围检查自然排除。
    if not HEART_RATE_MIN_BPM <= avg_bpm <= HEART_RATE_MAX_BPM:
        return ""
    if sample_count < HEART_RATE_MINIMUM_SAMPLES:
        return ""
    if avg_bpm <= HEART_RATE_PROMPT_THRESHOLD:
        return ""

    return f"你检测到用户当前心率偏高（{int(round(avg_bpm))}）"


def _render_requested_heart_rate_sentence(aggregate: dict[str, Any]) -> str:
    """Return the j-key requested heart-rate sentence, or an empty string.

    The bpm value is captured by the frontend at message-send time (latest
    valid sample within 5 seconds); the backend only re-validates the range.
    """
    bpm = aggregate.get("requested_heart_rate_bpm")
    if isinstance(bpm, bool) or not isinstance(bpm, (int, float)):
        return ""
    # NaN 参与比较结果为 False，会被该范围检查自然排除。
    if not HEART_RATE_MIN_BPM <= bpm <= HEART_RATE_MAX_BPM:
        return ""
    return f"此时用户心率为“{int(round(bpm))}”回复中提到这一点"


def _validated_average_heart_rate(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    avg_bpm = value.get("avg_bpm")
    sample_count = value.get("sample_count")
    if isinstance(avg_bpm, bool) or not isinstance(avg_bpm, (int, float)):
        return None
    if isinstance(sample_count, bool) or not isinstance(sample_count, int):
        return None
    if not HEART_RATE_MIN_BPM <= avg_bpm <= HEART_RATE_MAX_BPM:
        return None
    if sample_count < HEART_RATE_MINIMUM_SAMPLES:
        return None
    return {
        "avg_bpm": int(round(avg_bpm)),
        "sample_count": sample_count,
    }


def _validated_listening_segments(aggregate: dict[str, Any]) -> list[dict[str, Any]]:
    raw_segments = aggregate.get("listening_segments")
    if not isinstance(raw_segments, list):
        return []
    validated: list[dict[str, Any]] = []
    for raw_segment in raw_segments[:MAXIMUM_LISTENING_SEGMENTS]:
        if not isinstance(raw_segment, dict):
            continue
        text = raw_segment.get("text")
        emotion = raw_segment.get("emotion")
        duration_ms = raw_segment.get("duration_ms")
        if not isinstance(text, str):
            continue
        text = text.strip()[:MAXIMUM_LISTENING_TEXT_LENGTH]
        if not text or emotion not in SUPPORTED_EMOTIONS or emotion == "neutral":
            continue
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or not 300 <= duration_ms <= MAXIMUM_DURATION_MS
            or duration_ms % DURATION_ROUNDING_MS != 0
        ):
            continue
        validated.append(
            {
                "text": text,
                "emotion": emotion,
                "duration_ms": duration_ms,
                "interrupted": raw_segment.get("interrupted") is True,
            }
        )
    return validated


def _render_listening_sentences(aggregate: dict[str, Any]) -> list[str]:
    segments = sorted(
        _validated_listening_segments(aggregate),
        key=lambda item: item["duration_ms"],
        reverse=True,
    )[:MAXIMUM_PROMPT_LISTENING_SEGMENTS]
    return [
        (
            f"当用户听到你说“{segment['text']}”时，用户呈现出"
            f"{segment['duration_ms']}毫秒{EMOTION_LABELS_ZH[segment['emotion']]}的表情"
        )
        for segment in segments
    ]


def collect_analysis_data(optional_contexts: Any, context: Any) -> dict[str, Any]:
    """Return profiler-only sensor observations without creating chat context."""
    if getattr(getattr(context, "character_config", None), "conf_uid", "") != PROFILER_CONF_UID:
        return {}
    if not isinstance(optional_contexts, dict):
        return {}
    aggregate = optional_contexts.get("camera_emotion")
    if not isinstance(aggregate, dict):
        return {}

    result: dict[str, Any] = {}
    expression_sentence = _render_emotion_sentence(aggregate)
    user_heart_rate = _validated_average_heart_rate(aggregate.get("heart_rate"))
    if expression_sentence or user_heart_rate:
        user_input: dict[str, Any] = {}
        if expression_sentence:
            user_input["expression_summary"] = expression_sentence
            user_input["expression_durations"] = aggregate.get("emotion_durations", [])
            if aggregate.get("emotions") == ["neutral"]:
                user_input["neutral_duration_ms"] = aggregate.get("valid_duration_ms")
        if user_heart_rate:
            user_input["average_heart_rate_bpm"] = user_heart_rate["avg_bpm"]
            user_input["heart_rate_sample_count"] = user_heart_rate["sample_count"]
        result["user_input"] = user_input

    listening_segments = _validated_listening_segments(aggregate)
    assistant_heart_rate = _validated_average_heart_rate(
        aggregate.get("assistant_heart_rate")
    )
    if listening_segments or assistant_heart_rate:
        heard_previous_ai: dict[str, Any] = {
            "segments": listening_segments,
        }
        if assistant_heart_rate:
            heard_previous_ai["average_heart_rate_bpm"] = (
                assistant_heart_rate["avg_bpm"]
            )
            heard_previous_ai["heart_rate_sample_count"] = (
                assistant_heart_rate["sample_count"]
            )
        result["heard_previous_ai"] = heard_previous_ai
    return result


def build_request_context(optional_contexts: Any, context: Any = None) -> str:
    """Return the request-scoped camera context lines, or an empty string."""
    if getattr(getattr(context, "character_config", None), "conf_uid", "") == PROFILER_CONF_UID:
        # The profiler game must remain blind to expressions and heart rate.
        return ""
    if not isinstance(optional_contexts, dict):
        return ""
    aggregate = optional_contexts.get("camera_emotion")
    if not isinstance(aggregate, dict):
        return ""

    sentences = [
        sentence
        for sentence in (
            _render_emotion_sentence(aggregate),
            *_render_listening_sentences(aggregate),
            _render_requested_heart_rate_sentence(aggregate),
            _render_heart_rate_sentence(aggregate),
        )
        if sentence
    ]
    return "\n".join(sentences)
