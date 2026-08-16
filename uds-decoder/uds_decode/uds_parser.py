"""Parse and pair UDS request/response exchanges from reassembled ISO-TP payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

UDS_NEGATIVE_RESPONSE = 0x7F
UDS_READ_DATA_BY_ID = 0x22
UDS_READ_DATA_BY_ID_RESP = 0x62
OBD_SHOW_CURRENT_DATA = 0x01
OBD_SHOW_CURRENT_DATA_RESP = 0x41

NEGATIVE_RESPONSE_CODES = {
    0x10: "General reject",
    0x11: "Service not supported",
    0x12: "Subfunction not supported",
    0x13: "Message length or format incorrect",
    0x14: "Response too long",
    0x21: "Busy – repeat request",
    0x22: "Conditions not correct",
    0x24: "Request sequence error",
    0x31: "Out of range",
    0x33: "Security access denied",
    0x35: "Invalid key",
    0x36: "Exceed attempts",
    0x7E: "Subfunction not supported in active session",
    0x7F: "Service not supported in active session",
    0x78: "Busy – response pending",
}


@dataclass
class UdsExchange:
    """A matched UDS request/response pair."""

    timestamp: float
    request_id: int
    response_id: int
    is_extended_id: bool
    reg: list[int]
    payload: list[int] = field(default_factory=list)
    negative: bool = False
    negative_code: Optional[str] = None


def derive_request_id(response_id: int, is_extended_id: bool) -> int:
    """Derive request CAN ID from response ID (uds-decoder Form1.cs rules)."""
    if is_extended_id:
        return response_id - 0x20000
    if 0x7E8 <= response_id <= 0x7EF:
        return response_id - 0x08
    if response_id < 0xFFF:
        return response_id - 0x6A
    return response_id - 0x08


def decode_negative(data: bytes) -> str:
    if len(data) >= 3:
        return NEGATIVE_RESPONSE_CODES.get(data[2], f"Unknown error 0x{data[2]:02X}")
    return "Unknown negative response"


def parse_request(
    arbitration_id: int,
    is_extended_id: bool,
    data: bytes,
    timestamp: float,
) -> Optional[tuple[int, list[int]]]:
    """
    Parse a UDS/OBD request from ISO-TP payload.

    Returns (request_id, reg_bytes) or None.
    """
    if len(data) < 2:
        return None

    service = data[0]

    if service == UDS_READ_DATA_BY_ID and len(data) >= 3:
        reg = [service, data[1], data[2]]
        return arbitration_id, reg

    if service == OBD_SHOW_CURRENT_DATA and len(data) >= 2:
        reg = [service, data[1]]
        return arbitration_id, reg

    return None


def parse_response(
    arbitration_id: int,
    is_extended_id: bool,
    data: bytes,
    timestamp: float,
    pending_request: Optional[tuple[int, list[int], bool]] = None,
) -> Optional[UdsExchange]:
    """Parse a UDS/OBD response from ISO-TP payload."""
    if len(data) < 2:
        return None

    service = data[0]

    if service == UDS_NEGATIVE_RESPONSE:
        reg = list(data[1:3]) if len(data) >= 3 else list(data[1:])
        req_id = (
            pending_request[0]
            if pending_request
            else derive_request_id(arbitration_id, is_extended_id)
        )
        return UdsExchange(
            timestamp=timestamp,
            request_id=req_id,
            response_id=arbitration_id,
            is_extended_id=is_extended_id,
            reg=reg,
            payload=list(data),
            negative=True,
            negative_code=decode_negative(data),
        )

    reg: list[int] = []
    payload_start = 0

    if service == UDS_READ_DATA_BY_ID_RESP and len(data) >= 3:
        reg = [UDS_READ_DATA_BY_ID, data[1], data[2]]
        payload_start = 3
    elif service == OBD_SHOW_CURRENT_DATA_RESP and len(data) >= 2:
        reg = [OBD_SHOW_CURRENT_DATA, data[1]]
        payload_start = 2
    else:
        return None

    payload = list(data[payload_start:])
    # Pad/truncate to 24 bytes for GUI display
    display_payload = payload[:24]
    while len(display_payload) < 24:
        display_payload.append(0)

    req_id = (
        pending_request[0]
        if pending_request
        else derive_request_id(arbitration_id, is_extended_id)
    )

    return UdsExchange(
        timestamp=timestamp,
        request_id=req_id,
        response_id=arbitration_id,
        is_extended_id=is_extended_id,
        reg=reg,
        payload=display_payload,
        negative=False,
    )


class UdsTrafficMatcher:
    """Track pending requests and pair them with responses."""

    def __init__(self) -> None:
        self._pending: Optional[tuple[int, list[int], bool, float]] = None

    def handle_frame(
        self,
        arbitration_id: int,
        is_extended_id: bool,
        data: bytes,
        timestamp: float,
        direction_hint: str = "auto",
    ) -> Optional[UdsExchange]:
        """
        Process a reassembled ISO-TP payload.

        direction_hint: 'tx' for request, 'rx' for response, 'auto' to detect.
        """
        if len(data) < 2:
            return None

        service = data[0]

        # Detect request
        if service in (UDS_READ_DATA_BY_ID, OBD_SHOW_CURRENT_DATA):
            parsed = parse_request(arbitration_id, is_extended_id, data, timestamp)
            if parsed:
                req_id, reg = parsed
                self._pending = (req_id, reg, is_extended_id, timestamp)
            return None

        # Detect response
        if service in (UDS_READ_DATA_BY_ID_RESP, OBD_SHOW_CURRENT_DATA_RESP, UDS_NEGATIVE_RESPONSE):
            pending = None
            if self._pending:
                pending = (self._pending[0], self._pending[1], self._pending[2])
            exchange = parse_response(
                arbitration_id, is_extended_id, data, timestamp, pending
            )
            self._pending = None
            return exchange

        return None
