"""Validate and render request-scoped camera emotion context."""

from __future__ import annotations

from typing import Any


SUPPORTED_EMOTIONS = {
    "neutral",
    "happy",
    "sad",
    "angry",
    "surprise",
    "disgust",
}
EMOTION_LABELS_ZH = {
    "neutral": "中性",
    "happy": "开心",
    "sad": "悲伤",
    "angry": "愤怒",
    "surprise": "惊讶",
    "disgust": "厌恶",
}


def build_request_context(optional_contexts: Any) -> str:
    """Return the validated one-turn expression sentence, or an empty string."""
    if not isinstance(optional_contexts, dict):
        return ""
    aggregate = optional_contexts.get("camera_emotion")
    if not isinstance(aggregate, dict):
        return ""

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

    label = "或".join(EMOTION_LABELS_ZH[emotion] for emotion in emotions)
    return f"你能看到用户当前的表情为：{label}"
