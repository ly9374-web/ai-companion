from typing import Type
from .tts_interface import TTSInterface


class TTSFactory:
    @staticmethod
    def get_tts_engine(engine_type, **kwargs) -> Type[TTSInterface]:
        if engine_type == "qwen_tts":
            from .qwen_tts import TTSEngine as QwenTTSEngine

            return QwenTTSEngine(
                api_url=kwargs.get("api_url"),
                api_key=kwargs.get("api_key"),
                model=kwargs.get("model"),
                voice=kwargs.get("voice"),
                language_hint=kwargs.get("language_hint"),
                instruction=kwargs.get("instruction"),
                sample_rate=kwargs.get("sample_rate"),
                rate=kwargs.get("rate"),
                timeout=kwargs.get("timeout"),
            )
        else:
            raise ValueError(f"Unknown TTS engine type: {engine_type}")
