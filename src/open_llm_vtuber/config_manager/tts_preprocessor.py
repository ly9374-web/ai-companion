# config_manager/tts_preprocessor.py (simplified - no translation)
from typing import Dict, ClassVar
from pydantic import Field
from .i18n import I18nMixin, Description


class TranslatorConfig(I18nMixin):
    """Minimal stub — translation is removed in the simplified version."""

    translate_audio: bool = Field(False, alias="translate_audio")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "translate_audio": Description(
            en="Enable audio translation (disabled in simplified version)",
            zh="启用音频翻译（ai陪伴中已禁用）",
        ),
    }


class TTSPreprocessorConfig(I18nMixin):
    """Configuration for TTS preprocessor."""

    remove_special_char: bool = Field(..., alias="remove_special_char")
    ignore_brackets: bool = Field(default=True, alias="ignore_brackets")
    ignore_parentheses: bool = Field(default=True, alias="ignore_parentheses")
    ignore_asterisks: bool = Field(default=True, alias="ignore_asterisks")
    ignore_angle_brackets: bool = Field(default=True, alias="ignore_angle_brackets")
    translator_config: TranslatorConfig = Field(
        default_factory=TranslatorConfig, alias="translator_config"
    )

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "remove_special_char": Description(
            en="Remove special characters from the input text",
            zh="从输入文本中删除特殊字符",
        ),
        "translator_config": Description(
            en="Translation configuration (disabled in simplified version)",
            zh="翻译配置（ai陪伴中已禁用）",
        ),
    }
