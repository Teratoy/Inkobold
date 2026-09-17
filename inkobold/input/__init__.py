"""Unified tablet / pointer input: GDK primary, libinput enrichment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from inkobold.input.libinput_bind import Libinput, LibinputError, PointerSample
from inkobold.input.libwacom_bind import LibwacomDB, LibwacomError, TabletInfo


@dataclass
class InputHub:
    libinput: Optional[Libinput] = None
    libwacom: Optional[LibwacomDB] = None
    tablets: list[TabletInfo] = field(default_factory=list)
    devices: list[dict] = field(default_factory=list)
    last_sample: Optional[PointerSample] = None
    status: str = "GDK pointer only"

    def start(self) -> None:
        try:
            self.libwacom = LibwacomDB()
            self.tablets = self.libwacom.list_known()
        except LibwacomError as exc:
            self.status = f"libwacom unavailable: {exc}"

        try:
            li = Libinput()
            n = li.add_default_devices()
            self.devices = list(li.devices_info)
            li.on_sample(self._on_sample)
            self.libinput = li
            tablet_names = ", ".join(t.name for t in self.tablets) or "none"
            if n == 0:
                self.status = (
                    f"libinput ready (0 /dev/input nodes readable — add user to 'input' group "
                    f"for raw tablet axes); libwacom tablets=[{tablet_names}]; drawing via GDK"
                )
            else:
                self.status = f"libinput devices={n}; libwacom tablets=[{tablet_names}]"
        except LibinputError as exc:
            self.status = f"libinput unavailable ({exc}); using GDK"

    def _on_sample(self, sample: PointerSample) -> None:
        self.last_sample = sample

    def pressure(self, fallback: float | None = 1.0) -> float | None:
        if self.last_sample and self.last_sample.source == "tablet":
            return max(0.0, min(1.0, self.last_sample.pressure or 0.0))
        return fallback

    def poll(self, width: int, height: int) -> None:
        if self.libinput is not None:
            try:
                self.libinput.dispatch(width, height)
            except Exception:
                pass

    def close(self) -> None:
        if self.libinput is not None:
            self.libinput.close()
            self.libinput = None
        if self.libwacom is not None:
            self.libwacom.close()
            self.libwacom = None
