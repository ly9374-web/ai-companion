import struct
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger
from pydantic import SecretStr

from ..config_manager.tts import QWEN_TTS_LANGUAGE_HINTS, QWEN_TTS_VOICES
from .tts_interface import TTSInterface


class TTSEngine(TTSInterface):
    """Qwen-Audio Flash TTS over Alibaba Cloud Model Studio's HTTP API."""

    MAX_TEXT_CHARACTERS = 1300
    MAX_ATTEMPTS = 3
    MAX_AUDIO_BYTES = 100 * 1024 * 1024

    def __init__(
        self,
        api_url: str = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
        api_key: SecretStr | str = "",
        model: str = "qwen-audio-3.0-tts-flash",
        voice: str = "qwen-audio-3.0-tts-flash-longyuyaoluan",
        language_hint: str = "en",
        instruction: str = "",
        sample_rate: int = 24000,
        rate: float = 1.0,
        timeout: float = 120.0,
    ) -> None:
        if model != "qwen-audio-3.0-tts-flash":
            raise ValueError(f"Unsupported Qwen TTS model: {model}")
        if voice not in QWEN_TTS_VOICES:
            raise ValueError(f"Unsupported Qwen-Audio Flash voice: {voice}")
        if language_hint not in QWEN_TTS_LANGUAGE_HINTS:
            raise ValueError(f"Unsupported Qwen TTS language hint: {language_hint}")
        if len(instruction) > 2000:
            raise ValueError("Qwen TTS instruction must not exceed 2000 characters")
        if sample_rate != 24000:
            raise ValueError("Qwen-Audio Flash must use a 24000 Hz sample rate")
        if not 0.5 <= rate <= 2.0:
            raise ValueError("Qwen-Audio Flash rate must be between 0.5 and 2.0")

        self.api_url = api_url
        self.api_key = (
            api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        )
        self.model = model
        self.voice = voice
        self.language_hint = language_hint
        self.instruction = instruction.strip()
        self.sample_rate = sample_rate
        self.rate = rate
        self.timeout = timeout

        logger.info(
            "Qwen-Audio Flash TTS initialized with model={} voice={}",
            self.model,
            self.voice,
        )

    def generate_audio(self, text: str, file_name_no_ext=None) -> str:
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("Qwen TTS text must not be empty")
        if len(normalized_text) > self.MAX_TEXT_CHARACTERS:
            raise ValueError(
                f"Qwen TTS text exceeds {self.MAX_TEXT_CHARACTERS} characters"
            )

        output_path = Path(
            self.generate_cache_file_name(file_name_no_ext, file_extension="wav")
        )
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                trust_env=False,
            ) as client:
                response = self._request_with_retry(
                    client,
                    "POST",
                    self.api_url,
                    json={"model": self.model, "input": self._input_payload(normalized_text)},
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                payload = self._response_json(response)
                payload_status = int(payload.get("status_code", response.status_code))
                if payload_status != 200:
                    raise RuntimeError(self._provider_error(payload_status, payload))

                audio_url = self._audio_url(payload)
                download = self._request_with_retry(client, "GET", audio_url)
                audio = self._normalize_wav_sizes(download.content)
                if len(audio) > self.MAX_AUDIO_BYTES:
                    raise RuntimeError("Qwen TTS audio exceeds the download size limit")
                if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
                    raise RuntimeError("Qwen TTS returned an invalid WAV file")
                output_path.write_bytes(audio)
        except Exception:
            output_path.unlink(missing_ok=True)
            raise

        logger.info("Qwen-Audio Flash generated audio: {}", output_path)
        return str(output_path)

    def _input_payload(self, text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text": text,
            "voice": self.voice,
            "format": "wav",
            "sample_rate": self.sample_rate,
            "rate": self.rate,
            "language_hints": [self.language_hint],
        }
        if self.instruction:
            payload["instruction"] = self.instruction
        return payload

    def _request_with_retry(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                response = client.request(method, url, **kwargs)
            except httpx.TransportError as error:
                if attempt == self.MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"Qwen TTS network request failed after {self.MAX_ATTEMPTS} attempts"
                    ) from error
                time.sleep(0.25 * (2 ** (attempt - 1)))
                continue

            if response.status_code == 401:
                raise RuntimeError("Qwen TTS authentication failed; check the API key")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self.MAX_ATTEMPTS:
                    time.sleep(0.25 * (2 ** (attempt - 1)))
                    continue
            if response.status_code >= 400:
                try:
                    payload = self._response_json(response)
                except RuntimeError:
                    payload = {}
                raise RuntimeError(self._provider_error(response.status_code, payload))
            return response

        raise RuntimeError("Qwen TTS request reached an invalid retry state")

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError("Qwen TTS returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise RuntimeError("Qwen TTS returned a non-object response")
        return payload

    @staticmethod
    def _audio_url(payload: dict[str, Any]) -> str:
        try:
            audio = payload["output"]["audio"]
            audio_url = audio["url"]
        except (KeyError, TypeError) as error:
            raise RuntimeError("Qwen TTS response did not include an audio URL") from error
        if not isinstance(audio_url, str) or urlparse(audio_url).scheme not in {
            "http",
            "https",
        }:
            raise RuntimeError("Qwen TTS response included an invalid audio URL")
        return audio_url

    @staticmethod
    def _provider_error(status_code: int, payload: dict[str, Any]) -> str:
        code = " ".join(str(payload.get("code", "")).split())[:120]
        message = " ".join(str(payload.get("message", "")).split())[:300]
        return (
            f"Qwen TTS request failed (status={status_code}, "
            f"code={code or '-'}, message={message or '-'})"
        )

    @staticmethod
    def _normalize_wav_sizes(audio: bytes) -> bytes:
        if len(audio) < 20 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            return audio
        data_index = audio.find(b"data", 12, min(len(audio), 4096))
        if data_index < 0 or data_index + 8 > len(audio):
            return audio
        normalized = bytearray(audio)
        normalized[4:8] = struct.pack("<I", len(audio) - 8)
        normalized[data_index + 4 : data_index + 8] = struct.pack(
            "<I", len(audio) - data_index - 8
        )
        return bytes(normalized)
