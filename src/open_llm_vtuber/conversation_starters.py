"""Server-owned conversation starters for eligible accounts."""

from __future__ import annotations


CONVERSATION_STARTERS = {
    "english": {
        "label": "我想练英语",
        "prompt": (
            "我想和你练英文口语，给我找一个有意思的话题引导我与你聊天，保证后续都用英文和我对话,问我一下是否需要帮我纠正语法"
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
        "prompt": "我希望和你聊聊最近的校园生活，问问我最近校园生活情况",
    },
    "psychology": {
        "label": "我想学心理学",
        "prompt": (
            "我希望你能教我一个心理学的理论，先给我三个心理学的方向进行选择，"
            "并且告诉我这三个方向的现实意义都是什么，当我选择确定了之后开始从基础理论开始教我，"
            "然后引导我和你互动，让我在互动中学习知识"
        ),
    },
    "story": {
        "label": "给我讲个故事",
        "prompt": (
            "你随便挑一个领域，从中选一个中学生层级的概念。然后给我写一则寓言，"
            "用间接的方式把这个概念讲透。别急着点题，让答案在故事快收尾时才浮现出来。"
            "故事结束之后，再解释这个概念，以及里面的隐喻分别对应什么。"
        ),
    },
}


def get_conversation_starter(topic: object) -> dict[str, str] | None:
    """Return one allowlisted starter without accepting arbitrary hidden prompts."""
    if not isinstance(topic, str):
        return None
    starter = CONVERSATION_STARTERS.get(topic)
    return starter.copy() if starter else None
