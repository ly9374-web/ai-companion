import asyncio
import json
import re
from typing import Dict, Optional

import numpy as np
from fastapi import WebSocket
from loguru import logger

from ..chat_history_manager import store_message
from ..service_context import ServiceContext
from .single_conversation import process_single_conversation
from .conversation_utils import EMOJI_LIST
from prompts import prompt_builder, prompt_loader


BROWSER_TIME_PATTERN = re.compile(
    r"^(?:[1-9]|1[0-2])月(?:[1-9]|[12]\d|3[01])日，"
    r"周[一二三四五六日]，(?:[0-9]|1\d|2[0-3])时[0-5]\d分$"
)


async def handle_conversation_trigger(
    msg_type: str,
    data: dict,
    client_uid: str,
    context: ServiceContext,
    websocket: WebSocket,
    received_data_buffers: Dict[str, np.ndarray],
    current_conversation_tasks: Dict[str, Optional[asyncio.Task]],
) -> None:
    """Handle triggers that start a conversation"""
    metadata = None

    if msg_type == "ai-speak-signal":
        try:
            # Get proactive speak prompt from config
            prompt_name = "proactive_speak_prompt"
            prompt_file = context.system_config.tool_prompts.get(prompt_name)
            if prompt_file:
                user_input = prompt_loader.load_util(prompt_file)
            else:
                logger.warning("Proactive speak prompt not configured, using default")
                user_input = prompt_builder.load_runtime_prompt(
                    "proactive_speak_fallback"
                )
        except Exception as e:
            logger.error(f"Error loading proactive speak prompt: {e}")
            user_input = prompt_builder.load_runtime_prompt(
                "proactive_speak_fallback"
            )

        # Add metadata to indicate this is a proactive speak request
        # that should be skipped in both memory and history
        metadata = {
            "proactive_speak": True,
            "skip_memory": True,  # Skip storing in AI's internal memory
            "skip_history": True,  # Skip storing in local conversation history
        }

        await websocket.send_text(
            json.dumps(
                {
                    "type": "full-text",
                    "text": "AI wants to speak something...",
                }
            )
        )
    elif msg_type == "text-input":
        user_input = data.get("text", "")
    else:  # mic-audio-end
        user_input = received_data_buffers[client_uid]
        received_data_buffers[client_uid] = np.array([])

    if msg_type != "ai-speak-signal":
        browser_time = data.get("browser_time", "")
        if isinstance(browser_time, str) and BROWSER_TIME_PATTERN.fullmatch(
            browser_time
        ):
            metadata = {"browser_time": browser_time}
        else:
            metadata = None
            logger.warning("Missing or invalid browser time for {}", msg_type)

    images = data.get("images")
    session_emoji = np.random.choice(EMOJI_LIST)

    current_conversation_tasks[client_uid] = asyncio.create_task(
        process_single_conversation(
            context=context,
            websocket_send=websocket.send_text,
            client_uid=client_uid,
            user_input=user_input,
            images=images,
            session_emoji=session_emoji,
            metadata=metadata,
        )
    )


async def handle_individual_interrupt(
    client_uid: str,
    current_conversation_tasks: Dict[str, Optional[asyncio.Task]],
    context: ServiceContext,
    heard_response: str,
):
    if client_uid in current_conversation_tasks:
        task = current_conversation_tasks[client_uid]
        if task and not task.done():
            task.cancel()
            logger.info("🛑 Conversation task was successfully interrupted")

        try:
            context.agent_engine.handle_interrupt(heard_response)
        except Exception as e:
            logger.error(f"Error handling interrupt: {e}")

        if context.history_uid:
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=heard_response,
                name=context.character_config.character_name,
                avatar=context.character_config.avatar,
            )
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="system",
                content=prompt_builder.load_runtime_prompt(
                    "interrupted_by_user"
                ),
            )
