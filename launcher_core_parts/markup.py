from __future__ import annotations

import re

_TURN_RE = re.compile(r"(\**LLM Running \(Turn \d+\) \.\.\.\*\**)")
_CLEAN_BLOCK_RE = re.compile(r"(?P<fence>`{3,})[\s\S]*?(?P=fence)|<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_SUMMARY_RE = re.compile(r"<summary>\s*((?:(?!<summary>).)*?)\s*</summary>", re.DOTALL)

_RE_THINKING = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_RE_SUMMARY = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL | re.IGNORECASE)
_RE_TOOLUSE = re.compile(r"<tool_use>(.*?)</tool_use>", re.DOTALL | re.IGNORECASE)
_RE_FILE_CONTENT = re.compile(r"<file_content>(.*?)</file_content>", re.DOTALL | re.IGNORECASE)
_FILE_TAG_RE = re.compile(r"\[FILE:([^\]]+)\]")


def fold_turns(text):
    parts = _TURN_RE.split(text or "")
    if len(parts) < 4:
        return [{"type": "text", "content": text or ""}]
    segments = []
    if parts[0].strip():
        segments.append({"type": "text", "content": parts[0]})
    turns = []
    for i in range(1, len(parts), 2):
        marker = parts[i]
        content = parts[i + 1] if i + 1 < len(parts) else ""
        turns.append((marker, content))
    for idx, (marker, content) in enumerate(turns):
        if idx < len(turns) - 1:
            cleaned = _CLEAN_BLOCK_RE.sub("", content)
            match = _SUMMARY_RE.search(cleaned)
            if match:
                title = match.group(1).strip().split("\n")[0]
                if len(title) > 50:
                    title = title[:50] + "..."
            else:
                title = marker.strip("*")
            segments.append({"type": "fold", "title": title, "content": content})
        else:
            segments.append({"type": "text", "content": marker + content})
    return segments


def _normalize_markup(text):
    if not text:
        return ""
    text = _RE_THINKING.sub("", text)
    text = _RE_SUMMARY.sub(lambda m: f"\n> {m.group(1).strip()}\n", text)
    text = _RE_TOOLUSE.sub(lambda m: f"\n```tool_use\n{m.group(1).strip()}\n```\n", text)
    text = _RE_FILE_CONTENT.sub(lambda m: f"\n```file_content\n{m.group(1).strip()}\n```\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _assistant_visible_markup(text):
    raw = text or ""
    summaries = [m.strip() for m in _RE_SUMMARY.findall(raw) if m.strip()]
    visible = _RE_SUMMARY.sub("", raw)
    visible = _normalize_markup(visible)
    visible = _FILE_TAG_RE.sub(r"\1", visible).strip()
    if visible:
        return visible
    return "\n\n".join(summaries).strip()


def _strip_turn_marker(text):
    return _TURN_RE.sub("", text or "", count=1).strip()


def _turn_marker_title(text):
    m = _TURN_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).strip("*").strip()
