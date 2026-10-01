"""
This module contains the pydantic model for the configurations of
different types of agents. (simplified - basic_memory_agent only)
"""

from pydantic import BaseModel, Field
from typing import Dict, ClassVar, Optional, Literal, List
from .i18n import I18nMixin, Description
from .stateless_llm import StatelessLLMConfigs

# ======== Configuration for Basic Memory Agent ========


class BasicMemoryAgentConfig(I18nMixin, BaseModel):
    """Configuration for the basic memory agent."""

    llm_provider: Literal["deepseek_llm"] = Field(..., alias="llm_provider")
    long_term_summary_model: str = Field(
        "deepseek-v4-pro", alias="long_term_summary_model"
    )
    long_term_reconcile_model: str = Field(
        "deepseek-v4-pro", alias="long_term_reconcile_model"
    )
    rolling_summary_model: str = Field(
        "deepseek-v4-pro", alias="rolling_summary_model"
    )
    persona_profile_model: str = Field(
        "deepseek-v4-flash", alias="persona_profile_model"
    )
    persona_profile_source_max_bytes: int = Field(
        50_000, alias="persona_profile_source_max_bytes", ge=1_000
    )

    faster_first_response: Optional[bool] = Field(True, alias="faster_first_response")
    segment_method: Literal["regex", "pysbd"] = Field("pysbd", alias="segment_method")
    max_history_turns: int = Field(
        8,
        alias="max_history_turns",
        ge=1,
        le=100,
    )
    use_mcpp: Optional[bool] = Field(False, alias="use_mcpp")
    mcp_enabled_servers: Optional[List[str]] = Field([], alias="mcp_enabled_servers")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "llm_provider": Description(
            en="LLM provider to use for this agent",
            zh="Basic Memory Agent 智能体使用的大语言模型选项",
        ),
        "long_term_summary_model": Description(
            en="DeepSeek model used for long-term memory, short-term relationship, and relationship scoring",
            zh="用于长期记忆、短期关系总结和当前关系评分的 DeepSeek 模型",
        ),
        "long_term_reconcile_model": Description(
            en="DeepSeek model used to reconcile new and existing long-term memories",
            zh="用于长期记忆去重、状态更新与冲突归类的 DeepSeek 模型",
        ),
        "rolling_summary_model": Description(
            en="DeepSeek model used for per-chat rolling summaries",
            zh="用于单次聊天滚动总结的 DeepSeek 模型",
        ),
        "persona_profile_model": Description(
            en="DeepSeek model used for persona profile generation",
            zh="用于人物侧写生成的 DeepSeek 模型",
        ),
        "persona_profile_source_max_bytes": Description(
            en="Maximum UTF-8 bytes of recent non-debug turns used for persona profiles",
            zh="人物侧写使用的最近非调试完整对话的 UTF-8 字节上限",
        ),
        "faster_first_response": Description(
            en="Whether to respond as soon as encountering a comma in the first sentence to reduce latency (default: True)",
            zh="是否在第一句回应时遇上逗号就直接生成音频以减少首句延迟（默认：True）",
        ),
        "segment_method": Description(
            en="Method for segmenting sentences: 'regex' or 'pysbd' (default: 'pysbd')",
            zh="分割句子的方法：'regex' 或 'pysbd'（默认：'pysbd'）",
        ),
        "max_history_turns": Description(
            en="Maximum completed conversation turns included in each LLM request (default: 8)",
            zh="每次大模型请求携带的最近完整对话轮数（默认：8）",
        ),
        "use_mcpp": Description(
            en="Enable regex-triggered tool routing: when a search trigger phrase is detected, the agent calls the MCP tool directly and injects results into the user prompt, skipping the model's autonomous tool-calling loop (default: False)",
            zh="启用正则工具路由：检测到搜索触发词时代码直接调用 MCP 工具，并把结果拼进用户提示词，跳过模型自主工具调用（默认：False）",
        ),
        "mcp_enabled_servers": Description(
            en="List of MCP servers to enable for the agent",
            zh="为智能体启用 MCP 服务器列表",
        ),
    }


class AgentSettings(I18nMixin, BaseModel):
    """Settings for different types of agents."""

    basic_memory_agent: Optional[BasicMemoryAgentConfig] = Field(
        None, alias="basic_memory_agent"
    )

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "basic_memory_agent": Description(
            en="Configuration for basic memory agent", zh="基础记忆代理配置"
        ),
    }


class AgentConfig(I18nMixin, BaseModel):
    """This class contains all of the configurations related to agent."""

    conversation_agent_choice: Literal["basic_memory_agent"] = Field(
        ..., alias="conversation_agent_choice"
    )
    agent_settings: AgentSettings = Field(..., alias="agent_settings")
    llm_configs: StatelessLLMConfigs = Field(..., alias="llm_configs")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "conversation_agent_choice": Description(
            en="Type of conversation agent to use", zh="要使用的对话代理类型"
        ),
        "agent_settings": Description(
            en="Settings for different agent types", zh="不同代理类型的设置"
        ),
        "llm_configs": Description(
            en="Pool of LLM provider configurations", zh="语言模型提供者配置池"
        ),
        "faster_first_response": Description(
            en="Whether to respond as soon as encountering a comma in the first sentence to reduce latency (default: True)",
            zh="是否在第一句回应时遇上逗号就直接生成音频以减少首句延迟（默认：True）",
        ),
        "segment_method": Description(
            en="Method for segmenting sentences: 'regex' or 'pysbd' (default: 'pysbd')",
            zh="分割句子的方法：'regex' 或 'pysbd'（默认：'pysbd'）",
        ),
    }
