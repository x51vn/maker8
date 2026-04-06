"""Tests for deterministic font resolution, Vietnamese rendering, and layer warnings.

Covers:
- Roboto font loading (variable font with weight axes)
- Backward-compatible font:inter:* alias remapping
- Fallback warning when font_ref is unresolved
- Vietnamese glyph rendering regression
- valign "middle" → "center" normalisation
- Layer-level LAYER_ASSET_MISSING warnings in composer
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from maker8.models.spec import Canvas, Layer, Rect, TextStyle
from maker8.rendering.text import _font_cache, _load_font, render_text_image

# ── Font resolution ──────────────────────────────────────────────────────────


class TestFontResolution:
    """Verify that font_ref aliases resolve to real FreeTypeFont instances."""

    def setup_method(self) -> None:
        _font_cache.clear()

    def test_roboto_regular_loads(self) -> None:
        font = _load_font("font:roboto:regular", 48)
        assert font is not None
        assert hasattr(font, "getbbox")

    def test_roboto_bold_loads(self) -> None:
        font = _load_font("font:roboto:bold", 48)
        assert font is not None

    def test_inter_alias_resolves_to_roboto(self) -> None:
        regular = _load_font("font:inter:regular", 48)
        bold = _load_font("font:inter:bold", 48)
        assert regular is not None
        assert bold is not None
        # Both should be FreeTypeFont (not the default bitmap font)
        from PIL import ImageFont

        assert isinstance(regular, ImageFont.FreeTypeFont)
        assert isinstance(bold, ImageFont.FreeTypeFont)

    def test_unknown_ref_falls_back(self) -> None:
        """Unknown font_ref produces a usable font (with a warning)."""
        font = _load_font("font:nonexistent:regular", 36)
        assert font is not None
        assert hasattr(font, "getbbox")

    def test_font_cache_reuses(self) -> None:
        f1 = _load_font("font:roboto:regular", 48)
        f2 = _load_font("font:roboto:regular", 48)
        assert f1 is f2

    def test_different_sizes_are_separate(self) -> None:
        f1 = _load_font("font:roboto:regular", 48)
        f2 = _load_font("font:roboto:regular", 72)
        assert f1 is not f2


# ── Vietnamese text rendering ────────────────────────────────────────────────

_VIET_SAMPLES = [
    "Tiếng Việt",
    "Đặng",
    "Trường Sa",
    "ắằẳẵặ",
    "Chào mừng bạn đến với video hôm nay!",
]


class TestVietnameseRendering:
    """Regression tests: Vietnamese glyphs must render to non-empty images."""

    def setup_method(self) -> None:
        _font_cache.clear()

    @pytest.mark.parametrize("text", _VIET_SAMPLES, ids=_VIET_SAMPLES)
    def test_vietnamese_text_renders_pixels(self, text: str) -> None:
        style = TextStyle(font_ref="font:roboto:regular", size=48)
        arr = render_text_image(text, width=800, height=200, style=style)
        assert isinstance(arr, np.ndarray)
        assert arr.shape == (200, 800, 4)
        # At least some pixels must be non-transparent (alpha > 0)
        assert arr[:, :, 3].sum() > 0, f"No visible pixels for '{text}'"

    @pytest.mark.parametrize("text", _VIET_SAMPLES, ids=_VIET_SAMPLES)
    def test_legacy_inter_also_renders_vietnamese(self, text: str) -> None:
        style = TextStyle(font_ref="font:inter:regular", size=48)
        arr = render_text_image(text, width=800, height=200, style=style)
        assert arr[:, :, 3].sum() > 0


# ── valign normalisation ─────────────────────────────────────────────────────


class TestValignNormalisation:
    def setup_method(self) -> None:
        _font_cache.clear()

    def test_middle_normalised_to_center(self) -> None:
        style = TextStyle(font_ref="font:roboto:regular", size=48)
        arr_center = render_text_image(
            "Test", width=400, height=200, style=style, valign="center"
        )
        arr_middle = render_text_image(
            "Test", width=400, height=200, style=style, valign="middle"
        )
        np.testing.assert_array_equal(arr_center, arr_middle)

    def test_top_is_default(self) -> None:
        style = TextStyle(font_ref="font:roboto:regular", size=48)
        arr_top = render_text_image(
            "Test", width=400, height=200, style=style, valign="top"
        )
        arr_default = render_text_image(
            "Test", width=400, height=200, style=style, valign="unknown_value"
        )
        np.testing.assert_array_equal(arr_top, arr_default)


# ── Layer-level warnings ─────────────────────────────────────────────────────


class TestLayerWarnings:
    """Verify that missing asset layers emit LAYER_ASSET_MISSING warnings."""

    def test_missing_image_layer_emits_warning(self) -> None:
        from maker8.models.spec import Defaults, RenderSpec, Scene, SceneNarration
        from maker8.rendering.composer import RenderInput, _build_scene

        scene = Scene(
            scene_id="s1",
            duration=3.0,
            narration=SceneNarration(text="Test"),
            layers=[
                Layer(
                    layer_id="img1",
                    type="image",
                    rect=Rect(x=0, y=0, w=100, h=100),
                    asset_ref="missing_asset",
                ),
                Layer(
                    layer_id="txt1",
                    type="text",
                    rect=Rect(x=0, y=0, w=100, h=50),
                    text="Hello",
                ),
            ],
        )
        canvas = Canvas(w=200, h=200, fps=24)
        defaults = Defaults()
        ri = RenderInput(
            spec=RenderSpec(canvas=canvas, scenes=[scene]),
            asset_paths={},
            job_id="test-job",
        )

        clip = _build_scene(scene, ri, canvas, defaults)
        assert clip is not None

        # Should have exactly one LAYER_ASSET_MISSING warning
        assert len(ri.warnings) == 1
        w = ri.warnings[0]
        assert w.code == "LAYER_ASSET_MISSING"
        assert w.asset_id == "missing_asset"
        assert w.scene_id == "s1"
        assert "img1" in w.message

    def test_present_asset_no_warning(self, tmp_path: Path) -> None:
        # Create a minimal valid image file
        from PIL import Image

        from maker8.models.spec import Defaults, RenderSpec, Scene, SceneNarration
        from maker8.rendering.composer import RenderInput, _build_scene

        img_path = tmp_path / "test.png"
        Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(str(img_path))

        scene = Scene(
            scene_id="s1",
            duration=3.0,
            narration=SceneNarration(text="Test"),
            layers=[
                Layer(
                    layer_id="img1",
                    type="image",
                    rect=Rect(x=0, y=0, w=100, h=100),
                    asset_ref="a1",
                ),
            ],
        )
        canvas = Canvas(w=200, h=200, fps=24)
        defaults = Defaults()
        ri = RenderInput(
            spec=RenderSpec(canvas=canvas, scenes=[scene]),
            asset_paths={"a1": img_path},
            job_id="test-job",
        )

        clip = _build_scene(scene, ri, canvas, defaults)
        assert clip is not None
        assert len(ri.warnings) == 0
