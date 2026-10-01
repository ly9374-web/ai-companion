from typing import Union, List, Dict, Any, Optional
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
import numpy as np

from prompts import prompt_builder

from .conversation_utils import (
    create_batch_input,
    process_agent_output,
    send_conversation_start_signals,
    process_user_input,
    finalize_conversation_turn,
    notify_playback_ready,
    cleanup_conversation,
    EMOJI_LIST,
)
from .types import WebSocketSend
from .tts_manager import TTSTaskManager
from ..chat_history_manager import (
    extract_normal_turns,
    get_history,
    get_latest_user_message_time,
    get_metadata,
    render_history_message_for_frontend,
    store_message,
    update_metadata_state,
)
from .history_session import open_new_history
from ..service_context import ServiceContext
from ..mobile_prompt_logs import record_prompt, record_summary_job
from ..generated_images import save_generated_images
from ..conversation_state_manager import reserve_interval_event
from ..profiler_session import (
    PROFILER_INTERIM_ROUNDS,
    PROFILER_MINIMUM_RESOLUTION_ROUND,
    apply_hidden_round_protocol,
    formal_round_for_turn,
    has_problem_solved_protocol,
    is_profiler_character,
)
from ..profiler import queue_profiler_audio

# Import necessary types from agent outputs
from ..agent.output_types import SentenceOutput, AudioOutput


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
RELATIONSHIP_INJECTION_STATE_KEY = "relationship_injection_schedule"
CONTEXT_INJECTION_KEYS = (
    "short_term_relationship_context",
)
MOBILE_NEW_HISTORY_GAP_SECONDS = 60 * 60
TIME_CONTEXT_INTERVAL_TURNS = 5


def _is_time_request(input_text: str) -> bool:
    normalized = input_text.casefold()
    return any(command in normalized for command in TIME_REQUEST_COMMANDS)


def _format_elapsed_since_last_user(previous: datetime, now: datetime) -> str:
    elapsed_minutes = int((now - previous).total_seconds() // 60)
    if elapsed_minutes < 0:
        return ""
    if elapsed_minutes == 0:
        return prompt_builder.load_runtime_prompt("last_user_just_now")
    if elapsed_minutes < 60:
        key, count = "last_user_minutes_ago", elapsed_minutes
    elif elapsed_minutes < 24 * 60:
        key, count = "last_user_hours_ago", elapsed_minutes // 60
    else:
        key, count = "last_user_days_ago", elapsed_minutes // (24 * 60)
    return prompt_builder.load_runtime_prompt(key, count=count)


ENGLISH_REPLY_SUFFIX = "此次回复语言为：英文"


def _english_letter_ratio(input_text: str) -> float:
    """ASCII 英文字母占全部字母字符的比例；无字母（数字/标点/表情）返回 0。"""
    letters = [char for char in input_text if char.isalpha()]
    if not letters:
        return 0.0
    english = sum(1 for char in letters if char.isascii())
    return english / len(letters)


# 后台每 N 轮总结任务的日志标签与 prompts.yaml key（手机端日志面板用）。
_SUMMARY_JOB_LABELS = {
    "rolling": "短期记忆（滚动总结）",
    "short_term_relationship": "短期记忆（关系总结）",
    "long_term_memory": "长期记忆总结",
    "current_relationship_score": "关系打分（-5~5）",
}
_SUMMARY_JOB_PROMPT_KEYS = {
    "rolling": "rolling_context",
    "short_term_relationship": "short_term_relationship",
    "long_term_memory": "long_term_memory",
    "current_relationship_score": "current_relationship_score",
}


def _format_summary_job_turns(turns) -> str:
    """把喂给总结模型的那几轮对话拼成可读文本。"""
    lines: list[str] = []
    for turn in turns or []:
        if not isinstance(turn, dict):
            lines.append(str(turn))
            continue
        user = turn.get("user") or turn.get("human") or ""
        assistant = turn.get("assistant") or turn.get("ai") or ""
        if user:
            lines.append(f"用户：{user}")
        if assistant:
            lines.append(f"AI：{assistant}")
    return "\n".join(lines)


def _capture_summary_job(context, kind: str, turns, extra: str, output: str) -> None:
    """把一次后台总结任务的输入/输出记进手机端日志（仅在日志开启时）。"""
    if not getattr(context, "mobile_prompt_logging", False):
        return
    account = context.account_name
    character = context.character_config.conf_uid
    history = context.history_uid
    if not (account and character and history):
        return
    sections = [extra, _format_summary_job_turns(turns)]
    try:
        record_summary_job(
            account,
            character,
            history,
            kind=kind,
            label=_SUMMARY_JOB_LABELS.get(kind, kind),
            system_prompt=prompt_builder.load_summary_prompt(
                _SUMMARY_JOB_PROMPT_KEYS.get(kind, kind)
            ),
            input_text="\n".join(section for section in sections if section),
            output_text=output,
        )
    except Exception:
        logger.exception("Failed to capture mobile summary job log")


def _is_first_turn(
    conf_uid: str,
    history_uid: str,
    history_root: str | Path = "chat_history",
) -> bool:
    messages = get_history(conf_uid, history_uid, history_root)
    # Eligible accounts seed new histories with an assistant welcome message.
    # The first actual user message must still receive normal first-turn context.
    return not any(
        message.get("role") == "human" and not message.get("debug_mode")
        for message in messages
    )


def _get_completed_context_turns(
    conf_uid: str,
    history_uid: str,
    history_root: str | Path = "chat_history",
) -> tuple[int, int | None]:
    """Return completed turns and the last relationship snapshot turn."""
    metadata = get_metadata(conf_uid, history_uid, history_root)
    state = metadata.get(CONTEXT_INJECTION_SCHEDULE_METADATA_KEY, {})
    if isinstance(state, dict):
        completed_turns = state.get("completed_turns")
        if (
            not isinstance(completed_turns, bool)
            and isinstance(completed_turns, int)
            and completed_turns >= 0
        ):
            last_injection_turn = state.get("last_relationship_injection_turn")
            if (
                isinstance(last_injection_turn, bool)
                or not isinstance(last_injection_turn, int)
                or last_injection_turn < 1
            ):
                last_injection_turn = None
            return completed_turns, last_injection_turn

    messages = get_history(conf_uid, history_uid, history_root)
    completed_turns = len(extract_normal_turns(messages))
    _save_context_injection_schedule(
        conf_uid,
        history_uid,
        history_root=history_root,
        completed_turns=completed_turns,
    )
    return completed_turns, None


def _save_context_injection_schedule(
    conf_uid: str,
    history_uid: str,
    *,
    history_root: str | Path = "chat_history",
    completed_turns: int | None = None,
    last_relationship_injection_turn: int | None = None,
) -> None:
    """Persist schedule fields without discarding another schedule field."""
    updates: dict[str, int] = {}
    if completed_turns is not None:
        updates["completed_turns"] = completed_turns
    if last_relationship_injection_turn is not None:
        updates["last_relationship_injection_turn"] = (
            last_relationship_injection_turn
        )

    if not update_metadata_state(
        conf_uid,
        history_uid,
        CONTEXT_INJECTION_SCHEDULE_METADATA_KEY,
        updates,
        history_root,
    ):
        logger.error(
            "Failed to persist unified context injection schedule for history {}",
            history_uid,
        )


def _save_completed_context_turns(
    conf_uid: str,
    history_uid: str,
    completed_turns: int,
    history_root: str | Path = "chat_history",
) -> None:
    """Persist the unified count after one complete user-assistant turn."""
    _save_context_injection_schedule(
        conf_uid,
        history_uid,
        history_root=history_root,
        completed_turns=completed_turns,
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
    model_context_response = ""
    playback_waiter: asyncio.Task | None = None

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
        is_debug_turn = bool(context.debug_mode and not skip_history)
        if is_debug_turn:
            request_metadata["debug_mode"] = True
        browser_time = request_metadata.get("browser_time", "")
        current_time = datetime.now(timezone.utc)
        optional_feature_context = request_metadata.pop(
            "optional_feature_context", ""
        )
        analysis_data = request_metadata.pop("analysis_data", None)
        history_display_text = request_metadata.pop("history_display_text", "")
        skip_english_suffix = bool(
            request_metadata.pop("skip_english_suffix", False)
        )
        auto_new_history_uid = ""
        if (
            context.mobile_image_only
            and context.history_uid
            and not skip_history
            and not is_debug_turn
            and (input_text.strip() or images)
        ):
            previous_in_history = get_latest_user_message_time(
                context.character_config.conf_uid,
                context.history_root,
                context.history_uid,
            )
            if (
                previous_in_history is not None
                and (current_time - previous_in_history).total_seconds()
                > MOBILE_NEW_HISTORY_GAP_SECONDS
            ):
                auto_new_history_uid, _ = open_new_history(context)
                if not auto_new_history_uid:
                    logger.error("Failed to open a new mobile history after an idle gap")

        is_first_turn = False
        completed_context_turns = 0
        if context.history_uid and not skip_history:
            is_first_turn = _is_first_turn(
                context.character_config.conf_uid,
                context.history_uid,
                context.history_root,
            )
            (
                completed_context_turns,
                _,
            ) = _get_completed_context_turns(
                context.character_config.conf_uid,
                context.history_uid,
                context.history_root,
            )

        next_turn_number = completed_context_turns + 1
        is_profiler_turn = is_profiler_character(
            context.character_config.conf_uid
        )
        profiler_round = -1
        if is_profiler_turn:
            previous_messages = (
                get_history(
                    context.character_config.conf_uid,
                    context.history_uid,
                    context.history_root,
                )
                if context.history_uid
                else []
            )
            profiler_round = formal_round_for_turn(previous_messages, input_text)
        relationship_injection_interval = context.max_history_turns + 1
        should_inject_relationships = False
        if (
            context.history_uid
            and not skip_history
            and not is_debug_turn
            and not context.isolated_conversation_context
        ):
            _, should_inject_relationships = reserve_interval_event(
                context.character_config.conf_uid,
                RELATIONSHIP_INJECTION_STATE_KEY,
                relationship_injection_interval,
                force=is_first_turn,
                history_root=context.history_root,
            )

        browser_time_suffix = ""
        should_include_time = (
            is_first_turn
            or (
                not is_debug_turn
                and completed_context_turns >= TIME_CONTEXT_INTERVAL_TURNS
                and completed_context_turns % TIME_CONTEXT_INTERVAL_TURNS == 0
            )
            or _is_time_request(input_text)
        )
        if browser_time and should_include_time:
            browser_time_suffix = prompt_builder.load_runtime_prompt(
                "browser_time_suffix",
                browser_time=browser_time,
            )
            if is_first_turn and not is_debug_turn:
                previous_user = get_latest_user_message_time(
                    context.character_config.conf_uid,
                    context.history_root,
                )
                if previous_user is not None:
                    elapsed = _format_elapsed_since_last_user(
                        previous_user, current_time
                    )
                    if elapsed:
                        browser_time_suffix = prompt_builder.join_prompt_lines((
                            browser_time_suffix,
                            prompt_builder.load_runtime_prompt(
                                "last_user_time_suffix", elapsed=elapsed
                            ),
                        ))

        if is_first_turn:
            request_metadata["frontend_activity_context"] = (
                prompt_builder.load_runtime_prompt("new_chat_created")
            )

        if optional_feature_context:
            request_metadata["frontend_activity_context"] = (
                prompt_builder.join_prompt_sections(
                    (
                        request_metadata.get("frontend_activity_context", ""),
                        optional_feature_context,
                    )
                )
            )

        if profiler_round >= 0:
            profiler_context = (
                prompt_builder.load_runtime_prompt("profiler_opening_context")
                if profiler_round == 0
                else prompt_builder.load_runtime_prompt(
                    "profiler_round_context",
                    round_number=profiler_round,
                    convergence_instruction=(
                        prompt_builder.load_runtime_prompt(
                            "profiler_convergence_instruction"
                        )
                        if profiler_round >= PROFILER_MINIMUM_RESOLUTION_ROUND
                        else ""
                    ),
                )
            )
            request_metadata["frontend_activity_context"] = (
                prompt_builder.join_prompt_sections(
                    (
                        request_metadata.get("frontend_activity_context", ""),
                        profiler_context,
                    )
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
        retrieved_rag_contents: list[str] = []
        if context.history_uid and not skip_history and not is_debug_turn:
            context.refresh_rag_options()
            summarize_rolling_now = getattr(
                context.agent_engine, "summarize_rolling_context", None
            )
            if summarize_rolling_now is not None:
                await context.rolling_summary_manager.generate_ready_batches(
                    conf_uid=context.character_config.conf_uid,
                    history_uid=context.history_uid,
                    batch_size=context.max_history_turns,
                    summarize=summarize_rolling_now,
                )
            rolling_summary = context.rolling_summary_manager.read_injection(
                context.character_config.conf_uid,
                context.history_uid,
            )
            if rolling_summary:
                request_metadata["rolling_summary_context"] = (
                    prompt_builder.build_rolling_summary_injection(rolling_summary)
                )
            if not context.isolated_conversation_context:
                long_term_memory_context = (
                    await context.long_term_memory_manager.retrieve_injection(
                        conf_uid=context.character_config.conf_uid,
                        query=input_text,
                        top_k=context.rag_top_k,
                        threshold=context.rag_threshold,
                        hybrid_weight=context.rag_hybrid_weight,
                        retrieved_contents=retrieved_rag_contents,
                    )
                )
                if long_term_memory_context:
                    request_metadata["long_term_memory_context"] = (
                        long_term_memory_context
                    )
                if should_inject_relationships:
                    request_metadata["short_term_relationship_context"] = (
                        await context.short_term_relationship_manager.read_injection(
                            context.character_config.conf_uid
                        )
                    )

        # 英文回复引导：英语练习模式（“我想练英语”）或输入以英文为主
        # （英文字母占比 > 50%）时，给 LLM 提示词追加语言后缀；快速开场白
        # 自带指令不处理，后缀只进 LLM 提示词、不进聊天历史。若输入中已
        # 含该后缀则不重复拼接。
        prompt_text = input_text
        if (
            input_text.strip()
            and not skip_english_suffix
            and ENGLISH_REPLY_SUFFIX not in input_text
            and (
                context.english_mode
                or _english_letter_ratio(input_text) > 0.5
            )
        ):
            prompt_text = f"{input_text}\n{ENGLISH_REPLY_SUFFIX}"
        if browser_time_suffix:
            request_metadata["time_context_suffix"] = browser_time_suffix

        if (
            context.mobile_prompt_logging
            and context.account_name
            and context.history_uid
            and not skip_history
        ):
            account_name = context.account_name
            conf_uid = context.character_config.conf_uid
            history_uid = context.history_uid

            def capture_mobile_prompt(parts: dict) -> None:
                record_prompt(
                    account_name,
                    conf_uid,
                    history_uid,
                    {**parts, "rag_retrieved": list(retrieved_rag_contents)},
                )

            request_metadata["mobile_prompt_log_capture"] = capture_mobile_prompt

        # Create batch input
        batch_input = create_batch_input(
            input_text=prompt_text,
            images=images,
            from_name=context.character_config.human_name,
            metadata=request_metadata or None,
        )

        # Store user message (check if we should skip storing to history)
        if context.history_uid and not skip_history:
            visible_history_content = (
                history_display_text
                if isinstance(history_display_text, str) and history_display_text
                else None
            )
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
                display_content=visible_history_content,
                context_injections=context_injections,
                analysis_data=(
                    analysis_data if isinstance(analysis_data, dict) else None
                ),
                debug_mode=is_debug_turn,
                history_root=context.history_root,
            )
            if auto_new_history_uid:
                current_messages = get_history(
                    context.character_config.conf_uid,
                    auto_new_history_uid,
                    context.history_root,
                )
                await websocket_send(json.dumps({
                    "type": "new-history-created",
                    "history_uid": auto_new_history_uid,
                    "messages": [
                        render_history_message_for_frontend(message)
                        for message in current_messages
                    ],
                }))
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

            # Hand every sentence to TTS the moment the model emits it so playback
            # can start on the first sentence instead of waiting for the whole
            # reply to be written, synthesized and downloaded. Isolated (profiler)
            # turns still buffer everything because the hidden-round protocol
            # rewrites the final sentence before it is spoken.
            stream_sentences_immediately = not context.isolated_conversation_context
            accumulated_outputs: List[Union[SentenceOutput, AudioOutput]] = []

            async for output_item in agent_output_stream:
                if (
                    isinstance(output_item, dict)
                    and output_item.get("type") == "tool_call_status"
                ):
                    if (
                        context.mobile_image_only
                        and output_item.get("tool_name") == "text_to_image"
                        and output_item.get("status") == "completed"
                        and output_item.get("media_urls")
                        and context.account_name
                        and context.history_uid
                    ):
                        output_item["image_ids"] = await save_generated_images(
                            context.account_name,
                            context.character_config.conf_uid,
                            context.history_uid,
                            output_item["media_urls"],
                        )
                    # Handle tool status event: send WebSocket message immediately
                    output_item["name"] = context.character_config.character_name
                    logger.debug(f"Sending tool status update: {output_item}")

                    await websocket_send(json.dumps(output_item))

                elif isinstance(output_item, (SentenceOutput, AudioOutput)):
                    if not stream_sentences_immediately:
                        accumulated_outputs.append(output_item)
                        continue

                    model_context_response += (
                        output_item.display_text.text
                        if isinstance(output_item, SentenceOutput)
                        else output_item.transcript
                    )
                    response_part = await process_agent_output(
                        output=output_item,
                        character_config=context.character_config,
                        live2d_model=context.live2d_model,
                        tts_engine=context.tts_engine,
                        websocket_send=websocket_send,
                        tts_manager=tts_manager,
                        translate_engine=None,
                        generate_audio=context.generate_audio,
                    )
                    if response_part:
                        full_response += str(response_part)
                else:
                    logger.warning(
                        f"Received unexpected item type from agent chat stream: {type(output_item)}"
                    )
                    logger.debug(f"Unexpected item content: {output_item}")

            if accumulated_outputs:
                # Only isolated profiler turns buffer their outputs, because the
                # hidden-round protocol has to rewrite the final sentence first.
                if profiler_round >= 0:
                    last_sentence = next(
                        (
                            item
                            for item in reversed(accumulated_outputs)
                            if isinstance(item, SentenceOutput)
                        ),
                        None,
                    )
                    if last_sentence is not None:
                        last_sentence.display_text.text = (
                            apply_hidden_round_protocol(
                                last_sentence.display_text.text,
                                profiler_round,
                            )
                        )
                        last_sentence.tts_text = apply_hidden_round_protocol(
                            last_sentence.tts_text,
                            profiler_round,
                        )

                model_context_response = "".join(
                    item.display_text.text
                    if isinstance(item, SentenceOutput)
                    else item.transcript
                    for item in accumulated_outputs
                )
                if profiler_round >= 0:
                    model_context_response = apply_hidden_round_protocol(
                        model_context_response,
                        profiler_round,
                    )
                if all(
                    isinstance(output, SentenceOutput)
                    for output in accumulated_outputs
                ) and is_profiler_turn:
                    full_response = await queue_profiler_audio(
                        outputs=[
                            output
                            for output in accumulated_outputs
                            if isinstance(output, SentenceOutput)
                        ],
                        character_config=context.character_config,
                        tts_engine=context.tts_engine,
                        websocket_send=websocket_send,
                        tts_manager=tts_manager,
                        generate_audio=context.generate_audio,
                    )
                else:
                    for output in accumulated_outputs:
                        response_part = await process_agent_output(
                            output=output,
                            character_config=context.character_config,
                            live2d_model=context.live2d_model,
                            tts_engine=context.tts_engine,
                            websocket_send=websocket_send,
                            tts_manager=tts_manager,
                            translate_engine=None,
                            generate_audio=context.generate_audio,
                        )
                        if response_part:
                            full_response += str(response_part)

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

        # Let the browser finish playback while history and memory are saved.
        if tts_manager.task_list and not is_profiler_turn:
            await asyncio.gather(*tts_manager.task_list)
            await tts_manager.wait_until_payloads_sent()
            playback_waiter = await notify_playback_ready(
                client_uid, websocket_send
            )

        if context.history_uid and full_response and not skip_history:
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=model_context_response or full_response,
                name=context.character_config.character_name,
                avatar=context.character_config.avatar,
                display_content=(
                    full_response
                    if model_context_response
                    and model_context_response != full_response
                    else None
                ),
                debug_mode=is_debug_turn,
                history_root=context.history_root,
            )
            if not is_debug_turn:
                _save_completed_context_turns(
                    context.character_config.conf_uid,
                    context.history_uid,
                    next_turn_number,
                    context.history_root,
                )

            if profiler_round in PROFILER_INTERIM_ROUNDS or (
                profiler_round >= PROFILER_MINIMUM_RESOLUTION_ROUND
                and has_problem_solved_protocol(model_context_response)
            ):
                await websocket_send(
                    json.dumps(
                        {
                            "type": "profiler-analysis-ready",
                            "round": profiler_round,
                        },
                        ensure_ascii=False,
                    )
                )

            summary_conf_uid = context.character_config.conf_uid
            summary_history_uid = context.history_uid
            if is_debug_turn:
                logger.info(
                    "Debug turn is excluded from rolling summary, long-term memory, "
                    "short-term relationship, and current relationship scoring"
                )
            else:
                summarize_rolling = getattr(
                    context.agent_engine, "summarize_rolling_context", None
                )
                if summarize_rolling is not None:

                    async def summarize_rolling_captured(
                        turns,
                        previous_summary="",
                        _base=summarize_rolling,
                    ):
                        output = await _base(turns, previous_summary)
                        extra = (
                            f"【已有滚动总结】\n{previous_summary}"
                            if previous_summary and previous_summary.strip()
                            else ""
                        )
                        _capture_summary_job(context, "rolling", turns, extra, output)
                        return output

                    if not context.summary_coordinator.has_prefix(("rolling",)):
                        context.summary_coordinator.enqueue(
                            f"rolling:{summary_history_uid}",
                            lambda conf_uid=summary_conf_uid,
                            history_uid=summary_history_uid,
                            batch_size=context.max_history_turns,
                            callback=summarize_rolling_captured: (
                                context.rolling_summary_manager.generate_ready_batches(
                                    conf_uid=conf_uid,
                                    history_uid=history_uid,
                                    batch_size=batch_size,
                                    summarize=callback,
                                )
                            ),
                        )

                summarize = getattr(
                    context.agent_engine, "summarize_long_term_memory", None
                )
                reconcile_memory = getattr(
                    context.agent_engine, "reconcile_long_term_memory", None
                )
                if context.isolated_conversation_context:
                    logger.debug(
                        "Skipping cross-conversation memory and relationship updates "
                        "for isolated account {}",
                        context.account_name,
                    )
                elif summarize is None or reconcile_memory is None:
                    logger.error(
                        "The active agent does not support two-stage long-term memory summaries"
                    )
                else:
                    memory_will_summarize = await context.long_term_memory_manager.record_turn(
                        summary_conf_uid,
                        summary_history_uid,
                        input_text,
                        full_response,
                    )
                    if (
                        memory_will_summarize
                        and not context.summary_coordinator.has_prefix(
                            ("long_term_memory",)
                        )
                    ):
                        character_system_prompt = (
                            context.get_editable_system_prompt()
                        )

                        async def summarize_with_character_prompt(
                            turns,
                            callback=summarize,
                            prompt=character_system_prompt,
                            current_browser_time=browser_time,
                        ):
                            output = await callback(
                                turns,
                                prompt,
                                current_browser_time,
                            )
                            _capture_summary_job(
                                context, "long_term_memory", turns, "", output
                            )
                            return output

                        context.summary_coordinator.enqueue(
                            "long_term_memory",
                            lambda conf_uid=summary_conf_uid,
                            history_uid=summary_history_uid,
                            callback=summarize_with_character_prompt,
                            reconcile_callback=reconcile_memory: (
                                context.long_term_memory_manager.summarize_pending_turns(
                                    conf_uid=conf_uid,
                                    history_uid=history_uid,
                                    summarize=callback,
                                    require_full_batch=True,
                                    reconcile=reconcile_callback,
                                )
                            ),
                        )

                summarize_short_relationship = getattr(
                    context.agent_engine,
                    "summarize_short_term_relationship",
                    None,
                )
                if context.isolated_conversation_context:
                    pass
                elif summarize_short_relationship is None:
                    logger.error(
                        "The active agent does not support short-term relationship summaries"
                    )
                else:

                    async def summarize_short_relationship_captured(
                        turns,
                        existing_relationship="",
                        current_browser_time="",
                        _base=summarize_short_relationship,
                    ):
                        output = await _base(
                            turns, existing_relationship, current_browser_time
                        )
                        extra = (
                            f"【已有短期关系】\n{existing_relationship}"
                            if existing_relationship and existing_relationship.strip()
                            else ""
                        )
                        _capture_summary_job(
                            context, "short_term_relationship", turns, extra, output
                        )
                        return output

                    short_will_summarize = await context.short_term_relationship_manager.record_turn(
                        summary_conf_uid,
                        summary_history_uid,
                        input_text,
                        full_response,
                    )
                    if (
                        short_will_summarize
                        and not context.summary_coordinator.has_prefix(
                            ("short_term_relationship",)
                        )
                    ):
                        context.summary_coordinator.enqueue(
                            "short_term_relationship",
                            lambda conf_uid=summary_conf_uid,
                            history_uid=summary_history_uid,
                            current_browser_time=browser_time,
                            callback=summarize_short_relationship_captured: (
                                context.short_term_relationship_manager.summarize_pending_turns(
                                    conf_uid=conf_uid,
                                    history_uid=history_uid,
                                    summarize=callback,
                                    browser_time=current_browser_time,
                                    require_full_batch=True,
                                )
                            ),
                        )

                score_relationship = getattr(
                    context.agent_engine,
                    "score_current_relationship",
                    None,
                )
                if is_profiler_turn:
                    pass
                elif score_relationship is None:
                    logger.error(
                        "The active agent does not support relationship scoring"
                    )
                else:

                    async def score_relationship_captured(
                        turns,
                        _base=score_relationship,
                    ):
                        output = await _base(turns)
                        _capture_summary_job(
                            context, "current_relationship_score", turns, "", output
                        )
                        return output

                    score_will_summarize = await context.current_relationship_score_manager.record_turn(
                        summary_conf_uid,
                        summary_history_uid,
                        input_text,
                        full_response,
                    )
                    if (
                        score_will_summarize
                        and not context.summary_coordinator.has_prefix(
                            ("current_relationship_score",)
                        )
                    ):
                        context.summary_coordinator.enqueue(
                            "current_relationship_score",
                            lambda conf_uid=summary_conf_uid,
                            history_uid=summary_history_uid,
                            callback=score_relationship_captured: (
                                context.current_relationship_score_manager.summarize_pending_update(
                                    conf_uid=conf_uid,
                                    history_uid=history_uid,
                                    summarize=callback,
                                )
                            ),
                        )

        await finalize_conversation_turn(
            tts_manager=tts_manager,
            websocket_send=websocket_send,
            client_uid=client_uid,
            playback_waiter=playback_waiter,
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
        if playback_waiter is not None and not playback_waiter.done():
            playback_waiter.cancel()
            await asyncio.gather(playback_waiter, return_exceptions=True)
        cleanup_conversation(tts_manager, session_emoji)
