import logging
import subprocess
import time
from dataclasses import dataclass

from app.config import Config

CHUNK_SIZE = 1024 * 1024


@dataclass
class ProbeResult:
    bitrate_bps: int | None
    buffer_path: str | None
    proc: subprocess.Popen[bytes]
    total_bytes: int


def _build_streamlink_cmd(url: str) -> list[str]:
    return [
        "streamlink",
        "--retry-streams",
        "30",
        "--retry-max",
        "0",
        "--stdout",
        url,
        "best",
    ]


def probe_stream(url: str, seconds: int, buffer_path: str) -> ProbeResult:
    """Buffer the stream start for `seconds`, measuring its average bitrate.

    The streamlink process is left running so its stdout can be spliced into
    the ffmpeg pipeline without losing the buffered footage.
    """
    proc = subprocess.Popen(
        _build_streamlink_cmd(url),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    total = 0
    read_start: float | None = None
    with open(buffer_path, "wb") as buf:
        deadline = time.time() + seconds
        while time.time() < deadline:
            data = proc.stdout.read(CHUNK_SIZE)
            if not data:
                break
            if read_start is None:
                read_start = time.time()
            total += len(data)
            buf.write(data)
    elapsed = time.time() - read_start if read_start is not None else 0.0
    bitrate = int(total * 8 / elapsed) if elapsed > 0 else None
    if bitrate:
        logging.info(
            "Bitrate probe: %s in %.1fs -> %.0f kbps",
            _format_bytes(total),
            elapsed,
            bitrate / 1000,
        )
    else:
        logging.warning("Bitrate probe produced no data")
    return ProbeResult(bitrate, buffer_path, proc, total)


def compute_segment_time(bitrate_bps: int | None) -> int:
    """Duration that keeps each segment under the size limit but as large as possible."""
    if bitrate_bps is None or bitrate_bps <= 0:
        logging.warning(
            "Bitrate unavailable, falling back to SEGMENT_TIME=%ds",
            Config.SEGMENT_TIME,
        )
        return Config.SEGMENT_TIME
    max_bytes = Config.MAX_SEGMENT_SIZE_BYTES * Config.SEGMENT_SIZE_MARGIN
    segment_time = max(Config.MIN_SEGMENT_TIME, int(max_bytes * 8 / bitrate_bps))
    logging.info(
        "Segment time for %.0f kbps: %ds (~%.2f GiB/segment)",
        bitrate_bps / 1000,
        segment_time,
        max_bytes / 1024**3,
    )
    return segment_time


def _format_bytes(num: int) -> str:
    if num < 1024:
        return f"{num}B"
    if num < 1024**2:
        return f"{num / 1024:.1f}KiB"
    return f"{num / 1024**2:.1f}MiB"
