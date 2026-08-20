"""Validate and render request-scoped camera emotion context."""

from __future__ import annotations

from typing import Any


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


def _render_emotion_sentence(aggregate: dict[str, Any]) -> str:
    """Return the validated one-turn expression sentence, or an empty string."""
    raw_emotions = aggregate.get("emotions")
    if not isinstance(raw_emotions, list) or not 1 <= len(raw_emotions) <= 2:
        return ""

    emotions: list[str] = []
    for raw_emotion in raw_emotions:
        if not isinstance(raw_emotion, str):
            return ""
        emotion = raw_emotion.strip().lower()
        if emotion not in SUPPORTED_EMOTIONS or emotion in emotions:
            return ""
        emotions.append(emotion)

    # Neutral is always a single fallback result, never part of an ambiguous pair.
    if "neutral" in emotions and len(emotions) != 1:
        return ""

    valid_duration_ms = aggregate.get("valid_duration_ms")
    if (
        isinstance(valid_duration_ms, bool)
        or not isinstance(valid_duration_ms, int)
        or not 0 <= valid_duration_ms <= 86_400_000
    ):
        return ""

    labels = [EMOTION_LABELS_ZH[emotion] for emotion in emotions]
    label = labels[0] if len(labels) == 1 else f"先{labels[0]}转为{labels[1]}"
    return f"你看到用户回复你时的表情为：{label}"


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


def build_request_context(optional_contexts: Any) -> str:
    """Return the request-scoped camera context lines, or an empty string."""
    if not isinstance(optional_contexts, dict):
        return ""
    aggregate = optional_contexts.get("camera_emotion")
    if not isinstance(aggregate, dict):
        return ""

    sentences = [
        sentence
        for sentence in (
            _render_emotion_sentence(aggregate),
            _render_heart_rate_sentence(aggregate),
        )
        if sentence
    ]
    return "\n".join(sentences)
