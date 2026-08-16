"""ISO-TP (ISO 15765-2) frame reassembly for CAN diagnostic traffic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

RX_MAX = 700


@dataclass
class ReassembledFrame:
    """A complete ISO-TP payload after reassembly."""

    timestamp: float
    arbitration_id: int
    is_extended_id: bool
    data: bytes


@dataclass
class _TpSlot:
    timestamp: float = 0.0
    arbitration_id: int = 0
    is_extended_id: bool = False
    rx_data_len: int = 0
    rx_data: bytearray = field(default_factory=lambda: bytearray(RX_MAX))
    rx_data_rcv: int = 0
    frame_state: int = -1


class IsotpReassembler:
    """Reassemble ISO-TP single- and multi-frame messages per CAN ID."""

    def __init__(self) -> None:
        self._pending: list[_TpSlot] = []

    def _key(self, arbitration_id: int, is_extended_id: bool) -> tuple[int, bool]:
        return (arbitration_id, is_extended_id)

    def _get_slot(self, arbitration_id: int, is_extended_id: bool, frame_state: int) -> Optional[_TpSlot]:
        if frame_state <= 1:
            slot = _TpSlot()
            slot.arbitration_id = arbitration_id
            slot.is_extended_id = is_extended_id
            slot.frame_state = frame_state
            return slot

        remaining: list[_TpSlot] = []
        found: Optional[_TpSlot] = None
        for slot in self._pending:
            if (
                slot.arbitration_id == arbitration_id
                and slot.is_extended_id == is_extended_id
                and slot.frame_state == frame_state - 1
            ):
                slot.frame_state = 1 if frame_state == 16 else frame_state
                found = slot
            else:
                remaining.append(slot)
        self._pending = remaining
        return found

    def feed(
        self,
        timestamp: float,
        arbitration_id: int,
        is_extended_id: bool,
        data: bytes,
    ) -> Optional[ReassembledFrame]:
        """Feed one CAN frame; return a completed reassembled message if ready."""
        if not data:
            return None

        pci = data[0]
        frame_type = pci & 0xF0

        # Flow control — ignore
        if frame_type == 0x30:
            return None

        frame_state = -1
        if frame_type == 0x00:
            frame_state = 0
        elif frame_type == 0x10:
            frame_state = 1
        elif frame_type == 0x20:
            frame_state = (pci & 0x0F) + 1
        else:
            return None

        slot = self._get_slot(arbitration_id, is_extended_id, frame_state)
        if slot is None:
            return None

        slot.timestamp = timestamp

        # Single frame
        if frame_type == 0x00:
            slot.rx_data_len = pci & 0x0F
            slot.rx_data[: slot.rx_data_len] = data[1 : slot.rx_data_len + 1]

        # First frame
        elif frame_type == 0x10:
            slot.rx_data_rcv = 0
            slot.rx_data_len = data[1] + (pci & 0x0F) * 256
            if slot.rx_data_len > RX_MAX:
                return None
            slot.rx_data[:6] = data[2:8]
            slot.rx_data_rcv = 6

        # Consecutive frame
        elif frame_type == 0x20:
            if slot.rx_data_rcv <= 0:
                self._pending.append(slot)
                return None
            seq = pci & 0x0F
            payload_pos = 7 * seq - 1
            copy_len = min(len(data) - 1, slot.rx_data_len - payload_pos)
            if copy_len > 0:
                slot.rx_data[payload_pos : payload_pos + copy_len] = data[1 : 1 + copy_len]
                slot.rx_data_rcv = payload_pos + copy_len
            if slot.rx_data_rcv >= slot.rx_data_len:
                slot.frame_state = 0

        if slot.frame_state == 0:
            payload = bytes(slot.rx_data[: slot.rx_data_len])
            return ReassembledFrame(
                timestamp=slot.timestamp,
                arbitration_id=arbitration_id,
                is_extended_id=is_extended_id,
                data=payload,
            )

        self._pending.append(slot)
        return None
