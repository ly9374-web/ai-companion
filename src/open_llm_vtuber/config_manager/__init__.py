"""
Configuration management package for Open LLM VTuber (simplified).
"""
# Import main configuration classes
from .main import Config
from .system import SystemConfig
from .character import CharacterConfig
from .live import LiveConfig
from .stateless_llm import (
    OpenAICompatibleConfig,
    DeepseekConfig,
    GrokConfig,
)
from .asr import (
    ASRConfig,
    SherpaOnnxASRConfig,
)
from .tts import (
    TTSConfig,
    QwenTTSConfig,
)
from .vad import (
    VADConfig,
    SileroVADConfig,
)
from .tts_preprocessor import TTSPreprocessorConfig, TranslatorConfig
from .i18n import I18nMixin, Description, MultiLingualString
from .agent import (
    AgentConfig,
    AgentSettings,
    StatelessLLMConfigs,
    BasicMemoryAgentConfig,
)
from .utils import (
    read_yaml,
    validate_config,
    save_config,
    scan_config_alts_directory,
    scan_bg_directory,
)

__all__ = [
    "Config",
    "SystemConfig",
    "CharacterConfig",
    "LiveConfig",
    "OpenAICompatibleConfig",
    "DeepseekConfig",
    "GrokConfig",
    "AgentConfig",
    "AgentSettings",
    "StatelessLLMConfigs",
    "BasicMemoryAgentConfig",
    "ASRConfig",
    "SherpaOnnxASRConfig",
    "TTSConfig",
    "QwenTTSConfig",
    "VADConfig",
    "SileroVADConfig",
    "TTSPreprocessorConfig",
    "TranslatorConfig",
    "I18nMixin",
    "Description",
    "MultiLingualString",
    "read_yaml",
    "validate_config",
    "save_config",
    "scan_config_alts_directory",
    "scan_bg_directory",
]
