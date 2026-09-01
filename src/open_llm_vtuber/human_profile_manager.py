"""Generate versioned human profiles from one account/character chat archive."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .chat_history_manager import (
    extract_normal_turns,
    get_full_history_dir,
    get_history,
)


CHUNK_SIZE = 15_000
CONSOLIDATION_INPUT_SIZE = 45_000
FINAL_DOCUMENT_SIZE = 30_000
MAX_CONSOLIDATION_PASSES = 4
THINSLICE_START = "<!-- thinslice:start -->"
THINSLICE_END = "<!-- thinslice:end -->"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
REMOVED_FIELD_RE = re.compile(r"^(\s*)(证据|来源)\s*:\s*.*$")
CONFIDENCE_SOURCE_RE = re.compile(
    r"^(\s*置信度\s*:\s*)(['\"]?)(高|中|低)(?:（来源：.*?）)?\2\s*$"
)
TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$"
)

GenerateSection = Callable[[str, str], Awaitable[str]]
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _split_oversized_paragraph(paragraph: str, size: int) -> list[str]:
    pieces: list[str] = []
    remaining = paragraph
    sentence_endings = "。！？!?；;\n"
    while len(remaining) > size:
        split_at = -1
        for index in range(size, min(len(remaining), size + 1000)):
            if remaining[index] in sentence_endings:
                split_at = index + 1
                break
        if split_at < 0:
            split_at = size
        pieces.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    normalized: list[str] = []
    for paragraph in paragraphs:
        normalized.extend(_split_oversized_paragraph(paragraph, size))

    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for paragraph in normalized:
        added_size = len(paragraph) + (2 if current else 0)
        if current and current_size + added_size > size:
            chunks.append("\n\n".join(current).strip() + "\n")
            current = []
            current_size = 0
        current.append(paragraph)
        current_size += len(paragraph) + (2 if len(current) > 1 else 0)
    if current:
        chunks.append("\n\n".join(current).strip() + "\n")
    return chunks


def _snapshot_normal_turns(
    conf_uid: str,
    history_root: Path,
    account_name: str,
    character_name: str,
) -> tuple[str, int, int]:
    history_dir = Path(get_full_history_dir(conf_uid, history_root, create=True))
    collected: list[tuple[str, str, int, dict[str, Any]]] = []
    history_count = 0
    for history_path in sorted(history_dir.glob("*.json")):
        if not history_path.is_file():
            continue
        history_count += 1
        for turn_index, turn in enumerate(
            extract_normal_turns(get_history(conf_uid, history_path.stem, history_root)),
            start=1,
        ):
            collected.append(
                (
                    str(turn.get("timestamp", "")),
                    history_path.stem,
                    turn_index,
                    turn,
                )
            )
    collected.sort(key=lambda item: (item[0], item[1], item[2]))

    blocks = [
        f"# 当前用户与角色的非调试聊天快照\n\n"
        f"- 目标用户：{account_name}\n"
        f"- 对话角色：{character_name}\n"
        f"- 角色标识：{conf_uid}\n"
        f"- 快照时间：{_now_iso()}"
    ]
    for global_index, (timestamp, history_uid, turn_index, turn) in enumerate(
        collected,
        start=1,
    ):
        user_content = str(turn["user"].get("content", "")).strip()
        assistant_content = str(turn["assistant"].get("content", "")).strip()
        blocks.append(
            f"## turn_{global_index:06d}\n\n"
            f"history_uid: {history_uid}\n"
            f"history_turn: {turn_index}\n"
            f"timestamp: {timestamp}\n\n"
            f"用户（{account_name}）：{user_content}\n\n"
            f"角色（{character_name}）：{assistant_content}"
        )
    return "\n\n".join(blocks).strip() + "\n", len(collected), history_count


def _split_sections(markdown: str) -> OrderedDict[tuple[tuple[int, str], ...], list[str]]:
    sections: OrderedDict[tuple[tuple[int, str], ...], list[str]] = OrderedDict()
    path: list[tuple[int, str]] = []
    current_key: tuple[tuple[int, str], ...] | None = None
    for line in markdown.splitlines():
        match = HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if level == 1:
                current_key = None
                path = []
                continue
            path = [item for item in path if item[0] < level]
            path.append((level, title))
            current_key = tuple(path)
            sections.setdefault(current_key, [])
            continue
        if current_key is not None:
            sections.setdefault(current_key, []).append(line)
    return sections


def _merge_sections(markdowns: list[str], title: str) -> str:
    merged: OrderedDict[tuple[tuple[int, str], ...], list[str]] = OrderedDict()
    for markdown in markdowns:
        for key, lines in _split_sections(markdown).items():
            cleaned = "\n".join(lines).strip()
            merged.setdefault(key, [])
            if cleaned:
                merged[key].append(cleaned)
            elif not merged[key]:
                merged[key].append("")

    output: list[str] = [f"# {title}", ""]
    emitted: set[tuple[tuple[int, str], ...]] = set()
    for key, bodies in merged.items():
        for depth in range(1, len(key) + 1):
            heading_key = key[:depth]
            if heading_key in emitted:
                continue
            level, heading = heading_key[-1]
            output.extend((f"{'#' * level} {heading}", ""))
            emitted.add(heading_key)
        non_empty = [body for body in bodies if body.strip()]
        if non_empty:
            output.extend(("\n\n".join(non_empty).strip(), ""))
    return _strip_evidence_fields("\n".join(output).rstrip() + "\n")


def _is_table_header(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and lines[index].lstrip().startswith("|")
        and TABLE_SEPARATOR_RE.match(lines[index + 1]) is not None
    )


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _strip_table_columns(table_lines: list[str]) -> list[str]:
    header = _split_table_row(table_lines[0])
    removed = {
        index for index, cell in enumerate(header) if "证据" in cell or "来源" in cell
    }
    if not removed:
        return table_lines
    cleaned: list[str] = []
    for row_index, line in enumerate(table_lines):
        cells = [
            cell
            for cell_index, cell in enumerate(_split_table_row(line))
            if cell_index not in removed
        ]
        if row_index == 1:
            cells = ["---" for _ in cells]
        cleaned.append("| " + " | ".join(cells) + " |")
    return cleaned


def _strip_evidence_fields(markdown: str) -> str:
    output: list[str] = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_table_header(lines, index):
            table_lines: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            output.extend(_strip_table_columns(table_lines))
            continue
        heading_match = HEADING_RE.match(line)
        if heading_match and heading_match.group(2).strip() in {"证据", "来源"}:
            level = len(heading_match.group(1))
            index += 1
            while index < len(lines):
                next_heading = HEADING_RE.match(lines[index])
                if next_heading and len(next_heading.group(1)) <= level:
                    break
                index += 1
            continue
        field_match = REMOVED_FIELD_RE.match(line)
        if field_match:
            base_indent = len(field_match.group(1))
            index += 1
            while index < len(lines):
                next_line = lines[index]
                if not next_line.strip():
                    index += 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent > base_indent:
                    index += 1
                    continue
                break
            continue
        confidence_match = CONFIDENCE_SOURCE_RE.match(line)
        if confidence_match:
            prefix, quote, level = confidence_match.groups()
            line = f"{prefix}{quote}{level}{quote}"
        output.append(line)
        index += 1
    return "\n".join(output).rstrip() + "\n"


def _append_thinslice(persona: str, thinslice: str) -> str:
    block = f"{THINSLICE_START}\n\n{thinslice.strip()}\n\n{THINSLICE_END}"
    pattern = re.compile(
        rf"\n*{re.escape(THINSLICE_START)}.*?{re.escape(THINSLICE_END)}\s*",
        flags=re.DOTALL,
    )
    if pattern.search(persona):
        updated = pattern.sub(f"\n\n{block}\n", persona).rstrip()
    else:
        updated = f"{persona.rstrip()}\n\n{block}"
    return updated.rstrip() + "\n"


async def _consolidate_document(
    *,
    document: str,
    document_type: str,
    target_name: str,
    generate: GenerateSection,
    build_input: Callable[[str, str, str], str],
    progress: ProgressCallback,
) -> str:
    current = document
    pass_index = 0
    while len(current) > FINAL_DOCUMENT_SIZE and pass_index < MAX_CONSOLIDATION_PASSES:
        pass_index += 1
        batches = _chunk_text(current, CONSOLIDATION_INPUT_SIZE)
        outputs: list[str] = []
        for batch_index, batch in enumerate(batches, start=1):
            await progress(
                {
                    "step": "consolidating",
                    "document": document_type,
                    "pass": pass_index,
                    "current": batch_index,
                    "total": len(batches),
                }
            )
            outputs.append(
                await generate(
                    "persona_profile_consolidate",
                    build_input(target_name, document_type, batch),
                )
            )
        next_document = "\n\n".join(output.strip() for output in outputs).strip() + "\n"
        if len(next_document) >= len(current):
            break
        current = next_document
    if len(current) > FINAL_DOCUMENT_SIZE:
        raise RuntimeError(
            f"{document_type} 分层归并后仍超过安全上下文长度，请稍后重试或减少材料。"
        )
    return current


class HumanProfileManager:
    """Run the staged profile workflow and publish one numbered snapshot."""

    async def generate(
        self,
        *,
        account_name: str,
        conf_uid: str,
        character_name: str,
        history_root: Path,
        generate_section: GenerateSection,
        build_chunk_input: Callable[[str, str, str, int], str],
        build_consolidation_input: Callable[[str, str, str], str],
        build_final_input: Callable[[str, str, str, str, str], str],
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        await progress({"step": "snapshot", "progress": 3})
        source, turn_count, history_count = await asyncio.to_thread(
            _snapshot_normal_turns,
            conf_uid,
            history_root,
            account_name,
            character_name,
        )
        if turn_count == 0:
            return {"status": "empty", "turn_count": 0, "history_count": history_count}

        role_dir = history_root / conf_uid
        profiles_root = role_dir / "profiles"
        profiles_root.mkdir(parents=True, exist_ok=True)
        working_dir = profiles_root / f".working-{uuid.uuid4().hex}"
        chunks_dir = working_dir / "chunks"
        intermediate_dir = working_dir / "intermediate"
        chunks_dir.mkdir(parents=True, exist_ok=False)
        intermediate_dir.mkdir(parents=True, exist_ok=False)

        try:
            (working_dir / "source.md").write_text(source, encoding="utf-8")
            chunks = _chunk_text(source)
            for index, chunk in enumerate(chunks, start=1):
                (chunks_dir / f"chunk_{index:03d}.md").write_text(chunk, encoding="utf-8")

            manifest = {
                "target": account_name,
                "character": character_name,
                "conf_uid": conf_uid,
                "created_at": _now_iso(),
                "history_count": history_count,
                "turn_count": turn_count,
                "chunk_count": len(chunks),
                "debug_filter": "excluded pairs where either message has debug_mode=true",
            }
            (working_dir / "profile.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            relationships: list[str] = []
            memories: list[str] = []
            total_calls = len(chunks) * 2
            completed_calls = 0
            for index, chunk in enumerate(chunks, start=1):
                await progress(
                    {
                        "step": "relationship",
                        "progress": 10 + int(42 * completed_calls / max(total_calls, 1)),
                        "current": index,
                        "total": len(chunks),
                    }
                )
                relationship = await generate_section(
                    "persona_profile_relationship",
                    build_chunk_input(account_name, character_name, chunk, index),
                )
                relationships.append(relationship)
                (intermediate_dir / f"relationship_{index:03d}.md").write_text(
                    relationship.rstrip() + "\n", encoding="utf-8"
                )
                completed_calls += 1

                await progress(
                    {
                        "step": "memory",
                        "progress": 10 + int(42 * completed_calls / max(total_calls, 1)),
                        "current": index,
                        "total": len(chunks),
                    }
                )
                memory = await generate_section(
                    "persona_profile_memory",
                    build_chunk_input(account_name, character_name, chunk, index),
                )
                memories.append(memory)
                (intermediate_dir / f"memory_{index:03d}.md").write_text(
                    memory.rstrip() + "\n", encoding="utf-8"
                )
                completed_calls += 1

            await progress({"step": "merge", "progress": 55})
            relationship_full = _merge_sections(
                relationships, f"分关系互动侧写：{account_name}"
            )
            memory_full = _merge_sections(memories, f"记忆档案：{account_name}")
            (working_dir / "relationship_full.md").write_text(
                relationship_full, encoding="utf-8"
            )
            (working_dir / "memory_full.md").write_text(memory_full, encoding="utf-8")

            relationship = await _consolidate_document(
                document=relationship_full,
                document_type="relationship",
                target_name=account_name,
                generate=generate_section,
                build_input=build_consolidation_input,
                progress=progress,
            )
            memory = await _consolidate_document(
                document=memory_full,
                document_type="memory",
                target_name=account_name,
                generate=generate_section,
                build_input=build_consolidation_input,
                progress=progress,
            )
            (working_dir / "relationship.md").write_text(relationship, encoding="utf-8")
            (working_dir / "memory.md").write_text(memory, encoding="utf-8")

            single_chunk_source = source if len(chunks) == 1 else ""
            final_input = build_final_input(
                account_name,
                character_name,
                relationship,
                memory,
                single_chunk_source,
            )
            await progress({"step": "persona", "progress": 78})
            persona = await generate_section("persona_profile_persona", final_input)
            (working_dir / "persona.md").write_text(
                persona.rstrip() + "\n", encoding="utf-8"
            )

            await progress({"step": "thinslice", "progress": 89})
            thinslice = await generate_section("persona_profile_thinslice", final_input)
            (working_dir / "thinslice.md").write_text(
                thinslice.rstrip() + "\n", encoding="utf-8"
            )
            (working_dir / "persona.md").write_text(
                _append_thinslice(persona, thinslice), encoding="utf-8"
            )

            manifest["completed_at"] = _now_iso()
            manifest["status"] = "ready"
            (working_dir / "profile.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            existing_numbers = [
                int(path.name)
                for path in profiles_root.iterdir()
                if path.is_dir() and re.fullmatch(r"\d{3}", path.name)
            ]
            profile_number = max(existing_numbers, default=0) + 1
            final_dir = profiles_root / f"{profile_number:03d}"
            os.replace(working_dir, final_dir)
            await progress({"step": "complete", "progress": 100})
            return {
                "status": "success",
                "profile_number": f"{profile_number:03d}",
                "path": final_dir.as_posix(),
                "turn_count": turn_count,
                "history_count": history_count,
                "chunk_count": len(chunks),
            }
        except Exception as exc:
            failure = {
                "status": "failed",
                "failed_at": _now_iso(),
                "error": str(exc),
            }
            try:
                (working_dir / "failure.json").write_text(
                    json.dumps(failure, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
            raise
