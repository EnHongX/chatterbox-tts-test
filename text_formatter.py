from __future__ import annotations

import re
from dataclasses import dataclass


_SENTENCE_TERMINATORS = "。！？．!?…"
_CLOSING_WRAPPERS = "\"')]）】」』”’》>"
_QUOTE_OPENERS = "\"'「『“‘《<"
_HEADING_PATTERN = re.compile(
    r"^(?:\d+|[一二三四五六七八九十百千零〇两]+|第[\d一二三四五六七八九十百千零〇两]+[章节回部卷篇集])"
    r"[\s\.．、:：\-—]*\S{0,30}$"
)
_CJK_RANGE = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2A6DF),
)


@dataclass
class FormatResult:
    formatted_text: str
    original_char_count: int
    formatted_char_count: int
    paragraph_count: int


def format_text(
    text: str,
    *,
    paragraph_indent: bool = False,
    add_space_between_cjk_and_ascii: bool = False,
) -> FormatResult:
    original_char_count = len(text or "")
    if not text:
        return FormatResult("", 0, 0, 0)

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_blocks = re.split(r"\n\s*\n", normalized)

    cleaned_blocks: list[str] = []
    for raw in raw_blocks:
        lines = [line.strip() for line in raw.split("\n")]
        lines = [line for line in lines if line]
        if not lines:
            continue
        cleaned_blocks.append(_join_lines(lines))

    merged_blocks: list[str] = []
    for block in cleaned_blocks:
        starts_with_quote = bool(block) and block[0] in _QUOTE_OPENERS
        if (
            merged_blocks
            and not _ends_sentence(merged_blocks[-1])
            and not _is_heading(merged_blocks[-1])
            and not _is_heading(block)
            and not starts_with_quote
        ):
            merged_blocks[-1] = _concat(merged_blocks[-1], block)
        else:
            merged_blocks.append(block)

    final_blocks: list[str] = []
    for paragraph in merged_blocks:
        is_heading_paragraph = _is_heading(paragraph)
        paragraph = re.sub(r"[ \t]+", " ", paragraph)
        if not is_heading_paragraph:
            paragraph = re.sub(r"(?<=[\u4e00-\u9fff，。！？；：、]) +", "", paragraph)
            paragraph = re.sub(r" +(?=[\u4e00-\u9fff，。！？；：、])", "", paragraph)
        if add_space_between_cjk_and_ascii:
            paragraph = re.sub(
                r"([\u4e00-\u9fff])([A-Za-z0-9])", r"\1 \2", paragraph
            )
            paragraph = re.sub(
                r"([A-Za-z0-9])([\u4e00-\u9fff])", r"\1 \2", paragraph
            )
        paragraph = paragraph.strip()
        if paragraph_indent and not is_heading_paragraph:
            paragraph = "\u3000\u3000" + paragraph
        if paragraph:
            final_blocks.append(paragraph)

    formatted = "\n\n".join(final_blocks)
    return FormatResult(
        formatted_text=formatted,
        original_char_count=original_char_count,
        formatted_char_count=len(formatted),
        paragraph_count=len(final_blocks),
    )


def _join_lines(lines: list[str]) -> str:
    result = lines[0]
    for line in lines[1:]:
        result = _concat(result, line)
    return result


def _concat(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if _is_cjk(left[-1]) or _is_cjk(right[0]):
        return left + right
    return left + " " + right


def _ends_sentence(s: str) -> bool:
    if not s:
        return False
    stripped = s.rstrip(_CLOSING_WRAPPERS + " \t")
    if not stripped:
        return s[-1] in _SENTENCE_TERMINATORS
    return stripped[-1] in _SENTENCE_TERMINATORS


def _is_heading(s: str) -> bool:
    if not s:
        return False
    s = s.strip().lstrip("\u3000")
    if len(s) > 30:
        return False
    if any(ch in s for ch in "，,；;。！？.!?"):
        return False
    return bool(_HEADING_PATTERN.match(s))


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    for low, high in _CJK_RANGE:
        if low <= code <= high:
            return True
    return False
