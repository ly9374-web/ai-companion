"""Filter the removable expression protocol from display and TTS text."""

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

    valid = [
        emotion
        for emotion, filename in emotions.items()
        if isinstance(emotion, str)
        and isinstance(filename, str)
        and (FEATURE_DIR / filename).is_file()
    ]
    return tuple(sorted(valid, key=len, reverse=True))


def _protocol_pattern() -> re.Pattern[str]:
    # Accepted forms include:
    # 当前我的情绪为：愉快 / 当前情绪为：愉快 / 当前情绪为愉快
    # and the existing prompt typo: 当前情绪我的为：愉快
    subject = r"(?:我\s*的\s*情绪|情绪\s*我\s*的|情绪)"
    return re.compile(
        rf"[ \t\r\n]*[\"“「『]?当前\s*{subject}\s*为\s*[:：]?\s*"
        rf"(?P<emotion>.*?)\s*[。.!！?？]?\s*[\"”」』]?\s*$",
        re.DOTALL,
    )


def _strip_protocol(text: str, pattern: re.Pattern[str]) -> tuple[str, str | None]:
    if not isinstance(text, str):
        return text, None

    match = pattern.search(text)
    if match is None:
        return text, None

    return text[: match.start()].rstrip(), match.group("emotion")


def process_output(display_text: str, tts_text: str) -> dict[str, Any]:
    """Hide the trailing protocol and return a safe, supported emotion."""
    allowed_emotions = _allowed_emotions()
    pattern = _protocol_pattern()

    cleaned_display, display_emotion = _strip_protocol(display_text, pattern)
    cleaned_tts, tts_emotion = _strip_protocol(tts_text, pattern)
    raw_emotion = display_emotion or tts_emotion
    emotion = raw_emotion if raw_emotion in allowed_emotions else None
    if raw_emotion is not None and emotion is None:
        emotion = "中性" if "中性" in allowed_emotions else None
        logger.warning(
            "Unsupported expression label was hidden and reset to neutral: {}",
            raw_emotion,
        )
    return {
        "display_text": cleaned_display,
        "tts_text": cleaned_tts,
        "emotion": emotion,
    }
