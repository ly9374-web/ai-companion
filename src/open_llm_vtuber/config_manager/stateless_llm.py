# config_manager/llm.py
from typing import ClassVar, Literal
from pydantic import BaseModel, Field
from .i18n import I18nMixin, Description


class StatelessLLMBaseConfig(I18nMixin):
    """Base configuration for StatelessLLM."""

    interrupt_method: Literal["system", "user"] = Field(
        "user", alias="interrupt_method"
    )
    DESCRIPTIONS: ClassVar[dict[str, Description]] = {
        "interrupt_method": Description(
            en="""The method to use for prompting the interruption signal.
            If the provider supports inserting system prompt anywhere in the chat memory, use "system".
            Otherwise, use "user". You don't need to change this setting.""",
            zh="""用于表示中断信号的方法(提示词模式)。如果LLM支持在聊天记忆中的任何位置插入系统提示词，请使用"system"。
            否则，请使用"user"。您不需要更改此设置。""",
        ),
    }


class OpenAICompatibleConfig(StatelessLLMBaseConfig):
    """Configuration for OpenAI-compatible LLM providers."""

    base_url: str = Field(..., alias="base_url")
    llm_api_key: str = Field(..., alias="llm_api_key")
    model: str = Field(..., alias="model")
    organization_id: str | None = Field(None, alias="organization_id")
    project_id: str | None = Field(None, alias="project_id")
    temperature: float = Field(1.0, alias="temperature")

    _OPENAI_COMPATIBLE_DESCRIPTIONS: ClassVar[dict[str, Description]] = {
        "base_url": Description(en="Base URL for the API endpoint", zh="API的URL端点"),
        "llm_api_key": Description(en="API key for authentication", zh="API 认证密钥"),
        "organization_id": Description(
            en="Organization ID for the API (Optional)", zh="组织 ID (可选)"
        ),
        "project_id": Description(
            en="Project ID for the API (Optional)", zh="项目 ID (可选)"
        ),
        "model": Description(en="Name of the LLM model to use", zh="LLM 模型名称"),
        "temperature": Description(
            en="What sampling temperature to use, between 0 and 2.",
            zh="使用的采样温度，介于 0 和 2 之间。",
        ),
    }

    DESCRIPTIONS: ClassVar[dict[str, Description]] = {
        **StatelessLLMBaseConfig.DESCRIPTIONS,
        **_OPENAI_COMPATIBLE_DESCRIPTIONS,
    }


class DeepseekConfig(OpenAICompatibleConfig):
    """Configuration for Deepseek API."""

    base_url: str = Field("https://api.deepseek.com/v1", alias="base_url")


class GrokConfig(OpenAICompatibleConfig):
    """Configuration for xAI's OpenAI-compatible Grok API."""

    base_url: str = Field("https://api.x.ai/v1", alias="base_url")


class StatelessLLMConfigs(I18nMixin, BaseModel):
    """Pool of LLM provider configurations."""

    deepseek_llm: DeepseekConfig | None = Field(None, alias="deepseek_llm")
    grok_llm: GrokConfig | None = Field(None, alias="grok_llm")

    DESCRIPTIONS: ClassVar[dict[str, Description]] = {
        "deepseek_llm": Description(
            en="Configuration for Deepseek API", zh="Deepseek API 配置"
        ),
        "grok_llm": Description(
            en="Configuration for xAI Grok API", zh="xAI Grok API 配置"
        ),
    }
