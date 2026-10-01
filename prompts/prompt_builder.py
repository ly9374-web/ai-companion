"""Central loading and composition for every production LLM prompt."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable

from . import prompt_loader


_CHARACTER_OUTPUT_RULES_MARKER = "#声音效果与表情"


def load_system_prompt(name: str) -> str:
    return prompt_loader.load_prompt(f"system.{name}").strip()


def load_summary_prompt(name: str) -> str:
    return prompt_loader.load_prompt(f"summaries.{name}.system_prompt").strip()


def resolve_persona_prompt(
    persona_prompt_file: str | None,
    inline_persona_prompt: str,
) -> str:
    """Load a character persona template or legacy inline content."""
    if persona_prompt_file:
        return prompt_loader.load_persona(persona_prompt_file).strip()
    return inline_persona_prompt.strip()


def render_character_system_prompt(
    system_prompt_template: str,
    emomap_keys: str,
) -> str:
    # Older persona templates may contain their own copy of these rules. Drop
    # that legacy suffix before appending the canonical YAML-managed version.
    persona_template = system_prompt_template.partition(
        _CHARACTER_OUTPUT_RULES_MARKER
    )[0]
    persona_prompt = prompt_loader.render_text(
        persona_template,
        emomap_keys=emomap_keys,
    ).strip()
    output_rules = prompt_loader.render_prompt(
        "chat.character_output_rules",
        emomap_keys=emomap_keys,
    ).strip()
    return join_prompt_sections((persona_prompt, output_rules))


def strip_relationship_guidance(system_prompt: str) -> str:
    """Remove a saved relationship tier while retaining the surrounding persona."""
    marker = prompt_loader.load_prompt("chat.current_relationship_heading").strip()
    lines = system_prompt.splitlines()
    kept: list[str] = []
    in_guidance = False
    for line in lines:
        if line.strip() == marker:
            in_guidance = True
            continue
        if in_guidance and re.match(r"\s*#\S", line):
            in_guidance = False
        if not in_guidance:
            kept.append(line)
    return "\n".join(kept).strip()


def build_current_relationship_guidance(score: float) -> str:
    """Render the prescribed five-point tier for a persisted 0–100 score."""
    if not math.isfinite(score) or not 0 <= score <= 100:
        raise ValueError("Current relationship score must be between 0 and 100")
    tier = min(95, int(score // 5) * 5)
    guidance = prompt_loader.load_prompt(
        f"chat.current_relationship_tiers.tier_{tier}"
    ).strip()
    heading = prompt_loader.load_prompt("chat.current_relationship_heading")
    return join_prompt_sections((heading, guidance))


def load_runtime_prompt(name: str, **values: object) -> str:
    key = f"runtime.{name}"
    if values:
        return prompt_loader.render_prompt(key, **values).strip()
    return prompt_loader.load_prompt(key).strip()


def join_prompt_sections(sections: Iterable[str]) -> str:
    return "\n\n".join(section.strip() for section in sections if section.strip())


def join_prompt_lines(lines: Iterable[str]) -> str:
    return "\n".join(line for line in lines if line).strip()


def build_user_request(
    text_prompt: str,
    frontend_activity_context: str = "",
    tts_preference_change_context: str = "",
    rolling_summary_context: str = "",
    long_term_memory_context: str = "",
    short_term_relationship_context: str = "",
    has_images: bool = False,
    web_search_context: str = "",
) -> str:
    if not text_prompt and has_images:
        text_prompt = load_runtime_prompt("image_only_user_input")

    contexts = (
        long_term_memory_context,
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
            short_term_relationship_context=short_term_relationship_context,
            user_input=text_prompt,
        ).strip()
    while "\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n", "\n\n")
    return join_prompt_sections(
        (
            frontend_activity_context,
            tts_preference_change_context,
            rolling_summary_context,
            web_search_context,
            rendered,
        )
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


def build_short_relationship_injection(relationship_file: str) -> str:
    return prompt_loader.render_prompt(
        "chat.contexts.short_term_relationship",
        relationship_file=relationship_file.rstrip(),
    ).strip()


def build_rolling_summary_injection(summary: str) -> str:
    return load_runtime_prompt("rolling_summary_context", summary=summary)


def build_long_term_memory_summary_input(
    recent_turns: list[dict[str, str]],
    character_system_prompt: str = "",
    browser_time: str = "",
) -> str:
    browser_date = browser_time.partition("，")[0].strip()
    browser_date_context = (
        load_runtime_prompt(
            "long_term_memory_browser_date",
            browser_date=browser_date,
        )
        if browser_date
        else ""
    )
    return prompt_loader.render_prompt(
        "summaries.long_term_memory.user_prompt",
        character_system_prompt=character_system_prompt.strip(),
        browser_date_context_json=json.dumps(
            browser_date_context, ensure_ascii=False
        ),
        recent_turns_json=json.dumps(recent_turns, ensure_ascii=False),
    ).strip()


def build_long_term_memory_reconcile_input(
    reconciliation_input: dict[str, Any],
) -> str:
    return prompt_loader.render_prompt(
        "summaries.long_term_memory_reconcile.user_prompt",
        reconciliation_input_json=json.dumps(
            reconciliation_input, ensure_ascii=False
        ),
    ).strip()


def build_rolling_context_summary_input(
    turns: list[dict[str, str]],
    previous_summary: str = "",
) -> str:
    return prompt_loader.render_prompt(
        "summaries.rolling_context.user_prompt",
        previous_summary_json=json.dumps(previous_summary, ensure_ascii=False),
        conversation_turns_json=json.dumps(turns, ensure_ascii=False),
    ).strip()


def build_short_term_relationship_summary_input(
    recent_turns: list[dict[str, str]],
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
        existing_short_term_relationship_file_json=json.dumps(
            existing_short_term_relationship_file, ensure_ascii=False
        ),
    ).strip()


def build_current_relationship_score_input(
    recent_turns: list[dict[str, str]],
) -> str:
    return prompt_loader.render_prompt(
        "summaries.current_relationship_score.user_prompt",
        recent_turns_json=json.dumps(recent_turns, ensure_ascii=False),
    ).strip()


def build_persona_profile_chunk_input(
    target_name: str,
    character_name: str,
    chunk: str,
    chunk_index: int,
) -> str:
    return prompt_loader.render_prompt(
        "summaries.persona_profile_shared.chunk_user_prompt",
        target_name=target_name,
        character_name=character_name,
        chunk_index=f"{chunk_index:03d}",
        chunk_json=json.dumps(chunk, ensure_ascii=False),
    ).strip()


def build_persona_profile_consolidation_input(
    target_name: str,
    document_type: str,
    document: str,
) -> str:
    return prompt_loader.render_prompt(
        "summaries.persona_profile_consolidate.user_prompt",
        target_name=target_name,
        document_type=document_type,
        document_json=json.dumps(document, ensure_ascii=False),
    ).strip()


def build_persona_profile_final_input(
    target_name: str,
    character_name: str,
    relationship: str,
    memory: str,
    single_chunk_source: str = "",
) -> str:
    return prompt_loader.render_prompt(
        "summaries.persona_profile_shared.final_user_prompt",
        target_name=target_name,
        character_name=character_name,
        relationship_json=json.dumps(relationship, ensure_ascii=False),
        memory_json=json.dumps(memory, ensure_ascii=False),
        single_chunk_source_json=json.dumps(
            single_chunk_source, ensure_ascii=False
        ),
    ).strip()


def build_profiler_thinslice_input(
    target_name: str,
    analysis_payload: dict[str, Any],
) -> str:
    return prompt_loader.render_prompt(
        "summaries.profiler_thinslice.user_prompt",
        target_name=target_name,
        analysis_payload_json=json.dumps(
            analysis_payload, ensure_ascii=False, indent=2
        ),
    ).strip()


def build_tool_results(results: Iterable[str]) -> str:
    return join_prompt_lines(results)


def build_web_search_context(content: str) -> str:
    return load_runtime_prompt("web_search_context", content=content)


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
