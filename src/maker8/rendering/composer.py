"""Scene-based video composition using MoviePy 2.x.

``compose_video()`` is the single public entry point.  It receives a
``RenderInput`` dataclass (no dependency on ``pipeline.context``) and
returns the output file path together with ``OutputMeta``.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass, field
from pathlib import Path

from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    VideoClip,
    concatenate_videoclips,
)
from moviepy.audio.fx import AudioLoop, MultiplyVolume
from moviepy.video.fx import FadeIn, FadeOut

from maker8.models.common import OutputMeta
from maker8.models.spec import AudioTrack, Canvas, Defaults, RenderSpec, Scene
from maker8.plugins.base import EffectPlugin
from maker8.rendering.layers import build_layer_clip
from maker8.utils.color import hex_to_rgb
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_RENDER_TIMEOUT = 1800  # 30 minutes hard limit for write_videofile


class _RenderTimeout(Exception):
    """Raised when write_videofile exceeds the hard timeout."""


def _timeout_handler(signum: int, frame: object) -> None:
    raise _RenderTimeout(f"write_videofile exceeded {_RENDER_TIMEOUT}s timeout")


# ── Bridge dataclass (rendering ↔ pipeline) ─────────────────────────────────


@dataclass
class RenderInput:
    """All data the composer needs – built by ``pipeline.render``."""

    spec: RenderSpec
    asset_paths: dict[str, Path]  # asset_id → local file (normalised or downloaded)
    tts_audio: dict[str, tuple[Path, float]] = field(default_factory=dict)  # scene_id → (path, dur)
    output_dir: Path = Path("/tmp")
    job_id: str = ""
    effects_map: dict[str, EffectPlugin] = field(default_factory=dict)


# ── Public API ───────────────────────────────────────────────────────────────


def compose_video(ri: RenderInput) -> tuple[Path, OutputMeta]:
    """Compose all scenes and write the final video file.

    Returns ``(output_path, output_meta)``.
    """
    canvas = ri.spec.canvas
    defaults = ri.spec.defaults
    output_cfg = ri.spec.output

    scene_clips: list[VideoClip] = []
    transition_durs: list[float] = []

    for scene in ri.spec.scenes:
        clip = _build_scene(scene, ri, canvas, defaults)
        scene_clips.append(clip)
        transition_durs.append(
            scene.transition_out.duration if scene.transition_out else 0.0
        )

    final = _concatenate_with_transitions(scene_clips, transition_durs, canvas)

    output_path = ri.output_dir / f"{ri.job_id}.mp4"
    log.info(
        "composer.write.start",
        job_id=ri.job_id,
        path=str(output_path),
        duration=final.duration,
        timeout_sec=_RENDER_TIMEOUT,
    )

    # Set a hard timeout so a stuck encode cannot block the worker forever.
    prev_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(_RENDER_TIMEOUT)
    try:
        final.write_videofile(
            str(output_path),
            fps=canvas.fps,
            codec=output_cfg.codec,
            audio_codec=output_cfg.audio_codec,
            bitrate=output_cfg.bitrate,
            preset=output_cfg.preset,
            ffmpeg_params=["-pix_fmt", output_cfg.pix_fmt],
            audio_bitrate=output_cfg.audio_bitrate,
            logger="bar",
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev_handler)

    size_bytes = output_path.stat().st_size
    log.info(
        "composer.write.returned",
        job_id=ri.job_id,
        path=str(output_path),
        output_size_bytes=size_bytes,
        duration=final.duration,
    )

    meta = OutputMeta(
        duration=round(final.duration, 3),
        w=canvas.w,
        h=canvas.h,
        fps=canvas.fps,
        size_bytes=size_bytes,
    )

    # Clean up
    final.close()
    for c in scene_clips:
        c.close()

    return output_path, meta


# ── Scene builder ────────────────────────────────────────────────────────────


def _build_scene(
    scene: Scene,
    ri: RenderInput,
    canvas: Canvas,
    defaults: Defaults,
) -> VideoClip:
    """Compose layers + audio for a single scene."""

    timing = defaults.scene_timing
    tts_entry = ri.tts_audio.get(scene.scene_id)

    # ── Duration policy ──────────────────────────────────────────────
    if scene.duration is not None:
        duration = scene.duration
    elif tts_entry:
        _, tts_dur = tts_entry
        duration = timing.head_pad_sec + tts_dur + timing.tail_pad_sec
    else:
        duration = 5.0  # fallback

    # ── Background colour clip ───────────────────────────────────────
    bg = ColorClip(
        size=(canvas.w, canvas.h),
        color=hex_to_rgb(canvas.bg),
    ).with_duration(duration)

    # ── Visual layers ────────────────────────────────────────────────
    layer_clips: list[VideoClip] = [bg]
    for layer in scene.layers:
        lc = build_layer_clip(layer, ri.asset_paths, duration, canvas)
        if lc is not None:
            layer_clips.append(lc)

    composite = CompositeVideoClip(layer_clips, size=(canvas.w, canvas.h))
    composite = composite.with_duration(duration)

    # ── Audio ────────────────────────────────────────────────────────
    audio_clips: list[AudioFileClip] = []

    if tts_entry:
        narr_path, _ = tts_entry
        narr = AudioFileClip(str(narr_path))
        narr = narr.with_start(timing.head_pad_sec)
        audio_clips.append(narr)

    for track in scene.audio_tracks:
        acl = _build_audio_track(track, ri.asset_paths, duration)
        if acl is not None:
            audio_clips.append(acl)

    if audio_clips:
        composite = composite.with_audio(CompositeAudioClip(audio_clips))

    # ── Effects plugins ────────────────────────────────────────────────
    for effect_inst in scene.effects:
        plugin = ri.effects_map.get(effect_inst.plugin_id)
        if plugin:
            composite = plugin.apply(None, composite, effect_inst.model_dump())

    return composite


# ── Audio track helper ───────────────────────────────────────────────────────


def _build_audio_track(
    track: AudioTrack,
    asset_paths: dict[str, Path],
    scene_duration: float,
) -> AudioFileClip | None:
    if track.asset_ref not in asset_paths:
        return None

    clip = AudioFileClip(str(asset_paths[track.asset_ref]))

    if track.trim:
        t_start = track.trim.in_
        t_end = track.trim.out or clip.duration
        clip = clip.subclipped(t_start, min(t_end, clip.duration))

    if track.volume != 1.0:
        clip = clip.with_effects([MultiplyVolume(track.volume)])

    if track.loop:
        clip = clip.with_effects([AudioLoop(duration=scene_duration)])

    return clip


# ── Transition concatenation ─────────────────────────────────────────────────


def _concatenate_with_transitions(
    clips: list[VideoClip],
    transition_durs: list[float],
    canvas: Canvas,
) -> VideoClip:
    """Concatenate *clips*, overlapping by the transition duration."""
    if not clips:
        return ColorClip(size=(canvas.w, canvas.h), color=(0, 0, 0)).with_duration(0)

    has_transitions = any(d > 0 for d in transition_durs)
    if not has_transitions:
        return concatenate_videoclips(clips, method="compose")

    # Calculate start times accounting for overlap
    starts: list[float] = [0.0]
    for i in range(1, len(clips)):
        overlap = transition_durs[i - 1]
        starts.append(starts[-1] + clips[i - 1].duration - overlap)

    positioned: list[VideoClip] = []
    for i, clip in enumerate(clips):
        c = clip.with_start(starts[i])
        # Fade-out for current scene tail
        if transition_durs[i] > 0:
            c = c.with_effects([FadeOut(transition_durs[i])])
        # Fade-in from previous scene overlap
        if i > 0 and transition_durs[i - 1] > 0:
            c = c.with_effects([FadeIn(transition_durs[i - 1])])
        positioned.append(c)

    total_dur = starts[-1] + clips[-1].duration
    return CompositeVideoClip(positioned, size=(canvas.w, canvas.h)).with_duration(total_dur)

