"""Extract and scale signal values from UDS payload bytes."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ByteOrder(str, Enum):
    MSB = "MSB"
    LSB = "LSB"


class SignType(str, Enum):
    UNSIGNED = "+"
    SIGNED = "-"
    FLOAT = "f"


@dataclass
class DecodeParams:
    """Parameters for decoding a signal from payload bytes."""

    selected_bytes: list[int]  # indices 0-23 into payload
    bitmask: int = 0xFFFF
    mult: float = 1.0
    div: float = 1.0
    offset: float = 0.0
    sign: SignType = SignType.UNSIGNED
    byteorder: ByteOrder = ByteOrder.MSB
    uds_header_offset: int = 24  # startbit offset for ids.txt (3-byte UDS header)


@dataclass
class DecodeResult:
    raw_value: int
    physical_value: float
    startbit: int
    bitlength: int
    bitmask: int


def auto_bitmask(num_selected_bytes: int) -> int:
    """Generate a full-byte bitmask for N selected bytes."""
    if num_selected_bytes <= 0:
        return 0
    bits = num_selected_bytes * 8
    if bits >= 64:
        return (1 << 64) - 1
    return (1 << bits) - 1


def bitmask_offset_and_length(bitmask: int, num_selected_bytes: int) -> tuple[int, int]:
    """Compute bit offset and length within selected bytes from a bitmask."""
    total_bits = num_selected_bytes * 8
    if total_bits == 0 or bitmask == 0:
        return 0, 0

    width = min(total_bits, 64)
    mask = bitmask & ((1 << width) - 1)

    offset = 0
    for i in range(width + 1):
        if mask >> i == 0:
            offset = width - i
            break

    bitlength = 0
    for i in range(offset, width + 1):
        shifted = (mask << i) & ((1 << width) - 1)
        if shifted == 0:
            bitlength = i - offset
            break

    if bitlength == 0 and mask != 0:
        bitlength = width - offset

    return offset, bitlength


def extract_raw_value(
    payload: list[int],
    selected_bytes: list[int],
    bitmask: int,
    byteorder: ByteOrder,
) -> tuple[int, int, int]:
    """
    Extract raw integer from selected payload bytes using bitmask.

    Returns (raw_value, startbit, bitlength).
    """
    if not selected_bytes:
        return 0, 0, 0

    hex_vals = [payload[i] if i < len(payload) else 0 for i in selected_bytes]
    num_checked = len(hex_vals)
    offset, bitlength = bitmask_offset_and_length(bitmask, num_checked)

    if bitlength == 0:
        bitlength = num_checked * 8

    min_checked = min(selected_bytes)
    startbit = min_checked * 8 + offset + DecodeParams.uds_header_offset

    num_bytes = (bitlength + 7) // 8
    if num_bytes == 0:
        num_bytes = 1

    val = 0
    if byteorder == ByteOrder.LSB:
        for i in range(min(num_bytes, len(hex_vals))):
            val += hex_vals[i] << (i * 8)
    else:
        for i in range(min(num_bytes, len(hex_vals))):
            val += hex_vals[i] << ((num_bytes - 1 - i) * 8)

    len_mask = (1 << 64) - 1 >> (64 - bitlength) if bitlength < 64 else (1 << 64) - 1
    if byteorder == ByteOrder.MSB:
        val = (val >> offset) & len_mask
    else:
        val = val & len_mask

    return val, startbit, bitlength


def apply_scaling(
    raw: int,
    bitmask: int,
    bitlength: int,
    mult: float,
    div: float,
    offset: float,
    sign: SignType,
) -> float:
    """Apply signed/float handling and linear scaling."""
    if div == 0:
        div = 1.0

    if sign == SignType.FLOAT:
        raw_bytes = raw.to_bytes(4, "little", signed=False)
        float_val = struct.unpack("<f", raw_bytes)[0]
        return (float_val * mult / div) + offset

    pre = float(raw)
    if sign == SignType.SIGNED and bitlength > 0:
        if raw & (1 << (bitlength - 1)):
            pre = float(raw) - float(bitmask) - 1

    return (pre * mult / div) + offset


def decode_signal(payload: list[int], params: DecodeParams) -> Optional[DecodeResult]:
    """Decode a physical value from UDS payload bytes."""
    if not params.selected_bytes:
        return None

    raw, startbit, bitlength = extract_raw_value(
        payload,
        params.selected_bytes,
        params.bitmask,
        params.byteorder,
    )

    physical = apply_scaling(
        raw,
        params.bitmask,
        bitlength,
        params.mult,
        params.div,
        params.offset,
        params.sign,
    )

    return DecodeResult(
        raw_value=raw,
        physical_value=physical,
        startbit=startbit,
        bitlength=bitlength,
        bitmask=params.bitmask,
    )


def convert_data_legacy(
    data: bytes | list[int],
    startbyte: int,
    startbit: int,
    length: int,
    signed: bool,
    is_float: bool,
    mult: float,
    div: float,
    offset: float,
    byteorder: str,
) -> float:
    """
    Legacy conversion matching uds-decoder python_can_recorder/main.py.
    Used for validating against known ids.txt definitions.
    """
    if isinstance(data, bytes):
        data = list(data)

    num_bytes = int((length + startbit) / 8) + (1 if ((length + startbit) % 8) > 0 else 0)
    len_mask = (1 << 64) - 1 >> (64 - length) if length < 64 else (1 << 64) - 1
    pre = 0

    if byteorder == "LSB":
        for i in range(num_bytes):
            if startbyte + i < len(data):
                pre += data[startbyte + i] << (i * 8)
    else:
        for i in range(num_bytes):
            if startbyte + i < len(data):
                pre += data[startbyte + i] << ((num_bytes - i - 1) * 8)
        pre = (pre >> startbit) & len_mask

    if is_float:
        raw_bytes = pre.to_bytes(4, "little", signed=False)
        a = struct.unpack("<f", raw_bytes)[0]
        return (a * mult / div) + offset

    if signed and length > 0 and (pre & (1 << (length - 1))):
        return ((pre - len_mask) * mult / div) + offset

    return (pre * mult / div) + offset
