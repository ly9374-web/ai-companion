from typing import Union, List, Dict, Any, Optional
import asyncio
import json
from loguru import logger
import numpy as np

from prompts import prompt_builder

from .conversation_utils import (
    create_batch_input,
    process_agent_output,
    send_conversation_start_signals,
    process_user_input,
    finalize_conversation_turn,
    cleanup_conversation,
    EMOJI_LIST,
)
from .types import WebSocketSend
from .tts_manager import TTSTaskManager
from ..chat_history_manager import (
    get_history,
    get_metadata,
    store_message,
    update_metadate,
)
from ..long_term_memory_manager import long_term_memory_manager
from ..long_term_relationship_manager import long_term_relationship_manager
from ..short_term_relationship_manager import short_term_relationship_manager
from ..service_context import ServiceContext

# Import necessary types from agent outputs
from ..agent.output_types import SentenceOutput, AudioOutput, DisplayText, Actions


TIME_REQUEST_COMMANDS = {
    "发送时间",
    "现在几点",
    "现在几点了",
    "几点了",
    "现在是什么时间",
    "当前时间",
    "今天几号",
    "今天几月几号",
    "今天星期几",
    "今天周几",
    "send the time",
    "what time is it",
    "what's the time",
    "what is the time",
    "what's the date",
    "what is today's date",
    "what day is it",
}
CONTEXT_INJECTION_SCHEDULE_METADATA_KEY = "context_injection_schedule"
CONTEXT_INJECTION_KEYS = (
    "long_term_relationship_context",
    "short_term_relationship_context",
)


def _is_time_request(input_text: str) -> bool:
    normalized = input_text.strip().casefold().rstrip("。.!！?？").strip()
    return normalized in TIME_REQUEST_COMMANDS


def _is_first_turn(conf_uid: str, history_uid: str) -> bool:
    messages = get_history(conf_uid, history_uid)
    return not any(message.get("role") in {"human", "ai"} for message in messages)


def _get_completed_context_turns(
    conf_uid: str,
    history_uid: str,
) -> tuple[int, bool]:
    """Return the unified completed-turn count and whether it already existed."""
    metadata = get_metadata(conf_uid, history_uid)
    state = metadata.get(CONTEXT_INJECTION_SCHEDULE_METADATA_KEY, {})
    if isinstance(state, dict):
        completed_turns = state.get("completed_turns")
        if (
            not isinstance(completed_turns, bool)
            and isinstance(completed_turns, int)
            and completed_turns >= 0
        ):
            return completed_turns, True

    messages = get_history(conf_uid, history_uid)
    user_turns = sum(message.get("role") == "human" for message in messages)
    assistant_turns = sum(message.get("role") == "ai" for message in messages)
    completed_turns = min(user_turns, assistant_turns)
    update_metadate(
        conf_uid,
        history_uid,
        {
            CONTEXT_INJECTION_SCHEDULE_METADATA_KEY: {
                "completed_turns": completed_turns,
            }
        },
    )
    return completed_turns, False


def _save_completed_context_turns(
    conf_uid: str,
    history_uid: str,
    completed_turns: int,
) -> None:
    """Persist the unified count after one complete user-assistant turn."""
    if not update_metadate(
        conf_uid,
        history_uid,
        {
            CONTEXT_INJECTION_SCHEDULE_METADATA_KEY: {
                "completed_turns": completed_turns,
            }
        },
    ):
        logger.error(
            "Failed to persist unified context turn count for history {}",
            history_uid,
        )


async def process_single_conversation(
    context: ServiceContext,
    websocket_send: WebSocketSend,
    client_uid: str,
    user_input: Union[str, np.ndarray],
    images: Optional[List[Dict[str, Any]]] = None,
    session_emoji: str = np.random.choice(EMOJI_LIST),
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Process a single-user conversation turn

    Args:
        context: Service context containing all configurations and engines
        websocket_send: WebSocket send function
        client_uid: Client unique identifier
        user_input: Text or audio input from user
        images: Optional list of image data
        session_emoji: Emoji identifier for the conversation
        metadata: Optional metadata for special processing flags

    Returns:
        str: Complete response text
    """
    # Create TTSTaskManager for this conversation
    tts_manager = TTSTaskManager()
    full_response = ""  # Initialize full_response here

    try:
        # Send initial signals
        await send_conversation_start_signals(websocket_send)
        logger.info(f"New Conversation Chain {session_emoji} started!")

        # Process user input
        input_text = await process_user_input(
            user_input, context.asr_engine, websocket_send
        )

        skip_history = metadata and metadata.get("skip_history", False)
        request_metadata = dict(metadata or {})
        browser_time = request_metadata.get("browser_time", "")
        is_first_turn = False
        completed_context_turns = 0
        context_schedule_initialized = False
        if context.history_uid and not skip_history:
            is_first_turn = _is_first_turn(
                context.character_config.conf_uid,
                context.history_uid,
            )
            (
                completed_context_turns,
                context_schedule_initialized,
            ) = _get_completed_context_turns(
                context.character_config.conf_uid,
                context.history_uid,
            )

        next_turn_number = completed_context_turns + 1
        injection_turn_number = next_turn_number if context_schedule_initialized else 1

        if is_first_turn:
            activity_lines = [prompt_builder.load_runtime_prompt("new_chat_created")]
            if browser_time:
                activity_lines.append(
                    prompt_builder.load_runtime_prompt(
                        "new_chat_browser_time",
                        browser_time=browser_time,
                    )
                )
            request_metadata["frontend_activity_context"] = (
                prompt_builder.join_prompt_lines(activity_lines)
            )
        elif browser_time and _is_time_request(input_text):
            request_metadata["frontend_activity_context"] = (
                prompt_builder.load_runtime_prompt(
                    "requested_browser_time",
                    browser_time=browser_time,
                )
            )

        if not skip_history and (input_text.strip() or images):
            tts_preference_change_context = (
                context.consume_tts_preference_change_prompt()
            )
            if tts_preference_change_context:
                request_metadata["tts_preference_change_context"] = (
                    tts_preference_change_context
                )
        if context.history_uid and not skip_history:
            long_term_memory_context = (
                await long_term_memory_manager.retrieve_injection(
                    conf_uid=context.character_config.conf_uid,
                    query=input_text,
                    top_k=context.rag_top_k,
                    threshold=context.rag_threshold,
                    hybrid_weight=context.rag_hybrid_weight,
                )
            )
            if long_term_memory_context:
                request_metadata["long_term_memory_context"] = (
                    long_term_memory_context
                )
            long_term_relationship_context = (
                await long_term_relationship_manager.consume_injection(
                    conf_uid=context.character_config.conf_uid,
                    history_uid=context.history_uid,
                    turn_number=injection_turn_number,
                )
            )
            if long_term_relationship_context:
                request_metadata["long_term_relationship_context"] = (
                    long_term_relationship_context
                )
            short_term_relationship_context = (
                await short_term_relationship_manager.consume_injection(
                    conf_uid=context.character_config.conf_uid,
                    history_uid=context.history_uid,
                    turn_number=injection_turn_number,
                )
            )
            if short_term_relationship_context:
                request_metadata["short_term_relationship_context"] = (
                    short_term_relationship_context
                )

        # Create batch input
        batch_input = create_batch_input(
            input_text=input_text,
            images=images,
            from_name=context.character_config.human_name,
            metadata=request_metadata or None,
        )

        # Store user message (check if we should skip storing to history)
        if context.history_uid and not skip_history:
            context_injections = {
                key: request_metadata[key]
                for key in CONTEXT_INJECTION_KEYS
                if (
                    isinstance(request_metadata.get(key), str)
                    and request_metadata[key].strip()
                )
            }
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="human",
                content=input_text,
                name=context.character_config.human_name,
                context_injections=context_injections,
            )

        if skip_history:
            logger.debug("Skipping storing user input to history (proactive speak)")

        logger.info(f"User input: {input_text}")
        if images:
            logger.info(f"With {len(images)} images")

        try:
            # Keep long-lived sessions in sync with edits to the active
            # character's system prompt before any model request is created.
            await context.refresh_character_system_prompt()

            # agent.chat yields Union[SentenceOutput, Dict[str, Any]]
            agent_output_stream = context.agent_engine.chat(batch_input)

            # Accumulate all sentence/audio outputs for non-streaming TTS
            accumulated_outputs: List[Union[SentenceOutput, AudioOutput]] = []

            async for output_item in agent_output_stream:
                if (
                    isinstance(output_item, dict)
                    and output_item.get("type") == "tool_call_status"
                ):
                    # Handle tool status event: send WebSocket message immediately
                    output_item["name"] = context.character_config.character_name
                    logger.debug(f"Sending tool status update: {output_item}")

                    await websocket_send(json.dumps(output_item))

                elif isinstance(output_item, (SentenceOutput, AudioOutput)):
                    # Accumulate outputs instead of processing immediately
                    accumulated_outputs.append(output_item)
                else:
                    logger.warning(
                        f"Received unexpected item type from agent chat stream: {type(output_item)}"
                    )
                    logger.debug(f"Unexpected item content: {output_item}")

            # Merge all accumulated outputs and process as one
            if accumulated_outputs:
                merged_output = _merge_sentence_outputs(
                    accumulated_outputs,
                    character_name=context.character_config.character_name,
                    avatar=context.character_config.avatar,
                )
                response_part = await process_agent_output(
                    output=merged_output,
                    character_config=context.character_config,
                    live2d_model=context.live2d_model,
                    tts_engine=context.tts_engine,
                    websocket_send=websocket_send,
                    tts_manager=tts_manager,
                    translate_engine=None,
                    generate_audio=context.generate_audio,
                )
                full_response = str(response_part) if response_part else ""

        except Exception as e:
            logger.exception(
                f"Error processing agent response stream: {e}"
            )  # Log with stack trace
            await websocket_send(
                json.dumps(
                    {
                        "type": "error",
                        "message": f"Error processing agent response: {str(e)}",
                    }
                )
            )
            # full_response will contain partial response before error
        # --- End processing agent response ---

        # Wait for any pending TTS tasks
        if tts_manager.task_list:
            await asyncio.gather(*tts_manager.task_list)
            await websocket_send(json.dumps({"type": "backend-synth-complete"}))

        if context.history_uid and full_response and not skip_history:
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=full_response,
                name=context.character_config.character_name,
                avatar=context.character_config.avatar,
            )
            logger.info(f"AI response: {full_response}")

            _save_completed_context_turns(
                context.character_config.conf_uid,
                context.history_uid,
                next_turn_number,
            )

            summarize = getattr(
                context.agent_engine, "summarize_long_term_memory", None
            )
            if summarize is None:
                logger.error(
                    "The active agent does not support long-term memory summaries"
                )
            else:
                await long_term_memory_manager.record_completed_turn(
                    conf_uid=context.character_config.conf_uid,
                    history_uid=context.history_uid,
                    user_content=input_text,
                    assistant_content=full_response,
                    summarize=summarize,
                )

            summarize_short_relationship = getattr(
                context.agent_engine,
                "summarize_short_term_relationship",
                None,
            )
            if summarize_short_relationship is None:
                logger.error(
                    "The active agent does not support short-term relationship summaries"
                )
            else:
                await short_term_relationship_manager.record_completed_turn(
                    conf_uid=context.character_config.conf_uid,
                    history_uid=context.history_uid,
                    user_content=input_text,
                    assistant_content=full_response,
                    browser_time=browser_time,
                    summarize=summarize_short_relationship,
                )

            summarize_relationship = getattr(
                context.agent_engine,
                "summarize_long_term_relationship",
                None,
            )
            if summarize_relationship is None:
                logger.error(
                    "The active agent does not support long-term relationship summaries"
                )
            else:
                await long_term_relationship_manager.record_completed_turn(
                    conf_uid=context.character_config.conf_uid,
                    history_uid=context.history_uid,
                    user_content=input_text,
                    assistant_content=full_response,
                    summarize=summarize_relationship,
                )

        await finalize_conversation_turn(
            tts_manager=tts_manager,
            websocket_send=websocket_send,
            client_uid=client_uid,
        )

        return full_response  # Return accumulated full_response

    except asyncio.CancelledError:
        logger.info(f"🤡👍 Conversation {session_emoji} cancelled because interrupted.")
        raise
    except Exception as e:
        logger.error(f"Error in conversation chain: {e}")
        await websocket_send(
            json.dumps({"type": "error", "message": f"Conversation error: {str(e)}"})
        )
        raise
    finally:
        cleanup_conversation(tts_manager, session_emoji)


def _merge_sentence_outputs(
    outputs: List[Union[SentenceOutput, AudioOutput]],
    character_name: str = "AI",
    avatar: Optional[str] = None,
) -> SentenceOutput:
    """Merge multiple SentenceOutput/AudioOutput items into a single SentenceOutput.

    All display texts and TTS texts are concatenated so the frontend displays
    the full response at once and TTS generates a single audio file.

    Args:
        outputs: List of sentence/audio outputs to merge
        character_name: Character name for the display text
        avatar: Character avatar URL

    Returns:
        A single SentenceOutput containing the merged content
    """
    all_display_texts: List[str] = []
    all_tts_texts: List[str] = []
    all_expressions: List[str] = []

    for output in outputs:
        if isinstance(output, SentenceOutput):
            all_display_texts.append(output.display_text.text)
            if output.tts_text:
                all_tts_texts.append(output.tts_text)
            if output.actions and output.actions.expressions:
                all_expressions.extend(output.actions.expressions)
        elif isinstance(output, AudioOutput):
            all_display_texts.append(output.transcript)

    merged_display = DisplayText(
        text="".join(all_display_texts),
        name=character_name,
        avatar=avatar,
    )
    merged_tts = " ".join(all_tts_texts)
    merged_actions = Actions(
        expressions=all_expressions if all_expressions else None
    )

    logger.debug(
        f"Merged {len(outputs)} outputs: display={len(merged_display.text)} chars, "
        f"tts={len(merged_tts)} chars"
    )

    return SentenceOutput(
        display_text=merged_display,
        tts_text=merged_tts,
        actions=merged_actions,
    )
