"""Unit tests for _resolve_scene_trim — scene clip selection strategies.

All scenarios from specs/scene-clip-selection/spec.md are covered here.
"""

from __future__ import annotations

import pytest

from maker8.models.common import AssetWarning
from maker8.models.spec import Layer, Trim
from maker8.rendering.composer import _resolve_scene_trim
from render_contracts.render_spec import SceneBoundary

# ── Helpers ──────────────────────────────────────────────────────────────────

SCENE_ID = "scene-1"


def _layer(**kwargs) -> Layer:
    defaults = dict(
        layer_id="L1",
        type="video",
        asset_ref="asset-1",
    )
    defaults.update(kwargs)
    return Layer(**defaults)


def _boundaries(*intervals: tuple[float, float]) -> list[SceneBoundary]:
    return [SceneBoundary(start_sec=s, end_sec=e) for s, e in intervals]


def _run(
    layer: Layer,
    candidates: dict[str, list[SceneBoundary]] | None = None,
) -> tuple[Trim | None, list[AssetWarning]]:
    warnings: list[AssetWarning] = []
    sc = candidates if candidates is not None else {}
    result = _resolve_scene_trim(layer, sc, warnings, SCENE_ID)
    return result, warnings


def _has_code(warnings: list[AssetWarning], code: str) -> bool:
    return any(w.code == code for w in warnings)


# ── (a) scene_clip_select=None → returns None ────────────────────────────────


def test_none_strategy_returns_none():
    layer = _layer(scene_clip_select=None)
    trim, warnings = _run(layer, {"asset-1": _boundaries((0, 5))})
    assert trim is None
    assert warnings == []


# ── (b) "longest" selects widest interval ────────────────────────────────────


def test_longest_selects_widest():
    layer = _layer(scene_clip_select="longest")
    candidates = {"asset-1": _boundaries((0, 3), (3, 10), (10, 12))}
    trim, warnings = _run(layer, candidates)
    assert trim == Trim(in_=3, out=10)
    assert warnings == []


# ── (c) "auto" is alias for "longest" ────────────────────────────────────────


def test_auto_same_as_longest():
    candidates = {"asset-1": _boundaries((0, 3), (3, 10), (10, 12))}
    trim_auto, _ = _run(_layer(scene_clip_select="auto"), candidates)
    trim_longest, _ = _run(_layer(scene_clip_select="longest"), candidates)
    assert trim_auto == trim_longest
    assert trim_auto == Trim(in_=3, out=10)


# ── (d) "first" selects earliest boundary ────────────────────────────────────


def test_first_selects_earliest():
    layer = _layer(scene_clip_select="first")
    candidates = {"asset-1": _boundaries((0, 4), (4, 9))}
    trim, warnings = _run(layer, candidates)
    assert trim == Trim(in_=0, out=4)
    assert warnings == []


# ── (e) "last" selects final boundary ────────────────────────────────────────


def test_last_selects_final():
    layer = _layer(scene_clip_select="last")
    candidates = {"asset-1": _boundaries((0, 4), (4, 9))}
    trim, warnings = _run(layer, candidates)
    assert trim == Trim(in_=4, out=9)
    assert warnings == []


# ── (f) "shortest" selects narrowest interval ────────────────────────────────


def test_shortest_selects_narrowest():
    layer = _layer(scene_clip_select="shortest")
    candidates = {"asset-1": _boundaries((0, 3), (3, 10), (10, 12))}
    trim, warnings = _run(layer, candidates)
    # 3s, 7s, 2s → shortest is (10, 12)
    assert trim == Trim(in_=10, out=12)
    assert warnings == []


# ── (g) "index:1" with 3 boundaries selects second ───────────────────────────


def test_index_in_range():
    layer = _layer(scene_clip_select="index:1")
    candidates = {"asset-1": _boundaries((0, 3), (3, 8), (8, 12))}
    trim, warnings = _run(layer, candidates)
    assert trim == Trim(in_=3, out=8)
    assert warnings == []


# ── (h) "index:99" clamps to last boundary, emits warning ────────────────────


def test_index_oob_clamps_to_last():
    layer = _layer(scene_clip_select="index:99")
    candidates = {"asset-1": _boundaries((0, 3), (3, 8))}
    trim, warnings = _run(layer, candidates)
    assert trim == Trim(in_=3, out=8)
    assert _has_code(warnings, "SCENE_CLIP_SELECT_INDEX_OOB")


# ── (i) unrecognised strategy → warn, returns None ───────────────────────────


def test_unknown_strategy_warns_and_returns_none():
    layer = _layer(scene_clip_select="random")
    candidates = {"asset-1": _boundaries((0, 5))}
    trim, warnings = _run(layer, candidates)
    assert trim is None
    assert _has_code(warnings, "SCENE_CLIP_SELECT_UNKNOWN_STRATEGY")


# ── (j) scene_clip_select set but no candidates → warn, returns None ─────────


def test_no_candidates_warns_and_returns_none():
    layer = _layer(scene_clip_select="first")
    # asset-1 not in scene_candidates
    trim, warnings = _run(layer, {})
    assert trim is None
    assert _has_code(warnings, "SCENE_CLIP_SELECT_NO_CANDIDATES")


def test_no_candidates_empty_list_warns():
    layer = _layer(scene_clip_select="first")
    trim, warnings = _run(layer, {"asset-1": []})
    assert trim is None
    assert _has_code(warnings, "SCENE_CLIP_SELECT_NO_CANDIDATES")


# ── (k) both trim and scene_clip_select → scene_clip_select wins, warns ───────


def test_scene_clip_select_overrides_trim():
    layer = _layer(
        scene_clip_select="first",
        trim=Trim(in_=5, out=15),
    )
    candidates = {"asset-1": _boundaries((0, 4), (4, 9))}
    trim, warnings = _run(layer, candidates)
    assert trim == Trim(in_=0, out=4)
    assert _has_code(warnings, "SCENE_CLIP_SELECT_TRIM_OVERRIDE")
