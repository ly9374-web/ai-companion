import os
import json
from typing import Callable
from loguru import logger
from fastapi import WebSocket

from prompts import prompt_builder
from .live2d_model import Live2dModel
from .asr.asr_interface import ASRInterface
from .tts.tts_interface import TTSInterface
from .vad.vad_interface import VADInterface
from .agent.agents.agent_interface import AgentInterface

from .mcpp.server_registry import ServerRegistry
from .mcpp.tool_manager import ToolManager
from .mcpp.mcp_client import MCPClient
from .mcpp.tool_executor import ToolExecutor
from .mcpp.tool_adapter import ToolAdapter

from .asr.asr_factory import ASRFactory
from .tts.tts_factory import TTSFactory
from .vad.vad_factory import VADFactory
from .agent.agent_factory import AgentFactory

from .config_manager import (
    Config,
    AgentConfig,
    CharacterConfig,
    SystemConfig,
    ASRConfig,
    TTSConfig,
    VADConfig,
    read_yaml,
    validate_config,
)
from .config_manager.tts import QWEN_TTS_LANGUAGE_LABELS, QWEN_TTS_VOICE_LABELS


class ServiceContext:
    """Initializes, stores, and updates the asr, tts, and llm instances and other
    configurations for a connected client."""

    def __init__(self):
        self.config: Config = None
        self.system_config: SystemConfig = None
        self.character_config: CharacterConfig = None

        self.live2d_model: Live2dModel = None
        self.asr_engine: ASRInterface = None
        self.tts_engine: TTSInterface = None
        self.agent_engine: AgentInterface = None
        self.vad_engine: VADInterface | None = None

        self.mcp_server_registery: ServerRegistry | None = None
        self.tool_adapter: ToolAdapter | None = None
        self.tool_manager: ToolManager | None = None
        self.mcp_client: MCPClient | None = None
        self.tool_executor: ToolExecutor | None = None

        # the system prompt is a combination of the persona prompt and live2d expression prompt
        self.system_prompt: str = None
        self.persona_prompt: str = None

        # Store the generated MCP prompt string (if MCP enabled)
        self.mcp_prompt: str = ""

        self.history_uid: str = ""
        self.send_text: Callable = None
        self.client_uid: str = None

        # Per-client preference. Keep voice generation enabled by default so
        # existing clients retain the original conversation workflow.
        self.generate_audio: bool = True
        self.max_history_turns: int = 8
        self.rag_top_k: int = 5
        self.rag_threshold: float = 0.5
        self.rag_hybrid_weight: float = 0.5
        self._ai_known_tts_preferences: tuple[str, str] | None = None
        self._tts_preference_change_pending: bool = False

    def __str__(self):
        return (
            f"ServiceContext:\n"
            f"  System Config: {'Loaded' if self.system_config else 'Not Loaded'}\n"
            f"    Details: {json.dumps(self.system_config.model_dump(), indent=6) if self.system_config else 'None'}\n"
            f"  Live2D Model: {self.live2d_model.model_info if self.live2d_model else 'Not Loaded'}\n"
            f"  ASR Engine: {type(self.asr_engine).__name__ if self.asr_engine else 'Not Loaded'}\n"
            f"    Config: {json.dumps(self.character_config.asr_config.model_dump(), indent=6) if self.character_config.asr_config else 'None'}\n"
            f"  TTS Engine: {type(self.tts_engine).__name__ if self.tts_engine else 'Not Loaded'}\n"
            f"    Config: {json.dumps(self.character_config.tts_config.model_dump(), indent=6, default=str) if self.character_config.tts_config else 'None'}\n"
            f"  LLM Engine: {type(self.agent_engine).__name__ if self.agent_engine else 'Not Loaded'}\n"
            f"    Agent Config: {json.dumps(self.character_config.agent_config.model_dump(), indent=6) if self.character_config.agent_config else 'None'}\n"
            f"  VAD Engine: {type(self.vad_engine).__name__ if self.vad_engine else 'Not Loaded'}\n"
            f"    Agent Config: {json.dumps(self.character_config.vad_config.model_dump(), indent=6) if self.character_config.vad_config else 'None'}\n"
            f"  System Prompt: {self.system_prompt or 'Not Set'}\n"
            f"  MCP Enabled: {'Yes' if self.mcp_client else 'No'}"
        )

    # ==== Initializers

    async def _init_mcp_components(self, use_mcpp, enabled_servers):
        """Initializes MCP components based on configuration, dynamically fetching tool info."""
        logger.debug(
            f"Initializing MCP components: use_mcpp={use_mcpp}, enabled_servers={enabled_servers}"
        )

        self.mcp_server_registery = None
        self.tool_manager = None
        self.mcp_client = None
        self.tool_executor = None
        self.json_detector = None
        self.mcp_prompt = ""

        if use_mcpp and enabled_servers:
            self.mcp_server_registery = ServerRegistry()
            logger.info("ServerRegistry initialized or referenced.")

            if not self.tool_adapter:
                logger.error(
                    "ToolAdapter not initialized before calling _init_mcp_components."
                )
                self.mcp_prompt = prompt_builder.load_runtime_prompt(
                    "mcp_adapter_uninitialized"
                )
                return

            try:
                (
                    mcp_prompt_string,
                    openai_tools,
                    claude_tools,
                ) = await self.tool_adapter.get_tools(enabled_servers)
                self.mcp_prompt = mcp_prompt_string
                logger.info(
                    f"Dynamically generated MCP prompt string (length: {len(self.mcp_prompt)})."
                )
                logger.info(
                    f"Dynamically formatted tools - OpenAI: {len(openai_tools)}, Claude: {len(claude_tools)}."
                )

                _, raw_tools_dict = await self.tool_adapter.get_server_and_tool_info(
                    enabled_servers
                )
                self.tool_manager = ToolManager(
                    formatted_tools_openai=openai_tools,
                    formatted_tools_claude=claude_tools,
                    initial_tools_dict=raw_tools_dict,
                )
                logger.info("ToolManager initialized with dynamically fetched tools.")

            except Exception as e:
                logger.error(
                    f"Failed during dynamic MCP tool construction: {e}", exc_info=True
                )
                self.tool_manager = None
                self.mcp_prompt = prompt_builder.load_runtime_prompt(
                    "mcp_construction_error"
                )

            if self.mcp_server_registery:
                self.mcp_client = MCPClient(
                    self.mcp_server_registery, self.send_text, self.client_uid
                )
                logger.info("MCPClient initialized for this session.")
            else:
                logger.error(
                    "MCP enabled but ServerRegistry not available. MCPClient not created."
                )
                self.mcp_client = None

            if self.mcp_client and self.tool_manager:
                self.tool_executor = ToolExecutor(self.mcp_client, self.tool_manager)
                logger.info("ToolExecutor initialized for this session.")
            else:
                logger.warning(
                    "MCPClient or ToolManager not available. ToolExecutor not created."
                )
                self.tool_executor = None

            logger.info("StreamJSONDetector initialized for this session.")

        elif use_mcpp and not enabled_servers:
            logger.warning(
                "use_mcpp is True, but mcp_enabled_servers list is empty. MCP components not initialized."
            )
        else:
            logger.debug(
                "MCP components not initialized (use_mcpp is False or no enabled servers)."
            )

    async def close(self):
        """Clean up resources, especially the MCPClient."""
        logger.info("Closing ServiceContext resources...")
        if self.mcp_client:
            logger.info(f"Closing MCPClient for context instance {id(self)}...")
            await self.mcp_client.aclose()
            self.mcp_client = None
        if self.agent_engine and hasattr(self.agent_engine, "close"):
            await self.agent_engine.close()
        logger.info("ServiceContext closed.")

    async def load_cache(
        self,
        config: Config,
        system_config: SystemConfig,
        character_config: CharacterConfig,
        live2d_model: Live2dModel,
        asr_engine: ASRInterface,
        tts_engine: TTSInterface,
        vad_engine: VADInterface,
        agent_engine: AgentInterface | None = None,
        mcp_server_registery: ServerRegistry | None = None,
        tool_adapter: ToolAdapter | None = None,
        send_text: Callable = None,
        client_uid: str = None,
    ) -> None:
        """Load cached heavy engines and initialize client-owned conversation state.

        ASR, TTS, and Live2D engines may be shared between sessions. Conversation
        agents must not be shared because they contain mutable chat memory and the
        active system prompt.
        """
        if not character_config:
            raise ValueError("character_config cannot be None")
        if not system_config:
            raise ValueError("system_config cannot be None")

        self.config = config
        self.system_config = system_config
        self.character_config = character_config
        self.live2d_model = live2d_model
        self.asr_engine = asr_engine
        self.tts_engine = tts_engine
        self.vad_engine = vad_engine
        self.agent_engine = agent_engine
        self.mcp_server_registery = mcp_server_registery
        self.tool_adapter = tool_adapter
        self.send_text = send_text
        self.client_uid = client_uid

        self.max_history_turns = (
            self.character_config.agent_config.agent_settings.basic_memory_agent.max_history_turns
        )

        await self._init_mcp_components(
            self.character_config.agent_config.agent_settings.basic_memory_agent.use_mcpp,
            self.character_config.agent_config.agent_settings.basic_memory_agent.mcp_enabled_servers,
        )

        if self.agent_engine is None:
            persona_prompt = self.resolve_persona_prompt(self.character_config)
            await self.init_agent(
                self.character_config.agent_config,
                persona_prompt,
            )

        self.sync_ai_tts_preferences()
        logger.debug(f"Loaded service context with cache: {character_config}")

    async def load_from_config(self, config: Config) -> None:
        """Load the ServiceContext with the config."""
        is_initial_load = self.agent_engine is None
        if not self.config:
            self.config = config

        if not self.system_config:
            self.system_config = config.system_config

        if not self.character_config:
            self.character_config = config.character_config

        self.init_live2d(config.character_config.live2d_model_name)
        self.init_asr(config.character_config.asr_config)
        self.init_tts(config.character_config.tts_config)
        self.init_vad(config.character_config.vad_config)

        if (
            not self.tool_adapter
            and config.character_config.agent_config.agent_settings.basic_memory_agent.use_mcpp
        ):
            if not self.mcp_server_registery:
                logger.info(
                    "Initializing shared ServerRegistry within load_from_config."
                )
                self.mcp_server_registery = ServerRegistry()
            logger.info("Initializing shared ToolAdapter within load_from_config.")
            self.tool_adapter = ToolAdapter(server_registery=self.mcp_server_registery)

        await self._init_mcp_components(
            config.character_config.agent_config.agent_settings.basic_memory_agent.use_mcpp,
            config.character_config.agent_config.agent_settings.basic_memory_agent.mcp_enabled_servers,
        )

        if is_initial_load:
            self.max_history_turns = (
                config.character_config.agent_config.agent_settings.basic_memory_agent.max_history_turns
            )

        persona_prompt = self.resolve_persona_prompt(config.character_config)
        await self.init_agent(config.character_config.agent_config, persona_prompt)
        self.agent_engine.set_max_history_turns(self.max_history_turns)

        self.config = config
        self.system_config = config.system_config or self.system_config
        self.character_config = config.character_config
        self.sync_ai_tts_preferences()

    def init_live2d(self, live2d_model_name: str) -> None:
        logger.info(f"Initializing Live2D: {live2d_model_name}")
        try:
            self.live2d_model = Live2dModel(live2d_model_name)
            self.character_config.live2d_model_name = live2d_model_name
        except Exception as e:
            logger.critical(f"Error initializing Live2D: {e}")
            logger.critical("Try to proceed without Live2D...")

    def init_asr(self, asr_config: ASRConfig) -> None:
        if not self.asr_engine or (self.character_config.asr_config != asr_config):
            logger.info(f"Initializing ASR: {asr_config.asr_model}")
            self.asr_engine = ASRFactory.get_asr_system(
                asr_config.asr_model,
                **getattr(asr_config, asr_config.asr_model).model_dump(),
            )
            self.character_config.asr_config = asr_config
        else:
            logger.info("ASR already initialized with the same config.")

    def init_tts(self, tts_config: TTSConfig) -> None:
        if not self.tts_engine or (self.character_config.tts_config != tts_config):
            logger.info(f"Initializing TTS: {tts_config.tts_model}")
            self.tts_engine = TTSFactory.get_tts_engine(
                tts_config.tts_model,
                **getattr(tts_config, tts_config.tts_model.lower()).model_dump(),
            )
            self.character_config.tts_config = tts_config
        else:
            logger.info("TTS already initialized with the same config.")

    def set_tts_voice(self, voice: str) -> None:
        """Set the Qwen TTS voice for this client context only."""
        self.set_qwen_tts_options(voice=voice, notify_ai=True)

    def set_rag_options(
        self,
        *,
        top_k: int,
        threshold: float,
        hybrid_weight: float,
    ) -> None:
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise ValueError("top_k must be an integer")
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        self.rag_top_k = top_k
        self.rag_threshold = max(0.0, min(1.0, float(threshold)))
        self.rag_hybrid_weight = max(0.0, min(1.0, float(hybrid_weight)))

    def set_qwen_tts_options(
        self,
        *,
        voice: str | None = None,
        language_hint: str | None = None,
        instruction: str | None = None,
        notify_ai: bool = False,
        sync_ai_preferences: bool = False,
    ) -> None:
        """Set Qwen TTS options for this client context only."""
        tts_config = self.character_config.tts_config
        if tts_config.tts_model != "qwen_tts" or tts_config.qwen_tts is None:
            raise ValueError("The active TTS engine does not support Qwen voice switching")

        if notify_ai and self._ai_known_tts_preferences is None:
            self._ai_known_tts_preferences = (
                tts_config.qwen_tts.voice,
                tts_config.qwen_tts.language_hint,
            )

        updates = {}
        if voice is not None:
            updates["voice"] = voice
        if language_hint is not None:
            updates["language_hint"] = language_hint
        if instruction is not None:
            updates["instruction"] = instruction
        qwen_config = tts_config.qwen_tts.model_copy(update=updates)
        updated_tts_config = tts_config.model_copy(
            deep=True, update={"qwen_tts": qwen_config}
        )
        self.tts_engine = TTSFactory.get_tts_engine(
            "qwen_tts", **qwen_config.model_dump()
        )
        self.character_config.tts_config = updated_tts_config
        if self.config and self.config.character_config:
            self.config.character_config.tts_config = updated_tts_config.model_copy(
                deep=True
            )
        if sync_ai_preferences:
            self.sync_ai_tts_preferences()
        elif notify_ai:
            self._tts_preference_change_pending = True
        logger.info("Qwen TTS options updated for client {}", self.client_uid)

    def _current_tts_preferences(self) -> tuple[str, str] | None:
        if self.character_config is None:
            return None
        qwen_config = self.character_config.tts_config.qwen_tts
        if qwen_config is None:
            return None
        return qwen_config.voice, qwen_config.language_hint

    def sync_ai_tts_preferences(self) -> None:
        """Mark the current settings as already known, without notifying the AI."""
        self._ai_known_tts_preferences = self._current_tts_preferences()
        self._tts_preference_change_pending = False

    def consume_tts_preference_change_prompt(self) -> str:
        """Return a one-turn prompt describing user-initiated TTS changes."""
        current_preferences = self._current_tts_preferences()
        if current_preferences is None:
            return ""
        if (
            not self._tts_preference_change_pending
            or self._ai_known_tts_preferences is None
        ):
            return ""

        previous_voice, previous_language = self._ai_known_tts_preferences
        current_voice, current_language = current_preferences
        prompt_lines = []
        if current_voice != previous_voice:
            prompt_lines.append(
                prompt_builder.load_runtime_prompt(
                    "tts_voice_changed",
                    voice_name=QWEN_TTS_VOICE_LABELS[current_voice],
                )
            )
        if current_language != previous_language:
            prompt_lines.append(
                prompt_builder.load_runtime_prompt(
                    "tts_language_changed",
                    language_name=QWEN_TTS_LANGUAGE_LABELS[current_language],
                )
            )

        self._ai_known_tts_preferences = current_preferences
        self._tts_preference_change_pending = False
        return prompt_builder.join_prompt_lines(prompt_lines)

    def set_max_history_turns(self, max_history_turns: int) -> None:
        """Update the per-client LLM history window without deleting memory."""
        if self.agent_engine is None:
            raise RuntimeError("Cannot set history turns without an active agent")
        self.agent_engine.set_max_history_turns(max_history_turns)
        self.max_history_turns = max_history_turns
        logger.info(
            "Max history turns updated to {} for client {}",
            max_history_turns,
            self.client_uid,
        )

    def init_vad(self, vad_config: VADConfig) -> None:
        if vad_config.vad_model is None:
            logger.info("VAD is disabled.")
            self.vad_engine = None
            return

        if not self.vad_engine or (self.character_config.vad_config != vad_config):
            logger.info(f"Initializing VAD: {vad_config.vad_model}")
            self.vad_engine = VADFactory.get_vad_engine(
                vad_config.vad_model,
                **getattr(vad_config, vad_config.vad_model.lower()).model_dump(),
            )
            self.character_config.vad_config = vad_config
        else:
            logger.info("VAD already initialized with the same config.")

    async def init_agent(self, agent_config: AgentConfig, persona_prompt: str) -> None:
        """Initialize or update the LLM engine based on agent configuration."""
        logger.info(f"Initializing Agent: {agent_config.conversation_agent_choice}")

        system_prompt = await self.construct_system_prompt(persona_prompt)

        if (
            self.agent_engine is not None
            and agent_config == self.character_config.agent_config
            and persona_prompt == self.persona_prompt
            and system_prompt == self.system_prompt
        ):
            logger.debug("Agent already initialized with the same config.")
            return

        avatar = self.character_config.avatar or ""

        try:
            self.agent_engine = AgentFactory.create_agent(
                conversation_agent_choice=agent_config.conversation_agent_choice,
                agent_settings=agent_config.agent_settings.model_dump(),
                llm_configs=agent_config.llm_configs.model_dump(),
                system_prompt=system_prompt,
                live2d_model=self.live2d_model,
                tts_preprocessor_config=self.character_config.tts_preprocessor_config,
                character_avatar=avatar,
                system_config=self.system_config.model_dump(),
                tool_manager=self.tool_manager,
                tool_executor=self.tool_executor,
                mcp_prompt_string=self.mcp_prompt,
            )

            logger.debug(f"Agent choice: {agent_config.conversation_agent_choice}")
            logger.debug(f"System prompt: {system_prompt}")

            self.character_config.agent_config = agent_config
            self.persona_prompt = persona_prompt
            self.system_prompt = system_prompt

        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            raise

    # ==== utils

    async def construct_system_prompt(self, persona_prompt: str) -> str:
        """Render the role's complete YAML system prompt."""
        logger.debug("Rendering complete character system prompt from prompts.yaml")
        character_prompt = prompt_builder.render_character_system_prompt(
            persona_prompt,
            emomap_keys=self.live2d_model.emo_str,
        )
        qwen_tts_config = self.character_config.tts_config.qwen_tts
        language_hint = (
            qwen_tts_config.language_hint if qwen_tts_config is not None else "en"
        )
        language_instruction = prompt_builder.build_output_language_instruction(
            language_hint
        )
        system_prompt = prompt_builder.join_prompt_sections(
            (language_instruction, character_prompt)
        )

        logger.debug("\n === System Prompt ===")
        logger.debug(system_prompt)

        return system_prompt

    async def refresh_character_system_prompt(self) -> bool:
        """Reload the active character prompt and update the agent if it changed.

        Character prompts live in ``prompts.yaml`` and are intentionally read on
        every load. Refreshing immediately before a model request prevents a
        long-running server or browser session from continuing to use a prompt
        that has since been edited on disk.

        Returns:
            ``True`` when the active agent was updated, otherwise ``False``.
        """
        if self.character_config is None:
            raise RuntimeError("Cannot refresh system prompt without character config")
        if self.live2d_model is None:
            raise RuntimeError("Cannot refresh system prompt without Live2D model")

        persona_prompt = self.resolve_persona_prompt(self.character_config)
        system_prompt = await self.construct_system_prompt(persona_prompt)

        if (
            persona_prompt == self.persona_prompt
            and system_prompt == self.system_prompt
        ):
            return False

        if self.agent_engine is None:
            await self.init_agent(
                self.character_config.agent_config,
                persona_prompt,
            )
        else:
            set_system = getattr(self.agent_engine, "set_system", None)
            if not callable(set_system):
                raise RuntimeError(
                    "The active conversation agent cannot update its system prompt"
                )
            set_system(system_prompt)
            self.persona_prompt = persona_prompt
            self.system_prompt = system_prompt

        logger.info(
            "Reloaded character system prompt for {} ({})",
            self.character_config.conf_name,
            self.character_config.conf_uid,
        )
        return True

    @staticmethod
    def resolve_persona_prompt(character_config: CharacterConfig) -> str:
        """Resolve the new file-based persona or the legacy inline fallback."""
        return prompt_builder.resolve_persona_prompt(
            character_config.persona_prompt_file,
            character_config.persona_prompt,
        )

    async def handle_config_switch(
        self,
        websocket: WebSocket,
        config_file_name: str,
    ) -> None:
        """Handle the configuration switch request."""
        try:
            new_character_config_data = None

            if config_file_name == "conf.yaml":
                new_character_config_data = read_yaml("conf.yaml").get(
                    "character_config"
                )
            else:
                characters_dir = self.system_config.config_alts_dir
                file_path = os.path.normpath(
                    os.path.join(characters_dir, config_file_name)
                )
                if not file_path.startswith(characters_dir):
                    raise ValueError("Invalid configuration file path")

                alt_config_data = read_yaml(file_path).get("character_config")
                base_character_config = self.config.character_config.model_dump()
                if (
                    "persona_prompt" in alt_config_data
                    and "persona_prompt_file" not in alt_config_data
                ):
                    base_character_config["persona_prompt_file"] = None
                elif (
                    "persona_prompt_file" in alt_config_data
                    and "persona_prompt" not in alt_config_data
                ):
                    base_character_config["persona_prompt"] = ""
                new_character_config_data = deep_merge(
                    base_character_config, alt_config_data
                )

            if new_character_config_data:
                new_config = {
                    "system_config": self.system_config.model_dump(),
                    "character_config": new_character_config_data,
                }
                new_config = validate_config(new_config)
                await self.load_from_config(new_config)
                logger.debug(f"New config: {self}")
                logger.debug(
                    f"New character config: {self.character_config.model_dump()}"
                )

                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "set-model-and-conf",
                            "model_info": self.live2d_model.model_info,
                            "conf_name": self.character_config.conf_name,
                            "conf_uid": self.character_config.conf_uid,
                        }
                    )
                )

                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "config-switched",
                            "message": f"Switched to config: {config_file_name}",
                        }
                    )
                )

                logger.info(f"Configuration switched to {config_file_name}")
            else:
                raise ValueError(
                    f"Failed to load configuration from {config_file_name}"
                )

        except Exception as e:
            logger.error(f"Error switching configuration: {e}")
            logger.debug(self)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": f"Error switching configuration: {str(e)}",
                    }
                )
            )
            raise e


def deep_merge(dict1, dict2):
    """Recursively merges dict2 into dict1, prioritizing values from dict2."""
    result = dict1.copy()
    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
