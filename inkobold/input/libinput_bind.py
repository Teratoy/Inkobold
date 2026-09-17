"""ctypes bindings for libinput (device discovery + tablet/pointer events)."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Optional


class LibinputError(RuntimeError):
    pass


class EventType(IntEnum):
    DEVICE_ADDED = 100
    DEVICE_REMOVED = 101
    KEYBOARD_KEY = 300
    POINTER_MOTION = 400
    POINTER_MOTION_ABSOLUTE = 401
    POINTER_BUTTON = 402
    POINTER_AXIS = 403
    TOUCH_DOWN = 500
    TOUCH_UP = 501
    TOUCH_MOTION = 502
    TOUCH_CANCEL = 503
    TOUCH_FRAME = 504
    TABLET_TOOL_AXIS = 600
    TABLET_TOOL_PROXIMITY = 601
    TABLET_TOOL_TIP = 602
    TABLET_TOOL_BUTTON = 603
    TABLET_PAD_BUTTON = 700
    TABLET_PAD_RING = 701
    TABLET_PAD_STRIP = 702
    GESTURE_SWIPE_BEGIN = 800
    GESTURE_SWIPE_UPDATE = 801
    GESTURE_SWIPE_END = 802
    GESTURE_PINCH_BEGIN = 803
    GESTURE_PINCH_UPDATE = 804
    GESTURE_PINCH_END = 805


class TipState(IntEnum):
    UP = 0
    DOWN = 1


class ButtonState(IntEnum):
    RELEASED = 0
    PRESSED = 1


@dataclass
class PointerSample:
    x: float
    y: float
    pressure: float = 1.0
    tilt_x: float = 0.0
    tilt_y: float = 0.0
    button_pressed: bool = False
    tip_down: bool = False
    source: str = "pointer"
    device_name: str = ""


# libinput_interface callbacks (see libinput.h) — no libinput* first arg:
#   int  open_restricted(const char *path, int flags, void *user_data);
#   void close_restricted(int fd, void *user_data);
OPEN_RESTRICTED = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p
)
CLOSE_RESTRICTED = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)


class LibinputInterface(ctypes.Structure):
    _fields_ = [
        ("open_restricted", OPEN_RESTRICTED),
        ("close_restricted", CLOSE_RESTRICTED),
    ]


def _load() -> ctypes.CDLL:
    path = ctypes.util.find_library("input") or "libinput.so.10"
    try:
        return ctypes.CDLL(path)
    except OSError as exc:
        raise LibinputError(f"Unable to load libinput: {exc}") from exc


class Libinput:
    """Thin wrapper around libinput for tablet/pointer polling."""

    def __init__(self) -> None:
        self._lib = _load()
        self._bind()
        self._interface = LibinputInterface(
            open_restricted=OPEN_RESTRICTED(self._open_restricted),
            close_restricted=CLOSE_RESTRICTED(self._close_restricted),
        )
        # Keep refs so callbacks are not GC'd
        self._cb_keep = (self._interface.open_restricted, self._interface.close_restricted)
        self._ctx = self._lib.libinput_path_create_context(ctypes.byref(self._interface), None)
        if not self._ctx:
            raise LibinputError("libinput_path_create_context failed")
        self._devices: list[ctypes.c_void_p] = []
        self._listeners: list[Callable[[PointerSample], None]] = []
        self.devices_info: list[dict] = []

    def _bind(self) -> None:
        L = self._lib
        L.libinput_path_create_context.restype = ctypes.c_void_p
        L.libinput_path_create_context.argtypes = [ctypes.POINTER(LibinputInterface), ctypes.c_void_p]
        L.libinput_unref.argtypes = [ctypes.c_void_p]
        L.libinput_path_add_device.restype = ctypes.c_void_p
        L.libinput_path_add_device.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        L.libinput_get_fd.restype = ctypes.c_int
        L.libinput_get_fd.argtypes = [ctypes.c_void_p]
        L.libinput_dispatch.restype = ctypes.c_int
        L.libinput_dispatch.argtypes = [ctypes.c_void_p]
        L.libinput_get_event.restype = ctypes.c_void_p
        L.libinput_get_event.argtypes = [ctypes.c_void_p]
        L.libinput_event_destroy.argtypes = [ctypes.c_void_p]
        L.libinput_event_get_type.restype = ctypes.c_int
        L.libinput_event_get_type.argtypes = [ctypes.c_void_p]
        L.libinput_event_get_device.restype = ctypes.c_void_p
        L.libinput_event_get_device.argtypes = [ctypes.c_void_p]
        L.libinput_device_get_name.restype = ctypes.c_char_p
        L.libinput_device_get_name.argtypes = [ctypes.c_void_p]
        L.libinput_device_has_capability.restype = ctypes.c_int
        L.libinput_device_has_capability.argtypes = [ctypes.c_void_p, ctypes.c_int]

        for name, restype in (
            ("libinput_event_get_pointer_event", ctypes.c_void_p),
            ("libinput_event_get_tablet_tool_event", ctypes.c_void_p),
            ("libinput_event_pointer_get_absolute_x_transformed", ctypes.c_double),
            ("libinput_event_pointer_get_absolute_y_transformed", ctypes.c_double),
            ("libinput_event_pointer_get_button_state", ctypes.c_int),
            ("libinput_event_tablet_tool_get_x_transformed", ctypes.c_double),
            ("libinput_event_tablet_tool_get_y_transformed", ctypes.c_double),
            ("libinput_event_tablet_tool_get_pressure", ctypes.c_double),
            ("libinput_event_tablet_tool_get_tilt_x", ctypes.c_double),
            ("libinput_event_tablet_tool_get_tilt_y", ctypes.c_double),
            ("libinput_event_tablet_tool_get_tip_state", ctypes.c_int),
            ("libinput_event_tablet_tool_get_proximity_state", ctypes.c_int),
        ):
            fn = getattr(L, name)
            fn.restype = restype

        L.libinput_event_pointer_get_absolute_x_transformed.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        L.libinput_event_pointer_get_absolute_y_transformed.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        L.libinput_event_tablet_tool_get_x_transformed.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        L.libinput_event_tablet_tool_get_y_transformed.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        for n in (
            "libinput_event_tablet_tool_get_pressure",
            "libinput_event_tablet_tool_get_tilt_x",
            "libinput_event_tablet_tool_get_tilt_y",
            "libinput_event_tablet_tool_get_tip_state",
            "libinput_event_tablet_tool_get_proximity_state",
            "libinput_event_pointer_get_button_state",
        ):
            getattr(L, n).argtypes = [ctypes.c_void_p]

    @staticmethod
    def _open_restricted(path: bytes, flags: int, _user: ctypes.c_void_p) -> int:
        try:
            return os.open(path.decode(), flags)
        except OSError as exc:
            # libinput expects a negative errno on failure
            return -int(exc.errno or 1)

    @staticmethod
    def _close_restricted(fd: int, _user: ctypes.c_void_p) -> None:
        try:
            os.close(fd)
        except OSError:
            pass

    def add_default_devices(self) -> int:
        """Add readable /dev/input/event* nodes. Returns count added."""
        base = "/dev/input"
        added = 0
        if not os.path.isdir(base):
            return 0
        for name in sorted(os.listdir(base)):
            if not name.startswith("event"):
                continue
            path = os.path.join(base, name)
            try:
                # Probe readability without keeping the fd
                with open(path, "rb"):
                    pass
            except OSError:
                continue
            dev = self._lib.libinput_path_add_device(self._ctx, path.encode())
            if dev:
                self._devices.append(ctypes.c_void_p(dev))
                dname = self._lib.libinput_device_get_name(dev) or b""
                caps = []
                # LIBINPUT_DEVICE_CAP_POINTER=1, TOUCH=2, TABLET_TOOL=4, TABLET_PAD=5
                for cap, label in ((1, "pointer"), (2, "touch"), (4, "tablet_tool"), (5, "tablet_pad")):
                    if self._lib.libinput_device_has_capability(dev, cap):
                        caps.append(label)
                self.devices_info.append({"path": path, "name": dname.decode(errors="replace"), "caps": caps})
                added += 1
        return added

    @property
    def fd(self) -> int:
        return int(self._lib.libinput_get_fd(self._ctx))

    def on_sample(self, callback: Callable[[PointerSample], None]) -> None:
        self._listeners.append(callback)

    def dispatch(self, width: int = 1, height: int = 1) -> None:
        self._lib.libinput_dispatch(self._ctx)
        while True:
            ev = self._lib.libinput_get_event(self._ctx)
            if not ev:
                break
            try:
                sample = self._event_to_sample(ev, max(1, width), max(1, height))
                if sample is not None:
                    for cb in self._listeners:
                        cb(sample)
            finally:
                self._lib.libinput_event_destroy(ev)

    def _device_name(self, ev: int) -> str:
        dev = self._lib.libinput_event_get_device(ev)
        raw = self._lib.libinput_device_get_name(dev) if dev else None
        return (raw or b"").decode(errors="replace")

    _POINTER_EVENTS = frozenset((int(EventType.POINTER_MOTION_ABSOLUTE), int(EventType.POINTER_BUTTON)))
    _TABLET_EVENTS = frozenset(
        (
            int(EventType.TABLET_TOOL_AXIS),
            int(EventType.TABLET_TOOL_PROXIMITY),
            int(EventType.TABLET_TOOL_TIP),
            int(EventType.TABLET_TOOL_BUTTON),
        )
    )

    def _event_to_sample(self, ev: int, width: int, height: int) -> Optional[PointerSample]:
        L = self._lib
        # Compare raw ints: libinput emits types this enum doesn't list (gesture
        # hold, switch, pad key…) and constructing EventType would raise,
        # dropping the rest of the queue for this poll.
        et = int(L.libinput_event_get_type(ev))

        if et in self._POINTER_EVENTS:
            pe = L.libinput_event_get_pointer_event(ev)
            if not pe:
                return None
            name = self._device_name(ev)
            x = L.libinput_event_pointer_get_absolute_x_transformed(pe, width)
            y = L.libinput_event_pointer_get_absolute_y_transformed(pe, height)
            pressed = False
            if et == EventType.POINTER_BUTTON:
                pressed = L.libinput_event_pointer_get_button_state(pe) == ButtonState.PRESSED
            return PointerSample(x=x, y=y, button_pressed=pressed, tip_down=pressed, source="pointer", device_name=name)

        if et in self._TABLET_EVENTS:
            te = L.libinput_event_get_tablet_tool_event(ev)
            if not te:
                return None
            name = self._device_name(ev)
            x = L.libinput_event_tablet_tool_get_x_transformed(te, width)
            y = L.libinput_event_tablet_tool_get_y_transformed(te, height)
            pressure = float(L.libinput_event_tablet_tool_get_pressure(te))
            tilt_x = float(L.libinput_event_tablet_tool_get_tilt_x(te))
            tilt_y = float(L.libinput_event_tablet_tool_get_tilt_y(te))
            tip = L.libinput_event_tablet_tool_get_tip_state(te) == TipState.DOWN
            return PointerSample(
                x=x,
                y=y,
                pressure=pressure if pressure > 0 else (1.0 if tip else 0.0),
                tilt_x=tilt_x,
                tilt_y=tilt_y,
                tip_down=tip,
                button_pressed=tip,
                source="tablet",
                device_name=name,
            )
        return None

    def close(self) -> None:
        if self._ctx:
            self._lib.libinput_unref(self._ctx)
            self._ctx = None
