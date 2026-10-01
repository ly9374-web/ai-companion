"""Profiler-only TTS chunking, throttling, merging, and speech timing."""

import asyncio
import json
import re
import uuid
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from ..agent.output_types import Actions, DisplayText, SentenceOutput
from ..conversations.tts_manager import TTSTaskManager
from ..conversations.types import WebSocketSend
from ..optional_features import process_expression_output
from ..tts.tts_interface import TTSInterface
from ..utils.stream_audio import prepare_audio_payload


TTS_CHUNK_MIN_CHARACTERS = 30
TTS_CHUNK_MAX_CHARACTERS = 180
MAX_TTS_REQUESTS_PER_BATCH = 3
MIN_BATCH_START_INTERVAL_SECONDS = 1.05
INTER_CHUNK_SILENCE_MS = 100

_REQUEST_START_TIMES: deque[float] = deque()
_RATE_LIMIT_LOCK = asyncio.Lock()


async def queue_profiler_audio(
    outputs: List[SentenceOutput],
    character_config: Any,
    tts_engine: TTSInterface,
    websocket_send: WebSocketSend,
    tts_manager: TTSTaskManager,
    generate_audio: bool = True,
) -> str:
    """Queue one combined, timed audio payload for a profiler response."""
    natural_chunks = _group_sentence_outputs(
        outputs,
        character_name=character_config.character_name,
        avatar=character_config.avatar,
    )
    display_parts: List[str] = []
    tts_chunks: List[tuple[str, str]] = []
    expressions: List[str | int] = []
    final_emotion: Optional[str] = None

    for output in natural_chunks:
        expression_result = process_expression_output(
            output.display_text.text,
            output.tts_text,
            expression_dir=getattr(character_config, "expression_dir", None),
        )
        cleaned_display = expression_result["display_text"]
        cleaned_tts = expression_result["tts_text"]
        display_parts.append(cleaned_display)
        if len(re.sub(r'[\s.,!?，。！？\'"』」）】]+', "", cleaned_tts)) > 0:
            tts_chunks.append((cleaned_tts, cleaned_display))
        if expression_result["emotion"]:
            final_emotion = expression_result["emotion"]
        if output.actions and output.actions.expressions:
            expressions.extend(output.actions.expressions)

    merged_display = DisplayText(
        text="".join(display_parts),
        name=character_config.character_name,
        avatar=character_config.avatar,
    )
    merged_actions = Actions(expressions=expressions or None)

    if not generate_audio or not tts_chunks:
        await websocket_send(
            json.dumps(
                prepare_audio_payload(
                    audio_path=None,
                    display_text=merged_display,
                    actions=merged_actions,
                    emotion=final_emotion,
                )
            )
        )
        return merged_display.text

    task = asyncio.create_task(
        _synthesize_and_send(
            chunks=tts_chunks,
            display_text=merged_display,
            actions=merged_actions,
            tts_engine=tts_engine,
            websocket_send=websocket_send,
            emotion=final_emotion,
        )
    )
    tts_manager.task_list.append(task)
    return merged_display.text


def _group_sentence_outputs(
    outputs: List[SentenceOutput],
    character_name: str,
    avatar: Optional[str],
) -> List[SentenceOutput]:
    """Group on sentence boundaries after 30 useful characters."""
    groups: List[List[SentenceOutput]] = []
    current_group: List[SentenceOutput] = []
    current_length = 0
    current_text = ""

    for output in outputs:
        output_length = len(re.sub(r"\[[^\[\]]*\]|\s+", "", output.tts_text or ""))
        if (
            current_group
            and current_length >= TTS_CHUNK_MIN_CHARACTERS
            and current_length + output_length > TTS_CHUNK_MAX_CHARACTERS
            and not _has_unclosed_spoken_quote(current_text)
        ):
            groups.append(current_group)
            current_group = []
            current_length = 0
            current_text = ""

        current_group.append(output)
        current_length += output_length
        current_text += output.tts_text or ""
        if (
            current_length >= TTS_CHUNK_MIN_CHARACTERS
            and not _has_unclosed_spoken_quote(current_text)
        ):
            groups.append(current_group)
            current_group = []
            current_length = 0
            current_text = ""

    if current_group:
        if groups:
            groups[-1].extend(current_group)
        else:
            groups.append(current_group)

    merged_groups = [
        _merge_sentence_outputs(group, character_name, avatar)
        for group in groups
    ]
    logger.debug(
        "Profiler grouped {} sentence outputs into {} TTS chunks: {}",
        len(outputs),
        len(merged_groups),
        [len(chunk.tts_text) for chunk in merged_groups],
    )
    return merged_groups


def _merge_sentence_outputs(
    outputs: List[SentenceOutput],
    character_name: str,
    avatar: Optional[str],
) -> SentenceOutput:
    display_text = DisplayText(
        text="".join(output.display_text.text for output in outputs),
        name=character_name,
        avatar=avatar,
    )
    actions = Actions(
        expressions=[
            expression
            for output in outputs
            if output.actions and output.actions.expressions
            for expression in output.actions.expressions
        ]
        or None
    )
    return SentenceOutput(
        display_text=display_text,
        tts_text=" ".join(output.tts_text for output in outputs if output.tts_text),
        actions=actions,
    )


def _has_unclosed_spoken_quote(text: str) -> bool:
    return any(
        text.count(opening) > text.count(closing)
        for opening, closing in (("“", "”"), ("「", "」"), ("『", "』"), ("‘", "’"))
    )


async def _synthesize_and_send(
    chunks: List[tuple[str, str]],
    display_text: DisplayText,
    actions: Actions,
    tts_engine: TTSInterface,
    websocket_send: WebSocketSend,
    emotion: Optional[str],
) -> None:
    audio_paths: List[str] = []
    combined_path: Optional[str] = None
    try:
        for batch_start in range(0, len(chunks), MAX_TTS_REQUESTS_PER_BATCH):
            batch = chunks[batch_start : batch_start + MAX_TTS_REQUESTS_PER_BATCH]
            results = await asyncio.gather(
                *(_generate_audio(tts_engine, text) for text, _ in batch),
                return_exceptions=True,
            )
            errors = [result for result in results if isinstance(result, Exception)]
            audio_paths.extend(
                str(result) for result in results if not isinstance(result, Exception)
            )
            if errors:
                failed_texts = [
                    batch[index][0]
                    for index, result in enumerate(results)
                    if isinstance(result, Exception)
                ]
                raise RuntimeError(
                    "Profiler TTS chunk generation failed: " + " | ".join(failed_texts)
                ) from errors[0]

        combined_path = tts_engine.generate_cache_file_name(
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}_profiler",
            file_extension="wav",
        )
        speech_segments = await asyncio.to_thread(
            _merge_wav_files,
            audio_paths,
            [segment_text for _, segment_text in chunks],
            combined_path,
        )
        payload = prepare_audio_payload(
            audio_path=combined_path,
            display_text=display_text,
            actions=actions,
            emotion=emotion,
        )
        payload["speech_segments"] = speech_segments
        await websocket_send(json.dumps(payload))
    finally:
        for audio_path in audio_paths:
            tts_engine.remove_file(audio_path)
        if combined_path:
            Path(combined_path).unlink(missing_ok=True)


async def _generate_audio(tts_engine: TTSInterface, text: str) -> str:
    await _wait_for_request_slot()
    logger.debug("Generating profiler TTS chunk for: {!r}", text)
    return await tts_engine.async_generate_audio(
        text=text,
        file_name_no_ext=(
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"
        ),
    )


async def _wait_for_request_slot() -> None:
    loop = asyncio.get_running_loop()
    async with _RATE_LIMIT_LOCK:
        while True:
            now = loop.time()
            while (
                _REQUEST_START_TIMES
                and now - _REQUEST_START_TIMES[0] >= MIN_BATCH_START_INTERVAL_SECONDS
            ):
                _REQUEST_START_TIMES.popleft()
            if len(_REQUEST_START_TIMES) < MAX_TTS_REQUESTS_PER_BATCH:
                _REQUEST_START_TIMES.append(now)
                return
            await asyncio.sleep(
                MIN_BATCH_START_INTERVAL_SECONDS - (now - _REQUEST_START_TIMES[0])
            )


def _merge_wav_files(
    audio_paths: List[str],
    segment_texts: List[str],
    output_path: str,
) -> List[Dict[str, object]]:
    if not audio_paths or len(audio_paths) != len(segment_texts):
        raise ValueError("Cannot merge an incomplete profiler TTS audio batch")

    speech_segments: List[Dict[str, object]] = []
    reference_format: Optional[tuple[int, int, int, str]] = None
    cursor_frames = 0

    with wave.open(output_path, "wb") as writer:
        for index, (audio_path, segment_text) in enumerate(
            zip(audio_paths, segment_texts)
        ):
            with wave.open(audio_path, "rb") as reader:
                current_format = (
                    reader.getnchannels(),
                    reader.getsampwidth(),
                    reader.getframerate(),
                    reader.getcomptype(),
                )
                if reference_format is None:
                    reference_format = current_format
                    writer.setnchannels(current_format[0])
                    writer.setsampwidth(current_format[1])
                    writer.setframerate(current_format[2])
                    writer.setcomptype(current_format[3], reader.getcompname())
                elif current_format != reference_format:
                    raise ValueError("Profiler TTS WAV chunks use incompatible formats")

                frame_count = reader.getnframes()
                writer.writeframesraw(reader.readframes(frame_count))
                frame_rate = current_format[2]
                segment_start_ms = round(cursor_frames * 1000 / frame_rate)
                cursor_frames += frame_count
                segment_end_ms = round(cursor_frames * 1000 / frame_rate)
                speech_segments.append(
                    {
                        "text": segment_text,
                        "start_ms": segment_start_ms,
                        "end_ms": segment_end_ms,
                    }
                )

                if index < len(audio_paths) - 1:
                    silence_frames = round(frame_rate * INTER_CHUNK_SILENCE_MS / 1000)
                    writer.writeframesraw(
                        b"\x00"
                        * silence_frames
                        * current_format[0]
                        * current_format[1]
                    )
                    cursor_frames += silence_frames

    return speech_segments
