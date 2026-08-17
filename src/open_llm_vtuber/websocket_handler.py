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
    get_recent_normal_history_messages,
    render_history_message_for_frontend,
    store_message,
)
from .conversation_starters import WELCOME_MESSAGE
from .config_manager.utils import scan_config_alts_directory, scan_bg_directory
from .config_manager.tts import QWEN_TTS_VOICES
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
    instruction: Optional[str]
    notify_ai: Optional[bool]
    sync_ai_preferences: Optional[bool]
    browser_time: Optional[str]
    enabled: Optional[bool]
    max_history_turns: Optional[int]
    top_k: Optional[int]
    threshold: Optional[float]
    hybrid_weight: Optional[float]
    deepseek_api_key: Optional[str]
    grok_api_key: Optional[str]
    grok_enabled: Optional[bool]
    qwen_api_key: Optional[str]
    optional_contexts: Optional[dict]
    quick_start_topic: Optional[str]


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
            "set-debug-mode": self._handle_set_debug_mode,
            "set-max-history-turns": self._handle_set_max_history_turns,
            "set-rag-options": self._handle_set_rag_options,
            "set-api-keys": self._handle_set_api_keys,
            "summarize-pending-memory": self._handle_manual_summary,
            "summarize-rolling-context": self._handle_debug_rolling_summary,
            "fetch-backgrounds": self._handle_fetch_backgrounds,
            "request-init-config": self._handle_init_config_request,
            "heartbeat": self._handle_heartbeat,
        }

    async def handle_new_connection(
        self, websocket: WebSocket, client_uid: str, account_name: str
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
                websocket.send_text, client_uid, account_name
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
        self,
        send_text: Callable,
        client_uid: str,
        account_name: str | None = None,
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
        if account_name:
            session_service_context.configure_account(account_name)
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

        if msg_type in {
            "text-input",
            "mic-audio-end",
            "ai-speak-signal",
            "summarize-pending-memory",
            "summarize-rolling-context",
        }:
            context = self.client_contexts.get(client_uid)
            if context is not None and not context.has_deepseek_api_key():
                if msg_type == "mic-audio-end":
                    self.received_data_buffers[client_uid] = np.array([], dtype=np.float32)
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "api-key-required",
                            "provider": "deepseek",
                        }
                    )
                )
                return

            if (
                msg_type in {
                    "text-input",
                    "mic-audio-end",
                    "ai-speak-signal",
                }
                and context is not None
                and context.grok_enabled
                and not context.has_grok_api_key()
            ):
                if msg_type == "mic-audio-end":
                    self.received_data_buffers[client_uid] = np.array([], dtype=np.float32)
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "api-key-required",
                            "provider": "grok",
                        }
                    )
                )
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
        histories = get_history_list(
            context.character_config.conf_uid,
            context.history_root,
        )
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
            history_root=context.history_root,
        )

        messages = [
            render_history_message_for_frontend(msg)
            for msg in get_history(
                context.character_config.conf_uid,
                history_uid,
                context.history_root,
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
        history_uid = create_new_history(
            context.character_config.conf_uid,
            context.history_root,
        )
        if history_uid:
            context.history_uid = history_uid
            if context.conversation_starters_enabled:
                store_message(
                    conf_uid=context.character_config.conf_uid,
                    history_uid=history_uid,
                    role="ai",
                    content=WELCOME_MESSAGE,
                    name=context.character_config.character_name,
                    avatar=context.character_config.avatar,
                    history_root=context.history_root,
                )
            current_messages = get_history(
                context.character_config.conf_uid,
                history_uid,
                context.history_root,
            )
            recent_messages = get_recent_normal_history_messages(
                context.character_config.conf_uid,
                context.max_history_turns,
                context.history_root,
                exclude_history_uid=history_uid,
            )
            set_memory_from_messages = getattr(
                context.agent_engine, "set_memory_from_messages", None
            )
            if set_memory_from_messages is not None:
                set_memory_from_messages(recent_messages + current_messages)
            else:
                context.agent_engine.set_memory_from_history(
                    conf_uid=context.character_config.conf_uid,
                    history_uid=history_uid,
                    history_root=context.history_root,
                )
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "new-history-created",
                        "history_uid": history_uid,
                        "messages": [
                            render_history_message_for_frontend(message)
                            for message in current_messages
                        ],
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
            context.history_root,
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
        """Update Qwen voice and instruction for one session."""
        voice = data.get("voice")
        instruction = data.get("instruction")
        notify_ai = data.get("notify_ai", False)
        sync_ai_preferences = data.get("sync_ai_preferences", False)
        if not isinstance(voice, str) or voice not in QWEN_TTS_VOICES:
            raise ValueError("Unsupported Qwen-Audio Flash voice")
        if not isinstance(instruction, str) or len(instruction) > 2000:
            raise ValueError("Invalid Qwen TTS instruction")
        if not isinstance(notify_ai, bool):
            raise ValueError("Invalid notify-ai setting")
        if not isinstance(sync_ai_preferences, bool):
            raise ValueError("Invalid AI preference sync setting")

        context = self.client_contexts[client_uid]
        context.set_qwen_tts_options(
            voice=voice,
            instruction=instruction,
            notify_ai=notify_ai,
            sync_ai_preferences=sync_ai_preferences,
        )
        await websocket.send_text(
            json.dumps(
                {
                    "type": "qwen-tts-options-updated",
                    "voice": voice,
                    "instruction": instruction,
                },
                ensure_ascii=False,
            )
        )

    async def _handle_set_api_keys(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Apply browser-stored API keys to this client session only."""
        deepseek_api_key = data.get("deepseek_api_key")
        grok_api_key = data.get("grok_api_key")
        grok_enabled = data.get("grok_enabled")
        qwen_api_key = data.get("qwen_api_key")
        if not isinstance(deepseek_api_key, str) or len(deepseek_api_key) > 4096:
            raise ValueError("Invalid DeepSeek API key")
        if not isinstance(grok_api_key, str) or len(grok_api_key) > 4096:
            raise ValueError("Invalid Grok API key")
        if not isinstance(grok_enabled, bool):
            raise ValueError("Invalid Grok enabled state")
        if not isinstance(qwen_api_key, str) or len(qwen_api_key) > 4096:
            raise ValueError("Invalid Qwen API key")

        self.client_contexts[client_uid].set_runtime_api_keys(
            deepseek_api_key=deepseek_api_key.strip(),
            grok_api_key=grok_api_key.strip(),
            grok_enabled=grok_enabled,
            qwen_api_key=qwen_api_key.strip(),
        )
        await websocket.send_text(json.dumps({"type": "api-keys-updated"}))

    async def _handle_manual_summary(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Summarize pending completed turns without changing injection timing."""
        context = self.client_contexts[client_uid]
        if context.debug_mode:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "manual-summary-result",
                        "long_term_memory": "disabled",
                        "short_term_relationship": "disabled",
                    }
                )
            )
            return
        current_task = self.current_conversation_tasks.get(client_uid)
        current_turn_pending = int(
            current_task is not None
            and not current_task.done()
            and bool(context.history_uid)
        )
        natural_summary_pending = False
        if context.history_uid:
            conf_uid = context.character_config.conf_uid
            memory_state = context.long_term_memory_manager._get_state(
                conf_uid, context.history_uid
            )
            relationship_state = context.short_term_relationship_manager._get_state(
                conf_uid, context.history_uid
            )
            memory_pending = memory_state.get("pending_turns", [])
            relationship_pending = relationship_state.get("pending_turns", [])
            memory_pending_count = (
                len(memory_pending) if isinstance(memory_pending, list) else 0
            )
            relationship_pending_count = (
                len(relationship_pending)
                if isinstance(relationship_pending, list)
                else 0
            )
            natural_summary_pending = (
                context.summary_coordinator.has_prefix(
                    ("long_term_memory", "short_term_relationship")
                )
                or memory_pending_count + current_turn_pending
                >= context.long_term_memory_manager.summary_interval
                or relationship_pending_count + current_turn_pending
                >= context.short_term_relationship_manager.update_interval
            )
        if (
            natural_summary_pending
            or context.summary_coordinator.has_any({"manual"})
        ):
            await websocket.send_text(
                json.dumps({"type": "manual-summary-duplicate"})
            )
            return
        history_uid = context.history_uid
        if not history_uid:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "manual-summary-result",
                        "long_term_memory": "empty",
                        "short_term_relationship": "empty",
                    }
                )
            )
            return

        summarize_memory = getattr(
            context.agent_engine, "summarize_long_term_memory", None
        )
        reconcile_memory = getattr(
            context.agent_engine, "reconcile_long_term_memory", None
        )
        summarize_short_relationship = getattr(
            context.agent_engine, "summarize_short_term_relationship", None
        )
        conf_uid = context.character_config.conf_uid
        browser_time = data.get("browser_time", "")
        if not isinstance(browser_time, str):
            browser_time = ""

        async def run_manual_summary() -> tuple[str, str]:
            memory_result = (
                await context.long_term_memory_manager.summarize_pending_turns(
                    conf_uid=conf_uid,
                    history_uid=history_uid,
                    summarize=summarize_memory,
                    reconcile=reconcile_memory,
                )
                if summarize_memory is not None and reconcile_memory is not None
                else "unsupported"
            )
            relationship_result = (
                await context.short_term_relationship_manager.summarize_pending_turns(
                    conf_uid=conf_uid,
                    history_uid=history_uid,
                    summarize=summarize_short_relationship,
                    browser_time=browser_time,
                )
                if summarize_short_relationship is not None
                else "unsupported"
            )
            return memory_result, relationship_result

        future = context.summary_coordinator.enqueue("manual", run_manual_summary)

        async def send_result() -> None:
            try:
                result = await future
            except asyncio.CancelledError:
                return
            if not isinstance(result, tuple) or len(result) != 2:
                memory_result, relationship_result = "error", "error"
            else:
                memory_result, relationship_result = result
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "manual-summary-result",
                        "long_term_memory": memory_result,
                        "short_term_relationship": relationship_result,
                    }
                )
            )

        asyncio.create_task(send_result())

    async def _handle_debug_rolling_summary(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Run an on-demand rolling summary while debug mode is enabled."""
        context = self.client_contexts[client_uid]
        if not context.debug_mode:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "debug-rolling-summary-result",
                        "status": "disabled",
                    }
                )
            )
            return
        if context.summary_coordinator.has_prefix(("rolling",)):
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "debug-rolling-summary-result",
                        "status": "duplicate",
                    }
                )
            )
            return

        history_uid = context.history_uid
        summarize = getattr(
            context.agent_engine, "summarize_rolling_context", None
        )
        if not history_uid:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "debug-rolling-summary-result",
                        "status": "empty",
                    }
                )
            )
            return
        if summarize is None:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "debug-rolling-summary-result",
                        "status": "error",
                    }
                )
            )
            return

        conf_uid = context.character_config.conf_uid
        future = context.summary_coordinator.enqueue(
            "rolling_debug",
            lambda: context.rolling_summary_manager.generate_next_batch(
                conf_uid=conf_uid,
                history_uid=history_uid,
                batch_size=context.max_history_turns,
                summarize=summarize,
                force=True,
            ),
        )

        async def send_result() -> None:
            try:
                saved = await future
            except asyncio.CancelledError:
                return
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "debug-rolling-summary-result",
                        "status": "success" if saved else "error",
                    }
                )
            )

        asyncio.create_task(send_result())

    async def _handle_set_generate_audio(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Enable or disable TTS generation for one client session."""
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Invalid generate-audio setting")

        self.client_contexts[client_uid].generate_audio = enabled

    async def _handle_set_debug_mode(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Skip persistent memory and relationship summaries for one session."""
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Invalid debug-mode setting")

        self.client_contexts[client_uid].debug_mode = enabled
        await websocket.send_text(
            json.dumps({"type": "debug-mode-updated", "enabled": enabled})
        )

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
        context = self.client_contexts[client_uid]
        summarize_rolling = getattr(
            context.agent_engine, "summarize_rolling_context", None
        )
        if (
            context.history_uid
            and not context.debug_mode
            and summarize_rolling is not None
            and not context.summary_coordinator.has_prefix(("rolling",))
        ):
            context.summary_coordinator.enqueue(
                f"rolling:{context.history_uid}:resize",
                lambda conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                batch_size=max_history_turns,
                callback=summarize_rolling: (
                    context.rolling_summary_manager.generate_ready_batches(
                        conf_uid=conf_uid,
                        history_uid=history_uid,
                        batch_size=batch_size,
                        summarize=callback,
                    )
                ),
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
