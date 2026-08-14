#!/usr/bin/env python3
"""Convert a stream-archiver chat log into an ASS subtitle file.

The chat log lines look like:

    [2026-08-14 07:09:04] xmasflowx: message text

Timestamps are wall-clock time in Config.TIMEZONE (default Europe/Moscow),
while the segment/session name carries the UTC start time, e.g.:

    rostislav_999_2026-08-14T04-09-02_chat.txt

The generated subtitle renders a Twitch-style chat overlay: a fixed panel in
the top-right corner of the video (default 15% of width / 50% of height) that
holds as many recent messages as fit. New messages appear at the bottom of the
panel and push older ones up; the oldest message that no longer fits is hidden.

ASS (not SRT) is required because SRT cannot do positioning/boxes. VLC loads
.ass files the same way as .srt (Video > Subtitles > Add Subtitle File).

Usage:
    python scripts/chat_to_ass.py data/twitch/segments/rostislav_999_2026-08-14T04-09-02_chat.txt
    python scripts/chat_to_ass.py <chat.txt> --segment <video.mp4>
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.*?): (.*)$")
SESSION_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")

DEFAULT_TZ = os.getenv("TIMEZONE", "Europe/Moscow")
DEFAULT_SEGMENT_TIME = float(os.getenv("SEGMENT_TIME", "2630"))

USER_COLOR = "&H00B2D700"
TEXT_COLOR = "&H00FFFFFF"
BUBBLE_COLOR = "&H0A0E12"
BUBBLE_ALPHA = "&H66"
BORDER = 2.5
PAD_X = 8
PAD_Y = 5


@dataclass
class Message:
    offset: float
    user: str
    content: str
    lines: list[str]
    line_count: int
    start: float
    end: float
    hidden: bool = False
    segments: list[tuple[float, float, float]] = None  # (t0, t1, y)


@dataclass
class Geometry:
    width: int
    height: int
    font_size: int
    line_height: int
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    box_lines: int
    max_text_w: float
    bubble_h: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a stream-archiver chat log into an ASS chat-overlay subtitle."
    )
    parser.add_argument("chat_file", type=Path, help="Path to the *_chat.txt log file")
    parser.add_argument(
        "--segment",
        type=Path,
        help="Matching video segment (.mp4). Used for resolution, timestamp alignment "
        "and the output filename (so VLC auto-loads the subtitle).",
    )
    parser.add_argument(
        "--resolution",
        default=None,
        help="Video resolution WxH (e.g. 1920x1080). Defaults to the --segment size or 1920x1080.",
    )
    parser.add_argument(
        "--tz",
        default=DEFAULT_TZ,
        help=f"Timezone of the chat timestamps (default: {DEFAULT_TZ})",
    )
    parser.add_argument(
        "--font-size",
        type=float,
        default=0.0,
        help="Subtitle font size in pixels (default: 2.2%% of video height)",
    )
    parser.add_argument(
        "--display",
        type=float,
        default=6.0,
        help="How many seconds each message stays visible (default: 6)",
    )
    parser.add_argument(
        "--box-width",
        type=float,
        default=15.0,
        help="Max box width as %% of video width (default: 15)",
    )
    parser.add_argument(
        "--box-height",
        type=float,
        default=50.0,
        help="Max box height as %% of video height (default: 50)",
    )
    parser.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Shift all subtitles by this many seconds (negative = earlier)",
    )
    parser.add_argument("--out", type=Path, help="Output .ass path (default: next to chat file)")
    return parser.parse_args()


def derive_session_start(filename: str) -> datetime | None:
    m = SESSION_RE.search(filename)
    if not m:
        return None
    parts = [int(p) for p in m.groups()]
    return datetime(*parts, tzinfo=UTC)


def probe_width_height(video: Path) -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        w, h = out.stdout.strip().split("x")
        return int(w), int(h)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def probe_duration(video: Path) -> float | None:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def segment_start_seconds(video: Path) -> float:
    try:
        prefix, index_str = video.stem.rsplit("_", 1)
        index = int(index_str)
    except (ValueError, IndexError):
        return 0.0
    if index == 0:
        return 0.0
    total = 0.0
    for i in range(index):
        duration = probe_duration(video.parent / f"{prefix}_{i}.mp4")
        if duration is None:
            print(
                f"warning: could not measure {prefix}_{i}.mp4 duration, "
                f"falling back to SEGMENT_TIME={DEFAULT_SEGMENT_TIME}s",
                file=sys.stderr,
            )
            total += DEFAULT_SEGMENT_TIME
        else:
            total += duration
    return total


def sanitize(text: str) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.replace("\\", "\\\\").replace("{", "").replace("}", "")
    return text.strip()


def char_width(char: str, font_size: float) -> float:
    if ord(char) >= 0x1100:
        return font_size
    return 0.55 * font_size


def wrap_text(text: str, max_width: float, font_size: float, max_lines: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    current_w = 0.0
    space_w = 0.3 * font_size

    def hard_break(word: str) -> list[str]:
        pieces: list[str] = []
        chunk = ""
        chunk_w = 0.0
        for ch in word:
            w = char_width(ch, font_size)
            if chunk and chunk_w + w > max_width:
                pieces.append(chunk)
                chunk = ch
                chunk_w = w
            else:
                chunk += ch
                chunk_w += w
        if chunk:
            pieces.append(chunk)
        return pieces

    for word in words:
        ww = sum(char_width(c, font_size) for c in word)
        if ww > max_width:
            for piece in hard_break(word):
                if current:
                    lines.append(current)
                    current = ""
                    current_w = 0.0
                lines.append(piece)
            continue
        if not current:
            current = word
            current_w = ww
        elif current_w + space_w + ww <= max_width:
            current += " " + word
            current_w += space_w + ww
        else:
            lines.append(current)
            current = word
            current_w = ww
    if current:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        ellipsis = "…"
        while last and sum(char_width(c, font_size) for c in last) + char_width(
            ellipsis, font_size
        ) > max_width:
            last = last[:-1]
        lines[-1] = last + ellipsis
    return lines


def compute_geometry(
    width: int, height: int, font_size: float, box_width_pct: float, box_height_pct: float
) -> Geometry:
    margin = max(12, min(48, round(0.02 * width)))
    box_w = max(160, round(width * box_width_pct / 100.0))
    box_h = max(120, round(height * box_height_pct / 100.0))
    if font_size <= 0:
        font_size = round(height * 0.022)
    font_size = max(10.0, font_size)
    line_height = round(font_size * 1.35)
    box_lines = max(1, (box_h - 2 * PAD_Y) // line_height)
    max_text_w = box_w - 2 * PAD_X - 2 * BORDER
    box_x = width - margin - box_w
    box_y = margin
    return Geometry(
        width=width,
        height=height,
        font_size=int(font_size),
        line_height=line_height,
        box_x=box_x,
        box_y=box_y,
        box_w=box_w,
        box_h=box_h,
        box_lines=box_lines,
        max_text_w=max_text_w,
        bubble_h=line_height,
    )


def build_stack(messages: list[Message], geo: Geometry) -> None:
    ends = {m.start for m in messages} | {m.end for m in messages}
    boundaries = sorted(t for t in ends if t >= 0)
    if not boundaries:
        return

    box_bottom = geo.box_y + geo.box_h

    for b0, b1 in pairwise(boundaries):
        active = [m for m in messages if m.start <= b0 < m.end]
        if not active:
            continue

        visible: list[Message] = []
        used_lines = 0
        for m in reversed(active):
            if m.hidden:
                continue
            if used_lines + m.line_count > geo.box_lines:
                m.hidden = True
                continue
            visible.append(m)
            used_lines += m.line_count

        cursor = box_bottom
        for m in visible:
            h = m.line_count * geo.bubble_h
            y = cursor - h
            m.segments.append((b0, b1, y))
            cursor = y


def merge_segments(messages: list[Message]) -> None:
    for m in messages:
        merged: list[tuple[float, float, float]] = []
        for seg in m.segments:
            if merged and merged[-1][2] == seg[2] and merged[-1][1] == seg[0]:
                merged[-1] = (merged[-1][0], seg[1], seg[2])
            else:
                merged.append(seg)
        m.segments = merged


def ass_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    cs = round((seconds - int(seconds)) * 100)
    if cs >= 100:
        seconds += 1
        cs = 0
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_header(geo: Geometry) -> str:
    return f"""[Script Info]
; Chat overlay generated by scripts/chat_to_ass.py
ScriptType: v4.00+
PlayResX: {geo.width}
PlayResY: {geo.height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Chat,DejaVu Sans,{geo.font_size},{TEXT_COLOR},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{BORDER},0,7,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""


def escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "｛").replace("}", "｝")


def render(messages: list[Message], geo: Geometry) -> str:
    lines = [ass_header(geo)]
    events: list[tuple[float, str]] = []

    for m in messages:
        msg_lines = m.lines
        first = msg_lines[0]
        prefix = f"{m.user}: "
        if first.startswith(prefix):
            rest = first[len(prefix):]
        else:
            rest = first
        body = "\\N".join([rest] + msg_lines[1:])
        for t0, t1, y in m.segments:
            bubble_h = m.line_count * geo.bubble_h
            bubble = (
                f"{{\\an7\\pos({geo.box_x},{y})\\p1\\1c{BUBBLE_COLOR}\\1a{BUBBLE_ALPHA}"
                f"\\fad(120,120)}}m 0 0 l {geo.box_w} 0 l {geo.box_w} {bubble_h} "
                f"l 0 {bubble_h} l 0 0{{\\p0}}"
            )
            text_y = y + (bubble_h - m.line_count * geo.font_size) // 2
            text = (
                f"{{\\an7\\pos({geo.box_x + PAD_X},{text_y})\\fad(120,120)}}"
                f"{{\\b1\\1c{USER_COLOR}}}{escape_ass_text(m.user)}"
                f"{{\\b0\\1c{TEXT_COLOR}}}: {body}"
            )
            events.append((t0, f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},Chat,,0,0,0,,{bubble}"))
            events.append((t0, f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},Chat,,0,0,0,,{text}"))

    lines.extend(line for _, line in sorted(events, key=lambda e: e[0]))
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()

    try:
        tz = ZoneInfo(args.tz)
    except ZoneInfoNotFoundError:
        print(f"warning: unknown timezone {args.tz!r}, using {DEFAULT_TZ}", file=sys.stderr)
        tz = ZoneInfo(DEFAULT_TZ)

    if not args.chat_file.exists():
        print(f"error: chat file not found: {args.chat_file}", file=sys.stderr)
        return 1

    session_start = derive_session_start(args.chat_file.name)
    if session_start is None:
        print(
            f"error: cannot find session start timestamp in filename {args.chat_file.name!r}",
            file=sys.stderr,
        )
        return 1

    shift = args.offset
    if args.segment is not None:
        shift -= segment_start_seconds(args.segment)

    raw: list[Message] = []
    with open(args.chat_file, encoding="utf-8") as fh:
        for line in fh:
            match = LINE_RE.match(line)
            if not match:
                continue
            ts_str, user, content = match.groups()
            try:
                local = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
            except ValueError:
                continue
            offset = (local.astimezone(UTC) - session_start).total_seconds() + shift
            if offset < 0:
                offset = 0.0
            raw.append(Message(
                offset=offset,
                user=sanitize(user),
                content=sanitize(content),
                lines=[],
                line_count=0,
                start=0.0,
                end=0.0,
                segments=[],
            ))

    if not raw:
        print("error: no chat lines parsed", file=sys.stderr)
        return 1

    width = height = None
    if args.resolution:
        try:
            width, height = (int(p) for p in args.resolution.lower().split("x"))
        except ValueError:
            print(f"error: invalid --resolution {args.resolution!r}", file=sys.stderr)
            return 1
    elif args.segment is not None:
        probed = probe_width_height(args.segment)
        if probed is not None:
            width, height = probed
    if not width or not height:
        width, height = 1920, 1080
        print(
            f"warning: resolution unknown, using {width}x{height} "
            "(pass --segment or --resolution to fix)",
            file=sys.stderr,
        )

    geo = compute_geometry(width, height, args.font_size, args.box_width, args.box_height)
    text_width = geo.max_text_w

    messages: list[Message] = []
    for i, m in enumerate(sorted(raw, key=lambda x: x.offset)):
        m.lines = wrap_text(
            f"{m.user}: {m.content}",
            text_width,
            geo.font_size,
            geo.box_lines,
        )
        m.line_count = len(m.lines)
        m.start = m.offset
        m.end = m.offset + args.display
        messages.append(m)

    build_stack(messages, geo)
    merge_segments(messages)

    out_path = args.out
    if out_path is None:
        if args.segment is not None:
            out_path = args.segment.with_suffix(".ass")
        else:
            out_path = args.chat_file.with_suffix(".ass")
    out_path = out_path.resolve()

    out_path.write_text(render(messages, geo), encoding="utf-8")
    print(f"wrote {out_path} ({len(messages)} messages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
