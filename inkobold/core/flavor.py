"""App flavors: Inkobold and pipeline-specific variants built from the same code.

A flavor is chosen once at startup (before the window is built) and read
everywhere else through :func:`current`. Plain Inkobold is the default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Flavor:
    # Shown in the window title, tooltips and dialogs
    display_name: str
    # Gtk.Application id; distinct ids let flavors run side by side
    app_id: str
    # GLib prgname / icon name / desktop StartupWMClass
    prgname: str
    # Config folder name (Linux: ~/.config/<config_name>, Windows: %APPDATA%\<win_config_name>)
    config_name: str
    win_config_name: str
    # Prefix for <PREFIX>_CONFIG_HOME / <PREFIX>_ICON overrides
    env_prefix: str
    # Icon file under scripts/
    icon_file: str
    # ``python -m <module>`` relaunches this flavor (debug Reload)
    module: str
    # Accent used when the user has not picked a custom theme; None = classic Inkobold chrome
    theme_rgb: tuple[int, int, int] | None = None
    # Default visual-asset folder for the Assets tab and export dialogs; None = feature off
    asset_root: Path | None = None
    # Environment variable that overrides the asset folder
    asset_env: str = ""
    # Export Separated Layers prefixes each file with the layer number (``03_Name.png``)
    layer_export_index: bool = True
    # Goblin-rig characters (core.character): Rig tab, Open / New Character
    character_rig: bool = False
    # Pixel Art mode (hard pixels, pixel zoom/grid) for new windows and files saved without it
    pixel_default: bool = False
    # Tool ids left out of the toolbox until the user picks them in Visible Tools
    hidden_tools: tuple[str, ...] = ()
    # New File size buttons as (label, width, height); the first one is the default size
    new_file_presets: tuple[tuple[str, int, int], ...] = (
        ("HD", 1920, 1080),
        ("Square", 2048, 2048),
        ("A4@150", 1240, 1754),
        ("4K", 3840, 2160),
    )


INKOBOLD = Flavor(
    display_name="Inkobold",
    app_id="trinkets.inkobold",
    prgname="trinkets-inkobold",
    config_name="inkobold",
    win_config_name="Inkobold",
    env_prefix="INKOBOLD",
    icon_file="inkobold.png",
    module="inkobold",
)

GREEN_INKOBOLD = Flavor(
    display_name="GreenInkobold",
    app_id="trinkets.greeninkobold",
    prgname="trinkets-greeninkobold",
    config_name="greeninkobold",
    win_config_name="GreenInkobold",
    env_prefix="GREENINKOBOLD",
    icon_file="greeninkobold.png",
    module="inkobold.green",
    theme_rgb=(120, 200, 70),
    asset_root=Path.home() / "Toyfactory" / "GoblinEngineering" / "visual assets",
    asset_env="GREENINKOBOLD_ASSETS",
    layer_export_index=False,
    character_rig=True,
    # Sizes used most in the Goblin Engineering visual assets; Block first so it is the default
    new_file_presets=(
        ("Block 100×100", 100, 100),
        ("Rig part 400×500", 400, 500),
        ("Icon 50×50", 50, 50),
        ("Half block 100×50", 100, 50),
        ("Tall block 100×200", 100, 200),
        ("Big block 200×200", 200, 200),
        ("Grass 20×40", 20, 40),
        ("Fauna 70×50", 70, 50),
        ("Title 1000×500", 1000, 500),
        ("Screen 1920×1080", 1920, 1080),
    ),
)

INKIMP = Flavor(
    display_name="Inkimp",
    app_id="trinkets.inkimp",
    prgname="trinkets-inkimp",
    config_name="inkimp",
    win_config_name="Inkimp",
    env_prefix="INKIMP",
    icon_file="inkimp.png",
    module="inkobold.imp",
    theme_rgb=(214, 72, 62),
    pixel_default=True,
    # Soft, smudging or anti-aliased tools that fight a pixel grid
    hidden_tools=(
        "lay", "weld_brush", "gradient", "pen3d", "fill3d", "smear", "liquify", "type",
    ),
    # Sprite and tile sizes; 32×32 first so it is the default
    new_file_presets=(
        ("32×32", 32, 32),
        ("16×16", 16, 16),
        ("24×24", 24, 24),
        ("48×48", 48, 48),
        ("64×64", 64, 64),
        ("128×128", 128, 128),
        ("256×256", 256, 256),
        ("Screen 320×180", 320, 180),
        ("Screen 640×360", 640, 360),
    ),
)

_current: Flavor = INKOBOLD


def current() -> Flavor:
    return _current


def set_flavor(flavor: Flavor) -> None:
    global _current
    _current = flavor
