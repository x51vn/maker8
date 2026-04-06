"""Scene-based video composition using MoviePy 2.x.

``compose_video()`` is the single public entry point.  It receives a
``RenderInput`` dataclass (no dependency on ``pipeline.context``) and
returns the output file path together with ``OutputMeta``.

**Scene-level rendering (WS-D):** When no inter-scene transitions are
defined, each scene is rendered to an intermediate MP4, then FFmpeg's
``concat`` demuxer joins them.  This isolates scene memory, enables
per-scene profiling, and avoids a single enormous MoviePy graph.
"""

from __future__ import annotations

import signal
import subprocess
import tempfile
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

from maker8.models.common import AssetWarning, OutputMeta
from maker8.models.spec import AudioTrack, Canvas, Defaults, OutputConfig, RenderSpec, Scene
from maker8.observability.helpers import Timer
from maker8.observability.metrics import RENDER_FPS, SCENE_RENDER_DURATION
from maker8.plugins.base import EffectPlugin
from maker8.rendering.encoder import EncoderConfig, _cpu_config, resolve_encoder
from maker8.rendering.ffmpeg_runtime import resolve_ffmpeg_binary
from maker8.rendering.layers import build_layer_clip
from maker8.rendering.perf_profile import PerfProfile
from maker8.utils.color import hex_to_rgb
from maker8.utils.logging import get_logger

log = get_logger(__name__)

_RENDER_TIMEOUT = 1800  # 30 minutes hard limit for write_videofile


class _RenderTimeoutError(Exception):
    """Raised when write_videofile exceeds the hard timeout."""


def _timeout_handler(signum: int, frame: object) -> None:
    raise _RenderTimeoutError(f"write_videofile exceeded {_RENDER_TIMEOUT}s timeout")


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
    perf_profile: PerfProfile | None = None
    warnings: list[AssetWarning] = field(default_factory=list)


# ── Public API ───────────────────────────────────────────────────────────────


def compose_video(ri: RenderInput) -> tuple[Path, OutputMeta]:
    """Compose all scenes and write the final video file.

    Returns ``(output_path, output_meta)``.

    **Strategy selection:**

    * When no inter-scene transitions exist, each scene is rendered to an
      intermediate MP4 and the results are joined by FFmpeg's ``concat``
      demuxer (fast, low memory).
    * When transitions overlap scenes, the legacy MoviePy graph approach
      is used so that fade/slide overlaps remain correct.
    """
    canvas = ri.spec.canvas
    defaults = ri.spec.defaults
    output_cfg = ri.spec.output

    # Apply perf-profile overrides
    profile = ri.perf_profile
    effective_fps = canvas.fps
    if profile and profile.fps_cap > 0:
        effective_fps = min(canvas.fps, profile.fps_cap)
    allow_python_effects = profile.allow_python_effects if profile else True

    output_path = ri.output_dir / f"{ri.job_id}.mp4"
    encoder = resolve_encoder(output_cfg.codec, output_cfg.preset, output_cfg.pix_fmt)

    # Decide strategy: scene-level render (fast) or single-graph (transition)
    has_transitions = any(
        scene.transition_out and scene.transition_out.duration > 0 for scene in ri.spec.scenes
    )

    if not has_transitions and len(ri.spec.scenes) > 1:
        return _compose_scene_level(
            ri,
            canvas,
            defaults,
            output_cfg,
            encoder,
            effective_fps,
            allow_python_effects,
            output_path,
        )

    return _compose_single_graph(
        ri,
        canvas,
        defaults,
        output_cfg,
        encoder,
        effective_fps,
        allow_python_effects,
        output_path,
    )


# ── Scene-level render strategy (no transitions) ────────────────────────────


def _compose_scene_level(
    ri: RenderInput,
    canvas: Canvas,
    defaults: Defaults,
    output_cfg: OutputConfig,
    encoder: EncoderConfig,
    effective_fps: int,
    allow_python_effects: bool,
    output_path: Path,
) -> tuple[Path, OutputMeta]:
    """Render each scene to an intermediate MP4, then FFmpeg-concat them."""
    log.info(
        "composer.strategy.scene_level",
        job_id=ri.job_id,
        scene_count=len(ri.spec.scenes),
    )

    scene_files: list[Path] = []
    total_duration = 0.0

    try:
        for idx, scene in enumerate(ri.spec.scenes):
            scene_timer = Timer().start()
            clip = _build_scene(
                scene,
                ri,
                canvas,
                defaults,
                allow_python_effects,
                use_ffmpeg_postprocess=True,
            )
            scene_timer.stop()

            SCENE_RENDER_DURATION.observe(scene_timer.elapsed_sec)

            # Write scene to intermediate file
            scene_path = ri.output_dir / f"{ri.job_id}_scene_{idx:03d}.mp4"
            write_timer = Timer().start()
            try:
                _write_final_video(
                    clip,
                    scene_path,
                    ri.job_id,
                    effective_fps,
                    output_cfg,
                    encoder,
                )
            except _RenderTimeoutError:
                raise
            except Exception as exc:
                if encoder.is_gpu:
                    log.warning(
                        "composer.scene.gpu_failed",
                        job_id=ri.job_id,
                        scene_index=idx,
                        error=str(exc),
                    )
                    scene_path.unlink(missing_ok=True)
                    cpu_encoder = _cpu_config(output_cfg.preset, output_cfg.pix_fmt)
                    _write_final_video(
                        clip,
                        scene_path,
                        ri.job_id,
                        effective_fps,
                        output_cfg,
                        cpu_encoder,
                    )
                else:
                    raise
            write_timer.stop()

            # Apply FFmpeg-native effects as post-process
            scene_path = _apply_ffmpeg_effects(
                scene,
                ri,
                scene_path,
                canvas,
                clip.duration,
            )

            total_duration += clip.duration
            scene_files.append(scene_path)
            clip.close()

            log.info(
                "composer.scene.rendered",
                job_id=ri.job_id,
                scene_id=scene.scene_id,
                scene_index=idx,
                layers=len(scene.layers),
                effects=len(scene.effects),
                duration_sec=round(clip.duration, 3),
                build_sec=round(scene_timer.elapsed_sec, 3),
                write_sec=round(write_timer.elapsed_sec, 3),
            )

        # Concatenate with FFmpeg
        _ffmpeg_concat(scene_files, output_path, ri.job_id)

    finally:
        # Clean up intermediate scene files
        for sf in scene_files:
            sf.unlink(missing_ok=True)

    size_bytes = output_path.stat().st_size
    log.info(
        "composer.write.returned",
        job_id=ri.job_id,
        path=str(output_path),
        output_size_bytes=size_bytes,
        duration=total_duration,
        codec=encoder.codec,
        is_gpu=encoder.is_gpu,
        strategy="scene_level",
    )

    return output_path, OutputMeta(
        duration=round(total_duration, 3),
        w=canvas.w,
        h=canvas.h,
        fps=effective_fps,
        size_bytes=size_bytes,
    )


# ── Single-graph strategy (legacy, for transitions) ─────────────────────────


def _compose_single_graph(
    ri: RenderInput,
    canvas: Canvas,
    defaults: Defaults,
    output_cfg: OutputConfig,
    encoder: EncoderConfig,
    effective_fps: int,
    allow_python_effects: bool,
    output_path: Path,
) -> tuple[Path, OutputMeta]:
    """Build a single MoviePy composite graph and encode it."""
    log.info(
        "composer.strategy.single_graph",
        job_id=ri.job_id,
        scene_count=len(ri.spec.scenes),
    )

    scene_clips: list[VideoClip] = []
    transition_durs: list[float] = []

    for idx, scene in enumerate(ri.spec.scenes):
        scene_timer = Timer().start()
        clip = _build_scene(scene, ri, canvas, defaults, allow_python_effects)
        scene_timer.stop()
        scene_clips.append(clip)
        transition_durs.append(scene.transition_out.duration if scene.transition_out else 0.0)

        SCENE_RENDER_DURATION.observe(scene_timer.elapsed_sec)
        log.info(
            "composer.scene.built",
            job_id=ri.job_id,
            scene_id=scene.scene_id,
            scene_index=idx,
            layers=len(scene.layers),
            effects=len(scene.effects),
            duration_sec=round(clip.duration, 3),
            build_sec=round(scene_timer.elapsed_sec, 3),
        )

    final = _concatenate_with_transitions(scene_clips, transition_durs, canvas)

    try:
        _write_final_video(final, output_path, ri.job_id, effective_fps, output_cfg, encoder)
    except _RenderTimeoutError:
        raise
    except Exception as exc:
        if encoder.is_gpu:
            log.warning(
                "composer.gpu_encode_failed",
                job_id=ri.job_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            output_path.unlink(missing_ok=True)
            cpu_encoder = _cpu_config(output_cfg.preset, output_cfg.pix_fmt)
            _write_final_video(
                final,
                output_path,
                ri.job_id,
                effective_fps,
                output_cfg,
                cpu_encoder,
            )
        else:
            raise

    size_bytes = output_path.stat().st_size
    log.info(
        "composer.write.returned",
        job_id=ri.job_id,
        path=str(output_path),
        output_size_bytes=size_bytes,
        duration=final.duration,
        codec=encoder.codec,
        is_gpu=encoder.is_gpu,
        strategy="single_graph",
    )

    meta = OutputMeta(
        duration=round(final.duration, 3),
        w=canvas.w,
        h=canvas.h,
        fps=effective_fps,
        size_bytes=size_bytes,
    )

    final.close()
    for c in scene_clips:
        c.close()

    return output_path, meta


# ── FFmpeg concat demuxer ────────────────────────────────────────────────────


def _ffmpeg_concat(scene_files: list[Path], output_path: Path, job_id: str) -> None:
    """Concatenate intermediate scene MP4s using ``ffmpeg -f concat``.

    This is a stream-copy concat (no re-encode) — very fast since all
    scenes share the same codec, resolution, and frame rate.
    """
    ffmpeg = resolve_ffmpeg_binary()

    # Build the concat list file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        dir=str(output_path.parent),
    ) as f:
        for sf in scene_files:
            # FFmpeg concat format: file 'path'
            f.write(f"file '{sf}'\n")
        list_path = Path(f.name)

    log.info(
        "composer.concat.start",
        job_id=job_id,
        scene_count=len(scene_files),
    )

    concat_timer = Timer().start()
    try:
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",  # stream copy — no re-encode
            str(output_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            log.error(
                "composer.concat.failed",
                job_id=job_id,
                returncode=result.returncode,
                stderr=result.stderr[:500],
            )
            raise RuntimeError(
                f"FFmpeg concat failed (rc={result.returncode}): {result.stderr[:200]}"
            )
    finally:
        concat_timer.stop()
        list_path.unlink(missing_ok=True)

    log.info(
        "composer.concat.done",
        job_id=job_id,
        concat_sec=round(concat_timer.elapsed_sec, 3),
    )


# ── FFmpeg effect post-processing ────────────────────────────────────────────

_FFMPEG_EFFECT_TIMEOUT = 300  # 5 min per scene for filter post-process


def _apply_ffmpeg_effects(
    scene: Scene,
    ri: RenderInput,
    scene_path: Path,
    canvas: Canvas,
    duration: float,
) -> Path:
    """Apply FFmpeg-native effects to an already-written scene MP4.

    Returns *scene_path* unchanged if no FFmpeg effects apply, or returns
    a new path after re-encoding with the filter graph.
    """
    filters: list[str] = []
    for effect_inst in scene.effects:
        plugin = ri.effects_map.get(effect_inst.plugin_id)
        if plugin is None:
            continue
        vf = plugin.ffmpeg_filter_graph(
            effect_inst.params,
            canvas.w,
            canvas.h,
            canvas.fps,
            duration,
        )
        if vf:
            filters.append(vf)

    if not filters:
        return scene_path

    vf_chain = ",".join(filters)
    ffmpeg = resolve_ffmpeg_binary()
    filtered_path = scene_path.with_suffix(".filtered.mp4")

    log.info(
        "composer.ffmpeg_effects.start",
        job_id=ri.job_id,
        scene_id=scene.scene_id,
        filters=vf_chain,
    )

    fx_timer = Timer().start()
    try:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(scene_path),
            "-vf",
            vf_chain,
            "-c:a",
            "copy",
            str(filtered_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_FFMPEG_EFFECT_TIMEOUT,
        )
        if result.returncode != 0:
            log.warning(
                "composer.ffmpeg_effects.failed",
                job_id=ri.job_id,
                scene_id=scene.scene_id,
                returncode=result.returncode,
                stderr=result.stderr[:500],
            )
            filtered_path.unlink(missing_ok=True)
            return scene_path  # fall back to un-filtered scene
    finally:
        fx_timer.stop()

    log.info(
        "composer.ffmpeg_effects.done",
        job_id=ri.job_id,
        scene_id=scene.scene_id,
        filter_sec=round(fx_timer.elapsed_sec, 3),
    )

    # Replace original with filtered version
    scene_path.unlink(missing_ok=True)
    filtered_path.rename(scene_path)
    return scene_path


# ── Final video writer ───────────────────────────────────────────────────────


def _write_final_video(
    clip: VideoClip,
    output_path: Path,
    job_id: str,
    fps: int,
    output_cfg: OutputConfig,
    encoder: EncoderConfig,
) -> None:
    """Write *clip* to *output_path* using *encoder*, with SIGALRM timeout."""
    total_frames = int((clip.duration or 0) * fps)
    log.info(
        "composer.write.start",
        job_id=job_id,
        path=str(output_path),
        duration=clip.duration,
        total_frames=total_frames,
        codec=encoder.codec,
        preset=encoder.preset,
        is_gpu=encoder.is_gpu,
        timeout_sec=_RENDER_TIMEOUT,
    )

    write_timer = Timer().start()
    prev_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(_RENDER_TIMEOUT)
    try:
        clip.write_videofile(
            str(output_path),
            fps=fps,
            codec=encoder.codec,
            audio_codec=output_cfg.audio_codec,
            bitrate=output_cfg.bitrate,
            preset=encoder.preset,
            ffmpeg_params=encoder.ffmpeg_params,
            audio_bitrate=output_cfg.audio_bitrate,
            logger="bar",
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev_handler)
        write_timer.stop()

    avg_fps = total_frames / write_timer.elapsed_sec if write_timer.elapsed_sec > 0 else 0
    RENDER_FPS.observe(avg_fps)
    log.info(
        "composer.write.done",
        job_id=job_id,
        write_sec=round(write_timer.elapsed_sec, 3),
        total_frames=total_frames,
        avg_fps=round(avg_fps, 2),
        codec=encoder.codec,
        is_gpu=encoder.is_gpu,
    )


# ── Scene builder ────────────────────────────────────────────────────────────


def _build_scene(
    scene: Scene,
    ri: RenderInput,
    canvas: Canvas,
    defaults: Defaults,
    allow_python_effects: bool = True,
    use_ffmpeg_postprocess: bool = False,
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
        elif layer.type in ("image", "video") and layer.asset_ref:
            ri.warnings.append(
                AssetWarning(
                    asset_id=layer.asset_ref,
                    scene_id=scene.scene_id,
                    stage="RENDER",
                    code="LAYER_ASSET_MISSING",
                    message=(
                        f"Layer {layer.layer_id} ({layer.type}) dropped: "
                        f"asset_ref '{layer.asset_ref}' not in asset_paths"
                    ),
                    fallback_used="layer_skipped",
                )
            )
            log.warning(
                "composer.layer.asset_missing",
                job_id=ri.job_id,
                scene_id=scene.scene_id,
                layer_id=layer.layer_id,
                layer_type=layer.type,
                asset_ref=layer.asset_ref,
            )

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
            if not allow_python_effects and not plugin.has_ffmpeg_filter():
                log.debug(
                    "composer.effect.skipped",
                    job_id=ri.job_id,
                    scene_id=scene.scene_id,
                    plugin_id=effect_inst.plugin_id,
                    reason="python_effects_disabled",
                )
                continue
            # Skip effects that will be applied as FFmpeg post-process
            if use_ffmpeg_postprocess and plugin.ffmpeg_filter_graph(
                effect_inst.params,
                canvas.w,
                canvas.h,
                canvas.fps,
                duration,
            ):
                continue
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
