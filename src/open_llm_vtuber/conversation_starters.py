"""Server-owned conversation starters for eligible accounts."""

from __future__ import annotations


WELCOME_MESSAGE = "你好啊，今天有什么想聊的吗？"

CONVERSATION_STARTERS = {
    "english": {
        "label": "我想练英语",
        "prompt": (
            "我想和你练英文口语，给我找一个有意思的话题引导我与你聊天，"
            "保证以英文与我沟通。如果有什么语法错误要给我指出来，并且自然的"
            "帮我修正，同时给我相关的例子。但不要影响我们正常沟通话题。"
        ),
    },
    "work": {
        "label": "我想聊工作",
        "prompt": "我想和你聊聊工作中的事情，问问我工作中发生了什么",
    },
    "relationships": {
        "label": "我想聊关系",
        "prompt": "我想和你聊聊我和他人的关系，问问我最近在人际关系中发生了什么",
    },
    "school": {
        "label": "我想聊学校",
        "prompt": "我希望和你聊聊最近的校园生活",
    },
}


def get_conversation_starter(topic: object) -> dict[str, str] | None:
    """Return one allowlisted starter without accepting arbitrary hidden prompts."""
    if not isinstance(topic, str):
        return None
    starter = CONVERSATION_STARTERS.get(topic)
    return starter.copy() if starter else None
