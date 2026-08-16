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
    "neutral": "平静",
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

    raw_sequence = aggregate.get("emotion_sequence")
    if not isinstance(raw_sequence, list) or not 1 <= len(raw_sequence) <= 512:
        return ""

    sequence: list[list[str]] = []
    for raw_emotions in raw_sequence:
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
        if "neutral" in emotions and len(emotions) != 1:
            return ""
        sequence.append(emotions)

    if len(sequence) > 1:
        # Mixed neutral stages are meaningful only between two expression stages.
        if "neutral" in sequence[0] or "neutral" in sequence[-1]:
            return ""

    valid_duration_ms = aggregate.get("valid_duration_ms")
    if (
        isinstance(valid_duration_ms, bool)
        or not isinstance(valid_duration_ms, int)
        or not 0 <= valid_duration_ms <= 86_400_000
    ):
        return ""

    labels = [
        "或".join(EMOTION_LABELS_ZH[emotion] for emotion in emotions)
        for emotions in sequence
    ]
    label = labels[0] if len(labels) == 1 else f"先{'转为'.join(labels)}"
    return f"用户当前的表情为：{label}"
