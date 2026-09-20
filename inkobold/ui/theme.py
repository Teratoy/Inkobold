"""UI theme: DaemonDomain glass chrome (black / white / Tomorrow)."""

from __future__ import annotations

from typing import Iterable


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    l = (mx + mn) / 2.0
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == rf:
        h = ((gf - bf) / d + (6.0 if gf < bf else 0.0)) / 6.0
    elif mx == gf:
        h = ((bf - rf) / d + 2.0) / 6.0
    else:
        h = ((rf - gf) / d + 4.0) / 6.0
    return h, s, l


def hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    h, s, l = h % 1.0, _clamp01(s), _clamp01(l)

    def hue2rgb(p: float, q: float, t: float) -> float:
        if t < 0:
            t += 1.0
        if t > 1:
            t -= 1.0
        if t < 1 / 6:
            return p + (q - p) * 6.0 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6.0
        return p

    if s <= 1e-9:
        v = int(round(l * 255))
        return v, v, v
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    r = hue2rgb(p, q, h + 1 / 3)
    g = hue2rgb(p, q, h)
    b = hue2rgb(p, q, h - 1 / 3)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def _hex(rgb: Iterable[int]) -> str:
    r, g, b = rgb
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def _rgba(r: int, g: int, b: int, a: float) -> str:
    return f"rgba({int(r)},{int(g)},{int(b)},{a:.3f})"


def shades_from_rgb(r: int, g: int, b: int) -> dict[str, str]:
    """Derive a DaemonDomain-style glass palette from a seed accent color."""
    h, s, _l = rgb_to_hsl(r, g, b)
    achromatic = s < 0.08 or (r + g + b) / 3 > 230

    if achromatic:
        # Match DaemonDomain: pure black stage, white chrome, soft white glow.
        return {
            "window": "#000000",
            "panel": "#0a0f14",
            "sidebar": "#080c10",
            "status": "#050608",
            "text": "#ffffff",
            "muted": "#b8b8b8",
            "dim": "#737373",
            "accent": "#ffffff",
            "accent_glow": "rgba(255,255,255,0.35)",
            "separator": "#3a3a3a",
            "button": "#12161c",
            "button_hover": "#1c222b",
            "entry": "#000000",
            "border": "rgba(255,255,255,0.30)",
            "border_soft": "rgba(255,255,255,0.16)",
            "border_strong": "rgba(255,255,255,0.55)",
            "glass_hi": "rgba(255,255,255,0.12)",
            "glass_mid": "rgba(255,255,255,0.05)",
            "glass_lo": "rgba(0,0,0,0.55)",
            "inset_hi": "rgba(255,255,255,0.35)",
            "danger": "#ff6b6b",
            "ok": "#9dffb0",
        }

    # Keep chroma readable without neon chrome
    s = max(0.18, min(0.55, s))

    def shade(sat: float, light: float) -> tuple[int, int, int]:
        return hsl_to_rgb(h, sat, light)

    accent = shade(min(0.65, s + 0.1), max(0.42, min(0.62, _l if _l > 0.25 else 0.52)))
    ar, ag, ab = accent
    return {
        "window": _hex(shade(s * 0.35, 0.04)),
        "panel": _hex(shade(s * 0.45, 0.10)),
        "sidebar": _hex(shade(s * 0.4, 0.08)),
        "status": _hex(shade(s * 0.35, 0.05)),
        "text": "#ffffff",
        "muted": _hex(shade(s * 0.15, 0.72)),
        "dim": _hex(shade(s * 0.2, 0.48)),
        "accent": _hex(accent),
        "accent_glow": _rgba(ar, ag, ab, 0.40),
        "separator": _hex(shade(s * 0.4, 0.26)),
        "button": _hex(shade(s * 0.4, 0.14)),
        "button_hover": _hex(shade(s * 0.45, 0.20)),
        "entry": _hex(shade(s * 0.35, 0.06)),
        "border": _rgba(ar, ag, ab, 0.32),
        "border_soft": _rgba(ar, ag, ab, 0.18),
        "border_strong": _rgba(ar, ag, ab, 0.55),
        "glass_hi": _rgba(ar, ag, ab, 0.14),
        "glass_mid": _rgba(ar, ag, ab, 0.06),
        "glass_lo": "rgba(0,0,0,0.55)",
        "inset_hi": _rgba(255, 255, 255, 0.28),
        "danger": "#ff6b6b",
        "ok": "#9dffb0",
    }


def _glass_css(p: dict[str, str]) -> str:
    """Shared DaemonDomain glass rules parameterized by palette."""
    return f"""
* {{
    font-family: "Tomorrow", sans-serif;
    letter-spacing: 0.4px;
}}

window {{
    background: {p["window"]};
    color: {p["text"]};
}}

.toolbar {{
    background: {p["panel"]};
    background-image: linear-gradient(180deg, {p["glass_hi"]} 0%, {p["glass_mid"]} 45%, {p["glass_lo"]} 100%);
    color: {p["text"]};
    padding: 8px 10px;
    border-bottom: 1px solid {p["border"]};
    box-shadow: inset 0 1px 0 {p["inset_hi"]};
}}

.app-icon {{
    margin-right: 6px;
    border-radius: 6px;
    min-width: 28px;
    min-height: 28px;
}}

.doc-title {{
    color: {p["text"]};
    font-weight: 600;
    letter-spacing: 0.4px;
    padding: 0 4px;
    opacity: 0.92;
}}

.doc-tabs-host {{
    padding: 0 8px;
}}

.doc-tabs {{
    padding: 0 2px;
}}

.doc-tab {{
    background: rgba(255,255,255,0.04);
    border: 1px solid {p["border_soft"]};
    border-radius: 8px;
    padding: 2px 4px 2px 8px;
    min-height: 28px;
}}

.doc-tab:hover {{
    background: rgba(255,255,255,0.08);
}}

.doc-tab.active {{
    background: rgba(255,255,255,0.12);
    border-color: {p["border"]};
}}

.doc-tab.active .doc-title {{
    opacity: 1;
}}

.doc-tab-close {{
    min-width: 22px;
    min-height: 22px;
    padding: 0;
    font-size: 14px;
    font-weight: 600;
    border-radius: 6px;
    background: transparent;
    border: none;
    color: {p["muted"]};
    box-shadow: none;
}}

.doc-tab-close:hover {{
    background: rgba(255,255,255,0.12);
    color: {p["text"]};
    box-shadow: none;
}}

.doc-tab-new {{
    min-width: 28px;
    min-height: 28px;
    padding: 0 6px;
    font-weight: 700;
    border-radius: 8px;
}}

.toolbox {{
    background: {p["panel"]};
    background-image: linear-gradient(180deg, {p["glass_hi"]} 0%, {p["glass_mid"]} 45%, {p["glass_lo"]} 100%);
    color: {p["text"]};
    padding: 10px;
    border: 1px solid {p["border_soft"]};
    border-radius: 12px;
    margin: 6px;
    box-shadow: 0 0 12px {p["accent_glow"]}, inset 0 1px 0 {p["inset_hi"]};
}}

.toolbox .tool-btn {{
    min-width: 52px;
    min-height: 30px;
    padding: 4px 8px;
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.5px;
    background: {p["button"]};
    color: {p["text"]};
    border: 1px solid {p["border_soft"]};
    border-radius: 10px;
    box-shadow: none;
}}

.toolbox .tool-btn:hover {{
    background: {p["button_hover"]};
    border-color: {p["border"]};
    box-shadow: 0 0 10px {p["accent_glow"]};
}}

.pattern-thumb {{
    min-width: 56px;
    min-height: 56px;
    padding: 2px;
    background: {p["button"]};
    border: 1px solid {p["border_soft"]};
    border-radius: 8px;
    box-shadow: none;
}}

.pattern-thumb:hover {{
    background: {p["button_hover"]};
    border-color: {p["border"]};
}}

.pattern-thumb:checked {{
    border-color: {p["accent"]};
    box-shadow: 0 0 8px {p["accent_glow"]};
}}

.toolbox flowboxchild {{
    padding: 2px;
}}

.lib-cat-btn {{
    min-height: 38px;
    font-weight: 500;
    letter-spacing: 0.6px;
    background: {p["button"]};
    color: {p["text"]};
    border: 1px solid {p["border_soft"]};
    border-radius: 10px;
}}

.lib-cat-btn:hover {{
    background: {p["button_hover"]};
    border-color: {p["border"]};
    box-shadow: 0 0 10px {p["accent_glow"]};
}}

.sidebar {{
    background: {p["sidebar"]};
    background-image: linear-gradient(180deg, {p["glass_hi"]} 0%, {p["glass_mid"]} 40%, {p["glass_lo"]} 100%);
    color: {p["text"]};
    padding: 10px;
    border-left: 1px solid {p["border"]};
    box-shadow: inset 1px 0 0 {p["inset_hi"]};
}}

.status {{
    background: {p["status"]};
    background-image: linear-gradient(180deg, {p["glass_mid"]} 0%, {p["glass_lo"]} 100%);
    padding: 6px 12px;
    font-size: 12px;
    letter-spacing: 0.5px;
    color: {p["muted"]};
    border-top: 1px solid {p["border_soft"]};
}}

button.tool-active, togglebutton:checked {{
    background: rgba(255,255,255,0.12);
    color: {p["text"]};
    border: 1px solid {p["border"]};
    box-shadow: 0 0 12px {p["accent_glow"]}, inset 0 1px 0 {p["inset_hi"]};
}}

.exit-btn {{
    min-width: 64px;
    font-weight: 700;
    letter-spacing: 0.8px;
    border-radius: 20px;
}}

paned > separator {{
    background: {p["separator"]};
    min-width: 4px;
    min-height: 4px;
}}

notebook {{
    background: transparent;
}}

notebook > header {{
    background: transparent;
    border-bottom: 1px solid {p["border_soft"]};
}}

notebook > header tabs tab {{
    padding: 8px 14px;
    color: {p["dim"]};
    letter-spacing: 0.8px;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
}}

notebook > header tabs tab:hover {{
    color: {p["text"]};
}}

notebook > header tabs tab:checked {{
    color: {p["text"]};
    background: transparent;
    border-bottom-color: {p["accent"]};
}}

button {{
    background: {p["button"]};
    color: {p["text"]};
    border: 1px solid {p["border_soft"]};
    border-radius: 10px;
    padding: 6px 12px;
    letter-spacing: 0.5px;
    box-shadow: none;
}}

button:hover {{
    background: {p["button_hover"]};
    border-color: {p["border"]};
    box-shadow: 0 0 10px {p["accent_glow"]};
}}

button:active {{
    box-shadow: none;
}}

button.suggested-action {{
    background: rgba(255,255,255,0.10);
    border-color: {p["border"]};
    color: {p["text"]};
    font-weight: 700;
}}

button.suggested-action:hover {{
    background: rgba(255,255,255,0.16);
    box-shadow: 0 0 12px {p["accent_glow"]};
}}

button.destructive-action {{
    background: rgba(255,107,107,0.10);
    border-color: rgba(255,107,107,0.55);
    color: #ffd5d5;
}}

button.destructive-action:hover {{
    background: rgba(255,107,107,0.18);
    box-shadow: 0 0 10px rgba(255,107,107,0.45);
}}

menubutton > button {{
    background: transparent;
    color: {p["text"]};
    border: 1px solid transparent;
    border-radius: 20px;
    padding: 6px 14px;
}}

menubutton > button:hover {{
    background: rgba(255,255,255,0.10);
    border-color: {p["border"]};
    box-shadow: 0 0 10px {p["accent_glow"]};
}}

entry, spinbutton {{
    background: {p["entry"]};
    color: {p["text"]};
    border: 1px solid {p["border_soft"]};
    border-radius: 10px;
    padding: 4px 8px;
    box-shadow: none;
}}

entry:focus, spinbutton:focus-within {{
    border-color: {p["border_strong"]};
    box-shadow: 0 0 0 3px rgba(255,255,255,0.08);
}}

list {{
    background: rgba(0,0,0,0.35);
    color: {p["text"]};
    border: 1px solid {p["border_soft"]};
    border-radius: 10px;
}}

list row {{
    padding: 4px;
    border-radius: 8px;
}}

list row:hover {{
    background: rgba(255,255,255,0.06);
}}

list row:selected {{
    background: rgba(255,255,255,0.12);
    color: {p["text"]};
    box-shadow: inset 0 0 0 1px {p["border"]};
}}

label {{
    color: {p["text"]};
}}

.status label, label.status, .dim-label {{
    color: {p["muted"]};
}}

.dim-label {{
    color: {p["dim"]};
    font-size: 12px;
    letter-spacing: 0.6px;
}}

checkbutton {{
    color: {p["text"]};
}}

checkbutton:checked check {{
    background: {p["accent"]};
    border-color: {p["border"]};
    color: {p["window"]};
}}

expander title {{
    color: {p["muted"]};
    letter-spacing: 0.8px;
}}

expander:hover title {{
    color: {p["text"]};
}}

popover {{
    background: transparent;
}}

popover > contents {{
    background: {p["panel"]};
    background-image: linear-gradient(180deg, {p["glass_hi"]} 0%, {p["glass_mid"]} 45%, {p["glass_lo"]} 100%);
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: 12px;
    box-shadow: 0 8px 28px rgba(0,0,0,0.75), 0 0 15px {p["accent_glow"]}, inset 0 1px 0 {p["inset_hi"]};
    padding: 6px;
}}

popover contents modelbutton {{
    color: {p["text"]};
    border-radius: 8px;
    padding: 8px 10px;
}}

popover contents modelbutton:hover {{
    background: rgba(255,255,255,0.10);
}}

tooltip {{
    background: {p["panel"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: 8px;
    padding: 6px 10px;
}}

scrolledwindow trough {{
    background: rgba(0,0,0,0.35);
    border-radius: 5px;
}}

scrolledwindow slider {{
    background: linear-gradient(180deg, #555555, #333333);
    border: 1px solid {p["border_soft"]};
    border-radius: 8px;
    min-width: 10px;
    min-height: 40px;
}}

scrolledwindow slider:hover {{
    background: linear-gradient(180deg, {p["accent"]}, #555555);
}}

headerbar {{
    background: {p["panel"]};
    background-image: linear-gradient(180deg, {p["glass_hi"]} 0%, {p["glass_lo"]} 100%);
    color: {p["text"]};
    border-bottom: 1px solid {p["border"]};
    box-shadow: inset 0 1px 0 {p["inset_hi"]};
}}

dropdown > button {{
    border-radius: 10px;
}}
"""


# Default DaemonDomain chrome (black stage, white accent)
DEFAULT_CSS = _glass_css(shades_from_rgb(255, 255, 255))


def build_theme_css(r: int, g: int, b: int) -> str:
    return _glass_css(shades_from_rgb(r, g, b))
