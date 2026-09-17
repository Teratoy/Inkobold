"""ctypes bindings for libwacom (tablet identification / capabilities)."""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass, field
from typing import Optional


class LibwacomError(RuntimeError):
    pass


@dataclass
class TabletInfo:
    name: str
    vendor_id: int
    product_id: int
    width_mm: float
    height_mm: float
    num_buttons: int
    styli: list[str] = field(default_factory=list)
    path: str = ""


def _load() -> ctypes.CDLL:
    path = ctypes.util.find_library("wacom") or "libwacom.so.9"
    try:
        return ctypes.CDLL(path)
    except OSError as exc:
        raise LibwacomError(f"Unable to load libwacom: {exc}") from exc


class LibwacomDB:
    """Query connected tablets via the libwacom database."""

    def __init__(self) -> None:
        self._lib = _load()
        self._bind()
        self._db = self._lib.libwacom_database_new()
        if not self._db:
            raise LibwacomError("libwacom_database_new failed")

    def _bind(self) -> None:
        L = self._lib
        L.libwacom_database_new.restype = ctypes.c_void_p
        L.libwacom_database_destroy.argtypes = [ctypes.c_void_p]
        L.libwacom_new_from_path.restype = ctypes.c_void_p
        L.libwacom_new_from_path.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        L.libwacom_destroy.argtypes = [ctypes.c_void_p]
        L.libwacom_get_name.restype = ctypes.c_char_p
        L.libwacom_get_name.argtypes = [ctypes.c_void_p]
        L.libwacom_get_vendor_id.restype = ctypes.c_int
        L.libwacom_get_vendor_id.argtypes = [ctypes.c_void_p]
        L.libwacom_get_product_id.restype = ctypes.c_int
        L.libwacom_get_product_id.argtypes = [ctypes.c_void_p]
        L.libwacom_get_width.restype = ctypes.c_int
        L.libwacom_get_width.argtypes = [ctypes.c_void_p]
        L.libwacom_get_height.restype = ctypes.c_int
        L.libwacom_get_height.argtypes = [ctypes.c_void_p]
        L.libwacom_get_num_buttons.restype = ctypes.c_int
        L.libwacom_get_num_buttons.argtypes = [ctypes.c_void_p]
        # Fallback list API (may be unavailable on some builds)
        if hasattr(L, "libwacom_list_devices_from_database"):
            L.libwacom_list_devices_from_database.restype = ctypes.POINTER(ctypes.c_void_p)
            L.libwacom_list_devices_from_database.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]

    def tablet_from_path(self, path: str) -> Optional[TabletInfo]:
        err = ctypes.c_int(0)
        # WFALLBACK = 1
        dev = self._lib.libwacom_new_from_path(self._db, path.encode(), 1, ctypes.byref(err))
        if not dev:
            return None
        try:
            return self._info(dev, path)
        finally:
            self._lib.libwacom_destroy(dev)

    def list_known(self) -> list[TabletInfo]:
        """Best-effort scan of /dev/input/event* against the wacom DB."""
        import os

        found: list[TabletInfo] = []
        base = "/dev/input"
        if not os.path.isdir(base):
            return found
        for name in sorted(os.listdir(base)):
            if not name.startswith("event"):
                continue
            info = self.tablet_from_path(os.path.join(base, name))
            if not info:
                continue
            # Skip generic / non-tablet matches (touchpads etc.)
            if info.width_mm <= 0 or info.height_mm <= 0:
                continue
            lower = info.name.lower()
            if any(s in lower for s in ("touchpad", "trackpoint", "mouse", "keyboard")):
                continue
            key = (info.vendor_id, info.product_id, info.name)
            if not any((t.vendor_id, t.product_id, t.name) == key for t in found):
                found.append(info)
        return found

    def _info(self, dev: int, path: str) -> TabletInfo:
        L = self._lib
        raw = L.libwacom_get_name(dev) or b""
        return TabletInfo(
            name=raw.decode(errors="replace"),
            vendor_id=int(L.libwacom_get_vendor_id(dev)),
            product_id=int(L.libwacom_get_product_id(dev)),
            width_mm=float(L.libwacom_get_width(dev)),
            height_mm=float(L.libwacom_get_height(dev)),
            num_buttons=int(L.libwacom_get_num_buttons(dev)),
            path=path,
        )

    def close(self) -> None:
        if self._db:
            self._lib.libwacom_database_destroy(self._db)
            self._db = None
