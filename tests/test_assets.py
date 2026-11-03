"""GreenInkobold asset-pipeline rules (no GTK / OpenGL needed).

Run with ``python -m pytest tests`` or ``python tests/test_assets.py``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core import assets, flavor, paths, settings  # noqa: E402


def _project() -> tuple[Path, Path]:
    """Temp Godot project with a ``visual assets`` folder; returns (project, assets)."""
    project = Path(tempfile.mkdtemp(prefix="greeninkobold-"))
    (project / "project.godot").write_text("", encoding="utf-8")
    art = project / "visual assets"
    (art / "player" / "hats").mkdir(parents=True)
    (art / "player" / "head.png").write_bytes(b"")
    return project, art


def test_listing_hides_engine_sidecars_and_dotfiles():
    assert assets.is_listed("head.png")
    assert assets.is_listed("hats")
    assert not assets.is_listed("head.png.import")
    assert not assets.is_listed("script.gd.uid")
    assert not assets.is_listed(".gdignore")
    assert not assets.is_listed("")


def test_sort_puts_folders_first_case_insensitively():
    entries = [("b.png", False), ("Zeta", True), ("A.png", False), ("alpha", True)]
    ordered = [n for n, d in sorted(entries, key=lambda e: assets.sort_key(*e))]
    assert ordered == ["alpha", "Zeta", "A.png", "b.png"]


def test_asset_root_precedence():
    green = flavor.GREEN_INKOBOLD
    old = os.environ.pop(green.asset_env, None)
    try:
        assert assets.asset_root("", green) == green.asset_root
        assert assets.asset_root("/tmp/saved", green) == Path("/tmp/saved")
        os.environ[green.asset_env] = "/tmp/env"
        assert assets.asset_root("/tmp/saved", green) == Path("/tmp/env")
        # Plain Inkobold has no asset pipeline
        assert assets.asset_root("/tmp/saved", flavor.INKOBOLD) is None
    finally:
        os.environ.pop(green.asset_env, None)
        if old is not None:
            os.environ[green.asset_env] = old


def test_export_folder_prefers_document_then_selection_then_root():
    _project_dir, art = _project()
    head = art / "player" / "head.png"
    hats = art / "player" / "hats"
    outside = Path(tempfile.mkdtemp()) / "drawing.inkobold"
    # Feature off
    assert assets.export_folder(None, head, hats) is None
    # Document already in the tree exports next to itself
    assert assets.export_folder(art, head, hats) == art / "player"
    # Document elsewhere: the folder picked in the Assets tab (a selected file means its folder)
    assert assets.export_folder(art, outside, hats) == hats
    assert assets.export_folder(art, outside, head) == art / "player"
    # Nothing useful: asset root
    assert assets.export_folder(art, outside, None) == art
    assert assets.export_folder(art, None, outside.parent) == art
    # Missing root: leave the system default alone
    assert assets.export_folder(art / "nope", None, None) is None


def test_godot_res_path():
    project, art = _project()
    assert assets.godot_res_path(art / "player" / "head.png") == "res://visual assets/player/head.png"
    assert assets.godot_res_path(art / "player" / "hats") == "res://visual assets/player/hats"
    assert assets.godot_res_path(Path(tempfile.mkdtemp()) / "x.png") is None
    assert project.is_dir()


def test_flavors_keep_separate_config():
    old_env = {k: os.environ.pop(k, None) for k in ("INKOBOLD_CONFIG_HOME", "GREENINKOBOLD_CONFIG_HOME")}
    try:
        flavor.set_flavor(flavor.INKOBOLD)
        plain_cfg, plain_data = paths.config_dir(), paths.data_dir()
        assert settings.AppSettings().theme_rgb == settings.DEFAULT_THEME_RGB
        flavor.set_flavor(flavor.GREEN_INKOBOLD)
        assert paths.config_dir() != plain_cfg
        assert paths.config_dir().name.lower() == "greeninkobold"
        # Brushes / patterns / fonts libraries stay shared
        assert paths.data_dir() == plain_data
        assert settings.AppSettings().theme_rgb == flavor.GREEN_INKOBOLD.theme_rgb
    finally:
        flavor.set_flavor(flavor.INKOBOLD)
        for k, v in old_env.items():
            if v is not None:
                os.environ[k] = v



def test_inkimp_flavor():
    imp = flavor.INKIMP
    assert imp.pixel_default and not flavor.INKOBOLD.pixel_default
    assert imp.asset_root is None and not imp.character_rig
    # Hidden tools name real tools and keep the pixel tools
    from inkobold.tools import default_tools

    ids = set(default_tools())
    assert set(imp.hidden_tools) <= ids
    assert {"pen", "brush", "eraser", "line", "rectangle", "fill"}.isdisjoint(imp.hidden_tools)
    assert imp.new_file_presets[0][1:] == (32, 32)
    assert len({f.app_id for f in (flavor.INKOBOLD, flavor.GREEN_INKOBOLD, imp)}) == 3
    assert len({f.config_name for f in (flavor.INKOBOLD, flavor.GREEN_INKOBOLD, imp)}) == 3
    assert (Path(__file__).resolve().parents[1] / "scripts" / imp.icon_file).is_file()


def test_asset_root_setting_round_trips():
    cfg = Path(tempfile.mkdtemp())
    old = os.environ.get("GREENINKOBOLD_CONFIG_HOME")
    os.environ["GREENINKOBOLD_CONFIG_HOME"] = str(cfg)
    flavor.set_flavor(flavor.GREEN_INKOBOLD)
    try:
        s = settings.load_settings()
        assert s.asset_root == ""
        s.asset_root = "/somewhere/visual assets"
        settings.save_settings(s)
        assert settings.load_settings().asset_root == "/somewhere/visual assets"
    finally:
        flavor.set_flavor(flavor.INKOBOLD)
        if old is None:
            os.environ.pop("GREENINKOBOLD_CONFIG_HOME", None)
        else:
            os.environ["GREENINKOBOLD_CONFIG_HOME"] = old


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as exc:  # pragma: no cover - manual runner
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
