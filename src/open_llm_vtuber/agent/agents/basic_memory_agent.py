from typing import (
    AsyncIterator,
    List,
    Dict,
    Any,
    Callable,
    Literal,
    Union,
    Optional,
)
from pathlib import Path
import re
import datetime
from loguru import logger
from .agent_interface import AgentInterface
from ..output_types import SentenceOutput, DisplayText
from ..stateless_llm.stateless_llm_interface import StatelessLLMInterface
from ..stateless_llm.openai_compatible_llm import AsyncLLM as OpenAICompatibleAsyncLLM
from ...chat_history_manager import get_history
from ..transformers import (
    sentence_divider,
    actions_extractor,
    tts_filter,
    display_processor,
)
from ...config_manager import TTSPreprocessorConfig
from ..input_types import BatchInput, TextSource
from prompts import prompt_builder
from ...mcpp.tool_manager import ToolManager
from ...mcpp.json_detector import StreamJSONDetector
from ...mcpp.types import ToolCallObject
from ...mcpp.tool_executor import ToolExecutor


class BasicMemoryAgent(AgentInterface):
    """Agent with basic chat memory and tool calling support."""

    _system: str = prompt_builder.load_system_prompt("default_system")
    _WEB_SEARCH_TRIGGER_PHRASES = (
        "search it from the website",
        "帮我从网上搜索",
    )
    # 正则触发的搜索关键词；命中后用 _extract_search_query 提取查询词
    _WEB_SEARCH_TRIGGER_PATTERN = re.compile(
        r"(?:帮我)?从网上搜索|上网查|搜一下|search it from the website",
        re.IGNORECASE,
    )
    _WEB_SEARCH_TOOL_NAMES = frozenset(("search", "fetch_content"))
    _MCP_ALL_TOOLS_PATTERN = re.compile(
        r"(?:使用|应用|载入|加载|利用|启用|打开|调用|接入)(?:一下|这个)?\s*MCP(?![A-Za-z0-9_])"
        r"|(?:use|apply|load|enable|activate|invoke|call|turn\s+on)\s+(?:the\s+)?MCP\b",
        re.IGNORECASE,
    )
    _MCP_NEGATED_CLAUSE_PATTERN = re.compile(
        r"(?:不要|别|不用|无需|不需要|不使用|不启用|不加载|禁止|别再|do\s+not|don't|dont|no\s+need\s+to|without|disable)"
        r"[^，,。.；;！？!?\n]{0,40}",
        re.IGNORECASE,
    )
    _MCP_IMAGE_ACTION_PATTERN = re.compile(
        r"生成|制作|创建|创作|设计|绘制|画|做|渲染"
        r"|generate|create|make|draw|design|render|paint|illustrate|produce",
        re.IGNORECASE,
    )
    _MCP_IMAGE_TARGET_PATTERN = re.compile(
        r"图片|图像|插画|海报|封面|头像|壁纸|表情包|照片|画面|PNG|JPE?G|WEBP"
        r"|images?|pictures?|illustrations?|posters?|covers?|avatars?|wallpapers?|memes?|photos?|artworks?",
        re.IGNORECASE,
    )
    _MCP_VIDEO_ACTION_PATTERN = re.compile(
        r"生成|制作|创建|创作|做|转成|转换成|变成"
        r"|generate|create|make|produce|turn\s+into|convert\s+(?:it\s+)?to",
        re.IGNORECASE,
    )
    _MCP_VIDEO_TARGET_PATTERN = re.compile(
        r"视频|动画|短片|影片|影像|视频片段"
        r"|videos?|animations?|clips?|movies?|films?",
        re.IGNORECASE,
    )
    _MCP_IMAGE_TO_VIDEO_PATTERN = re.compile(
        r"图生视频|图片转视频|图像转视频|(?:让|把).{0,20}(?:图片|图像|这张图|上一张图).{0,20}(?:动起来|做成视频|变成视频)"
        r"|基于.{0,12}(?:图片|图像|上一张图).{0,12}(?:生成|制作).{0,8}视频"
        r"|image\s*[- ]?to\s*[- ]?video|animate\s+(?:this|the|last|previous)\s+(?:image|picture)"
        r"|make\s+(?:this|the|last|previous)\s+(?:image|picture)\s+move"
        r"|use\s+(?:this|the|last|previous)\s+(?:image|picture)\s+as\s+(?:the\s+)?first\s+frame",
        re.IGNORECASE,
    )
    _MCP_VIDEO_QUERY_PATTERN = re.compile(
        r"(?:查询|查看|检查|查一下|看看).{0,20}(?:视频|生成任务).{0,20}(?:进度|状态|结果|完成)"
        r"|(?:视频|生成任务).{0,20}(?:好了吗|完成了吗|进度|状态|结果)"
        r"|(?:check|query|show|get).{0,20}(?:video|generation\s+task).{0,20}(?:status|progress|result)"
        r"|(?:is|has).{0,12}(?:the\s+)?video.{0,12}(?:ready|done|finished|completed)",
        re.IGNORECASE,
    )
    _MCP_TTS_PATTERN = re.compile(
        r"朗读|念出来|读出来|配音|文字转语音|文本转语音|转成语音|生成语音|生成音频|合成语音"
        r"|read\s+(?:it\s+)?aloud|text\s*[- ]?to\s*[- ]?speech|TTS\b|voice\s*over|voiceover"
        r"|generate\s+(?:an?\s+)?audio|synthesi[sz]e\s+speech|turn.{0,20}into\s+(?:speech|audio)",
        re.IGNORECASE,
    )
    _MCP_VOICE_LIST_PATTERN = re.compile(
        r"(?:有哪些|列出|查看|查询|显示|推荐|选择|选一个).{0,16}(?:声音|音色|嗓音|语音)"
        r"|(?:声音|音色|嗓音|语音).{0,16}(?:有哪些|列表|可用|选择|推荐)"
        r"|(?:list|show|find|choose|recommend).{0,16}(?:available\s+)?voices?"
        r"|(?:available|supported)\s+voices?",
        re.IGNORECASE,
    )
    _MCP_VOICE_CLONE_ACTION_PATTERN = re.compile(
        r"克隆|复刻|复制|模仿|仿制|clone|copy|imitate|replicate",
        re.IGNORECASE,
    )
    _MCP_VOICE_TARGET_PATTERN = re.compile(
        r"声音|音色|嗓音|声线|女声|男声|语音|录音|\bvoices?\b|\btimbre\b|\brecording\b",
        re.IGNORECASE,
    )
    _MCP_VOICE_DESIGN_PATTERN = re.compile(
        r"(?:设计|创造|定制).{0,24}(?:声音|音色|嗓音|声线|女声|男声)"
        r"|(?:创建|生成|制作).{0,16}(?:新|全新|自定义|独特|专属).{0,8}(?:声音|音色|嗓音|声线|女声|男声)"
        r"|(?:design|craft|customi[sz]e).{0,24}\bvoices?\b"
        r"|(?:create|generate|make).{0,20}(?:new|custom|unique|original).{0,8}\bvoices?\b",
        re.IGNORECASE,
    )
    _MCP_PLAY_AUDIO_ACTION_PATTERN = re.compile(
        r"播放|放一下|播一下|听听|play|listen\s+to",
        re.IGNORECASE,
    )
    _MCP_AUDIO_TARGET_PATTERN = re.compile(
        r"音频|语音|录音|声音|歌曲|MP3|WAV|audio|recording|sound|song|MP3|WAV",
        re.IGNORECASE,
    )
    _MAX_TOOL_ROUNDS = 8
    _WEB_SEARCH_RESULT_MAX_CHARS = 1500
    _CONTEXT_INJECTION_KEYS = (
        "short_term_relationship_context",
    )

    def __init__(
        self,
        llm: StatelessLLMInterface,
        system: str,
        live2d_model,
        grok_llm: Optional[StatelessLLMInterface] = None,
        summary_llm: Optional[StatelessLLMInterface] = None,
        reconcile_llm: Optional[StatelessLLMInterface] = None,
        rolling_summary_llm: Optional[StatelessLLMInterface] = None,
        persona_profile_llm: Optional[StatelessLLMInterface] = None,
        persona_profile_source_max_bytes: int = 50_000,
        tts_preprocessor_config: TTSPreprocessorConfig = None,
        faster_first_response: bool = True,
        segment_method: str = "pysbd",
        max_history_turns: int = 8,
        use_mcpp: bool = False,
        interrupt_method: Literal["system", "user"] = "user",
        tool_prompts: Dict[str, str] = None,
        tool_manager: Optional[ToolManager] = None,
        tool_executor: Optional[ToolExecutor] = None,
        mcp_prompt_string: str = "",
    ):
        """Initialize agent with LLM and configuration."""
        super().__init__()
        self._memory = []
        self._live2d_model = live2d_model
        self._tts_preprocessor_config = tts_preprocessor_config
        self._faster_first_response = faster_first_response
        self._segment_method = segment_method
        self.set_max_history_turns(max_history_turns)
        self._use_mcpp = use_mcpp
        self.mobile_image_only = False
        self.interrupt_method = interrupt_method
        self._tool_prompts = tool_prompts or {}
        self._interrupt_handled = False
        self.prompt_mode_flag = False

        self._tool_manager = tool_manager
        self._tool_executor = tool_executor
        self._mcp_prompt_string = mcp_prompt_string
        self._json_detector = StreamJSONDetector()
        self._turn_sequence = 0

        self._formatted_tools_openai = []
        if self._tool_manager:
            self._formatted_tools_openai = self._tool_manager.get_formatted_tools(
                "OpenAI"
            )
            logger.debug(
                f"Agent received pre-formatted tools - OpenAI: {len(self._formatted_tools_openai)}"
            )
        else:
            logger.debug(
                "ToolManager not provided, agent will not have pre-formatted tools."
            )

        self._deepseek_llm = llm
        self._grok_llm = grok_llm
        self._grok_enabled = False
        self._set_llm(llm)
        self._summary_llm = summary_llm or llm
        self._reconcile_llm = reconcile_llm or self._summary_llm
        self._rolling_summary_llm = rolling_summary_llm or self._summary_llm
        self._persona_profile_llm = persona_profile_llm or self._summary_llm
        self._persona_profile_source_max_bytes = persona_profile_source_max_bytes
        self.set_system(system if system else self._system)

        if self._use_mcpp and not all(
            [
                self._tool_manager,
                self._tool_executor,
                self._json_detector,
            ]
        ):
            logger.warning(
                "use_mcpp is True, but some MCP components are missing in the agent. Tool calling might not work as expected."
            )
        elif not self._use_mcpp and any(
            [
                self._tool_manager,
                self._tool_executor,
                self._json_detector,
            ]
        ):
            logger.warning(
                "use_mcpp is False, but some MCP components were passed to the agent."
            )

        logger.info("BasicMemoryAgent initialized.")

    def _set_llm(self, llm: StatelessLLMInterface):
        """Set the LLM for chat completion."""
        self._llm = llm
        self.chat = self._chat_function_factory()

    def set_grok_enabled(self, enabled: bool) -> None:
        """Route interactive chat only; summary clients always remain DeepSeek."""
        if enabled and self._grok_llm is None:
            raise ValueError("Grok is not configured")
        self._grok_enabled = enabled
        self._set_llm(self._grok_llm if enabled else self._deepseek_llm)
        logger.info("Interactive chat model switched to {}", "Grok" if enabled else "DeepSeek")

    def refresh_mcp_tools(
        self,
        tool_manager: Any = None,
        tool_executor: Any = None,
        mcp_prompt_string: str | None = None,
    ) -> None:
        """Swap MCP components after runtime tool re-discovery (e.g. browser API key)."""
        if tool_manager is not None:
            self._tool_manager = tool_manager
        if tool_executor is not None:
            self._tool_executor = tool_executor
        if mcp_prompt_string is not None:
            self._mcp_prompt_string = mcp_prompt_string
        if self._tool_manager:
            self._formatted_tools_openai = self._tool_manager.get_formatted_tools(
                "OpenAI"
            )
        else:
            self._formatted_tools_openai = []
        logger.info(
            f"Agent MCP tools refreshed - OpenAI: {len(self._formatted_tools_openai)}"
        )

    def set_deepseek_model(self, model: str) -> None:
        """Switch the interactive DeepSeek chat model (e.g. pro/flash) at runtime."""
        if self._deepseek_llm is None:
            raise ValueError("DeepSeek is not configured")
        if not hasattr(self._deepseek_llm, "set_model"):
            raise ValueError("DeepSeek LLM does not support runtime model switching")
        self._deepseek_llm.set_model(model)
        logger.info("Interactive DeepSeek model switched to {}", model)

    def set_system(self, system: str):
        """Set the system prompt."""
        logger.debug(f"Memory Agent: Setting system prompt: '''{system}'''")

        if self.interrupt_method == "user":
            system = prompt_builder.join_prompt_sections(
                [
                    system,
                    prompt_builder.load_system_prompt("interrupt_instruction"),
                ]
            )

        self._system = system

    def set_max_history_turns(self, max_history_turns: int) -> None:
        """Set how many user and assistant turns are included in LLM context."""
        if isinstance(max_history_turns, bool) or not isinstance(
            max_history_turns, int
        ):
            raise ValueError("max_history_turns must be an integer")
        if not 1 <= max_history_turns <= 100:
            raise ValueError("max_history_turns must be between 1 and 100")
        self._max_history_turns = max_history_turns

    def _get_context_memory(
        self,
        superseded_context_keys: set[str] | None = None,
        include_debug: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return history after both configured role counts are reached.

        The complete in-process memory remains available for history persistence
        and summaries. Only the messages copied into the next LLM request are
        truncated. Hidden context snapshots are rendered only at their latest
        retained position, and a fresh snapshot on the current request replaces
        the historical snapshot of the same type.
        """
        eligible_memory = [
            message
            for message in self._memory
            if include_debug or not message.get("debug_mode")
        ]
        max_history_turns = getattr(self, "_max_history_turns", 8)
        user_turns = 0
        assistant_turns = 0
        start_index = 0

        for index in range(len(eligible_memory) - 1, -1, -1):
            role = eligible_memory[index].get("role")
            if role == "user":
                user_turns += 1
            elif role == "assistant":
                assistant_turns += 1
            else:
                continue

            start_index = index
            if (
                user_turns >= max_history_turns
                and assistant_turns >= max_history_turns
            ):
                break

        if (
            user_turns < max_history_turns
            or assistant_turns < max_history_turns
        ):
            start_index = 0

        context_memory = [
            message.copy() for message in eligible_memory[start_index:]
        ]
        superseded_context_keys = superseded_context_keys or set()
        latest_context_positions: Dict[str, int] = {}

        for index, message in enumerate(context_memory):
            message.pop("debug_mode", None)
            context_injections = message.get("context_injections")
            if not isinstance(context_injections, dict):
                continue
            for key in self._CONTEXT_INJECTION_KEYS:
                value = context_injections.get(key)
                if (
                    key not in superseded_context_keys
                    and isinstance(value, str)
                    and value.strip()
                ):
                    latest_context_positions[key] = index

        rendered_memory: List[Dict[str, Any]] = []
        for index, message in enumerate(context_memory):
            rendered_message = message.copy()
            context_injections = rendered_message.pop("context_injections", None)
            if rendered_message.get("role") == "user" and isinstance(
                context_injections, dict
            ):
                active_contexts = {
                    key: context_injections[key]
                    for key in self._CONTEXT_INJECTION_KEYS
                    if latest_context_positions.get(key) == index
                }
                if active_contexts:
                    rendered_message["content"] = prompt_builder.build_user_request(
                        text_prompt=str(rendered_message.get("content", "")),
                        **active_contexts,
                    )
            rendered_memory.append(rendered_message)

        return rendered_memory

    def _add_message(
        self,
        message: Union[str, List[Dict[str, Any]]],
        role: str,
        display_text: DisplayText | None = None,
        skip_memory: bool = False,
        context_injections: Dict[str, str] | None = None,
        debug_mode: bool = False,
    ):
        """Add message to memory."""
        if skip_memory:
            return

        text_content = ""
        if isinstance(message, list):
            for item in message:
                if item.get("type") == "text":
                    text_content += item["text"] + " "
            text_content = text_content.strip()
        elif isinstance(message, str):
            text_content = message
        else:
            logger.warning(
                f"_add_message received unexpected message type: {type(message)}"
            )
            text_content = str(message)

        if not text_content and role == "assistant":
            return

        message_data = {
            "role": role,
            "content": text_content,
        }

        normalized_context_injections = {
            key: value
            for key, value in (context_injections or {}).items()
            if (
                key in self._CONTEXT_INJECTION_KEYS
                and isinstance(value, str)
                and value.strip()
            )
        }
        if normalized_context_injections:
            message_data["context_injections"] = normalized_context_injections
        if debug_mode:
            message_data["debug_mode"] = True

        if display_text:
            if display_text.name:
                message_data["name"] = display_text.name
            if display_text.avatar:
                message_data["avatar"] = display_text.avatar

        if (
            self._memory
            and self._memory[-1]["role"] == role
            and self._memory[-1]["content"] == text_content
            and self._memory[-1].get("context_injections")
            == message_data.get("context_injections")
            and self._memory[-1].get("debug_mode")
            == message_data.get("debug_mode")
        ):
            return

        self._memory.append(message_data)

    def set_memory_from_history(
        self,
        conf_uid: str,
        history_uid: str,
        history_root: str | Path = "chat_history",
    ) -> None:
        """Load memory from chat history."""
        messages = get_history(conf_uid, history_uid, history_root)
        self.set_memory_from_messages(messages)

    def set_memory_from_messages(self, messages: List[Dict[str, Any]]) -> None:
        """Load model memory from persisted or model-only history messages."""
        self._memory = []
        for msg in messages:
            role = {
                "human": "user",
                "ai": "assistant",
                "system": "system",
            }.get(msg["role"])
            if role is None:
                logger.warning(f"Skipping history message with invalid role: {msg}")
                continue
            content = msg["content"]
            if isinstance(content, str) and content:
                message_data = {
                    "role": role,
                    "content": content,
                }
                context_injections = msg.get("context_injections")
                if isinstance(context_injections, dict):
                    normalized_context_injections = {
                        key: value
                        for key, value in context_injections.items()
                        if (
                            key in self._CONTEXT_INJECTION_KEYS
                            and isinstance(value, str)
                            and value.strip()
                        )
                    }
                    if normalized_context_injections:
                        message_data["context_injections"] = (
                            normalized_context_injections
                        )
                if msg.get("debug_mode"):
                    message_data["debug_mode"] = True
                self._memory.append(message_data)
            else:
                logger.warning(f"Skipping invalid message from history: {msg}")
        logger.info(f"Loaded {len(self._memory)} messages from history.")

    def handle_interrupt(
        self,
        heard_response: str,
        debug_mode: bool = False,
    ) -> None:
        """Handle user interruption."""
        if self._interrupt_handled:
            return

        self._interrupt_handled = True

        if self._memory and self._memory[-1]["role"] == "assistant":
            if not self._memory[-1]["content"].endswith("..."):
                self._memory[-1]["content"] = heard_response + "..."
            else:
                self._memory[-1]["content"] = heard_response + "..."
            if debug_mode:
                self._memory[-1]["debug_mode"] = True
        else:
            if heard_response:
                self._memory.append(
                    {
                        "role": "assistant",
                        "content": heard_response + "...",
                        **({"debug_mode": True} if debug_mode else {}),
                    }
                )

        interrupt_role = "system" if self.interrupt_method == "system" else "user"
        self._memory.append(
            {
                "role": interrupt_role,
                "content": prompt_builder.load_runtime_prompt(
                    "interrupted_by_user"
                ),
                **({"debug_mode": True} if debug_mode else {}),
            }
        )
        logger.info(f"Handled interrupt with role '{interrupt_role}'.")

    def _to_text_prompt(self, input_data: BatchInput) -> str:
        """Format input data to text prompt."""
        message_parts = []

        for text_data in input_data.texts:
            if text_data.source == TextSource.INPUT:
                message_parts.append(text_data.content)
            elif text_data.source == TextSource.CLIPBOARD:
                message_parts.append(
                    prompt_builder.build_clipboard_content(text_data.content)
                )

        if input_data.images:
            message_parts.append(prompt_builder.load_runtime_prompt("image_notice"))

        return prompt_builder.join_prompt_lines(message_parts)

    @classmethod
    def _web_search_requested(cls, input_data: BatchInput) -> bool:
        """Enable web tools only when a search trigger phrase is detected."""
        return any(
            cls._WEB_SEARCH_TRIGGER_PATTERN.search(text_data.content)
            for text_data in input_data.texts
            if text_data.source == TextSource.INPUT
        )

    @classmethod
    def _extract_search_query(cls, input_data: BatchInput) -> str:
        """从触发短语之后到第一个句号之间提取搜索关键词。

        无句号时取到句末；提取结果为空则返回空串（调用方据此跳过搜索）。
        """
        for text_data in input_data.texts:
            if text_data.source != TextSource.INPUT:
                continue
            match = cls._WEB_SEARCH_TRIGGER_PATTERN.search(text_data.content)
            if not match:
                continue
            after = text_data.content[match.end():]
            stop = re.search(r"[。.]", after)
            query = after[: stop.start()] if stop else after
            query = query.strip(" ，,。.")
            if query:
                return query
        return ""

    @staticmethod
    def _raw_user_text(input_data: BatchInput) -> str:
        """Return only this turn's user-authored text for deterministic routing."""
        return "\n".join(
            text_data.content
            for text_data in input_data.texts
            if text_data.source == TextSource.INPUT
        )

    @classmethod
    def _matches_action_target(
        cls,
        text: str,
        action_pattern: re.Pattern,
        target_pattern: re.Pattern,
    ) -> bool:
        """Match an intent when action and target both occur in the turn."""
        return bool(
            action_pattern.search(text)
            and target_pattern.search(text)
        )

    def _select_mcp_tools_for_turn(
        self, input_data: BatchInput
    ) -> List[Dict[str, Any]]:
        """Expose only MCP tools explicitly suggested by the raw user request."""
        text = self._raw_user_text(input_data)
        if not text:
            return []
        # Remove short negated clauses before matching, so "不要图片，改成视频"
        # disables only the first request and still allows the second one.
        text = self._MCP_NEGATED_CLAUSE_PATTERN.sub("", text)
        if not text.strip():
            return []

        available_tools = list(self._formatted_tools_openai or [])
        available_names = {
            tool.get("function", {}).get("name")
            for tool in available_tools
            if tool.get("function", {}).get("name")
        }

        if self.mobile_image_only:
            image_requested = self._matches_action_target(
                text, self._MCP_IMAGE_ACTION_PATTERN, self._MCP_IMAGE_TARGET_PATTERN
            ) and not self._MCP_IMAGE_TO_VIDEO_PATTERN.search(text)
            return [
                tool for tool in available_tools
                if image_requested
                and tool.get("function", {}).get("name") == "text_to_image"
            ]

        all_tools_requested = bool(self._MCP_ALL_TOOLS_PATTERN.search(text))
        if all_tools_requested:
            logger.info(
                "MCP route selected all available tools: {}",
                sorted(available_names),
            )
            return available_tools

        selected_names = set()

        image_to_video_requested = bool(self._MCP_IMAGE_TO_VIDEO_PATTERN.search(text))
        if not image_to_video_requested and self._matches_action_target(
            text,
            self._MCP_IMAGE_ACTION_PATTERN,
            self._MCP_IMAGE_TARGET_PATTERN,
        ):
            selected_names.add("text_to_image")

        video_generation_requested = self._matches_action_target(
            text,
            self._MCP_VIDEO_ACTION_PATTERN,
            self._MCP_VIDEO_TARGET_PATTERN,
        )
        if image_to_video_requested or video_generation_requested:
            selected_names.update(("generate_video", "query_video_generation"))
        elif self._MCP_VIDEO_QUERY_PATTERN.search(text):
            selected_names.add("query_video_generation")

        if self._MCP_TTS_PATTERN.search(text):
            selected_names.add("text_to_audio")

        if self._MCP_VOICE_LIST_PATTERN.search(text):
            selected_names.add("list_voices")

        if self._matches_action_target(
            text,
            self._MCP_VOICE_CLONE_ACTION_PATTERN,
            self._MCP_VOICE_TARGET_PATTERN,
        ):
            selected_names.add("voice_clone")

        if self._MCP_VOICE_DESIGN_PATTERN.search(text):
            selected_names.add("voice_design")

        if self._matches_action_target(
            text,
            self._MCP_PLAY_AUDIO_ACTION_PATTERN,
            self._MCP_AUDIO_TARGET_PATTERN,
        ):
            selected_names.add("play_audio")

        routed_tools = [
            tool
            for tool in available_tools
            if tool.get("function", {}).get("name") in selected_names
        ]
        logger.info(
            "MCP route selected tools: {}",
            [tool.get("function", {}).get("name") for tool in routed_tools],
        )
        return routed_tools

    def _build_mcp_prompt_for_tools(
        self, tools: List[Dict[str, Any]]
    ) -> str:
        """Build the prompt-mode fallback using only this turn's routed tools."""
        if not tools or not self._tool_manager:
            return ""

        servers_info: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for tool in tools:
            tool_name = tool.get("function", {}).get("name")
            if not tool_name:
                continue
            tool_info = self._tool_manager.get_tool(tool_name)
            if not tool_info or not tool_info.related_server:
                continue
            server_tools = servers_info.setdefault(tool_info.related_server, {})
            server_tools[tool_name] = {
                "description": tool_info.description,
                "parameters": tool_info.input_schema.get("properties", {}),
                "required": tool_info.input_schema.get("required", []),
            }

        return prompt_builder.build_mcp_prompt(servers_info) if servers_info else ""

    def _get_web_search_tools(self) -> List[Dict[str, Any]]:
        """Return only DuckDuckGo search and webpage-fetch tools."""
        return [
            tool
            for tool in self._formatted_tools_openai
            if tool.get("function", {}).get("name")
            in self._WEB_SEARCH_TOOL_NAMES
        ]

    def _to_messages(
        self,
        input_data: BatchInput,
        web_search_context: str = "",
    ) -> List[Dict[str, Any]]:
        """Prepare messages for LLM API call."""
        user_content = []
        text_prompt = self._to_text_prompt(input_data)
        long_term_memory_context = ""
        short_term_relationship_context = ""
        tts_preference_change_context = ""
        rolling_summary_context = ""
        frontend_activity_context = ""
        if input_data.metadata:
            frontend_activity_context = input_data.metadata.get(
                "frontend_activity_context", ""
            )
            time_context_suffix = input_data.metadata.get("time_context_suffix", "")
            long_term_memory_context = input_data.metadata.get(
                "long_term_memory_context", ""
            )
            short_term_relationship_context = input_data.metadata.get(
                "short_term_relationship_context", ""
            )
            tts_preference_change_context = input_data.metadata.get(
                "tts_preference_change_context", ""
            )
            rolling_summary_context = input_data.metadata.get(
                "rolling_summary_context", ""
            )
        else:
            time_context_suffix = ""
        debug_mode = bool(
            input_data.metadata and input_data.metadata.get("debug_mode")
        )

        # RAG memory is request-scoped. Relationship snapshots retain their
        # existing history behavior, while retrieved memory never enters it.
        context_injections = {
            "short_term_relationship_context": short_term_relationship_context,
        }
        active_context_injections = {
            key: value
            for key, value in context_injections.items()
            if isinstance(value, str) and value.strip()
        }
        superseded_context_keys = set(active_context_injections)
        if debug_mode:
            superseded_context_keys.update(self._CONTEXT_INJECTION_KEYS)
        messages = self._get_context_memory(
            superseded_context_keys=superseded_context_keys,
            include_debug=debug_mode,
        )

        # The time line belongs to this request only; _add_message keeps
        # text_prompt without it for later model turns.
        request_text = prompt_builder.build_user_request(
            text_prompt=prompt_builder.join_prompt_lines(
                (text_prompt, time_context_suffix)
            ),
            frontend_activity_context=frontend_activity_context,
            tts_preference_change_context=tts_preference_change_context,
            rolling_summary_context=rolling_summary_context,
            long_term_memory_context=long_term_memory_context,
            short_term_relationship_context=short_term_relationship_context,
            has_images=bool(input_data.images),
            web_search_context=web_search_context,
        )

        capture_prompt = (
            input_data.metadata.get("mobile_prompt_log_capture")
            if input_data.metadata else None
        )
        if callable(capture_prompt):
            try:
                capture_prompt({
                    "request_text": request_text,
                    "text_prompt": text_prompt,
                    "frontend_activity_context": frontend_activity_context,
                    "tts_preference_change_context": tts_preference_change_context,
                    "rolling_summary_context": rolling_summary_context,
                    "long_term_memory_context": long_term_memory_context,
                    "short_term_relationship_context": short_term_relationship_context,
                    "web_search_context": web_search_context,
                    "has_images": bool(input_data.images),
                })
            except Exception:
                logger.exception("Failed to capture mobile prompt log")

        if request_text:
            user_content.append({"type": "text", "text": request_text})

        if input_data.images:
            image_added = False
            for img_data in input_data.images:
                if isinstance(img_data.data, str) and img_data.data.startswith(
                    "data:image"
                ):
                    user_content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": img_data.data, "detail": "auto"},
                        }
                    )
                    image_added = True
                else:
                    logger.error(
                        f"Invalid image data format: {type(img_data.data)}. Skipping image."
                    )

            if not image_added and not text_prompt:
                logger.warning(
                    "User input contains images but none could be processed."
                )

        if user_content:
            user_message = {"role": "user", "content": user_content}
            messages.append(user_message)

            skip_memory = False
            if input_data.metadata and input_data.metadata.get("skip_memory", False):
                skip_memory = True

            if not skip_memory:
                self._add_message(
                    text_prompt
                    if text_prompt
                    else prompt_builder.load_runtime_prompt(
                        "image_memory_placeholder"
                    ),
                    "user",
                    context_injections=active_context_injections,
                    debug_mode=debug_mode,
                )
        else:
            logger.warning("No content generated for user message.")

        return messages

    async def summarize_long_term_memory(
        self,
        turns: List[Dict[str, str]],
        character_system_prompt: str = "",
        browser_time: str = "",
    ) -> str:
        """Use the configured DeepSeek model for one memory-analysis request."""
        system_prompt = prompt_builder.load_summary_prompt("long_term_memory")

        summary_input = prompt_builder.build_long_term_memory_summary_input(
            recent_turns=turns,
            character_system_prompt=character_system_prompt,
            browser_time=browser_time,
        )
        messages = [
            {
                "role": "user",
                "content": summary_input,
            }
        ]

        chunks: List[str] = []
        async for event in self._summary_llm.chat_completion(messages, system_prompt):
            if isinstance(event, str):
                chunks.append(event)
            elif isinstance(event, dict) and event.get("type") == "text_delta":
                chunks.append(event.get("text", ""))
        raw_output = "".join(chunks).strip()
        return raw_output

    async def reconcile_long_term_memory(
        self,
        reconciliation_input: Dict[str, Any],
    ) -> str:
        """Classify new memories against retrieved existing memories."""
        system_prompt = prompt_builder.load_summary_prompt(
            "long_term_memory_reconcile"
        )
        user_prompt = prompt_builder.build_long_term_memory_reconcile_input(
            reconciliation_input
        )
        messages = [{"role": "user", "content": user_prompt}]

        chunks: List[str] = []
        async for event in self._reconcile_llm.chat_completion(
            messages, system_prompt
        ):
            if isinstance(event, str):
                chunks.append(event)
            elif isinstance(event, dict) and event.get("type") == "text_delta":
                chunks.append(event.get("text", ""))
        return "".join(chunks).strip()

    async def summarize_rolling_context(
        self,
        turns: List[Dict[str, str]],
        previous_summary: str = "",
    ) -> str:
        """Merge one new x-turn batch into this chat's rolling summary."""
        system_prompt = prompt_builder.load_summary_prompt("rolling_context")
        summary_input = prompt_builder.build_rolling_context_summary_input(
            turns,
            previous_summary,
        )
        messages = [{"role": "user", "content": summary_input}]

        chunks: List[str] = []
        async for event in self._rolling_summary_llm.chat_completion(
            messages, system_prompt
        ):
            if isinstance(event, str):
                chunks.append(event)
            elif isinstance(event, dict) and event.get("type") == "text_delta":
                chunks.append(event.get("text", ""))
        raw_output = "".join(chunks).strip()
        return raw_output

    async def summarize_short_term_relationship(
        self,
        recent_turns: List[Dict[str, str]],
        existing_short_term_relationship_file: str,
        browser_time: str = "",
    ) -> str:
        """Use DeepSeek Pro to rewrite the recent relationship state."""
        system_prompt = prompt_builder.load_summary_prompt(
            "short_term_relationship"
        )

        summary_input = prompt_builder.build_short_term_relationship_summary_input(
            recent_turns,
            existing_short_term_relationship_file,
            browser_time,
        )
        messages = [
            {
                "role": "user",
                "content": summary_input,
            }
        ]

        chunks: List[str] = []
        async for event in self._summary_llm.chat_completion(messages, system_prompt):
            if isinstance(event, str):
                chunks.append(event)
            elif isinstance(event, dict) and event.get("type") == "text_delta":
                chunks.append(event.get("text", ""))
        raw_output = "".join(chunks).strip()
        return raw_output

    async def score_current_relationship(
        self,
        recent_turns: List[Dict[str, str]],
    ) -> str:
        """Rate exactly one five-turn batch using the summary model."""
        system_prompt = prompt_builder.load_summary_prompt(
            "current_relationship_score"
        )
        summary_input = prompt_builder.build_current_relationship_score_input(
            recent_turns
        )
        chunks: List[str] = []
        async for event in self._summary_llm.chat_completion(
            [{"role": "user", "content": summary_input}], system_prompt
        ):
            if isinstance(event, str):
                chunks.append(event)
            elif isinstance(event, dict) and event.get("type") == "text_delta":
                chunks.append(event.get("text", ""))
        return "".join(chunks).strip()

    async def generate_persona_profile_section(
        self,
        prompt_name: str,
        user_prompt: str,
    ) -> str:
        """Generate one human-profile artifact with the DeepSeek summary model."""
        allowed_prompts = {
            "persona_profile_relationship",
            "persona_profile_memory",
            "persona_profile_consolidate",
            "persona_profile_persona",
            "persona_profile_thinslice",
            "profiler_thinslice",
        }
        if prompt_name not in allowed_prompts:
            raise ValueError("Unsupported persona profile prompt")
        system_prompt = prompt_builder.load_summary_prompt(prompt_name)
        messages = [{"role": "user", "content": user_prompt}]
        chunks: List[str] = []
        async for event in self._persona_profile_llm.chat_completion(
            messages, system_prompt
        ):
            if isinstance(event, str):
                chunks.append(event)
            elif isinstance(event, dict) and event.get("type") == "text_delta":
                chunks.append(event.get("text", ""))
        output = "".join(chunks).strip()
        if not output:
            raise RuntimeError("侧写模型返回了空内容")
        return output

    async def _openai_tool_interaction_loop(
        self,
        initial_messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        mcp_prompt_string: str,
        turn_id: int,
        llm: StatelessLLMInterface,
        debug_mode: bool = False,
    ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """Handle OpenAI interaction with tool support."""
        messages = initial_messages.copy()
        current_turn_text = ""
        pending_tool_calls: Union[List[ToolCallObject], List[Dict[str, Any]]] = []
        current_system_prompt = self._system
        tool_rounds = 0
        tools = list(tools) if tools else None
        allowed_tool_names = {
            tool.get("function", {}).get("name")
            for tool in (tools or [])
            if tool.get("function", {}).get("name")
        }

        while True:
            if self.prompt_mode_flag:
                if mcp_prompt_string:
                    current_system_prompt = prompt_builder.join_prompt_sections(
                        [self._system, mcp_prompt_string]
                    )
                else:
                    logger.warning("Prompt mode active but mcp_prompt_string is empty!")
                    current_system_prompt = self._system
                tools_for_api = None
            else:
                current_system_prompt = self._system
                tools_for_api = tools

            stream = llm.chat_completion(
                messages, current_system_prompt, tools=tools_for_api
            )
            pending_tool_calls.clear()
            current_turn_text = ""
            assistant_message_for_api = None
            detected_prompt_json = None
            goto_next_while_iteration = False

            async for event in stream:
                if self.prompt_mode_flag:
                    if isinstance(event, str):
                        current_turn_text += event
                        if self._json_detector:
                            potential_json = self._json_detector.process_chunk(event)
                            if potential_json:
                                try:
                                    if isinstance(potential_json, list):
                                        detected_prompt_json = potential_json
                                    elif isinstance(potential_json, dict):
                                        detected_prompt_json = [potential_json]

                                    if detected_prompt_json:
                                        break
                                except Exception as e:
                                    logger.error(f"Error parsing detected JSON: {e}")
                                    if self._json_detector:
                                        self._json_detector.reset()
                                    yield prompt_builder.load_runtime_prompt(
                                        "tool_json_parse_error", error=e
                                    )
                                    goto_next_while_iteration = True
                                    break
                        yield event
                else:
                    if isinstance(event, str):
                        current_turn_text += event
                        yield event
                    elif isinstance(event, list) and all(
                        isinstance(tc, ToolCallObject) for tc in event
                    ):
                        pending_tool_calls = event
                        assistant_message_for_api = {
                            "role": "assistant",
                            "content": current_turn_text if current_turn_text else None,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": tc.type,
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in pending_tool_calls
                            ],
                        }
                        break
                    elif event == "__API_NOT_SUPPORT_TOOLS__":
                        logger.warning(
                            f"LLM {getattr(llm, 'model', '')} has no native tool support. Switching to prompt mode."
                        )
                        self.prompt_mode_flag = True
                        if self._json_detector:
                            self._json_detector.reset()
                        goto_next_while_iteration = True
                        break
            if goto_next_while_iteration:
                continue

            if detected_prompt_json:
                logger.info("Processing tools detected via prompt mode JSON.")
                self._add_message(
                    current_turn_text, "assistant", debug_mode=debug_mode
                )

                parsed_tools = self._tool_executor.process_tool_from_prompt_json(
                    detected_prompt_json
                )
                if parsed_tools:
                    tool_results_for_llm = []
                    if not self._tool_executor:
                        logger.error(
                            "Prompt Tool interaction requested but ToolExecutor/MCPClient is not available."
                        )
                        yield prompt_builder.load_runtime_prompt(
                            "tool_executor_missing_prompt_mode"
                        )
                        continue

                    tool_executor_iterator = self._tool_executor.execute_tools(
                        tool_calls=parsed_tools,
                        caller_mode="Prompt",
                        allowed_tool_names=allowed_tool_names,
                    )
                    try:
                        while True:
                            update = await anext(tool_executor_iterator)
                            if update.get("type") == "final_tool_results":
                                tool_results_for_llm = update.get("results", [])
                                break
                            else:
                                yield update
                    except StopAsyncIteration:
                        logger.warning(
                            "Prompt mode tool executor finished without final results marker."
                        )

                    if tool_results_for_llm:
                        result_strings = [
                            res.get(
                                "content",
                                prompt_builder.load_runtime_prompt(
                                    "malformed_tool_result"
                                ),
                            )
                            for res in tool_results_for_llm
                        ]
                        combined_results_str = prompt_builder.build_tool_results(
                            result_strings
                        )
                        messages.append(
                            {"role": "user", "content": combined_results_str}
                        )
                continue

            elif pending_tool_calls and assistant_message_for_api:
                messages.append(assistant_message_for_api)
                if current_turn_text:
                    self._add_message(
                        current_turn_text, "assistant", debug_mode=debug_mode
                    )

                # 工具轮次上限：防止模型陷入无限工具调用（付费 API 安全阀）
                if tool_rounds >= self._MAX_TOOL_ROUNDS:
                    logger.warning(
                        "Tool interaction round limit reached; forcing text-only reply."
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": prompt_builder.load_runtime_prompt(
                                "tool_round_limit"
                            ),
                        }
                    )
                    tools = None
                    continue

                tool_rounds += 1
                tool_results_for_llm = []
                if not self._tool_executor:
                    logger.error(
                        "OpenAI Tool interaction requested but ToolExecutor/MCPClient is not available."
                    )
                    yield prompt_builder.load_runtime_prompt(
                        "tool_executor_missing_openai_mode"
                    )
                    continue

                tool_executor_iterator = self._tool_executor.execute_tools(
                    tool_calls=pending_tool_calls,
                    caller_mode="OpenAI",
                    allowed_tool_names=allowed_tool_names,
                )
                try:
                    while True:
                        update = await anext(tool_executor_iterator)
                        if update.get("type") == "final_tool_results":
                            tool_results_for_llm = update.get("results", [])
                            break
                        else:
                            yield update
                except StopAsyncIteration:
                    logger.warning(
                        "OpenAI tool executor finished without final results marker."
                    )

                if tool_results_for_llm:
                    messages.extend(tool_results_for_llm)
                continue

            else:
                if current_turn_text:
                    self._add_message(
                        current_turn_text, "assistant", debug_mode=debug_mode
                    )
                return

    def _chat_function_factory(
        self,
    ) -> Callable[[BatchInput], AsyncIterator[Union[SentenceOutput, Dict[str, Any]]]]:
        """Create the chat pipeline function."""

        @tts_filter(self._tts_preprocessor_config)
        @display_processor()
        @actions_extractor(self._live2d_model)
        @sentence_divider(
            faster_first_response=self._faster_first_response,
            segment_method=self._segment_method,
            valid_tags=["think"],
        )
        async def chat_with_memory(
            input_data: BatchInput,
        ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
            """Process chat with memory and tools."""
            self.reset_interrupt()
            active_llm = self._llm
            self.prompt_mode_flag = not getattr(active_llm, "support_tools", True)
            self._turn_sequence += 1
            turn_id = self._turn_sequence
            debug_mode = bool(
                input_data.metadata and input_data.metadata.get("debug_mode")
            )

            # 正则工具路由：命中搜索触发词时，代码直接调用 ddg-search，
            # 跳过模型自主工具调用与 mcp_prompt 拼接。
            web_search_context = ""
            if (
                self._use_mcpp
                and not self.mobile_image_only
                and self._tool_executor
                and self._web_search_requested(input_data)
            ):
                query = self._extract_search_query(input_data)
                if query:
                    tool_id = f"regex_search_{turn_id}"
                    now_ts = (
                        datetime.datetime.now(datetime.timezone.utc).isoformat()
                        + "Z"
                    )
                    yield {
                        "type": "tool_call_status",
                        "tool_id": tool_id,
                        "tool_name": "search",
                        "status": "running",
                        "content": prompt_builder.load_runtime_prompt(
                            "web_searching_status"
                        ),
                        "timestamp": now_ts,
                    }
                    is_error, text_content, _, _ = (
                        await self._tool_executor.run_single_tool(
                            "search", tool_id, {"query": query}
                        )
                    )
                    now_ts = (
                        datetime.datetime.now(datetime.timezone.utc).isoformat()
                        + "Z"
                    )
                    if is_error or not text_content:
                        web_search_context = prompt_builder.build_web_search_context(
                            prompt_builder.load_runtime_prompt("web_search_failed")
                        )
                        yield {
                            "type": "tool_call_status",
                            "tool_id": tool_id,
                            "tool_name": "search",
                            "status": "error",
                            "content": text_content
                            or prompt_builder.load_runtime_prompt(
                                "web_search_failed"
                            ),
                            "timestamp": now_ts,
                        }
                    else:
                        truncated = text_content[
                            : self._WEB_SEARCH_RESULT_MAX_CHARS
                        ]
                        web_search_context = (
                            prompt_builder.build_web_search_context(truncated)
                        )
                        yield {
                            "type": "tool_call_status",
                            "tool_id": tool_id,
                            "tool_name": "search",
                            "status": "completed",
                            "content": truncated[:200],
                            "timestamp": now_ts,
                        }
                else:
                    web_search_context = prompt_builder.build_web_search_context(
                        prompt_builder.load_runtime_prompt("web_search_empty")
                    )

            messages = self._to_messages(
                input_data, web_search_context=web_search_context
            )

            # MCP 保持连接，但每轮只向模型暴露原始用户输入命中的工具。
            routed_tools = self._select_mcp_tools_for_turn(input_data)
            if (
                self._use_mcpp
                and self._tool_manager
                and self._tool_executor
                and routed_tools
            ):
                routed_mcp_prompt = self._build_mcp_prompt_for_tools(routed_tools)
                async for output in self._openai_tool_interaction_loop(
                    initial_messages=messages,
                    tools=routed_tools,
                    mcp_prompt_string=routed_mcp_prompt,
                    turn_id=turn_id,
                    llm=active_llm,
                    debug_mode=debug_mode,
                ):
                    yield output
                return

            token_stream = active_llm.chat_completion(messages, self._system)
            complete_response = ""
            async for event in token_stream:
                text_chunk = ""
                if isinstance(event, dict) and event.get("type") == "text_delta":
                    text_chunk = event.get("text", "")
                elif isinstance(event, str):
                    text_chunk = event
                else:
                    continue
                if text_chunk:
                    yield text_chunk
                    complete_response += text_chunk
            if complete_response:
                self._add_message(
                    complete_response,
                    "assistant",
                    debug_mode=debug_mode,
                )

        return chat_with_memory

    async def chat(
        self,
        input_data: BatchInput,
    ) -> AsyncIterator[Union[SentenceOutput, Dict[str, Any]]]:
        """Run chat pipeline."""
        chat_func_decorated = self._chat_function_factory()
        async for output in chat_func_decorated(input_data):
            yield output

    def reset_interrupt(self) -> None:
        """Reset interrupt flag."""
        self._interrupt_handled = False
