"""Telegram-specific HTML rendering and chunking helpers."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, List

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

DEFAULT_TELEGRAM_TEXT_LIMIT = 4000


@dataclass(frozen=True)
class TelegramFormattedChunk:
    """A single Telegram-ready HTML chunk and its plain fallback."""

    html: str
    text: str


@dataclass(frozen=True)
class _OpenHtmlTag:
    name: str
    open_tag: str
    close_tag: str


_HTML_TAG_RE = re.compile(r"(<\/?)([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*?>")
_FILE_EXTENSIONS_WITH_TLD = {
    "am",
    "at",
    "be",
    "c",
    "cc",
    "cpp",
    "go",
    "h",
    "hpp",
    "java",
    "js",
    "json",
    "jsx",
    "lock",
    "md",
    "pl",
    "py",
    "rb",
    "rs",
    "sh",
    "sql",
    "toml",
    "ts",
    "tsx",
    "txt",
    "yaml",
    "yml",
}
_FILE_REFERENCE_RE = re.compile(
    r"(^|[^a-zA-Z0-9_/\-])([a-zA-Z0-9_.\-/]+\.(?:"
    + "|".join(sorted(_FILE_EXTENSIONS_WITH_TLD))
    + r"))(?=$|[^a-zA-Z0-9_/\-])",
    re.IGNORECASE,
)


def escape_html(text: str) -> str:
    """Escape plain text for Telegram HTML mode."""
    return html.escape(text, quote=False)


def escape_html_attr(text: str) -> str:
    """Escape text for use in HTML attributes."""
    return html.escape(text, quote=True)


@lru_cache(maxsize=1)
def _markdown_parser() -> MarkdownIt:
    """Return the configured markdown parser used for Telegram rendering."""
    return MarkdownIt(
        "default",
        {
            "html": False,
            "linkify": False,
            "breaks": False,
        },
    )


def markdown_to_telegram_html(markdown: str | None) -> str | None:
    """Render markdown-like content to Telegram-safe HTML."""
    if not markdown:
        return markdown
    root = SyntaxTreeNode(_markdown_parser().parse(markdown))
    return _render_block_sequence(root.children or [])


def markdown_to_telegram_chunks(
    markdown: str | None,
    limit: int = DEFAULT_TELEGRAM_TEXT_LIMIT,
) -> List[TelegramFormattedChunk]:
    """Render markdown-like content to HTML chunks plus plain fallbacks."""
    rendered = markdown_to_telegram_html(markdown)
    if not rendered:
        return [TelegramFormattedChunk(html="", text="")]

    html_chunks = split_telegram_html_chunks(rendered, limit)
    return [
        TelegramFormattedChunk(
            html=chunk,
            text=telegram_html_to_plain_text(chunk),
        )
        for chunk in html_chunks
    ]


def telegram_html_to_plain_text(rendered_html: str | None) -> str | None:
    """Convert Telegram-safe HTML into readable plain text."""
    if rendered_html is None:
        return None
    if rendered_html == "":
        return ""

    text = re.sub(r"<[^>]+>", "", rendered_html)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def split_telegram_html_chunks(rendered_html: str, limit: int) -> List[str]:
    """Split rendered Telegram HTML into standalone valid chunks."""
    if not rendered_html:
        return []

    normalized_limit = max(1, int(limit))
    if len(rendered_html) <= normalized_limit:
        return [rendered_html]

    chunks: List[str] = []
    open_tags: List[_OpenHtmlTag] = []
    current = ""
    chunk_has_payload = False

    def _open_prefix() -> str:
        return "".join(tag.open_tag for tag in open_tags)

    def _close_suffix() -> str:
        return "".join(tag.close_tag for tag in reversed(open_tags))

    def _close_suffix_length() -> int:
        return sum(len(tag.close_tag) for tag in open_tags)

    def _reset_current() -> None:
        nonlocal current, chunk_has_payload
        current = _open_prefix()
        chunk_has_payload = False

    def _flush_current() -> None:
        nonlocal current
        if not chunk_has_payload:
            return
        chunks.append(f"{current}{_close_suffix()}")
        _reset_current()

    def _append_text(segment: str) -> None:
        nonlocal current, chunk_has_payload
        remaining = segment
        while remaining:
            available = normalized_limit - len(current) - _close_suffix_length()
            if available <= 0:
                if not chunk_has_payload:
                    raise ValueError(
                        f"Telegram HTML chunk limit exceeded by tag overhead (limit={normalized_limit})"
                    )
                _flush_current()
                continue

            if len(remaining) <= available:
                current += remaining
                chunk_has_payload = True
                break

            split_at = _find_safe_split_index(remaining, available)
            if split_at <= 0:
                if not chunk_has_payload:
                    raise ValueError(
                        f"Telegram HTML chunk limit exceeded by leading entity (limit={normalized_limit})"
                    )
                _flush_current()
                continue

            current += remaining[:split_at]
            chunk_has_payload = True
            remaining = remaining[split_at:]
            _flush_current()

    _reset_current()
    last_index = 0
    for match in _HTML_TAG_RE.finditer(rendered_html):
        tag_start = match.start()
        tag_end = match.end()
        _append_text(rendered_html[last_index:tag_start])

        raw_tag = match.group(0)
        is_closing = match.group(1) == "</"
        tag_name = match.group(2).lower()
        is_self_closing = raw_tag.endswith("/>")

        if not is_closing:
            next_close_length = 0 if is_self_closing else len(f"</{tag_name}>")
            if chunk_has_payload and (
                len(current) + len(raw_tag) + _close_suffix_length() + next_close_length
                > normalized_limit
            ):
                _flush_current()

        current += raw_tag
        if is_self_closing:
            chunk_has_payload = True
        if is_closing:
            _pop_open_tag(open_tags, tag_name)
        elif not is_self_closing:
            open_tags.append(
                _OpenHtmlTag(
                    name=tag_name,
                    open_tag=raw_tag,
                    close_tag=f"</{tag_name}>",
                )
            )
        last_index = tag_end

    _append_text(rendered_html[last_index:])
    _flush_current()
    return chunks or [rendered_html]


def _find_safe_split_index(text: str, max_length: int) -> int:
    """Pick a natural split point that does not cut HTML entities."""
    if len(text) <= max_length:
        return len(text)

    candidate = _find_entity_safe_limit(text, max_length)
    search_start = max(0, candidate // 2)

    for needle in ("\n", " "):
        split_at = text.rfind(needle, search_start, candidate)
        if split_at != -1:
            return split_at + 1

    return candidate


def _find_entity_safe_limit(text: str, max_length: int) -> int:
    """Ensure the split point does not land inside an HTML entity."""
    normalized = max(1, int(max_length))
    if len(text) <= normalized:
        return len(text)

    last_ampersand = text.rfind("&", 0, normalized)
    if last_ampersand == -1:
        return normalized

    last_semicolon = text.rfind(";", 0, normalized)
    if last_ampersand < last_semicolon:
        return normalized

    entity_end = _find_html_entity_end(text, last_ampersand)
    if entity_end == -1 or entity_end < normalized:
        return normalized

    return last_ampersand


def _is_hex_char(ch: str) -> bool:
    """Return True for a single hexadecimal digit character."""
    return ch in "0123456789ABCDEFabcdef"


def _find_html_entity_end(text: str, start: int) -> int:
    """Return the index of the semicolon ending an entity, or -1."""
    if text[start] != "&":
        return -1

    index = start + 1
    if index >= len(text):
        return -1

    if text[index] == "#":
        index += 1
        if index >= len(text):
            return -1
        if text[index] in ("x", "X"):
            index += 1
            hex_start = index
            while index < len(text) and _is_hex_char(text[index]):
                index += 1
            if index == hex_start:
                return -1
        else:
            digit_start = index
            while index < len(text) and text[index].isdigit():
                index += 1
            if index == digit_start:
                return -1
    else:
        name_start = index
        while index < len(text) and text[index].isalnum():
            index += 1
        if index == name_start:
            return -1

    return index if index < len(text) and text[index] == ";" else -1


def _pop_open_tag(open_tags: List[_OpenHtmlTag], name: str) -> None:
    """Remove the most recent open tag of the given name."""
    for index in range(len(open_tags) - 1, -1, -1):
        if open_tags[index].name == name:
            open_tags.pop(index)
            return


def _render_block_sequence(nodes: Iterable[SyntaxTreeNode]) -> str:
    parts = [_render_block(node) for node in nodes]
    return "\n\n".join(part for part in parts if part)


def _render_block(node: SyntaxTreeNode) -> str:
    if node.type == "paragraph":
        return _render_inline_sequence(node.children or [])
    if node.type == "heading":
        inner = _render_inline_sequence(node.children or [])
        return f"<b>{inner}</b>" if inner else ""
    if node.type == "bullet_list":
        return _render_list(node, depth=0, ordered=False)
    if node.type == "ordered_list":
        return _render_list(node, depth=0, ordered=True)
    if node.type == "blockquote":
        inner = "\n".join(part for part in (_render_block(child) for child in node.children or []) if part)
        return f"<blockquote>{inner}</blockquote>" if inner else ""
    if node.type in {"fence", "code_block"}:
        code = node.content.rstrip("\n")
        return f"<pre><code>{escape_html(code)}</code></pre>"
    if node.type == "table":
        table_text = _render_table_text(node)
        return f"<pre><code>{escape_html(table_text)}</code></pre>" if table_text else ""
    if node.type == "hr":
        return "────────"
    if node.type == "inline":
        return _render_inline_sequence(node.children or [])
    if node.children:
        return _render_block_sequence(node.children)
    return escape_html(node.content or "")


def _render_list(node: SyntaxTreeNode, depth: int, ordered: bool) -> str:
    lines: List[str] = []
    item_number = 1

    for item in node.children or []:
        if item.type != "list_item":
            continue
        marker = f"{item_number}. " if ordered else "• "
        continuation = "  " * depth + " " * len(marker)
        prefix = "  " * depth + marker

        buffered_blocks: List[str] = []
        for child in item.children or []:
            if child.type in {"bullet_list", "ordered_list"}:
                if buffered_blocks:
                    lines.extend(_indent_block("\n".join(buffered_blocks), prefix, continuation))
                    buffered_blocks = []
                nested = _render_list(child, depth + 1, child.type == "ordered_list")
                if nested:
                    lines.extend(nested.splitlines())
            else:
                rendered = _render_block(child)
                if rendered:
                    buffered_blocks.append(rendered)

        if buffered_blocks:
            lines.extend(_indent_block("\n".join(buffered_blocks), prefix, continuation))

        item_number += 1

    return "\n".join(lines)


def _indent_block(block: str, first_prefix: str, continuation_prefix: str) -> List[str]:
    """Indent a multi-line block for list rendering."""
    lines = block.splitlines() or [block]
    output: List[str] = []
    for index, line in enumerate(lines):
        prefix = first_prefix if index == 0 else continuation_prefix
        output.append(f"{prefix}{line}" if line else prefix.rstrip())
    return output


def _render_table_text(node: SyntaxTreeNode) -> str:
    """Render a markdown table to aligned plain text."""
    headers: List[List[str]] = []
    rows: List[List[str]] = []

    for child in node.children or []:
        if child.type == "thead":
            headers = [_render_table_row(row) for row in child.children or [] if row.type == "tr"]
        elif child.type == "tbody":
            rows = [_render_table_row(row) for row in child.children or [] if row.type == "tr"]

    all_rows = headers + rows
    if not all_rows:
        return ""

    width_count = max(len(row) for row in all_rows)
    widths = [0] * width_count
    for row in all_rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def _format_row(cells: List[str]) -> str:
        padded = [
            (cells[index] if index < len(cells) else "").ljust(widths[index])
            for index in range(width_count)
        ]
        return f"| {' | '.join(padded)} |"

    rendered_rows: List[str] = []
    if headers:
        rendered_rows.extend(_format_row(row) for row in headers)
        rendered_rows.append("| " + " | ".join("-" * max(3, width) for width in widths) + " |")
    rendered_rows.extend(_format_row(row) for row in rows)
    return "\n".join(rendered_rows)


def _render_table_row(node: SyntaxTreeNode) -> List[str]:
    cells: List[str] = []
    for cell in node.children or []:
        rendered = _render_inline_sequence(cell.children or [])
        plain = telegram_html_to_plain_text(rendered) or ""
        cells.append(plain)
    return cells


def _render_inline_sequence(nodes: Iterable[SyntaxTreeNode]) -> str:
    return "".join(_render_inline(node) for node in nodes)


def _render_inline(node: SyntaxTreeNode) -> str:
    if node.type == "text":
        return _wrap_file_references(node.content or "")
    if node.type in {"softbreak", "hardbreak"}:
        return "\n"
    if node.type == "strong":
        return f"<b>{_render_inline_sequence(node.children or [])}</b>"
    if node.type == "em":
        return f"<i>{_render_inline_sequence(node.children or [])}</i>"
    if node.type == "s":
        return f"<s>{_render_inline_sequence(node.children or [])}</s>"
    if node.type == "code_inline":
        return f"<code>{escape_html(node.content or '')}</code>"
    if node.type == "link":
        href = (node.attrs or {}).get("href", "").strip()
        label = _render_inline_sequence(node.children or [])
        plain_label = telegram_html_to_plain_text(label) or ""
        if not href:
            return label
        if _is_file_reference(href, plain_label):
            return f"<code>{escape_html(plain_label)}</code>"
        return f'<a href="{escape_html_attr(href)}">{label}</a>'
    if node.type == "image":
        alt_text = _render_inline_sequence(node.children or [])
        src = (node.attrs or {}).get("src", "").strip()
        if alt_text and src:
            return f"{alt_text} ({escape_html(src)})"
        if src:
            return escape_html(src)
        return alt_text
    if node.type == "html_inline":
        return escape_html(node.content or "")
    if node.children:
        return _render_inline_sequence(node.children)
    return escape_html(node.content or "")


def _wrap_file_references(text: str) -> str:
    """Wrap standalone filename-like references in <code> tags."""
    if not text:
        return ""

    parts: List[str] = []
    last_index = 0
    for match in _FILE_REFERENCE_RE.finditer(text):
        prefix = match.group(1)
        filename = match.group(2)
        full_start = match.start()
        full_end = match.end()
        filename_start = full_start + len(prefix)

        if filename.startswith("//"):
            continue

        parts.append(escape_html(text[last_index:full_start]))
        parts.append(escape_html(prefix))
        parts.append(f"<code>{escape_html(filename)}</code>")
        last_index = filename_start + len(filename)
        if last_index < full_end:
            parts.append(escape_html(text[last_index:full_end]))
            last_index = full_end

    parts.append(escape_html(text[last_index:]))
    return "".join(parts)


def _is_file_reference(href: str, label: str) -> bool:
    """Return True when a link target/label should be rendered as a file reference."""
    normalized = (href or "").strip()
    if not normalized or "://" in normalized or normalized.startswith("mailto:"):
        return False
    return bool(_FILE_REFERENCE_RE.fullmatch(label or normalized))
