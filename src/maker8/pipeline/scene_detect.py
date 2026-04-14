"""SCENE_DETECT – FFmpeg-based scene boundary detection and post-processing.

Detects scene change points in video assets using FFmpeg's ``select`` filter
with ``scene`` score, then converts raw timestamps into non-overlapping
``SceneBoundary`` intervals.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from maker8.models.common import AssetWarning, RenderStage
from maker8.pipeline.context import PipelineContext
from maker8.pipeline.stage import Stage
from maker8.rendering.ffmpeg_runtime import resolve_ffmpeg_binary, resolve_ffprobe_binary
from maker8.retry import StageError
from maker8.utils.logging import get_logger
from render_contracts.render_spec import AssetSourceOptions, SceneBoundary

log = get_logger(__name__)

# ── Defaults ─────────────────────────────────────────────────────────────────

_DEFAULT_THRESHOLD = 0.35
_DEFAULT_SAMPLE_FPS = 3
_DEFAULT_SCALE_WIDTH = 640
_DEFAULT_DETECT_TIMEOUT = 120  # seconds

# Regex to extract pts_time from showinfo filter output.
# Line format: "[Parsed_showinfo_...] n:   0 pts:   1234 pts_time:1.234 ..."
_PTS_TIME_RE = re.compile(r"pts_time:\s*([\d.]+)")


# ── Command builder ──────────────────────────────────────────────────────────


def _build_detect_cmd(
    src: Path,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
    sample_fps: int = _DEFAULT_SAMPLE_FPS,
    scale_width: int = _DEFAULT_SCALE_WIDTH,
) -> list[str]:
    """Build the FFmpeg command for scene detection.

    Uses ``fps`` + ``scale`` for fast processing, ``select=gt(scene,T)`` to
    filter frames at scene boundaries, and ``showinfo`` to emit timestamps.
    """
    ffmpeg = resolve_ffmpeg_binary()
    vf = f"fps={sample_fps},scale={scale_width}:-2,select='gt(scene\\,{threshold})',showinfo"
    return [
        ffmpeg,
        "-hide_banner",
        "-i",
        str(src),
        "-vf",
        vf,
        "-an",
        "-f",
        "null",
        "-",
    ]


# ── Output parser ────────────────────────────────────────────────────────────


def _parse_showinfo_output(stderr: str) -> list[float]:
    """Extract ``pts_time`` values from FFmpeg showinfo filter output.

    Returns a sorted list of unique timestamps (seconds).  Non-numeric values
    and negative timestamps are silently skipped.
    """
    timestamps: list[float] = []
    for line in stderr.splitlines():
        if "showinfo" not in line.lower() and "pts_time" not in line:
            continue
        match = _PTS_TIME_RE.search(line)
        if match:
            try:
                ts = float(match.group(1))
                if ts >= 0:
                    timestamps.append(ts)
            except ValueError:
                continue
    return sorted(set(timestamps))


# ── Public API ───────────────────────────────────────────────────────────────


def detect_scenes(
    src: Path,
    options: AssetSourceOptions | None = None,
    *,
    timeout: int = _DEFAULT_DETECT_TIMEOUT,
) -> list[float]:
    """Run FFmpeg scene detection on *src* and return raw change-point timestamps.

    Parameters
    ----------
    src:
        Path to the video file.
    options:
        Per-asset detection options.  ``None`` fields use module defaults.
    timeout:
        Maximum seconds for the FFmpeg subprocess.

    Returns
    -------
    list[float]
        Sorted list of scene-change timestamps (seconds).  May be empty if no
        scene changes detected.

    Raises
    ------
    StageError
        On FFmpeg subprocess failure or timeout.
    """
    threshold = options.scene_detect_threshold if options else None
    if threshold is None:
        threshold = _DEFAULT_THRESHOLD
    sample_fps = options.scene_detect_sample_fps if options else None
    if sample_fps is None:
        sample_fps = _DEFAULT_SAMPLE_FPS
    scale_width = options.scene_detect_scale_width if options else None
    if scale_width is None:
        scale_width = _DEFAULT_SCALE_WIDTH

    cmd = _build_detect_cmd(
        src,
        threshold=threshold,
        sample_fps=sample_fps,
        scale_width=scale_width,
    )

    log.info("scene_detect.start", src=str(src), threshold=threshold)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as err:
        log.error("scene_detect.timeout", src=str(src), timeout=timeout)
        raise StageError(
            stage=RenderStage.SCENE_DETECT,
            code="SCENE_DETECT_TIMEOUT",
            message=f"Scene detection timed out after {timeout}s for {src.name}",
            retryable=True,
        ) from err

    # showinfo writes to stderr
    stderr = result.stderr or ""

    if result.returncode != 0:
        log.error(
            "scene_detect.ffmpeg_error",
            src=str(src),
            returncode=result.returncode,
            stderr=stderr[:500],
        )
        raise StageError(
            stage=RenderStage.SCENE_DETECT,
            code="SCENE_DETECT_FFMPEG_ERROR",
            message=f"FFmpeg scene detect failed (rc={result.returncode}): {stderr[:200]}",
            retryable=False,
        )

    timestamps = _parse_showinfo_output(stderr)
    log.info("scene_detect.success", src=str(src), scene_changes=len(timestamps))
    return timestamps


# ── Post-processing ──────────────────────────────────────────────────────────

_DEDUP_TOLERANCE = 0.1  # seconds – timestamps closer than this are merged
_DEFAULT_MIN_SCENE_LEN = 1.0  # seconds


def post_process_candidates(
    raw_timestamps: list[float],
    duration: float,
    *,
    min_scene_len_sec: float | None = None,
    max_scenes: int | None = None,
) -> list[SceneBoundary]:
    """Convert raw change-point timestamps to non-overlapping ``SceneBoundary`` intervals.

    1. Inject implicit ``0`` and ``duration`` boundaries.
    2. De-duplicate near-equal timestamps (within *_DEDUP_TOLERANCE*).
    3. Clamp all timestamps to ``[0, duration]``.
    4. Build ``(start, end)`` intervals from consecutive points.
    5. Merge intervals shorter than *min_scene_len_sec* into their longer neighbour.
    6. If total intervals exceed *max_scenes*, merge shortest intervals first.
    7. If result is empty, return a single ``[0, duration]`` fallback.

    The returned list always covers ``[0, duration]`` with no gaps or overlaps.
    """
    min_len = min_scene_len_sec if min_scene_len_sec is not None else _DEFAULT_MIN_SCENE_LEN

    if duration <= 0:
        return [SceneBoundary(start_sec=0.0, end_sec=0.0)]

    # 1. Inject implicit boundaries and clamp
    points = [0.0] + [max(0.0, min(t, duration)) for t in raw_timestamps] + [duration]
    points = sorted(set(points))

    # 2. De-duplicate near-equal timestamps
    deduped: list[float] = [points[0]]
    for p in points[1:]:
        if p - deduped[-1] >= _DEDUP_TOLERANCE:
            deduped.append(p)
    # Ensure duration endpoint is always present
    if abs(deduped[-1] - duration) > _DEDUP_TOLERANCE:
        deduped.append(duration)
    points = deduped

    # 3. Build intervals
    intervals: list[SceneBoundary] = []
    for i in range(len(points) - 1):
        start, end = points[i], points[i + 1]
        if end > start:
            intervals.append(SceneBoundary(start_sec=start, end_sec=end))

    if not intervals:
        return [SceneBoundary(start_sec=0.0, end_sec=duration)]

    # 4. Merge short scenes into neighbour
    intervals = _merge_short_scenes(intervals, min_len)

    # 5. Limit to max_scenes by merging shortest first
    if max_scenes is not None and len(intervals) > max_scenes:
        intervals = _limit_scenes(intervals, max_scenes)

    return intervals


def _merge_short_scenes(
    intervals: list[SceneBoundary],
    min_len: float,
) -> list[SceneBoundary]:
    """Merge any interval shorter than *min_len* into its longer neighbour."""
    if len(intervals) <= 1:
        return intervals

    merged: list[SceneBoundary] = list(intervals)
    changed = True
    while changed:
        changed = False
        new: list[SceneBoundary] = []
        i = 0
        while i < len(merged):
            seg = merged[i]
            seg_len = seg.end_sec - seg.start_sec
            if seg_len < min_len and len(merged) > 1:
                # Merge with the longer neighbour
                if i > 0 and (i == len(merged) - 1 or _seg_len(new[-1]) >= _seg_len(merged[i + 1])):
                    # Merge into previous
                    prev = new.pop()
                    new.append(SceneBoundary(start_sec=prev.start_sec, end_sec=seg.end_sec))
                elif i < len(merged) - 1:
                    # Merge into next
                    nxt = merged[i + 1]
                    new.append(SceneBoundary(start_sec=seg.start_sec, end_sec=nxt.end_sec))
                    i += 1  # skip next since we consumed it
                else:
                    new.append(seg)
                changed = True
            else:
                new.append(seg)
            i += 1
        merged = new
    return merged


def _limit_scenes(intervals: list[SceneBoundary], max_scenes: int) -> list[SceneBoundary]:
    """Merge shortest scenes until at most *max_scenes* remain."""
    while len(intervals) > max_scenes and len(intervals) > 1:
        # Find the shortest interval
        shortest_idx = min(range(len(intervals)), key=lambda i: _seg_len(intervals[i]))
        seg = intervals[shortest_idx]
        # Merge with adjacent (prefer merging with the shorter neighbour)
        if shortest_idx > 0 and (
            shortest_idx == len(intervals) - 1
            or _seg_len(intervals[shortest_idx - 1]) <= _seg_len(intervals[shortest_idx + 1])
        ):
            # Merge with previous
            prev = intervals[shortest_idx - 1]
            merged = SceneBoundary(start_sec=prev.start_sec, end_sec=seg.end_sec)
            intervals = intervals[: shortest_idx - 1] + [merged] + intervals[shortest_idx + 1 :]
        else:
            # Merge with next
            nxt = intervals[shortest_idx + 1]
            merged = SceneBoundary(start_sec=seg.start_sec, end_sec=nxt.end_sec)
            intervals = intervals[:shortest_idx] + [merged] + intervals[shortest_idx + 2 :]
    return intervals


def _seg_len(seg: SceneBoundary) -> float:
    return seg.end_sec - seg.start_sec


# ── Duration probe ───────────────────────────────────────────────────────────

_PROBE_TIMEOUT = 15  # seconds


def _probe_duration(src: Path) -> float | None:
    """Return the duration in seconds of *src* via ffprobe, or ``None`` on failure."""
    ffprobe = resolve_ffprobe_binary()
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(src),
            ],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


# ── Pipeline stage ───────────────────────────────────────────────────────────


class SceneDetectStage(Stage):
    """Run FFmpeg scene detection on video assets with ``scene_detect_enabled``."""

    @property
    def name(self) -> RenderStage:
        return RenderStage.SCENE_DETECT

    def execute(self, ctx: PipelineContext) -> None:  # noqa: C901
        for asset in ctx.render_spec.assets:
            if asset.type != "video":
                continue
            if not asset.source.options.scene_detect_enabled:
                continue
            if asset.id in ctx.failed_assets:
                continue

            src = ctx.downloaded_assets.get(asset.id)
            if src is None:
                log.debug(
                    "scene_detect.skip_no_download",
                    job_id=ctx.job_id,
                    asset_id=asset.id,
                )
                continue

            log.info("scene_detect.asset.start", job_id=ctx.job_id, asset_id=asset.id)
            report: dict[str, Any] = {"asset_id": asset.id, "status": "pending"}

            try:
                timestamps = detect_scenes(src, asset.source.options)
            except StageError:
                # Per-asset isolation: warn and continue.
                ctx.warnings.append(
                    AssetWarning(
                        asset_id=asset.id,
                        stage="SCENE_DETECT",
                        code="SCENE_DETECT_FAILED",
                        message=f"Scene detection failed for asset {asset.id}",
                        fallback_used="full_video",
                    )
                )
                report["status"] = "failed"
                ctx.scene_detect_reports.append(report)
                log.warning(
                    "scene_detect.asset.failed",
                    job_id=ctx.job_id,
                    asset_id=asset.id,
                )
                continue

            # Probe duration for interval construction
            duration = _probe_duration(src)
            if duration is None or duration <= 0:
                ctx.warnings.append(
                    AssetWarning(
                        asset_id=asset.id,
                        stage="SCENE_DETECT",
                        code="SCENE_DETECT_PROBE_FAILED",
                        message=f"Cannot determine duration for {asset.id}",
                        fallback_used="asset_skipped",
                    )
                )
                report["status"] = "probe_failed"
                ctx.scene_detect_reports.append(report)
                log.warning(
                    "scene_detect.probe_failed",
                    job_id=ctx.job_id,
                    asset_id=asset.id,
                )
                continue

            opts = asset.source.options
            boundaries = post_process_candidates(
                timestamps,
                duration,
                min_scene_len_sec=opts.scene_detect_min_scene_len_sec,
                max_scenes=opts.scene_detect_max_scenes,
            )

            if not timestamps:
                ctx.warnings.append(
                    AssetWarning(
                        asset_id=asset.id,
                        stage="SCENE_DETECT",
                        code="SCENE_DETECT_EMPTY",
                        message=f"No scene changes detected in {asset.id}; using full video",
                        fallback_used="full_video",
                    )
                )

            ctx.scene_candidates[asset.id] = boundaries
            report.update(
                {
                    "status": "ok",
                    "raw_count": len(timestamps),
                    "boundary_count": len(boundaries),
                    "duration": duration,
                }
            )
            ctx.scene_detect_reports.append(report)
            log.info(
                "scene_detect.asset.done",
                job_id=ctx.job_id,
                asset_id=asset.id,
                raw_count=len(timestamps),
                boundaries=len(boundaries),
            )
