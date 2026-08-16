"""Passive PCAN listener using python-can."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional

try:
    import can
    from can import BusState
except ImportError as exc:
    raise ImportError("python-can is required: pip install python-can") from exc


@dataclass
class CanFrame:
    timestamp: float
    arbitration_id: int
    is_extended_id: bool
    data: bytes


DEFAULT_CHANNELS = [
    "PCAN_USBBUS1",
    "PCAN_USBBUS2",
    "PCAN_USBBUS3",
    "PCAN_USBBUS4",
    "PCAN_USBBUS5",
    "PCAN_USBBUS6",
    "PCAN_USBBUS7",
    "PCAN_USBBUS8",
]

DEFAULT_BITRATES = [125000, 250000, 500000, 1000000]


class PcanListener:
    """Capture CAN frames from PCAN in listen-only (passive) mode."""

    def __init__(
        self,
        frame_queue: queue.Queue,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._queue = frame_queue
        self._on_error = on_error
        self._bus: Optional[can.BusABC] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._frame_count = 0

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def is_connected(self) -> bool:
        return self._running and self._bus is not None

    def connect(self, channel: str = "PCAN_USBBUS1", bitrate: int = 500000) -> None:
        if self._running:
            self.disconnect()

        self._bus = can.Bus(
            interface="pcan",
            channel=channel,
            bitrate=bitrate,
            state=BusState.PASSIVE,
        )
        self._running = True
        self._frame_count = 0
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                pass
            self._bus = None

    def _read_loop(self) -> None:
        assert self._bus is not None
        while self._running:
            try:
                msg = self._bus.recv(timeout=0.1)
                if msg is None:
                    continue
                self._frame_count += 1
                frame = CanFrame(
                    timestamp=msg.timestamp,
                    arbitration_id=msg.arbitration_id,
                    is_extended_id=msg.is_extended_id,
                    data=bytes(msg.data),
                )
                self._queue.put(frame)
            except Exception as exc:
                if self._running:
                    if self._on_error:
                        self._on_error(str(exc))
                break
