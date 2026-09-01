"""Hide the profiler's round and expression protocols from display and TTS."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger


FEATURE_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = FEATURE_DIR / "manifest.json"


def _allowed_emotions() -> tuple[str, ...]:
    with MANIFEST_PATH.open("r", encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
    emotions = manifest.get("emotions")
    if not isinstance(emotions, dict):
        return ()
    return tuple(
        sorted(
            (
                emotion
                for emotion, filename in emotions.items()
                if isinstance(emotion, str)
                and isinstance(filename, str)
                and (FEATURE_DIR / filename).is_file()
            ),
            key=len,
            reverse=True,
        )
    )


def _emotion_pattern() -> re.Pattern[str]:
    subject = r"(?:我\s*的\s*情绪|情绪\s*我\s*的|情绪)"
    return re.compile(
        rf"[ \t\r\n]*[\"“「『]?当前\s*{subject}\s*为\s*[:：]?\s*"
        rf"(?P<emotion>.*?)\s*[。.!！?？]?[\"”」』]?\s*$",
        re.DOTALL,
    )


ROUND_PATTERN = re.compile(
    r"[ \t\r\n]*[\"“]?本轮为第\s*\d+\s*轮[。.!！?？]?[\"”]?\s*$"
)
BRACKET_PATTERN = re.compile(r"\[[^\[\]]*\]")
WHITESPACE_PATTERN = re.compile(r"\s+")


def _strip_emotion(
    text: str,
    pattern: re.Pattern[str],
) -> tuple[str, str | None]:
    if not isinstance(text, str):
        return text, None
    match = pattern.search(text)
    if match is None:
        return text, None
    return text[: match.start()].rstrip(), match.group("emotion")


def _strip_round(text: str) -> str:
    if not isinstance(text, str):
        return text
    return ROUND_PATTERN.sub("", text).rstrip()


def _strip_display_markers(text: str) -> str:
    cleaned = BRACKET_PATTERN.sub("", text)
    return WHITESPACE_PATTERN.sub(" ", cleaned).strip()


def process_output(display_text: str, tts_text: str) -> dict[str, Any]:
    allowed_emotions = _allowed_emotions()
    pattern = _emotion_pattern()
    cleaned_display, display_emotion = _strip_emotion(display_text, pattern)
    cleaned_tts, tts_emotion = _strip_emotion(tts_text, pattern)
    cleaned_display = _strip_display_markers(_strip_round(cleaned_display))
    cleaned_tts = _strip_round(cleaned_tts)
    raw_emotion = display_emotion or tts_emotion
    protocol_detected = display_emotion is not None or tts_emotion is not None
    emotion = raw_emotion if raw_emotion in allowed_emotions else None
    if raw_emotion is not None and emotion is None:
        emotion = "中性" if "中性" in allowed_emotions else None
        logger.warning(
            "Unsupported profiler expression label was reset to neutral: {}",
            raw_emotion,
        )
    return {
        "display_text": cleaned_display,
        "tts_text": cleaned_tts,
        "emotion": emotion,
        "raw_emotion": raw_emotion,
        "protocol_detected": protocol_detected,
    }
