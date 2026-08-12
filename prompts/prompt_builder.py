"""Central loading and composition for every production LLM prompt."""

from __future__ import annotations

import json
from typing import Any, Iterable

from . import prompt_loader


def load_system_prompt(name: str) -> str:
    return prompt_loader.load_prompt(f"system.{name}").strip()


def load_summary_prompt(name: str) -> str:
    return prompt_loader.load_prompt(f"summaries.{name}.system_prompt").strip()


def resolve_persona_prompt(
    persona_prompt_file: str | None,
    inline_persona_prompt: str,
) -> str:
    """Load a complete character system template or legacy inline content."""
    if persona_prompt_file:
        return prompt_loader.load_persona(persona_prompt_file).strip()
    return inline_persona_prompt.strip()


def render_character_system_prompt(
    system_prompt_template: str,
    emomap_keys: str,
) -> str:
    return prompt_loader.render_text(
        system_prompt_template,
        emomap_keys=emomap_keys,
    ).strip()


def load_runtime_prompt(name: str, **values: object) -> str:
    key = f"runtime.{name}"
    if values:
        return prompt_loader.render_prompt(key, **values).strip()
    return prompt_loader.load_prompt(key).strip()


def join_prompt_sections(sections: Iterable[str]) -> str:
    return "\n\n".join(section.strip() for section in sections if section.strip())


def join_prompt_lines(lines: Iterable[str]) -> str:
    return "\n".join(line for line in lines if line).strip()


def build_output_language_instruction(language_hint: str) -> str:
    """Map the frontend TTS language hint to the main LLM instruction."""
    output_language = "中文" if language_hint == "zh" else "english"
    return f"输出语言为{output_language}"


def build_user_request(
    text_prompt: str,
    frontend_activity_context: str = "",
    tts_preference_change_context: str = "",
    long_term_memory_context: str = "",
    long_term_relationship_context: str = "",
    short_term_relationship_context: str = "",
    has_images: bool = False,
) -> str:
    if not text_prompt and has_images:
        text_prompt = load_runtime_prompt("image_only_user_input")

    contexts = (
        long_term_memory_context,
        long_term_relationship_context,
        short_term_relationship_context,
    )
    if not any(context for context in contexts):
        rendered = prompt_loader.render_prompt(
            "chat.user_prompt.without_context",
            user_input=text_prompt,
        ).strip()
    else:
        rendered = prompt_loader.render_prompt(
            "chat.user_prompt.with_context",
            long_term_memory_context=long_term_memory_context,
            long_term_relationship_context=long_term_relationship_context,
            short_term_relationship_context=short_term_relationship_context,
            user_input=text_prompt,
        ).strip()
    while "\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n", "\n\n")
    return join_prompt_sections(
        (frontend_activity_context, tts_preference_change_context, rendered)
    )


def build_clipboard_content(content: str) -> str:
    return prompt_loader.render_prompt(
        "chat.contexts.clipboard", content=content
    ).strip()


def build_memory_injection(memories: Iterable[str]) -> str:
    memory_lines = "\n".join(f"- {content}" for content in memories if content)
    return prompt_loader.render_prompt(
        "chat.contexts.long_term_memory",
        memories=memory_lines,
    ).strip()


def build_long_relationship_injection(relationship_file: str) -> str:
    return prompt_loader.render_prompt(
        "chat.contexts.long_term_relationship",
        relationship_file=relationship_file.rstrip(),
    ).strip()


def build_short_relationship_injection(relationship_file: str) -> str:
    return prompt_loader.render_prompt(
        "chat.contexts.short_term_relationship",
        relationship_file=relationship_file.rstrip(),
    ).strip()


def build_long_term_memory_summary_input(
    recent_turns: list[dict[str, str]],
) -> str:
    return prompt_loader.render_prompt(
        "summaries.long_term_memory.user_prompt",
        recent_turns_json=json.dumps(recent_turns, ensure_ascii=False),
    ).strip()


def build_long_term_relationship_summary_input(
    long_term_memory_contents: list[str],
    existing_relationship_file: str,
    short_term_relationship_file: str,
) -> str:
    return prompt_loader.render_prompt(
        "summaries.long_term_relationship.user_prompt",
        long_term_memory_contents_json=json.dumps(
            long_term_memory_contents, ensure_ascii=False
        ),
        existing_relationship_file_json=json.dumps(
            existing_relationship_file, ensure_ascii=False
        ),
        short_term_relationship_file_json=json.dumps(
            short_term_relationship_file, ensure_ascii=False
        ),
    ).strip()


def build_short_term_relationship_summary_input(
    recent_turns: list[dict[str, str]],
    long_term_relationship_file: str,
    existing_short_term_relationship_file: str,
    browser_time: str = "",
) -> str:
    current_time_context = (
        load_runtime_prompt(
            "short_relationship_browser_time",
            browser_time=browser_time,
        )
        if browser_time
        else ""
    )
    return prompt_loader.render_prompt(
        "summaries.short_term_relationship.user_prompt",
        current_time_context_json=json.dumps(
            current_time_context, ensure_ascii=False
        ),
        recent_turns_json=json.dumps(recent_turns, ensure_ascii=False),
        long_term_relationship_file_json=json.dumps(
            long_term_relationship_file, ensure_ascii=False
        ),
        existing_short_term_relationship_file_json=json.dumps(
            existing_short_term_relationship_file, ensure_ascii=False
        ),
    ).strip()


def build_tool_results(results: Iterable[str]) -> str:
    return join_prompt_lines(results)


def build_mcp_prompt(servers_info: dict[str, dict[str, Any]]) -> str:
    server_blocks = []
    for server_name, tools in servers_info.items():
        if not tools:
            continue
        tool_blocks = []
        for tool_name, tool_info in tools.items():
            parameter_blocks = []
            for param_name, param_info in tool_info.get("parameters", {}).items():
                parameter_blocks.append(
                    prompt_loader.render_prompt(
                        "tools.parameter_block",
                        parameter_name=param_name,
                        parameter_type=param_info.get("type", "string"),
                        parameter_description=(
                            param_info.get("description")
                            or param_info.get("title")
                            or load_runtime_prompt("mcp_no_parameter_description")
                        ),
                    ).rstrip()
                )
            parameters = ""
            if parameter_blocks:
                parameters = prompt_loader.render_prompt(
                    "tools.parameters_section",
                    parameters="\n".join(parameter_blocks),
                ).rstrip()
            required = tool_info.get("required", [])
            required_section = ""
            if required:
                required_section = prompt_loader.render_prompt(
                    "tools.required_section",
                    required=", ".join(required),
                ).rstrip()
            tool_blocks.append(
                prompt_loader.render_prompt(
                    "tools.tool_block",
                    tool_name=tool_name,
                    description=(
                        tool_info.get("description")
                        or load_runtime_prompt("mcp_no_tool_description")
                    ),
                    parameters=parameters,
                    required=required_section,
                ).rstrip()
            )
        server_blocks.append(
            prompt_loader.render_prompt(
                "tools.server_block",
                server_name=server_name,
                tools="\n".join(tool_blocks),
            ).rstrip()
        )

    return prompt_loader.render_prompt(
        "tools.mcp_prompt",
        servers="\n\n".join(server_blocks),
    ).strip()
