"""Visual-asset folder helpers (GreenInkobold): root lookup, listing rules, export targets.

GTK-free so the rules can be tested without a display.
"""

from __future__ import annotations

import os
from pathlib import Path

from inkobold.core.flavor import Flavor, current

# Engine sidecars that sit next to every source image (Godot .import / .uid)
HIDDEN_SUFFIXES = frozenset({".import", ".uid"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"})
OPENABLE_SUFFIXES = IMAGE_SUFFIXES | {".inkobold", ".scribbler"}


def asset_root(saved: str = "", flavor: Flavor | None = None) -> Path | None:
    """Asset folder for *flavor*: env override > saved setting > flavor default.

    None when the flavor has no asset pipeline. The folder may not exist yet.
    """
    flavor = flavor or current()
    if flavor.asset_root is None:
        return None
    env = os.environ.get(flavor.asset_env) if flavor.asset_env else None
    for raw in (env, saved):
        if raw:
            return Path(raw).expanduser()
    return flavor.asset_root


def is_listed(name: str) -> bool:
    """Whether a folder entry belongs in the asset tree (hide dotfiles and engine sidecars)."""
    if not name or name.startswith("."):
        return False
    return Path(name).suffix.lower() not in HIDDEN_SUFFIXES


def sort_key(name: str, is_dir: bool) -> tuple[int, str, str]:
    """Folders first, then case-insensitive name."""
    return (0 if is_dir else 1, name.casefold(), name)


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def export_folder(
    root: Path | None,
    doc_path: Path | None = None,
    selected: Path | None = None,
) -> Path | None:
    """Folder an export dialog should start in, or None to keep the system default.

    A document that already lives in the asset tree exports next to itself;
    otherwise the folder picked in the Assets tab; otherwise the asset root.
    """
    if root is None:
        return None
    candidates = []
    if doc_path is not None:
        candidates.append(doc_path.parent)
    if selected is not None:
        candidates.append(selected if selected.is_dir() else selected.parent)
    for cand in candidates:
        if cand.is_dir() and is_within(cand, root):
            return cand
    return root if root.is_dir() else None


def godot_project_dir(path: Path) -> Path | None:
    """Nearest ancestor of *path* holding a project.godot, if any."""
    for parent in (path, *path.parents):
        if (parent / "project.godot").is_file():
            return parent
    return None


def godot_res_path(path: Path) -> str | None:
    """``res://`` path Godot uses for *path*, or None outside a Godot project."""
    project = godot_project_dir(path.parent if not path.is_dir() else path)
    if project is None:
        return None
    try:
        rel = path.resolve().relative_to(project.resolve())
    except ValueError:
        return None
    return "res://" + rel.as_posix()
