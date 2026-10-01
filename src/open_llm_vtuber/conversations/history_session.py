"""Open a conversation history and preload the same context for every client."""

from ..chat_history_manager import (
    create_new_history,
    get_history,
    get_recent_normal_history_messages,
    store_message,
)
from ..optional_features import get_optional_new_history_messages
from ..service_context import ServiceContext


def open_new_history(context: ServiceContext) -> tuple[str, list[dict]]:
    conf_uid = context.character_config.conf_uid
    history_uid = create_new_history(conf_uid, context.history_root)
    if not history_uid:
        return "", []

    context.history_uid = history_uid
    context.english_mode = False
    for initial_message in get_optional_new_history_messages(context):
        store_message(
            conf_uid=conf_uid,
            history_uid=history_uid,
            role=str(initial_message.get("role", "ai")),
            content=str(initial_message.get("content", "")),
            name=str(initial_message.get("name", "")),
            avatar=str(initial_message.get("avatar", "")),
            history_root=context.history_root,
        )

    current_messages = get_history(conf_uid, history_uid, context.history_root)
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
    return history_uid, current_messages
