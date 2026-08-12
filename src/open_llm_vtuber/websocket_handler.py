from typing import Dict, List, Optional, Callable, TypedDict
from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import json
import numpy as np
from loguru import logger

from .service_context import ServiceContext
from .message_handler import message_handler
from .chat_history_manager import (
    create_new_history,
    get_history,
    delete_history,
    get_history_list,
)
from .config_manager.utils import scan_config_alts_directory, scan_bg_directory
from .config_manager.tts import QWEN_TTS_LANGUAGE_HINTS, QWEN_TTS_VOICES
from .conversations.conversation_handler import (
    handle_conversation_trigger,
    handle_individual_interrupt,
)


class WSMessage(TypedDict, total=False):
    """Type definition for WebSocket messages"""

    type: str
    action: Optional[str]
    text: Optional[str]
    audio: Optional[List[float]]
    images: Optional[List[str]]
    history_uid: Optional[str]
    file: Optional[str]
    display_text: Optional[dict]
    voice: Optional[str]
    language_hint: Optional[str]
    instruction: Optional[str]
    notify_ai: Optional[bool]
    sync_ai_preferences: Optional[bool]
    browser_time: Optional[str]
    enabled: Optional[bool]
    max_history_turns: Optional[int]
    top_k: Optional[int]
    threshold: Optional[float]
    hybrid_weight: Optional[float]


class WebSocketHandler:
    """Handles WebSocket connections and message routing"""

    def __init__(self, default_context_cache: ServiceContext):
        """Initialize the WebSocket handler with default context"""
        self.client_contexts: Dict[str, ServiceContext] = {}
        self.current_conversation_tasks: Dict[str, Optional[asyncio.Task]] = {}
        self.default_context_cache = default_context_cache
        self.received_data_buffers: Dict[str, np.ndarray] = {}

        # Message handlers mapping
        self._message_handlers = self._init_message_handlers()

    def _init_message_handlers(self) -> Dict[str, Callable]:
        """Initialize message type to handler mapping"""
        return {
            "fetch-history-list": self._handle_history_list_request,
            "fetch-and-set-history": self._handle_fetch_history,
            "create-new-history": self._handle_create_history,
            "delete-history": self._handle_delete_history,
            "interrupt-signal": self._handle_interrupt,
            "mic-audio-data": self._handle_audio_data,
            "mic-audio-end": self._handle_conversation_trigger,
            "raw-audio-data": self._handle_raw_audio_data,
            "text-input": self._handle_conversation_trigger,
            "ai-speak-signal": self._handle_conversation_trigger,
            "fetch-configs": self._handle_fetch_configs,
            "switch-config": self._handle_config_switch,
            "set-tts-voice": self._handle_set_tts_voice,
            "set-qwen-tts-options": self._handle_set_qwen_tts_options,
            "set-generate-audio": self._handle_set_generate_audio,
            "set-max-history-turns": self._handle_set_max_history_turns,
            "set-rag-options": self._handle_set_rag_options,
            "fetch-backgrounds": self._handle_fetch_backgrounds,
            "request-init-config": self._handle_init_config_request,
            "heartbeat": self._handle_heartbeat,
        }

    async def handle_new_connection(
        self, websocket: WebSocket, client_uid: str
    ) -> None:
        """
        Handle new WebSocket connection setup

        Args:
            websocket: The WebSocket connection
            client_uid: Unique identifier for the client

        Raises:
            Exception: If initialization fails
        """
        try:
            session_service_context = await self._init_service_context(
                websocket.send_text, client_uid
            )

            await self._store_client_data(client_uid, session_service_context)

            await self._send_initial_messages(websocket, session_service_context)

            logger.info(f"Connection established for client {client_uid}")

        except Exception as e:
            logger.error(
                f"Failed to initialize connection for client {client_uid}: {e}"
            )
            await self._cleanup_failed_connection(client_uid)
            raise

    async def _store_client_data(
        self,
        client_uid: str,
        session_service_context: ServiceContext,
    ):
        """Store data for a connected client."""
        self.client_contexts[client_uid] = session_service_context
        self.received_data_buffers[client_uid] = np.array([])

    async def _send_initial_messages(
        self,
        websocket: WebSocket,
        session_service_context: ServiceContext,
    ):
        """Send initial connection messages to the client"""
        await websocket.send_text(
            json.dumps({"type": "full-text", "text": "Connection established"})
        )

        await websocket.send_text(
            json.dumps(
                {
                    "type": "set-model-and-conf",
                    "model_info": session_service_context.live2d_model.model_info,
                    "conf_name": session_service_context.character_config.conf_name,
                    "conf_uid": session_service_context.character_config.conf_uid,
                }
            )
        )

        # Start microphone (disabled: user opens mic manually)
        # await websocket.send_text(json.dumps({"type": "control", "text": "start-mic"}))

    async def _init_service_context(
        self, send_text: Callable, client_uid: str
    ) -> ServiceContext:
        """Initialize service context for a new session by cloning the default context"""
        session_service_context = ServiceContext()
        await session_service_context.load_cache(
            config=self.default_context_cache.config.model_copy(deep=True),
            system_config=self.default_context_cache.system_config.model_copy(
                deep=True
            ),
            character_config=self.default_context_cache.character_config.model_copy(
                deep=True
            ),
            live2d_model=self.default_context_cache.live2d_model,
            asr_engine=self.default_context_cache.asr_engine,
            tts_engine=self.default_context_cache.tts_engine,
            vad_engine=self.default_context_cache.vad_engine,
            # Agents contain mutable memory and system-prompt state. Each client
            # must own a separate instance even when heavy media engines are cached.
            agent_engine=None,
            mcp_server_registery=self.default_context_cache.mcp_server_registery,
            tool_adapter=self.default_context_cache.tool_adapter,
            send_text=send_text,
            client_uid=client_uid,
        )
        return session_service_context

    async def handle_websocket_communication(
        self, websocket: WebSocket, client_uid: str
    ) -> None:
        """
        Handle ongoing WebSocket communication

        Args:
            websocket: The WebSocket connection
            client_uid: Unique identifier for the client
        """
        try:
            while True:
                try:
                    data = await websocket.receive_json()
                    message_handler.handle_message(client_uid, data)
                    await self._route_message(websocket, client_uid, data)
                except WebSocketDisconnect:
                    raise
                except json.JSONDecodeError:
                    logger.error("Invalid JSON received")
                    continue
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": str(e)})
                    )
                    continue

        except WebSocketDisconnect:
            logger.info(f"Client {client_uid} disconnected")
            raise
        except Exception as e:
            logger.error(f"Fatal error in WebSocket communication: {e}")
            raise

    async def _route_message(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """
        Route incoming message to appropriate handler

        Args:
            websocket: The WebSocket connection
            client_uid: Client identifier
            data: Message data
        """
        msg_type = data.get("type")
        if not msg_type:
            logger.warning("Message received without type")
            return

        handler = self._message_handlers.get(msg_type)
        if handler:
            await handler(websocket, client_uid, data)
        else:
            if msg_type != "frontend-playback-complete":
                logger.warning(f"Unknown message type: {msg_type}")

    async def handle_disconnect(self, client_uid: str) -> None:
        """Handle client disconnection"""
        context = self.client_contexts.pop(client_uid, None)
        self.received_data_buffers.pop(client_uid, None)
        if client_uid in self.current_conversation_tasks:
            task = self.current_conversation_tasks[client_uid]
            if task and not task.done():
                task.cancel()
            self.current_conversation_tasks.pop(client_uid, None)

        # Call context close to clean up resources (e.g., MCPClient)
        if context:
            await context.close()

        logger.info(f"Client {client_uid} disconnected")
        message_handler.cleanup_client(client_uid)

    async def _cleanup_failed_connection(self, client_uid: str) -> None:
        """Clean up failed connection data"""
        self.client_contexts.pop(client_uid, None)
        self.received_data_buffers.pop(client_uid, None)

        if client_uid in self.current_conversation_tasks:
            task = self.current_conversation_tasks[client_uid]
            if task and not task.done():
                task.cancel()
            self.current_conversation_tasks.pop(client_uid, None)

        message_handler.cleanup_client(client_uid)

    async def _handle_interrupt(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle conversation interruption"""
        heard_response = data.get("text", "")
        context = self.client_contexts[client_uid]
        await handle_individual_interrupt(
            client_uid=client_uid,
            current_conversation_tasks=self.current_conversation_tasks,
            context=context,
            heard_response=heard_response,
        )

    async def _handle_history_list_request(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle request for chat history list"""
        context = self.client_contexts[client_uid]
        histories = get_history_list(context.character_config.conf_uid)
        await websocket.send_text(
            json.dumps({"type": "history-list", "histories": histories})
        )

    async def _handle_fetch_history(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle fetching and setting specific chat history"""
        history_uid = data.get("history_uid")
        if not history_uid:
            return

        context = self.client_contexts[client_uid]
        # Update history_uid in service context
        context.history_uid = history_uid
        context.agent_engine.set_memory_from_history(
            conf_uid=context.character_config.conf_uid,
            history_uid=history_uid,
        )

        messages = [
            {
                key: value
                for key, value in msg.items()
                if key != "context_injections"
            }
            for msg in get_history(
                context.character_config.conf_uid,
                history_uid,
            )
            if msg["role"] != "system"
        ]
        await websocket.send_text(
            json.dumps({"type": "history-data", "messages": messages})
        )

    async def _handle_create_history(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle creation of new chat history"""
        context = self.client_contexts[client_uid]
        history_uid = create_new_history(context.character_config.conf_uid)
        if history_uid:
            context.history_uid = history_uid
            context.agent_engine.set_memory_from_history(
                conf_uid=context.character_config.conf_uid,
                history_uid=history_uid,
            )
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "new-history-created",
                        "history_uid": history_uid,
                    }
                )
            )

    async def _handle_delete_history(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle deletion of chat history"""
        history_uid = data.get("history_uid")
        if not history_uid:
            return

        context = self.client_contexts[client_uid]
        success = delete_history(
            context.character_config.conf_uid,
            history_uid,
        )
        await websocket.send_text(
            json.dumps(
                {
                    "type": "history-deleted",
                    "success": success,
                    "history_uid": history_uid,
                }
            )
        )
        if history_uid == context.history_uid:
            context.history_uid = None

    async def _handle_audio_data(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle incoming audio data"""
        audio_data = data.get("audio", [])
        if audio_data:
            self.received_data_buffers[client_uid] = np.append(
                self.received_data_buffers[client_uid],
                np.array(audio_data, dtype=np.float32),
            )

    async def _handle_raw_audio_data(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle incoming raw audio data for VAD processing"""
        context = self.client_contexts[client_uid]
        chunk = data.get("audio", [])
        if chunk:
            for audio_bytes in context.vad_engine.detect_speech(chunk):
                if audio_bytes == b"<|PAUSE|>":
                    await websocket.send_text(
                        json.dumps({"type": "control", "text": "interrupt"})
                    )
                elif audio_bytes == b"<|RESUME|>":
                    pass
                elif len(audio_bytes) > 1024:
                    # Detected audio activity (voice)
                    self.received_data_buffers[client_uid] = np.append(
                        self.received_data_buffers[client_uid],
                        np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32),
                    )
                    await websocket.send_text(
                        json.dumps({"type": "control", "text": "mic-audio-end"})
                    )

    async def _handle_conversation_trigger(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle triggers that start a conversation"""
        await handle_conversation_trigger(
            msg_type=data.get("type", ""),
            data=data,
            client_uid=client_uid,
            context=self.client_contexts[client_uid],
            websocket=websocket,
            received_data_buffers=self.received_data_buffers,
            current_conversation_tasks=self.current_conversation_tasks,
        )

    async def _handle_fetch_configs(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle fetching available configurations"""
        context = self.client_contexts[client_uid]
        config_files = scan_config_alts_directory(context.system_config.config_alts_dir)
        await websocket.send_text(
            json.dumps({"type": "config-files", "configs": config_files})
        )

    async def _handle_config_switch(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle switching to a different configuration"""
        config_file_name = data.get("file")
        if config_file_name:
            context = self.client_contexts[client_uid]
            await context.handle_config_switch(websocket, config_file_name)

    async def _handle_set_tts_voice(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Update the Qwen voice for one client session."""
        voice = data.get("voice")
        if not isinstance(voice, str) or voice not in QWEN_TTS_VOICES:
            raise ValueError("Unsupported Qwen-Audio Flash voice")

        context = self.client_contexts[client_uid]
        context.set_tts_voice(voice)
        await websocket.send_text(
            json.dumps({"type": "tts-voice-updated", "voice": voice})
        )

    async def _handle_set_qwen_tts_options(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Update Qwen voice, language hint, and instruction for one session."""
        voice = data.get("voice")
        language_hint = data.get("language_hint")
        instruction = data.get("instruction")
        notify_ai = data.get("notify_ai", False)
        sync_ai_preferences = data.get("sync_ai_preferences", False)
        if not isinstance(voice, str) or voice not in QWEN_TTS_VOICES:
            raise ValueError("Unsupported Qwen-Audio Flash voice")
        if (
            not isinstance(language_hint, str)
            or language_hint not in QWEN_TTS_LANGUAGE_HINTS
        ):
            raise ValueError("Unsupported Qwen TTS language hint")
        if not isinstance(instruction, str) or len(instruction) > 2000:
            raise ValueError("Invalid Qwen TTS instruction")
        if not isinstance(notify_ai, bool):
            raise ValueError("Invalid notify-ai setting")
        if not isinstance(sync_ai_preferences, bool):
            raise ValueError("Invalid AI preference sync setting")

        context = self.client_contexts[client_uid]
        context.set_qwen_tts_options(
            voice=voice,
            language_hint=language_hint,
            instruction=instruction,
            notify_ai=notify_ai,
            sync_ai_preferences=sync_ai_preferences,
        )
        await context.refresh_character_system_prompt()
        await websocket.send_text(
            json.dumps(
                {
                    "type": "qwen-tts-options-updated",
                    "voice": voice,
                    "language_hint": language_hint,
                    "instruction": instruction,
                },
                ensure_ascii=False,
            )
        )

    async def _handle_set_generate_audio(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Enable or disable TTS generation for one client session."""
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Invalid generate-audio setting")

        self.client_contexts[client_uid].generate_audio = enabled

    async def _handle_set_max_history_turns(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Set the number of completed dialogue turns sent to the LLM."""
        max_history_turns = data.get("max_history_turns")
        if isinstance(max_history_turns, bool) or not isinstance(
            max_history_turns, int
        ):
            raise ValueError("max_history_turns must be an integer")
        if not 1 <= max_history_turns <= 100:
            raise ValueError("max_history_turns must be between 1 and 100")

        self.client_contexts[client_uid].set_max_history_turns(
            max_history_turns
        )

        await websocket.send_text(
            json.dumps(
                {
                    "type": "max-history-turns-updated",
                    "max_history_turns": max_history_turns,
                }
            )
        )

    async def _handle_fetch_backgrounds(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle fetching available background images"""
        bg_files = scan_bg_directory()
        await websocket.send_text(
            json.dumps({"type": "background-files", "files": bg_files})
        )

    async def _handle_init_config_request(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle request for initialization configuration"""
        context = self.client_contexts.get(client_uid)
        if not context:
            context = self.default_context_cache

        await websocket.send_text(
            json.dumps(
                {
                    "type": "set-model-and-conf",
                    "model_info": context.live2d_model.model_info,
                    "conf_name": context.character_config.conf_name,
                    "conf_uid": context.character_config.conf_uid,
                }
            )
        )

    async def _handle_set_rag_options(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        context = self.client_contexts[client_uid]
        try:
            context.set_rag_options(
                top_k=data.get("top_k", 5),
                threshold=data.get("threshold", 0.5),
                hybrid_weight=data.get("hybrid_weight", 0.5),
            )
        except (TypeError, ValueError) as exc:
            await websocket.send_text(
                json.dumps({"type": "error", "message": f"Invalid RAG settings: {exc}"})
            )
            return
        await websocket.send_text(
            json.dumps(
                {
                    "type": "rag-options-updated",
                    "top_k": context.rag_top_k,
                    "threshold": context.rag_threshold,
                    "hybrid_weight": context.rag_hybrid_weight,
                }
            )
        )

    async def _handle_heartbeat(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle heartbeat messages from clients"""
        try:
            await websocket.send_json({"type": "heartbeat-ack"})
        except Exception as e:
            logger.error(f"Error sending heartbeat acknowledgment: {e}")
