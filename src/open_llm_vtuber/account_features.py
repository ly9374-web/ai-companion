"""Account rules and conversation starters shared by every launch mode."""

from __future__ import annotations

import asyncio
import os
import unicodedata
from pathlib import Path
from typing import Any

from loguru import logger
from prompts import prompt_builder


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
    """Persist CS account metadata in every launch mode."""
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

