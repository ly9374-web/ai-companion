# config_manager/tts.py (simplified - Qwen TTS only)
from pydantic import ValidationInfo, Field, SecretStr, model_validator
from typing import Literal, Optional, Dict, ClassVar
from .i18n import I18nMixin, Description

QwenTTSVoice = Literal[
    "qwen-audio-3.0-tts-flash-longyuyaoluan",
    "qwen-audio-3.0-tts-flash-longshanzhuxin",
    "qwen-audio-3.0-tts-flash-longxiniyan",
    "qwen-audio-3.0-tts-flash-loongivyhu",
    "qwen-audio-3.0-tts-flash-longqingxiangju",
    "qwen-audio-3.0-tts-flash-loongolivialin",
    "qwen-audio-3.0-tts-flash-longxuansongxing",
    "qwen-audio-3.0-tts-flash-loongadriangao",
    "qwen-audio-3.0-tts-flash-longfengjinhe",
    "qwen-audio-3.0-tts-flash-longhuiluling",
    "qwen-audio-3.0-tts-flash-longxueyujun",
    "qwen-audio-3.0-tts-flash-longhexueling",
    "qwen-audio-3.0-tts-flash-longhongweifeng",
    "qwen-audio-3.0-tts-flash-longfengxiuche",
]

QWEN_TTS_VOICES = frozenset(QwenTTSVoice.__args__)
QWEN_TTS_VOICE_LABELS = {
    "qwen-audio-3.0-tts-flash-longyuyaoluan": "龙羽瑶鸾",
    "qwen-audio-3.0-tts-flash-longshanzhuxin": "龙杉竹昕",
    "qwen-audio-3.0-tts-flash-longxiniyan": "龙溪霓燕",
    "qwen-audio-3.0-tts-flash-loongivyhu": "Ivy Hu（艾薇·胡）",
    "qwen-audio-3.0-tts-flash-longqingxiangju": "龙晴湘菊",
    "qwen-audio-3.0-tts-flash-loongolivialin": "Olivia Lin（奥利维亚·林）",
    "qwen-audio-3.0-tts-flash-longxuansongxing": "龙璇松杏",
    "qwen-audio-3.0-tts-flash-loongadriangao": "Adrian Gao（艾德里安·高）",
    "qwen-audio-3.0-tts-flash-longfengjinhe": "龙峰瑾鹤",
    "qwen-audio-3.0-tts-flash-longhuiluling": "龙晦露凌",
    "qwen-audio-3.0-tts-flash-longxueyujun": "龙雪瑜珺",
    "qwen-audio-3.0-tts-flash-longhexueling": "龙荷雪翎",
    "qwen-audio-3.0-tts-flash-longhongweifeng": "龙鸿薇枫",
    "qwen-audio-3.0-tts-flash-longfengxiuche": "龙凤岫澈",
}
QwenTTSLanguageHint = Literal["en", "zh"]
QWEN_TTS_LANGUAGE_HINTS = frozenset(QwenTTSLanguageHint.__args__)
QWEN_TTS_LANGUAGE_LABELS = {"en": "英文", "zh": "中文"}


class QwenTTSConfig(I18nMixin):
    """Configuration for Qwen-Audio Flash TTS."""

    api_url: str = Field(
        "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
        alias="api_url",
    )
    api_key: SecretStr = Field(..., alias="api_key")
    model: Literal["qwen-audio-3.0-tts-flash"] = Field(
        "qwen-audio-3.0-tts-flash", alias="model"
    )
    voice: QwenTTSVoice = Field(
        "qwen-audio-3.0-tts-flash-longyuyaoluan", alias="voice"
    )
    language_hint: QwenTTSLanguageHint = Field("en", alias="language_hint")
    instruction: str = Field("", max_length=2000, alias="instruction")
    sample_rate: Literal[24000] = Field(24000, alias="sample_rate")
    rate: float = Field(1.0, ge=0.5, le=2.0, alias="rate")
    timeout: float = Field(120.0, gt=0, alias="timeout")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_url": Description(
            en="Qwen-Audio TTS HTTP endpoint",
            zh="Qwen-Audio TTS HTTP 接口地址",
        ),
        "api_key": Description(
            en="Alibaba Cloud Model Studio API key for the Beijing region",
            zh="阿里云百炼北京地域的 API Key",
        ),
        "model": Description(
            en="Qwen-Audio TTS model", zh="Qwen-Audio TTS 模型"
        ),
        "voice": Description(
            en="Qwen-Audio Flash voice ID", zh="Qwen-Audio Flash 音色 ID"
        ),
        "language_hint": Description(
            en="Language hint sent to Qwen-Audio Flash",
            zh="发送给 Qwen-Audio Flash 的语言提示",
        ),
        "instruction": Description(
            en="Optional natural-language speaking instruction",
            zh="可选的自然语言语音指令",
        ),
        "sample_rate": Description(en="Audio sample rate", zh="音频采样率"),
        "rate": Description(en="Speaking rate", zh="语速"),
        "timeout": Description(en="Request timeout in seconds", zh="请求超时秒数"),
    }


class TTSConfig(I18nMixin):
    """Configuration for Text-to-Speech."""

    tts_model: Literal["qwen_tts"] = Field(..., alias="tts_model")
    qwen_tts: Optional[QwenTTSConfig] = Field(None, alias="qwen_tts")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "tts_model": Description(
            en="Text-to-speech model to use", zh="要使用的文本转语音模型"
        ),
        "qwen_tts": Description(
            en="Configuration for Qwen-Audio Flash TTS",
            zh="Qwen-Audio Flash TTS 配置",
        ),
    }

    @model_validator(mode="after")
    def check_tts_config(cls, values: "TTSConfig", info: ValidationInfo):
        tts_model = values.tts_model
        if tts_model == "qwen_tts" and values.qwen_tts is not None:
            values.qwen_tts.model_validate(values.qwen_tts.model_dump())
        return values
