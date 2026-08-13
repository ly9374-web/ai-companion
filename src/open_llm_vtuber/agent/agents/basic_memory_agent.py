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
import json
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
    _CONTEXT_INJECTION_KEYS = (
        "long_term_relationship_context",
        "short_term_relationship_context",
    )

    def __init__(
        self,
        llm: StatelessLLMInterface,
        system: str,
        live2d_model,
        summary_llm: Optional[StatelessLLMInterface] = None,
        rolling_summary_llm: Optional[StatelessLLMInterface] = None,
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
        self.interrupt_method = interrupt_method
        self._tool_prompts = tool_prompts or {}
        self._interrupt_handled = False
        self.prompt_mode_flag = False

        self._tool_manager = tool_manager
        self._tool_executor = tool_executor
        self._mcp_prompt_string = mcp_prompt_string
        self._json_detector = StreamJSONDetector()
        self._turn_sequence = 0
        self._request_sequence = 0

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

        self._set_llm(llm)
        self._summary_llm = summary_llm or llm
        self._rolling_summary_llm = rolling_summary_llm or self._summary_llm
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
    ) -> List[Dict[str, Any]]:
        """Return history after both configured role counts are reached.

        The complete in-process memory remains available for history persistence
        and summaries. Only the messages copied into the next LLM request are
        truncated. Hidden context snapshots are rendered only at their latest
        retained position, and a fresh snapshot on the current request replaces
        the historical snapshot of the same type.
        """
        max_history_turns = getattr(self, "_max_history_turns", 8)
        user_turns = 0
        assistant_turns = 0
        start_index = 0

        for index in range(len(self._memory) - 1, -1, -1):
            role = self._memory[index].get("role")
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

        context_memory = [message.copy() for message in self._memory[start_index:]]
        superseded_context_keys = superseded_context_keys or set()
        latest_context_positions: Dict[str, int] = {}

        for index, message in enumerate(context_memory):
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
        ):
            return

        self._memory.append(message_data)

    def set_memory_from_history(self, conf_uid: str, history_uid: str) -> None:
        """Load memory from chat history."""
        messages = get_history(conf_uid, history_uid)

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
                self._memory.append(message_data)
            else:
                logger.warning(f"Skipping invalid message from history: {msg}")
        logger.info(f"Loaded {len(self._memory)} messages from history.")

    def handle_interrupt(self, heard_response: str) -> None:
        """Handle user interruption."""
        if self._interrupt_handled:
            return

        self._interrupt_handled = True

        if self._memory and self._memory[-1]["role"] == "assistant":
            if not self._memory[-1]["content"].endswith("..."):
                self._memory[-1]["content"] = heard_response + "..."
            else:
                self._memory[-1]["content"] = heard_response + "..."
        else:
            if heard_response:
                self._memory.append(
                    {
                        "role": "assistant",
                        "content": heard_response + "...",
                    }
                )

        interrupt_role = "system" if self.interrupt_method == "system" else "user"
        self._memory.append(
            {
                "role": interrupt_role,
                "content": prompt_builder.load_runtime_prompt(
                    "interrupted_by_user"
                ),
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

    def _to_messages(self, input_data: BatchInput) -> List[Dict[str, Any]]:
        """Prepare messages for LLM API call."""
        user_content = []
        text_prompt = self._to_text_prompt(input_data)
        long_term_memory_context = ""
        long_term_relationship_context = ""
        short_term_relationship_context = ""
        tts_preference_change_context = ""
        rolling_summary_context = ""
        frontend_activity_context = ""
        if input_data.metadata:
            frontend_activity_context = input_data.metadata.get(
                "frontend_activity_context", ""
            )
            long_term_memory_context = input_data.metadata.get(
                "long_term_memory_context", ""
            )
            long_term_relationship_context = input_data.metadata.get(
                "long_term_relationship_context", ""
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

        # RAG memory is request-scoped. Relationship snapshots retain their
        # existing history behavior, while retrieved memory never enters it.
        context_injections = {
            "long_term_relationship_context": long_term_relationship_context,
            "short_term_relationship_context": short_term_relationship_context,
        }
        active_context_injections = {
            key: value
            for key, value in context_injections.items()
            if isinstance(value, str) and value.strip()
        }
        messages = self._get_context_memory(
            superseded_context_keys=set(active_context_injections)
        )

        request_text = prompt_builder.build_user_request(
            text_prompt=text_prompt,
            frontend_activity_context=frontend_activity_context,
            tts_preference_change_context=tts_preference_change_context,
            rolling_summary_context=rolling_summary_context,
            long_term_memory_context=long_term_memory_context,
            long_term_relationship_context=long_term_relationship_context,
            short_term_relationship_context=short_term_relationship_context,
            has_images=bool(input_data.images),
        )

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
                )
        else:
            logger.warning("No content generated for user message.")

        return messages

    async def summarize_long_term_memory(
        self,
        turns: List[Dict[str, str]],
    ) -> str:
        """Use the configured DeepSeek model for one memory-analysis request."""
        system_prompt = prompt_builder.load_summary_prompt("long_term_memory")

        summary_input = prompt_builder.build_long_term_memory_summary_input(
            recent_turns=turns,
        )
        messages = [
            {
                "role": "user",
                "content": summary_input,
            }
        ]

        logger.info("Long-term memory system prompt:\n{}", system_prompt)
        logger.info("Long-term memory user prompt:\n{}", summary_input)
        chunks: List[str] = []
        async for event in self._summary_llm.chat_completion(messages, system_prompt):
            if isinstance(event, str):
                chunks.append(event)
            elif isinstance(event, dict) and event.get("type") == "text_delta":
                chunks.append(event.get("text", ""))
        raw_output = "".join(chunks).strip()
        logger.info(
            "Long-term memory raw model output:\n{}",
            raw_output or "[empty output]",
        )
        return raw_output

    async def summarize_rolling_context(
        self,
        turns: List[Dict[str, str]],
    ) -> str:
        """Summarize the portion of this chat outside the direct context window."""
        system_prompt = prompt_builder.load_summary_prompt("rolling_context")
        summary_input = prompt_builder.build_rolling_context_summary_input(turns)
        messages = [{"role": "user", "content": summary_input}]

        logger.info("Rolling context system prompt:\n{}", system_prompt)
        logger.info("Rolling context user prompt:\n{}", summary_input)
        chunks: List[str] = []
        async for event in self._rolling_summary_llm.chat_completion(
            messages, system_prompt
        ):
            if isinstance(event, str):
                chunks.append(event)
            elif isinstance(event, dict) and event.get("type") == "text_delta":
                chunks.append(event.get("text", ""))
        raw_output = "".join(chunks).strip()
        logger.info(
            "Rolling context raw model output:\n{}",
            raw_output or "[empty output]",
        )
        return raw_output

    async def summarize_long_term_relationship(
        self,
        long_term_memory_contents: List[str],
        existing_relationship_file: str,
        short_term_relationship_file: str,
    ) -> str:
        """Use DeepSeek Pro to rewrite the current character's relationship JSON."""
        system_prompt = prompt_builder.load_summary_prompt(
            "long_term_relationship"
        )

        summary_input = prompt_builder.build_long_term_relationship_summary_input(
            long_term_memory_contents,
            existing_relationship_file,
            short_term_relationship_file,
        )
        messages = [
            {
                "role": "user",
                "content": summary_input,
            }
        ]

        logger.info("Long-term relationship system prompt:\n{}", system_prompt)
        logger.info("Long-term relationship user prompt:\n{}", summary_input)
        chunks: List[str] = []
        async for event in self._summary_llm.chat_completion(messages, system_prompt):
            if isinstance(event, str):
                chunks.append(event)
            elif isinstance(event, dict) and event.get("type") == "text_delta":
                chunks.append(event.get("text", ""))
        raw_output = "".join(chunks).strip()
        logger.info(
            "Long-term relationship raw model output:\n{}",
            raw_output or "[empty output]",
        )
        return raw_output

    async def summarize_short_term_relationship(
        self,
        recent_turns: List[Dict[str, str]],
        long_term_relationship_file: str,
        existing_short_term_relationship_file: str,
        browser_time: str = "",
    ) -> str:
        """Use DeepSeek Pro to rewrite the recent relationship state."""
        system_prompt = prompt_builder.load_summary_prompt(
            "short_term_relationship"
        )

        summary_input = prompt_builder.build_short_term_relationship_summary_input(
            recent_turns,
            long_term_relationship_file,
            existing_short_term_relationship_file,
            browser_time,
        )
        messages = [
            {
                "role": "user",
                "content": summary_input,
            }
        ]

        logger.info("Short-term relationship system prompt:\n{}", system_prompt)
        logger.info("Short-term relationship user prompt:\n{}", summary_input)
        chunks: List[str] = []
        async for event in self._summary_llm.chat_completion(messages, system_prompt):
            if isinstance(event, str):
                chunks.append(event)
            elif isinstance(event, dict) and event.get("type") == "text_delta":
                chunks.append(event.get("text", ""))
        raw_output = "".join(chunks).strip()
        logger.info(
            "Short-term relationship raw model output:\n{}",
            raw_output or "[empty output]",
        )
        return raw_output

    @staticmethod
    def _sanitize_request_for_log(value: Any) -> Any:
        """Remove bulky image payloads while preserving the request structure."""
        if isinstance(value, dict):
            return {
                key: BasicMemoryAgent._sanitize_request_for_log(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [BasicMemoryAgent._sanitize_request_for_log(item) for item in value]
        if isinstance(value, str) and value.startswith("data:image"):
            return f"[image data omitted from log: {len(value)} characters]"
        return value

    def _log_llm_request(
        self,
        turn_id: int,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Log each request immediately before it is sent to the LLM backend."""
        self._request_sequence += 1
        tool_names = []
        for tool in tools or []:
            function = tool.get("function", tool) if isinstance(tool, dict) else {}
            if isinstance(function, dict) and function.get("name"):
                tool_names.append(function["name"])

        request_log = {
            "turn_id": turn_id,
            "request_id": self._request_sequence,
            "provider": type(self._llm).__name__,
            "model": getattr(self._llm, "model", None),
            "base_url": getattr(self._llm, "base_url", None),
            "system_prompt": system_prompt,
            "messages": self._sanitize_request_for_log(messages),
            "tools": tool_names,
        }
        logger.info(
            "LLM REQUEST\n{}",
            json.dumps(request_log, ensure_ascii=False, indent=2, default=str),
        )

    async def _openai_tool_interaction_loop(
        self,
        initial_messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        turn_id: int,
    ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """Handle OpenAI interaction with tool support."""
        messages = initial_messages.copy()
        current_turn_text = ""
        pending_tool_calls: Union[List[ToolCallObject], List[Dict[str, Any]]] = []
        current_system_prompt = self._system

        while True:
            if self.prompt_mode_flag:
                if self._mcp_prompt_string:
                    current_system_prompt = prompt_builder.join_prompt_sections(
                        [self._system, self._mcp_prompt_string]
                    )
                else:
                    logger.warning("Prompt mode active but mcp_prompt_string is empty!")
                    current_system_prompt = self._system
                tools_for_api = None
            else:
                current_system_prompt = self._system
                tools_for_api = tools

            self._log_llm_request(
                turn_id, messages, current_system_prompt, tools_for_api
            )
            stream = self._llm.chat_completion(
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
                            f"LLM {getattr(self._llm, 'model', '')} has no native tool support. Switching to prompt mode."
                        )
                        self.prompt_mode_flag = True
                        if self._tool_manager:
                            self._tool_manager.disable()
                        if self._json_detector:
                            self._json_detector.reset()
                        goto_next_while_iteration = True
                        break
            if goto_next_while_iteration:
                continue

            if detected_prompt_json:
                logger.info("Processing tools detected via prompt mode JSON.")
                self._add_message(current_turn_text, "assistant")

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
                    self._add_message(current_turn_text, "assistant")

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
                    self._add_message(current_turn_text, "assistant")
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
            self.prompt_mode_flag = False
            self._turn_sequence += 1
            turn_id = self._turn_sequence

            messages = self._to_messages(input_data)
            tools = None
            tool_mode = None
            llm_supports_native_tools = False

            if self._use_mcpp and self._tool_manager:
                tools = None
                if isinstance(self._llm, OpenAICompatibleAsyncLLM):
                    tool_mode = "OpenAI"
                    tools = self._formatted_tools_openai
                    llm_supports_native_tools = True
                else:
                    logger.warning(
                        f"LLM type {type(self._llm)} not explicitly handled for tool mode determination."
                    )

                if llm_supports_native_tools and not tools:
                    logger.warning(
                        f"No tools available/formatted for '{tool_mode}' mode, despite MCP being enabled."
                    )

            if self._use_mcpp and tool_mode == "OpenAI":
                logger.debug(
                    f"Starting OpenAI tool interaction loop with {len(tools)} tools."
                )
                async for output in self._openai_tool_interaction_loop(
                    messages, tools if tools else [], turn_id
                ):
                    yield output
                return
            else:
                logger.info("Starting simple chat completion.")
                self._log_llm_request(turn_id, messages, self._system)
                token_stream = self._llm.chat_completion(messages, self._system)
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
                    self._add_message(complete_response, "assistant")

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
