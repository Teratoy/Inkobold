"""Edit → Mirror / Flip / Rotate and the Effects menu for ``MainWindow``.

``EffectsMixin`` is mixed into ``MainWindow``: the ``action_effect_*`` handlers
open a live-preview dialog, and the session helpers apply the effect to the
layer snapshots and commit or roll back on the dialog's response.
"""

from __future__ import annotations

import numpy as np

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from inkobold.core.effects import (
    DITHER_MODES,
    EDGE_STYLES,
    LIQUIFY_MODES,
    METAL_PRESETS,
    NORMAL_MAP_Y,
    apply_tone_curves,
    blend_effect,
    dither,
    drop_shadow,
    edge_detect,
    emboss,
    flora,
    gaussian_blur,
    color_basics,
    grayscale,
    invert,
    kuwahara,
    liquify,
    metal_relief,
    milk,
    normal_map,
    pixelate,
    posterize,
    sharpen,
    solarize,
    threshold,
)
from inkobold.ui.dialogs import EFFECT_APPLY_TO_ALL, CurvesDialog, EffectOption, EffectPreviewDialog


class EffectsMixin:
    """Layer transforms and preview-dialog effects for ``MainWindow``."""

    def _apply_layer_transform(self, op) -> None:
        if not self.document:
            return
        self._push_history()
        op(self.document)
        self.canvas.renderer.invalidate()
        self.canvas.queue_render()
        self._set_status()

    def action_mirror_horizontal(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.mirror_active_layer(horizontal=True))

    def action_mirror_vertical(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.mirror_active_layer(horizontal=False))

    def action_flip_horizontal(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.flip_active_layer(horizontal=True))

    def action_flip_vertical(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.flip_active_layer(horizontal=False))

    def action_rotate_cw(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.rotate_active_layer(3))

    def action_rotate_ccw(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.rotate_active_layer(1))

    def action_rotate_180(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.rotate_active_layer(2))

    def action_effect_pixelate(self, *_a) -> None:
        self._open_effect_dialog(
            title="Pixelate",
            blurb="Average the active layer into square color blocks.",
            options=[
                EffectOption("block_size", "Block size", "spin", 8, minimum=2, maximum=128, step=1, digits=0),
            ],
            effect_fn=lambda src, p: pixelate(src, int(p["block_size"])),
            debounce_ms=40,
        )

    def action_effect_kuwahara(self, *_a) -> None:
        self._open_effect_dialog(
            title="Kuwahara",
            blurb="Edge-preserving paint-like blur on the active layer.",
            options=[
                EffectOption("radius", "Radius", "spin", 3, minimum=1, maximum=12, step=1, digits=0),
            ],
            effect_fn=lambda src, p: kuwahara(src, int(p["radius"])),
            debounce_ms=80,
            preview_max_side=720,
        )

    def action_effect_gaussian_blur(self, *_a) -> None:
        self._open_effect_dialog(
            title="Gaussian Blur",
            blurb="Soft Gaussian blur on the active layer (all channels).",
            options=[
                EffectOption("radius", "Radius", "spin", 3, minimum=1, maximum=64, step=1, digits=0),
            ],
            effect_fn=lambda src, p: gaussian_blur(src, int(p["radius"])),
            debounce_ms=80,
            preview_max_side=720,
        )

    def action_effect_sharpen(self, *_a) -> None:
        self._open_effect_dialog(
            title="Sharpen",
            blurb=(
                "Unsharp-mask sharpen on the active layer’s RGB (alpha preserved). "
                "Threshold skips weak detail to limit noise."
            ),
            options=[
                EffectOption("amount", "Amount", "spin", 100, minimum=0, maximum=500, step=1),
                EffectOption("radius", "Radius", "spin", 1, minimum=1, maximum=32, step=1, digits=0),
                EffectOption(
                    "threshold", "Threshold", "spin", 0, minimum=0, maximum=100, step=1
                ),
            ],
            effect_fn=lambda src, p: sharpen(
                src,
                amount=float(p["amount"]),
                radius=int(p["radius"]),
                threshold=float(p["threshold"]),
            ),
            debounce_ms=80,
            preview_max_side=720,
        )

    def action_effect_drop_shadow(self, *_a) -> None:
        self._open_effect_dialog(
            title="Drop Shadow",
            blurb=(
                "Cast a shadow from the active layer’s alpha. "
                "Direction: 0° = right, 90° = down. Color alpha sets opacity."
            ),
            options=[
                EffectOption("distance", "Distance", "spin", 8, minimum=0, maximum=256, step=1, digits=0),
                EffectOption(
                    "direction", "Direction °", "spin", 135, minimum=0, maximum=360, step=1
                ),
                EffectOption("blur", "Blur", "spin", 4, minimum=0, maximum=64, step=1, digits=0),
                EffectOption("spread", "Spread", "spin", 0, minimum=0, maximum=64, step=1, digits=0),
                EffectOption(
                    "color",
                    "Color",
                    "color",
                    (0, 0, 0, 255),
                    use_alpha=True,
                ),
            ],
            effect_fn=lambda src, p: drop_shadow(
                src,
                distance=int(p["distance"]),
                direction=float(p["direction"]),
                blur=int(p["blur"]),
                spread=int(p["spread"]),
                color=tuple(p["color"]),  # type: ignore[arg-type]
            ),
            debounce_ms=80,
            preview_max_side=720,
        )

    def action_effect_dither(self, *_a) -> None:
        self._open_effect_dialog(
            title="Dither",
            blurb="Reduce color depth with Floyd–Steinberg, ordered, or hard threshold dithering.",
            options=[
                EffectOption(
                    "mode",
                    "Mode",
                    "choice",
                    "Floyd–Steinberg",
                    choices=DITHER_MODES,
                ),
                EffectOption("levels", "Levels", "spin", 2, minimum=2, maximum=16, step=1, digits=0),
            ],
            effect_fn=lambda src, p: dither(src, mode=str(p["mode"]), levels=int(p["levels"])),
            debounce_ms=60,
        )

    def action_effect_posterize(self, *_a) -> None:
        self._open_effect_dialog(
            title="Posterize",
            blurb="Clamp each RGB channel to a fixed number of tonal steps.",
            options=[
                EffectOption("levels", "Levels", "spin", 4, minimum=2, maximum=32, step=1, digits=0),
            ],
            effect_fn=lambda src, p: posterize(src, int(p["levels"])),
            debounce_ms=40,
        )

    def action_effect_flora(self, *_a) -> None:
        def _gpu(src, **kw):
            return self.canvas.apply_flora(src, **kw)

        self._open_effect_dialog(
            title="Flora",
            blurb=(
                "Gritty pseudo-HDR look: wide local contrast, midtone grit, "
                "neon yellow/magenta/cyan accents, bleached neutrals, and Orton bloom. "
                "Uses the GPU when available; alpha is preserved."
            ),
            options=[
                EffectOption("clarity", "Clarity", "spin", 75, minimum=0, maximum=150, step=1),
                EffectOption(
                    "clarity_radius", "Clarity radius", "spin", 28, minimum=4, maximum=96, step=1, digits=0
                ),
                EffectOption("bloom", "Bloom", "spin", 28, minimum=0, maximum=100, step=1),
                EffectOption(
                    "bloom_radius", "Bloom radius", "spin", 14, minimum=2, maximum=64, step=1, digits=0
                ),
                EffectOption("grit", "Grit", "spin", 45, minimum=0, maximum=100, step=1),
                EffectOption("neon", "Neon accents", "spin", 70, minimum=0, maximum=150, step=1),
                EffectOption("bleach", "Midtone bleach", "spin", 55, minimum=0, maximum=100, step=1),
                EffectOption("lift", "Shadow lift", "spin", 30, minimum=0, maximum=100, step=1),
            ],
            effect_fn=lambda src, p: flora(
                src,
                clarity=float(p["clarity"]),
                bloom=float(p["bloom"]),
                grit=float(p["grit"]),
                neon=float(p["neon"]),
                bleach=float(p["bleach"]),
                lift=float(p["lift"]),
                clarity_radius=int(p["clarity_radius"]),
                bloom_radius=int(p["bloom_radius"]),
                gpu_apply=_gpu,
            ),
            debounce_ms=80,
            preview_max_side=512,
        )

    def action_effect_color_basics(self, *_a) -> None:
        self._open_effect_dialog(
            title="Color Basics",
            blurb=(
                "Hue, brightness, contrast, and exposure on the active layer "
                "(alpha preserved). Brightness / contrast are percents; "
                "exposure 100 is neutral."
            ),
            options=[
                EffectOption("hue", "Hue °", "spin", 0, minimum=-180, maximum=180, step=1),
                EffectOption(
                    "brightness", "Brightness", "spin", 0, minimum=-100, maximum=100, step=1
                ),
                EffectOption(
                    "contrast", "Contrast", "spin", 0, minimum=-100, maximum=100, step=1
                ),
                EffectOption(
                    "exposure", "Exposure", "spin", 100, minimum=0, maximum=200, step=1
                ),
            ],
            effect_fn=lambda src, p: color_basics(
                src,
                hue=float(p["hue"]),
                brightness=float(p["brightness"]),
                contrast=float(p["contrast"]),
                exposure=float(p["exposure"]),
            ),
            debounce_ms=40,
        )

    def action_effect_grayscale(self, *_a) -> None:
        self._open_effect_dialog(
            title="Grayscale",
            blurb=(
                "Convert to Rec. 601 grayscale on the active layer "
                "(alpha preserved). Amount 100 is full gray."
            ),
            options=[
                EffectOption("amount", "Amount", "spin", 100, minimum=0, maximum=100, step=1),
            ],
            effect_fn=lambda src, p: grayscale(src, amount=float(p["amount"])),
            debounce_ms=40,
        )

    def action_effect_invert(self, *_a) -> None:
        self._open_effect_dialog(
            title="Invert",
            blurb="Invert RGB on the active layer (alpha preserved).",
            options=[],
            effect_fn=lambda src, _p: invert(src),
            debounce_ms=40,
        )

    def action_effect_solarize(self, *_a) -> None:
        self._open_effect_dialog(
            title="Solarize",
            blurb=(
                "Invert RGB channels at or above a threshold (Sabattier). "
                "Alpha is preserved."
            ),
            options=[
                EffectOption("level", "Level", "spin", 50, minimum=0, maximum=100, step=1),
            ],
            effect_fn=lambda src, p: solarize(src, level=float(p["level"])),
            debounce_ms=40,
        )

    def action_effect_curves(self, *_a) -> None:
        self._open_curves_dialog()

    def action_effect_threshold(self, *_a) -> None:
        self._open_effect_dialog(
            title="Threshold",
            blurb="Hard black / white cut from the active layer’s luma (alpha preserved).",
            options=[
                EffectOption(
                    "style",
                    "Style",
                    "choice",
                    "Light on dark",
                    choices=EDGE_STYLES,
                ),
                EffectOption("level", "Level", "spin", 50, minimum=0, maximum=100, step=1),
            ],
            effect_fn=lambda src, p: threshold(
                src,
                level=float(p["level"]),
                style=str(p["style"]),
            ),
            debounce_ms=40,
        )

    def action_effect_liquify(self, *_a) -> None:
        self._open_effect_dialog(
            title="Liquify",
            blurb="Warp the whole active layer from a grid of origins (swirl, pinch, or bulge).",
            options=[
                EffectOption(
                    "mode",
                    "Mode",
                    "choice",
                    "Swirl",
                    choices=LIQUIFY_MODES,
                ),
                EffectOption("strength", "Strength", "spin", 50, minimum=0, maximum=100, step=1),
                EffectOption("grid", "Origins grid", "spin", 3, minimum=1, maximum=8, step=1, digits=0),
                EffectOption("radius_pct", "Radius %", "spin", 100, minimum=20, maximum=150, step=5),
            ],
            effect_fn=lambda src, p: liquify(
                src,
                mode=str(p["mode"]),
                strength=float(p["strength"]),
                grid=int(p["grid"]),
                radius_pct=float(p["radius_pct"]),
            ),
            debounce_ms=50,
            preview_max_side=1280,
        )

    def action_effect_edge_detect(self, *_a) -> None:
        self._open_effect_dialog(
            title="Edge Detect",
            blurb="Sobel edges from the active layer’s luma (alpha preserved).",
            options=[
                EffectOption(
                    "style",
                    "Style",
                    "choice",
                    "Light on dark",
                    choices=EDGE_STYLES,
                ),
                EffectOption("strength", "Strength", "spin", 100, minimum=0, maximum=200, step=1),
                EffectOption("threshold", "Threshold", "spin", 0, minimum=0, maximum=100, step=1),
                EffectOption("radius", "Pre-blur", "spin", 0, minimum=0, maximum=16, step=1, digits=0),
            ],
            effect_fn=lambda src, p: edge_detect(
                src,
                strength=float(p["strength"]),
                threshold=float(p["threshold"]),
                radius=int(p["radius"]),
                style=str(p["style"]),
            ),
            debounce_ms=50,
            preview_max_side=1280,
        )

    def action_effect_emboss(self, *_a) -> None:
        self._open_effect_dialog(
            title="Emboss",
            blurb=(
                "Classic directional emboss from luma — mid-gray flats with "
                "bright / dark relief (alpha preserved). Direction matches Drop Shadow."
            ),
            options=[
                EffectOption(
                    "direction",
                    "Direction °",
                    "spin",
                    135,
                    minimum=0,
                    maximum=360,
                    step=1,
                ),
                EffectOption("depth", "Depth", "spin", 100, minimum=0, maximum=200, step=1),
                EffectOption("height", "Height", "spin", 2, minimum=1, maximum=64, step=1, digits=0),
                EffectOption("radius", "Smooth", "spin", 0, minimum=0, maximum=16, step=1, digits=0),
            ],
            effect_fn=lambda src, p: emboss(
                src,
                direction=float(p["direction"]),
                depth=float(p["depth"]),
                height=int(p["height"]),
                radius=int(p["radius"]),
            ),
            debounce_ms=50,
            preview_max_side=1280,
        )

    def action_effect_normal_map(self, *_a) -> None:
        self._open_effect_dialog(
            title="Normal Map",
            blurb="Bake a tangent-space normal map from luma-as-height (alpha preserved).",
            options=[
                EffectOption(
                    "y_convention",
                    "Y axis",
                    "choice",
                    "OpenGL",
                    choices=NORMAL_MAP_Y,
                ),
                EffectOption("strength", "Strength", "spin", 100, minimum=1, maximum=500, step=1),
                EffectOption("radius", "Smooth", "spin", 0, minimum=0, maximum=16, step=1, digits=0),
            ],
            effect_fn=lambda src, p: normal_map(
                src,
                strength=float(p["strength"]),
                radius=int(p["radius"]),
                y_convention=str(p["y_convention"]),
            ),
            debounce_ms=50,
            preview_max_side=1280,
        )

    def action_effect_metal_relief(self, *_a) -> None:
        self._open_effect_dialog(
            title="Metal Relief",
            blurb=(
                "Emboss luma as raised metal (copper, silver, gold, …) with "
                "gloss, shadow, exposure, and split highlight/shadow hues. "
                "Alpha is preserved."
            ),
            options=[
                EffectOption(
                    "metal",
                    "Metal",
                    "choice",
                    "Copper",
                    choices=METAL_PRESETS,
                ),
                EffectOption("depth", "Depth", "spin", 70, minimum=0, maximum=100, step=1),
                EffectOption(
                    "glossiness", "Glossiness", "spin", 55, minimum=0, maximum=100, step=1
                ),
                EffectOption("shadow", "Shadow", "spin", 40, minimum=0, maximum=100, step=1),
                EffectOption(
                    "reflections", "Reflections", "spin", 50, minimum=0, maximum=100, step=1
                ),
                EffectOption(
                    "exposure", "Exposure", "spin", 100, minimum=0, maximum=200, step=1
                ),
                EffectOption("hue", "Hue", "spin", 0, minimum=-180, maximum=180, step=1),
                EffectOption(
                    "saturation",
                    "Saturation",
                    "spin",
                    100,
                    minimum=0,
                    maximum=200,
                    step=1,
                ),
                EffectOption(
                    "highlight_hue",
                    "Highlight hue",
                    "spin",
                    0,
                    minimum=-180,
                    maximum=180,
                    step=1,
                ),
                EffectOption(
                    "shadow_hue",
                    "Shadow hue",
                    "spin",
                    0,
                    minimum=-180,
                    maximum=180,
                    step=1,
                ),
                EffectOption("radius", "Smooth", "spin", 1, minimum=0, maximum=16, step=1, digits=0),
            ],
            effect_fn=lambda src, p: metal_relief(
                src,
                metal=str(p["metal"]),
                depth=float(p["depth"]),
                glossiness=float(p["glossiness"]),
                shadow=float(p["shadow"]),
                reflections=float(p["reflections"]),
                exposure=float(p["exposure"]),
                hue=float(p["hue"]),
                highlight_hue=float(p["highlight_hue"]),
                shadow_hue=float(p["shadow_hue"]),
                saturation=float(p["saturation"]),
                radius=int(p["radius"]),
                light_dir=self._sun_light_dir(),
            ),
            debounce_ms=50,
            preview_max_side=1280,
        )

    def action_effect_milk(self, *_a) -> None:
        self._open_effect_dialog(
            title="Milk",
            blurb=(
                "Emboss luma as creamy viscous fluid — wet sheen, liquid edges, "
                "brush-mode bubbles, clear spots. Raise Expiration for yellow / "
                "mould / brown spoil."
            ),
            options=[
                EffectOption("depth", "Depth", "spin", 55, minimum=0, maximum=100, step=1),
                EffectOption(
                    "glossiness", "Glossiness", "spin", 75, minimum=0, maximum=100, step=1
                ),
                EffectOption(
                    "thickness", "Thickness", "spin", 90, minimum=0, maximum=100, step=1
                ),
                EffectOption("shadow", "Shadow", "spin", 30, minimum=0, maximum=100, step=1),
                EffectOption(
                    "exposure", "Exposure", "spin", 100, minimum=0, maximum=200, step=1
                ),
                EffectOption("hue", "Hue", "spin", 8, minimum=-180, maximum=180, step=1),
                EffectOption(
                    "saturation",
                    "Saturation",
                    "spin",
                    35,
                    minimum=0,
                    maximum=200,
                    step=1,
                ),
                EffectOption("radius", "Smooth", "spin", 8, minimum=0, maximum=32, step=1, digits=0),
                EffectOption(
                    "bubbles", "Bubbles", "spin", 45, minimum=0, maximum=100, step=1
                ),
                EffectOption(
                    "bubble_size",
                    "Bubble size",
                    "spin",
                    50,
                    minimum=0,
                    maximum=100,
                    step=1,
                ),
                EffectOption(
                    "clear_spots", "Clear spots", "spin", 40, minimum=0, maximum=100, step=1
                ),
                EffectOption(
                    "edge_melt", "Edge melt", "spin", 70, minimum=0, maximum=100, step=1
                ),
                EffectOption(
                    "wetness", "Wetness", "spin", 45, minimum=0, maximum=100, step=1
                ),
                EffectOption(
                    "expiration",
                    "Expiration date",
                    "spin",
                    0,
                    minimum=0,
                    maximum=100,
                    step=1,
                ),
            ],
            effect_fn=lambda src, p: milk(
                src,
                depth=float(p["depth"]),
                glossiness=float(p["glossiness"]),
                thickness=float(p["thickness"]),
                shadow=float(p["shadow"]),
                exposure=float(p["exposure"]),
                hue=float(p["hue"]),
                saturation=float(p["saturation"]),
                radius=int(p["radius"]),
                bubbles=float(p["bubbles"]),
                bubble_size=float(p["bubble_size"]),
                clear_spots=float(p["clear_spots"]),
                edge_melt=float(p["edge_melt"]),
                wetness=float(p["wetness"]),
                expiration=float(p["expiration"]),
                light_dir=self._sun_milk_light_dir(),
            ),
            # Downscaled live preview for speed; Apply still runs full-res.
            debounce_ms=90,
            preview_max_side=480,
        )

    def _open_effect_dialog(
        self,
        *,
        title: str,
        blurb: str,
        options: list,
        effect_fn,
        debounce_ms: int = 60,
        preview_max_side: int | None = None,
    ) -> None:
        if not self.document or self._effect_session is not None:
            return
        snapshots: dict[str, np.ndarray] = {}
        for ly in self.document.layers:
            ly.apply_offset()
            snapshots[ly.id] = ly.pixels.copy()
        sel_mask = None
        if self.document.selection.active and self.document.selection.mask is not None:
            sel_mask = self.document.selection.mask.copy()

        self._push_history()
        self._effect_session = {
            "snapshots": snapshots,
            "active_layer_id": self.document.active_layer.id,
            "selection": sel_mask,
            "effect_fn": effect_fn,
            "preview_max_side": preview_max_side,
            "last_params": None,
        }

        def on_preview(params: dict) -> None:
            self._preview_effect(params, final=False)

        dlg = EffectPreviewDialog(
            self,
            title,
            blurb,
            options,
            on_preview,
            debounce_ms=debounce_ms,
        )
        dlg.connect_response(self._on_effect_dialog_response)
        dlg.present()

    def _open_curves_dialog(self) -> None:
        if not self.document or self._effect_session is not None:
            return
        snapshots: dict[str, np.ndarray] = {}
        for ly in self.document.layers:
            ly.apply_offset()
            snapshots[ly.id] = ly.pixels.copy()
        sel_mask = None
        if self.document.selection.active and self.document.selection.mask is not None:
            sel_mask = self.document.selection.mask.copy()

        self._push_history()
        self._effect_session = {
            "snapshots": snapshots,
            "active_layer_id": self.document.active_layer.id,
            "selection": sel_mask,
            "effect_fn": lambda src, p: apply_tone_curves(src, p.get("curves")),
            "preview_max_side": None,
            "last_params": None,
        }

        def on_preview(params: dict) -> None:
            self._preview_effect(params, final=False)

        dlg = CurvesDialog(self, on_preview, debounce_ms=16)
        dlg.connect_response(self._on_effect_dialog_response)
        dlg.present()

    def _run_effect(
        self,
        session: dict,
        src: np.ndarray,
        params: dict,
        *,
        final: bool,
    ) -> np.ndarray:
        effect_fn = session["effect_fn"]
        max_side = session.get("preview_max_side")
        h, w = src.shape[:2]
        if final or not max_side or max(h, w) <= max_side:
            return effect_fn(src, params)

        scale = max_side / float(max(h, w))
        small_h = max(1, int(round(h * scale)))
        small_w = max(1, int(round(w * scale)))
        ys = (np.linspace(0, h - 1, small_h)).astype(np.int32)
        xs = (np.linspace(0, w - 1, small_w)).astype(np.int32)
        small = src[ys][:, xs]
        preview_params = dict(params)
        if "radius" in preview_params:
            preview_params["radius"] = max(1, int(round(int(preview_params["radius"]) * scale)))
        if "clarity_radius" in preview_params:
            preview_params["clarity_radius"] = max(
                1, int(round(int(preview_params["clarity_radius"]) * scale))
            )
        if "bloom_radius" in preview_params:
            preview_params["bloom_radius"] = max(
                1, int(round(int(preview_params["bloom_radius"]) * scale))
            )
        if "block_size" in preview_params:
            preview_params["block_size"] = max(1, int(round(int(preview_params["block_size"]) * scale)))
        for key in ("distance", "blur", "spread"):
            if key in preview_params:
                preview_params[key] = max(0, int(round(int(preview_params[key]) * scale)))
        small_out = effect_fn(small, preview_params)
        y_up = (np.linspace(0, small_h - 1, h)).astype(np.int32)
        x_up = (np.linspace(0, small_w - 1, w)).astype(np.int32)
        return np.ascontiguousarray(small_out[y_up][:, x_up])

    def _preview_effect(self, params: dict, *, final: bool = False) -> None:
        session = self._effect_session
        if not session or not self.document:
            return
        snapshots: dict[str, np.ndarray] = session["snapshots"]
        if session["active_layer_id"] not in snapshots:
            return
        session["last_params"] = dict(params)
        apply_all = params.get("apply_to") == EFFECT_APPLY_TO_ALL
        target_ids = set(snapshots) if apply_all else {session["active_layer_id"]}
        for ly in self.document.layers:
            snap = snapshots.get(ly.id)
            if snap is None:
                continue
            if ly.id in target_ids:
                effected = self._run_effect(session, snap, params, final=final)
                blended = blend_effect(snap, effected, session["selection"])
                np.copyto(ly.pixels, blended)
            else:
                # Restore layers left untouched (e.g. after switching from All → Active).
                np.copyto(ly.pixels, snap)
            ly.bump()
            self.canvas.renderer.invalidate(ly.id)
        self.document.dirty = True
        self.canvas.queue_render()
        self._set_status()

    def _on_effect_dialog_response(self, dlg: EffectPreviewDialog, response: int) -> None:
        session = self._effect_session
        self._effect_session = None
        if not self.document or session is None:
            return
        if response == Gtk.ResponseType.OK:
            params = dlg.params()
            # Re-apply at full resolution (preview may have been downscaled).
            self._effect_session = session
            self._preview_effect(params, final=True)
            self._effect_session = None
            return
        # Cancel: restore original pixels and drop the undo entry pushed on open.
        for ly in self.document.layers:
            snap = session["snapshots"].get(ly.id)
            if snap is None:
                continue
            np.copyto(ly.pixels, snap)
            ly.bump()
            self.canvas.renderer.invalidate(ly.id)
        self.canvas.queue_render()
        self.history.discard_last_push()
        self._set_status()
