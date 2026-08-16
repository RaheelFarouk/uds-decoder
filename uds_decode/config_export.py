"""Export and import signal definitions (YAML and uds-decoder ids.txt format)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from uds_decode.decoder import ByteOrder, SignType


@dataclass
class SignalDefinition:
    name: str = "unnamed"
    request_id: int = 0
    response_id: int = 0
    is_extended_id: bool = False
    reg: list[int] = field(default_factory=list)
    sign: SignType = SignType.UNSIGNED
    startbit: int = 0
    bitlen: int = 16
    byteorder: ByteOrder = ByteOrder.MSB
    add: float = 0.0
    mult: float = 1.0
    div: float = 1.0
    unit: str = ""
    comment: str = ""
    reqid: int = 0  # VCDS request ID number for ids.txt
    group: int = 10
    freq: int = 100

    def to_yaml_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "request_id": f"0x{self.request_id:X}",
            "response_id": f"0x{self.response_id:X}",
            "is_extended_id": self.is_extended_id,
            "reg": [f"0x{b:02X}" for b in self.reg],
            "type": {
                "sign": self.sign.value,
                "startbit": self.startbit,
                "bitlen": self.bitlen,
                "byteorder": self.byteorder.value,
            },
            "conv": {"add": self.add, "mult": self.mult, "div": self.div},
            "unit": self.unit,
            "comment": self.comment,
        }

    def to_ids_txt_line(self) -> str:
        """Generate uds-decoder compatible ids.txt line."""
        sign = self.sign.value
        reg_str = ",".join(f"0x{b:02X}" for b in self.reg)
        ext_flag = ",EXT" if self.is_extended_id else ""
        can_id = f"0x{self.request_id:X}"

        br0 = f"{self.reg[1]:02X}" if len(self.reg) > 1 else "00"
        br1 = f"{self.reg[2]:02X}" if len(self.reg) > 2 else "00"

        return (
            f"[CCAN=UDS,{self.reqid},{can_id},{self.group},{self.freq},"
            f"REG={{{reg_str}}},"
            f"TYPE={{{sign},{self.startbit},{self.bitlen},{self.byteorder.value}}},"
            f"CONV={{{self.add},{self.mult},{self.div}}}{ext_flag}]"
            f"  # {self.name}"
        )


def _parse_hex(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError(f"Cannot parse hex value: {value}")


def signal_from_yaml_dict(data: dict[str, Any]) -> SignalDefinition:
    reg_raw = data.get("reg", [])
    reg = [_parse_hex(b) for b in reg_raw]
    type_info = data.get("type", {})
    conv = data.get("conv", {})

    return SignalDefinition(
        name=data.get("name", "unnamed"),
        request_id=_parse_hex(data.get("request_id", 0)),
        response_id=_parse_hex(data.get("response_id", 0)),
        is_extended_id=data.get("is_extended_id", False),
        reg=reg,
        sign=SignType(type_info.get("sign", "+")),
        startbit=int(type_info.get("startbit", 0)),
        bitlen=int(type_info.get("bitlen", 16)),
        byteorder=ByteOrder(type_info.get("byteorder", "MSB")),
        add=float(conv.get("add", 0)),
        mult=float(conv.get("mult", 1)),
        div=float(conv.get("div", 1)),
        unit=data.get("unit", ""),
        comment=data.get("comment", ""),
    )


def save_signals(path: Path | str, signals: list[SignalDefinition]) -> None:
    path = Path(path)
    data = [s.to_yaml_dict() for s in signals]
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def load_signals(path: Path | str) -> list[SignalDefinition]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    return [signal_from_yaml_dict(item) for item in data]


def append_signal(path: Path | str, signal: SignalDefinition) -> None:
    signals = load_signals(path)
    signals.append(signal)
    save_signals(path, signals)
