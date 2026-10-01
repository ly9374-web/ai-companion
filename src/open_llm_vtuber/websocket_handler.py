from typing import Dict, List, Optional, Callable, TypedDict
from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import json
import re
from datetime import datetime
import numpy as np
from loguru import logger

from .service_context import ServiceContext
from .mobile_prompt_logs import LOG_CATEGORIES, read_prompts
from .generated_images import list_history_images
from .account_manager import (
    get_persisted_rag_options,
    get_persisted_minimax_api_key,
    update_persisted_last_state,
    update_persisted_rag_options,
)
from .message_handler import message_handler
from .human_profile_manager import HumanProfileManager
from prompts import prompt_builder
from .chat_history_manager import (
    extract_normal_turns,
    get_history,
    delete_history,
    get_history_list,
    get_recent_normal_history_messages,
    get_recent_normal_turns,
    render_history_message_for_frontend,
    store_message,
    undo_latest_chat_message,
)
from .config_manager.utils import scan_config_alts_directory, scan_bg_directory
from .config_manager.tts import QWEN_TTS_VOICES
from .optional_features import (
    collect_optional_analysis_data,
    optional_account_can_access_character,
    run_optional_character_switch_action,
)
from .conversations.history_session import open_new_history
from .profiler_session import (
    PROFILER_INTERIM_ROUNDS,
    ProfilerAnalysisManager,
    is_profiler_character,
)
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
    instruction_preset: Optional[str]
    notify_ai: Optional[bool]
    sync_ai_preferences: Optional[bool]
    browser_time: Optional[str]
    enabled: Optional[bool]
    max_history_turns: Optional[int]
    top_k: Optional[int]
    threshold: Optional[float]
    hybrid_weight: Optional[float]
    request_id: Optional[str]
    deepseek_api_key: Optional[str]
    grok_api_key: Optional[str]
    grok_enabled: Optional[bool]
    qwen_api_key: Optional[str]
    optional_contexts: Optional[dict]
    content: Optional[str]
    round: Optional[int]
    categories: Optional[List[str]]


class WebSocketHandler:
    """Handles WebSocket connections and message routing"""

    def __init__(self, default_context_cache: ServiceContext):
        """Initialize the WebSocket handler with default context"""
        self.client_contexts: Dict[str, ServiceContext] = {}
        self.current_conversation_tasks: Dict[str, Optional[asyncio.Task]] = {}
        self.persona_profile_tasks: Dict[tuple[str, str], asyncio.Task] = {}
        self.profiler_analysis_tasks: Dict[tuple, asyncio.Task] = {}
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
            "undo-last-message": self._handle_undo_last_message,
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
            "set-mobile-prompt-logging": self._handle_set_mobile_prompt_logging,
            "fetch-mobile-prompt-logs": self._handle_fetch_mobile_prompt_logs,
            "set-max-history-turns": self._handle_set_max_history_turns,
            "set-rag-options": self._handle_set_rag_options,
            "set-api-keys": self._handle_set_api_keys,
            "set-minimax-account-key": self._handle_set_minimax_account_key,
            "summarize-pending-memory": self._handle_manual_summary,
            "summarize-rolling-context": self._handle_debug_rolling_summary,
            "generate-persona-profile": self._handle_generate_persona_profile,
            "profiler-finalize": self._handle_profiler_finalize,
            "fetch-backgrounds": self._handle_fetch_backgrounds,
            "request-init-config": self._handle_init_config_request,
            "fetch-system-prompt": self._handle_fetch_system_prompt,
            "update-system-prompt": self._handle_update_system_prompt,
            "reset-system-prompt": self._handle_reset_system_prompt,
            "heartbeat": self._handle_heartbeat,
        }

    async def handle_new_connection(
        self, websocket: WebSocket, client_uid: str, account_name: str,
        mobile_image_only: bool = False,
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
                websocket.send_text, client_uid, account_name, mobile_image_only
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
                    "expression_dir": session_service_context.character_config.expression_dir,
                    "tts_voice": session_service_context.get_current_tts_voice(),
                }
            )
        )

        await websocket.send_text(
            json.dumps(
                {
                    "type": "rag-options-updated",
                    "top_k": session_service_context.rag_top_k,
                    "threshold": session_service_context.rag_threshold,
                    "hybrid_weight": session_service_context.rag_hybrid_weight,
                    "persisted": bool(
                        session_service_context.account_name
                        and get_persisted_rag_options(
                            session_service_context.account_name
                        )
                    ),
                }
            )
        )
        if session_service_context.mobile_image_only:
            await websocket.send_text(json.dumps({
                "type": "minimax-key-status",
                "configured": bool(get_persisted_minimax_api_key(
                    session_service_context.account_name
                )),
                "available": bool(
                    session_service_context.tool_manager
                    and session_service_context.tool_manager.get_tool("text_to_image")
                ),
            }))

        # Start microphone (disabled: user opens mic manually)
        # await websocket.send_text(json.dumps({"type": "control", "text": "start-mic"}))

    async def _init_service_context(
        self,
        send_text: Callable,
        client_uid: str,
        account_name: str | None = None,
        mobile_image_only: bool = False,
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
            account_name=account_name,
            mobile_image_only=mobile_image_only,
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
            "generate-persona-profile",
            "profiler-finalize",
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
                            "request_type": msg_type,
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
                            "request_type": msg_type,
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

    async def _handle_generate_persona_profile(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Start one full non-debug profile snapshot for the active account/role."""
        context = self.client_contexts[client_uid]
        conf_uid = context.character_config.conf_uid
        task_key = (context.history_root.resolve().as_posix(), conf_uid)
        existing_task = self.persona_profile_tasks.get(task_key)
        if existing_task is not None and not existing_task.done():
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "persona-profile-status",
                        "status": "duplicate",
                    }
                )
            )
            return

        generate_section = getattr(
            context.agent_engine, "generate_persona_profile_section", None
        )
        if not callable(generate_section):
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "persona-profile-status",
                        "status": "failed",
                        "error": "当前对话代理不支持人物侧写",
                    },
                    ensure_ascii=False,
                )
            )
            return

        async def send_status(payload: dict) -> None:
            message = {
                "type": "persona-profile-status",
                "status": "running",
                **payload,
            }
            try:
                await websocket.send_text(json.dumps(message, ensure_ascii=False))
            except Exception:
                logger.debug(
                    "Persona profile progress receiver disconnected for {} / {}",
                    context.account_name,
                    conf_uid,
                )

        async def run_profile() -> None:
            manager = HumanProfileManager()
            try:
                result = await manager.generate(
                    account_name=context.account_name,
                    conf_uid=conf_uid,
                    character_name=context.character_config.character_name,
                    history_root=context.history_root,
                    generate_section=generate_section,
                    build_chunk_input=prompt_builder.build_persona_profile_chunk_input,
                    build_consolidation_input=(
                        prompt_builder.build_persona_profile_consolidation_input
                    ),
                    build_final_input=prompt_builder.build_persona_profile_final_input,
                    progress=send_status,
                    source_max_bytes=getattr(
                        context.agent_engine,
                        "_persona_profile_source_max_bytes",
                        50_000,
                    ),
                )
                status = str(result.pop("status", "failed"))
                await send_status({"status": status, **result})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "Persona profile generation failed for {} / {}: {}",
                    context.account_name,
                    conf_uid,
                    exc,
                )
                await send_status({"status": "failed", "error": str(exc)})
            finally:
                current = self.persona_profile_tasks.get(task_key)
                if current is asyncio.current_task():
                    self.persona_profile_tasks.pop(task_key, None)

        await websocket.send_text(
            json.dumps(
                {
                    "type": "persona-profile-status",
                    "status": "accepted",
                }
            )
        )
        task = asyncio.create_task(run_profile())
        self.persona_profile_tasks[task_key] = task

    async def _handle_profiler_finalize(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Generate a profiler report after the simulated crisis is resolved."""
        context = self.client_contexts[client_uid]
        history_uid = context.history_uid
        if not is_profiler_character(context.character_config.conf_uid) or not history_uid:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "profiler-analysis-status",
                        "status": "failed",
                        "error": "当前会话不是可生成报告的侧写师会话",
                    },
                    ensure_ascii=False,
                )
            )
            return

        requested_round = data.get("round")
        interim_round = (
            requested_round
            if isinstance(requested_round, int)
            and requested_round in PROFILER_INTERIM_ROUNDS
            else None
        )

        task_key = (
            context.history_root.resolve().as_posix(),
            history_uid,
            interim_round,
        )
        existing_task = self.profiler_analysis_tasks.get(task_key)
        if existing_task is not None and not existing_task.done():
            await websocket.send_text(
                json.dumps(
                    {"type": "profiler-analysis-status", "status": "duplicate"},
                    ensure_ascii=False,
                )
            )
            return

        generate_section = getattr(
            context.agent_engine, "generate_persona_profile_section", None
        )
        if not callable(generate_section):
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "profiler-analysis-status",
                        "status": "failed",
                        "error": "当前对话代理不支持 DeepSeek 侧写分析",
                    },
                    ensure_ascii=False,
                )
            )
            return

        final_listening_data = collect_optional_analysis_data(
            data.get("optional_contexts"),
            context,
        )

        async def send_status(payload: dict) -> None:
            if interim_round is not None:
                payload.setdefault("round", interim_round)
            try:
                await websocket.send_text(
                    json.dumps(
                        {"type": "profiler-analysis-status", **payload},
                        ensure_ascii=False,
                    )
                )
            except Exception:
                logger.debug(
                    "Profiler analysis receiver disconnected for {} / {}",
                    context.account_name,
                    history_uid,
                )

        async def run_analysis() -> None:
            manager = ProfilerAnalysisManager()
            try:
                await send_status({"status": "running", "progress": 10})
                result = await manager.generate(
                    account_name=context.account_name,
                    history_uid=history_uid,
                    history_root=context.history_root,
                    final_listening_data=final_listening_data,
                    generate=lambda user_prompt: generate_section(
                        "profiler_thinslice", user_prompt
                    ),
                    interim_round=interim_round,
                )
                await send_status({"progress": 100, **result})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "Profiler analysis failed for {} / {}: {}",
                    context.account_name,
                    history_uid,
                    exc,
                )
                await send_status({"status": "failed", "error": str(exc)})
            finally:
                current = self.profiler_analysis_tasks.get(task_key)
                if current is asyncio.current_task():
                    self.profiler_analysis_tasks.pop(task_key, None)

        await send_status({"status": "accepted", "progress": 0})
        task = asyncio.create_task(run_analysis())
        self.profiler_analysis_tasks[task_key] = task

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

    async def _rebuild_cancelled_summaries(
        self,
        context: ServiceContext,
        history_uid: str,
        cancelled_labels: set[str],
        browser_time: str = "",
    ) -> None:
        """Re-run cancelled summary work from the latest six complete turns."""
        if not cancelled_labels:
            return

        rebuild_all = "undo-rebuild" in cancelled_labels
        needs_rolling = rebuild_all or any(
            label.startswith("rolling") for label in cancelled_labels
        )
        needs_memory = rebuild_all or "manual" in cancelled_labels or any(
            label.startswith("long_term_memory") for label in cancelled_labels
        )
        needs_short_relationship = (
            rebuild_all
            or "manual" in cancelled_labels
            or any(
                label.startswith("short_term_relationship")
                for label in cancelled_labels
            )
        )

        conf_uid = context.character_config.conf_uid
        recent_turns = get_recent_normal_turns(
            conf_uid,
            6,
            context.history_root,
        )
        current_turns = [
            {
                "user": str(turn["user"].get("content", "")).strip(),
                "assistant": str(turn["assistant"].get("content", "")).strip(),
            }
            for turn in extract_normal_turns(
                get_history(conf_uid, history_uid, context.history_root)
            )[-6:]
        ]

        if needs_rolling and current_turns:
            summarize_rolling = getattr(
                context.agent_engine, "summarize_rolling_context", None
            )
            if summarize_rolling is not None:
                await context.rolling_summary_manager.regenerate_recent_turns(
                    conf_uid,
                    history_uid,
                    current_turns,
                    summarize_rolling,
                )

        if needs_memory and recent_turns:
            summarize_memory = getattr(
                context.agent_engine, "summarize_long_term_memory", None
            )
            reconcile_memory = getattr(
                context.agent_engine, "reconcile_long_term_memory", None
            )
            if summarize_memory is not None and reconcile_memory is not None:
                character_system_prompt = context.get_editable_system_prompt()

                async def summarize_with_character_prompt(turns):
                    return await summarize_memory(
                        turns,
                        character_system_prompt,
                        browser_time,
                    )

                await context.long_term_memory_manager.replace_pending_turns(
                    conf_uid, history_uid, recent_turns
                )
                await context.long_term_memory_manager.summarize_pending_turns(
                    conf_uid=conf_uid,
                    history_uid=history_uid,
                    summarize=summarize_with_character_prompt,
                    reconcile=reconcile_memory,
                )

        if needs_short_relationship and recent_turns:
            summarize_short = getattr(
                context.agent_engine,
                "summarize_short_term_relationship",
                None,
            )
            if summarize_short is not None:
                await context.short_term_relationship_manager.replace_pending_turns(
                    conf_uid, history_uid, recent_turns
                )
                await context.short_term_relationship_manager.summarize_pending_turns(
                    conf_uid=conf_uid,
                    history_uid=history_uid,
                    summarize=summarize_short,
                    recent_turns_override=recent_turns,
                )

    async def _handle_undo_last_message(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Cancel active work and remove one latest user/assistant message."""
        context = self.client_contexts[client_uid]
        history_uid = context.history_uid
        if not history_uid:
            await websocket.send_text(
                json.dumps({"type": "undo-last-message-result", "success": False})
            )
            return

        task = self.current_conversation_tasks.get(client_uid)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.current_conversation_tasks[client_uid] = None

        conf_uid = context.character_config.conf_uid
        current_messages = get_history(
            conf_uid, history_uid, context.history_root
        )
        completed_turns_before = extract_normal_turns(current_messages)
        if not any(
            message.get("role") in {"human", "ai"}
            for message in current_messages
        ):
            await websocket.send_text(
                json.dumps({"type": "undo-last-message-result", "success": False})
            )
            return

        cancelled_labels = await context.summary_coordinator.cancel_all()
        removed = undo_latest_chat_message(
            conf_uid, history_uid, context.history_root
        )
        if removed is None:
            await websocket.send_text(
                json.dumps({"type": "undo-last-message-result", "success": False})
            )
            return

        current_messages = get_history(
            conf_uid, history_uid, context.history_root
        )
        completed_turns_after = extract_normal_turns(current_messages)
        if len(completed_turns_after) < len(completed_turns_before):
            withdrawn_turn = completed_turns_before[-1]
            withdrawn_turn_payload = {
                "user": str(
                    withdrawn_turn["user"].get("content", "")
                ).strip(),
                "assistant": str(
                    withdrawn_turn["assistant"].get("content", "")
                ).strip(),
            }
            await context.long_term_memory_manager.discard_pending_turn(
                conf_uid, history_uid, withdrawn_turn_payload
            )
            await context.short_term_relationship_manager.discard_pending_turn(
                conf_uid, history_uid, withdrawn_turn_payload
            )
            await context.current_relationship_score_manager.discard_pending_turn(
                conf_uid, history_uid, withdrawn_turn_payload
            )
        recent_messages = []
        if not context.isolated_conversation_context:
            recent_messages = get_recent_normal_history_messages(
                conf_uid,
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
                conf_uid=conf_uid,
                history_uid=history_uid,
                history_root=context.history_root,
            )

        if cancelled_labels:
            browser_time = data.get("browser_time", "")
            if not isinstance(browser_time, str):
                browser_time = ""
            context.summary_coordinator.enqueue(
                "undo-rebuild",
                lambda: self._rebuild_cancelled_summaries(
                    context,
                    history_uid,
                    cancelled_labels,
                    browser_time,
                ),
            )

        await websocket.send_text(
            json.dumps(
                {
                    "type": "undo-last-message-result",
                    "success": True,
                    "removed_role": removed.get("role"),
                    "messages": [
                        render_history_message_for_frontend(message)
                        for message in current_messages
                        if message.get("role") != "system"
                    ],
                    "histories": get_history_list(
                        conf_uid, context.history_root
                    ),
                    "summary_rebuild_started": bool(cancelled_labels),
                }
            )
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
        context.english_mode = False
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
        if context.mobile_image_only:
            for message in messages:
                try:
                    message["sort_time"] = datetime.fromisoformat(
                        message["timestamp"]
                    ).timestamp()
                except (KeyError, TypeError, ValueError):
                    pass
        await websocket.send_text(
            json.dumps({
                "type": "history-data",
                "history_uid": history_uid,
                "messages": messages,
                "generated_images": list_history_images(
                    context.account_name,
                    context.character_config.conf_uid,
                    history_uid,
                ) if context.mobile_image_only else [],
            })
        )

    async def _handle_create_history(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle creation of new chat history"""
        context = self.client_contexts[client_uid]
        history_uid, current_messages = open_new_history(context)
        if history_uid:
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
        scanned_configs = scan_config_alts_directory(
            context.system_config.config_alts_dir
        )
        config_files = [
            {"filename": config["filename"], "name": config["name"]}
            for config in scanned_configs
            if optional_account_can_access_character(
                context.account_name,
                config.get("conf_uid"),
            )
        ]
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
            selected_config = next(
                (
                    config
                    for config in scan_config_alts_directory(
                        context.system_config.config_alts_dir
                    )
                    if config.get("filename") == config_file_name
                ),
                None,
            )
            if selected_config and not optional_account_can_access_character(
                context.account_name,
                selected_config.get("conf_uid"),
            ):
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "message": "当前账号无法使用该角色",
                        }
                    )
                )
                return
            await context.handle_config_switch(websocket, config_file_name)
            if selected_config:
                update_persisted_last_state(
                    context.account_name, role_file=config_file_name
                )
            optional_payload = await run_optional_character_switch_action(context)
            if optional_payload:
                await websocket.send_text(json.dumps(optional_payload))

    async def _handle_set_tts_voice(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Update the Qwen voice for one client session."""
        voice = data.get("voice")
        if not isinstance(voice, str) or voice not in QWEN_TTS_VOICES:
            raise ValueError("Unsupported Qwen-Audio Flash voice")

        context = self.client_contexts[client_uid]
        context.set_tts_voice(voice)
        update_persisted_last_state(context.account_name, voice=voice)
        await websocket.send_text(
            json.dumps({"type": "tts-voice-updated", "voice": voice})
        )

    async def _handle_set_qwen_tts_options(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Update Qwen voice and instruction for one session."""
        voice = data.get("voice")
        instruction = data.get("instruction")
        instruction_preset = data.get("instruction_preset")
        notify_ai = data.get("notify_ai", False)
        sync_ai_preferences = data.get("sync_ai_preferences", False)
        if not isinstance(voice, str) or voice not in QWEN_TTS_VOICES:
            raise ValueError("Unsupported Qwen-Audio Flash voice")
        if not isinstance(instruction, str) or len(instruction) > 2000:
            raise ValueError("Invalid Qwen TTS instruction")
        if instruction_preset is not None and (
            not isinstance(instruction_preset, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", instruction_preset) is None
        ):
            raise ValueError("Invalid Qwen TTS instruction preset")
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
        update_persisted_last_state(
            context.account_name,
            voice=voice,
            # The current desktop client has only one nonempty preset and
            # sends its instruction text without the preset key.
            instruction_preset=(
                instruction_preset
                if instruction_preset is not None
                else "none" if not instruction else "instruction1"
            ),
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
        minimax_api_key = data.get("minimax_api_key", "")
        deepseek_model = data.get("deepseek_model")
        if not isinstance(deepseek_api_key, str) or len(deepseek_api_key) > 4096:
            raise ValueError("Invalid DeepSeek API key")
        if not isinstance(grok_api_key, str) or len(grok_api_key) > 4096:
            raise ValueError("Invalid Grok API key")
        if not isinstance(grok_enabled, bool):
            raise ValueError("Invalid Grok enabled state")
        if not isinstance(qwen_api_key, str) or len(qwen_api_key) > 4096:
            raise ValueError("Invalid Qwen API key")
        if not isinstance(minimax_api_key, str) or len(minimax_api_key) > 4096:
            raise ValueError("Invalid MiniMax API key")
        if self.client_contexts[client_uid].mobile_image_only:
            minimax_api_key = ""
        if deepseek_model is not None and (
            not isinstance(deepseek_model, str)
            or deepseek_model not in ("deepseek-v4-pro", "deepseek-v4-flash")
        ):
            raise ValueError("Invalid DeepSeek model")

        await self.client_contexts[client_uid].set_runtime_api_keys(
            deepseek_api_key=deepseek_api_key.strip(),
            grok_api_key=grok_api_key.strip(),
            grok_enabled=grok_enabled,
            qwen_api_key=qwen_api_key.strip(),
            minimax_api_key=minimax_api_key,
            deepseek_model=deepseek_model,
        )
        await websocket.send_text(json.dumps({"type": "api-keys-updated"}))

    async def _handle_set_minimax_account_key(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        context = self.client_contexts[client_uid]
        if context.mobile_image_only:
            raise ValueError("MiniMax key can only be configured on the desktop page")
        key = data.get("minimax_api_key")
        if not isinstance(key, str) or len(key) > 4096:
            raise ValueError("Invalid MiniMax API key")
        await context.set_minimax_account_key(key.strip())
        for other_uid, other_context in list(self.client_contexts.items()):
            if (
                other_uid != client_uid
                and other_context.account_name == context.account_name
                and other_context.mobile_image_only
            ):
                await other_context.refresh_minimax_account_key()
                if other_context.send_text:
                    await other_context.send_text(json.dumps({
                        "type": "minimax-key-status",
                        "configured": bool(key.strip()),
                        "available": bool(
                            other_context.tool_manager
                            and other_context.tool_manager.get_tool("text_to_image")
                        ),
                    }))
        await websocket.send_text(json.dumps({"type": "minimax-account-key-updated"}))

    async def _handle_manual_summary(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Summarize pending completed turns without changing injection timing."""
        context = self.client_contexts[client_uid]
        if context.debug_mode or context.isolated_conversation_context:
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

        character_system_prompt = context.get_editable_system_prompt()

        async def summarize_memory_with_character_prompt(turns):
            return await summarize_memory(
                turns,
                character_system_prompt,
                browser_time,
            )

        async def run_manual_summary() -> tuple[str, str]:
            memory_result = (
                await context.long_term_memory_manager.summarize_pending_turns(
                    conf_uid=conf_uid,
                    history_uid=history_uid,
                    summarize=summarize_memory_with_character_prompt,
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

    async def _handle_set_mobile_prompt_logging(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Invalid mobile prompt logging setting")
        context = self.client_contexts[client_uid]
        context.mobile_prompt_logging = enabled
        await websocket.send_text(
            json.dumps({"type": "mobile-prompt-logging-updated", "enabled": enabled})
        )

    async def _handle_fetch_mobile_prompt_logs(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        categories = data.get("categories")
        if (
            not isinstance(categories, list)
            or any(
                not isinstance(item, str) or item not in LOG_CATEGORIES
                for item in categories
            )
        ):
            raise ValueError("Invalid mobile prompt log categories")
        context = self.client_contexts[client_uid]
        result = read_prompts(
            context.account_name,
            context.character_config.conf_uid,
            context.history_uid,
            set(categories),
        )
        await websocket.send_text(
            json.dumps({"type": "mobile-prompt-logs", **result}, ensure_ascii=False)
        )

    async def _handle_fetch_system_prompt(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Return the editable section of the active character's system prompt."""
        context = self.client_contexts[client_uid]
        try:
            content = context.get_editable_system_prompt()
        except Exception as exc:
            logger.error("Failed to fetch editable system prompt: {}", exc)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "system-prompt",
                        "error": str(exc),
                    }
                )
            )
            return
        await websocket.send_text(
            json.dumps({"type": "system-prompt", "content": content})
        )

    async def _handle_update_system_prompt(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Persist the edited editable section and apply it to the agent."""
        content = data.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if not content.strip():
            raise ValueError("content cannot be empty")

        context = self.client_contexts[client_uid]
        try:
            context.set_system_prompt_override(content)
        except Exception as exc:
            logger.error("Failed to update system prompt override: {}", exc)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "system-prompt-updated",
                        "success": False,
                        "error": str(exc),
                    }
                )
            )
            return
        await websocket.send_text(
            json.dumps({"type": "system-prompt-updated", "success": True})
        )

    async def _handle_reset_system_prompt(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Remove the override file and restore the default system prompt."""
        context = self.client_contexts[client_uid]
        try:
            default_content = context.reset_system_prompt_override()
        except Exception as exc:
            logger.error("Failed to reset system prompt override: {}", exc)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "system-prompt-reset",
                        "success": False,
                        "error": str(exc),
                    }
                )
            )
            return
        await websocket.send_text(
            json.dumps(
                {
                    "type": "system-prompt-reset",
                    "success": True,
                    "content": default_content,
                }
            )
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
                    "expression_dir": context.character_config.expression_dir,
                    "tts_voice": context.get_current_tts_voice(),
                }
            )
        )

    async def _handle_set_rag_options(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        context = self.client_contexts[client_uid]
        request_id = data.get("request_id")
        response_id = {"request_id": request_id} if isinstance(request_id, str) else {}
        previous_rag = (
            context.rag_top_k,
            context.rag_threshold,
            context.rag_hybrid_weight,
        )
        try:
            context.set_rag_options(
                top_k=data.get("top_k", 5),
                threshold=data.get("threshold", 0.5),
                hybrid_weight=data.get("hybrid_weight", 0.5),
            )
        except (TypeError, ValueError) as exc:
            await websocket.send_text(
                json.dumps({"type": "error", "message": f"Invalid RAG settings: {exc}", **response_id})
            )
            return
        if context.account_name:
            try:
                update_persisted_rag_options(
                    context.account_name,
                    top_k=context.rag_top_k,
                    threshold=context.rag_threshold,
                    hybrid_weight=context.rag_hybrid_weight,
                )
            except Exception:
                logger.exception(
                    "Failed to persist RAG options for account {}",
                    context.account_name,
                )
                (
                    context.rag_top_k,
                    context.rag_threshold,
                    context.rag_hybrid_weight,
                ) = previous_rag
                context.refresh_rag_options()
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "Failed to save RAG settings", **response_id})
                )
                return
        await websocket.send_text(
            json.dumps(
                {
                    "type": "rag-options-updated",
                    "top_k": context.rag_top_k,
                    "threshold": context.rag_threshold,
                    "hybrid_weight": context.rag_hybrid_weight,
                    **response_id,
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
