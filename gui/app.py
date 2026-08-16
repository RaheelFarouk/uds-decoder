"""Tkinter GUI for live UDS signal decoding with PCAN."""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Optional

from uds_decode.config_export import SignalDefinition, append_signal, load_signals
from uds_decode.decoder import (
    ByteOrder,
    DecodeParams,
    SignType,
    auto_bitmask,
    decode_signal,
)
from uds_decode.isotp import IsotpReassembler
from uds_decode.pcan_listener import DEFAULT_BITRATES, DEFAULT_CHANNELS, PcanListener
from uds_decode.uds_parser import UdsExchange, UdsTrafficMatcher

PAYLOAD_BYTES = 24
POLL_MS = 50
HIGHLIGHT_COLOR = "#ffe0a0"
NORMAL_COLOR = "white"


class UdsDecoderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("UDS Decoder — VCDS + PCAN")
        self.geometry("980x820")
        self.minsize(900, 700)

        self._frame_queue: queue.Queue = queue.Queue()
        self._listener = PcanListener(self._frame_queue, on_error=self._on_can_error)
        self._isotp = IsotpReassembler()
        self._matcher = UdsTrafficMatcher()

        self._exchange_count = 0
        self._current_exchange: Optional[UdsExchange] = None
        self._payload: list[int] = [0] * PAYLOAD_BYTES
        self._update_bitmask = False

        self._build_ui()
        self._poll_can()
        self._recalculate()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self._build_connection_frame()
        self._build_exchange_frame()
        self._build_byte_frame()
        self._build_decode_frame()
        self._build_export_frame()

    def _build_connection_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="PCAN Connection", padding=8)
        frame.pack(fill=tk.X, padx=8, pady=4)

        ttk.Label(frame, text="Channel:").grid(row=0, column=0, sticky=tk.W)
        self._channel_var = tk.StringVar(value=DEFAULT_CHANNELS[0])
        ttk.Combobox(
            frame,
            textvariable=self._channel_var,
            values=DEFAULT_CHANNELS,
            width=16,
            state="readonly",
        ).grid(row=0, column=1, padx=4)

        ttk.Label(frame, text="Bitrate:").grid(row=0, column=2, sticky=tk.W, padx=(12, 0))
        self._bitrate_var = tk.StringVar(value="500000")
        ttk.Combobox(
            frame,
            textvariable=self._bitrate_var,
            values=[str(b) for b in DEFAULT_BITRATES],
            width=10,
            state="readonly",
        ).grid(row=0, column=3, padx=4)

        self._connect_btn = ttk.Button(frame, text="Connect", command=self._toggle_connection)
        self._connect_btn.grid(row=0, column=4, padx=8)

        self._status_var = tk.StringVar(value="Disconnected")
        ttk.Label(frame, textvariable=self._status_var).grid(row=0, column=5, padx=8)

        self._frame_count_var = tk.StringVar(value="Frames: 0")
        ttk.Label(frame, textvariable=self._frame_count_var).grid(row=0, column=6)

        self._exchange_count_var = tk.StringVar(value="Exchanges: 0")
        ttk.Label(frame, textvariable=self._exchange_count_var).grid(row=0, column=7, padx=8)

    def _build_exchange_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="Last UDS Exchange", padding=8)
        frame.pack(fill=tk.X, padx=8, pady=4)

        ttk.Label(frame, text="Request ID:").grid(row=0, column=0, sticky=tk.W)
        self._req_id_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self._req_id_var, width=12, state="readonly").grid(
            row=0, column=1, padx=4
        )

        ttk.Label(frame, text="Response ID:").grid(row=0, column=2, sticky=tk.W, padx=(12, 0))
        self._resp_id_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self._resp_id_var, width=12, state="readonly").grid(
            row=0, column=3, padx=4
        )

        ttk.Label(frame, text="REG (DID/PID):").grid(row=0, column=4, sticky=tk.W, padx=(12, 0))
        self._reg_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self._reg_var, width=16, state="readonly").grid(
            row=0, column=5, padx=4
        )

        self._negative_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._negative_var, foreground="red").grid(
            row=1, column=0, columnspan=6, sticky=tk.W, pady=(4, 0)
        )

    def _build_byte_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="Payload Bytes (select bytes to decode)", padding=8)
        frame.pack(fill=tk.X, padx=8, pady=4)

        self._byte_vars: list[tk.StringVar] = []
        self._byte_entries: list[tk.Entry] = []
        self._byte_checks: list[tk.BooleanVar] = []

        for i in range(PAYLOAD_BYTES):
            row = i // 8
            col = (i % 8) * 3

            chk_var = tk.BooleanVar(value=False)
            chk_var.trace_add("write", lambda *_a, idx=i: self._on_byte_check(idx))
            self._byte_checks.append(chk_var)
            ttk.Checkbutton(frame, variable=chk_var).grid(row=row * 2, column=col, sticky=tk.W)

            ttk.Label(frame, text=f"B{i}").grid(row=row * 2, column=col + 1, sticky=tk.W)
            var = tk.StringVar(value="")
            entry = tk.Entry(frame, textvariable=var, width=4, state="readonly", bg=NORMAL_COLOR)
            entry.grid(row=row * 2 + 1, column=col, columnspan=2, padx=2, pady=2)
            self._byte_vars.append(var)
            self._byte_entries.append(entry)

    def _build_decode_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="Decode Tuning", padding=8)
        frame.pack(fill=tk.X, padx=8, pady=4)

        row = 0
        ttk.Label(frame, text="Bitmask (hex):").grid(row=row, column=0, sticky=tk.W)
        self._bitmask_var = tk.StringVar(value="0xFFFF")
        bitmask_entry = ttk.Entry(frame, textvariable=self._bitmask_var, width=18)
        bitmask_entry.grid(row=row, column=1, padx=4, sticky=tk.W)
        bitmask_entry.bind("<KeyRelease>", lambda _e: self._recalculate())

        ttk.Label(frame, text="Mult:").grid(row=row, column=2, sticky=tk.W, padx=(12, 0))
        self._mult_var = tk.StringVar(value="1")
        mult_entry = ttk.Entry(frame, textvariable=self._mult_var, width=10)
        mult_entry.grid(row=row, column=3, padx=4)
        mult_entry.bind("<KeyRelease>", lambda _e: self._recalculate())

        ttk.Label(frame, text="Div:").grid(row=row, column=4, sticky=tk.W, padx=(12, 0))
        self._div_var = tk.StringVar(value="1")
        div_entry = ttk.Entry(frame, textvariable=self._div_var, width=10)
        div_entry.grid(row=row, column=5, padx=4)
        div_entry.bind("<KeyRelease>", lambda _e: self._recalculate())

        row += 1
        ttk.Label(frame, text="Add (offset):").grid(row=row, column=0, sticky=tk.W)
        self._add_var = tk.StringVar(value="0")
        add_entry = ttk.Entry(frame, textvariable=self._add_var, width=10)
        add_entry.grid(row=row, column=1, padx=4, sticky=tk.W)
        add_entry.bind("<KeyRelease>", lambda _e: self._recalculate())

        self._signed_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Signed", variable=self._signed_var, command=self._recalculate).grid(
            row=row, column=2, sticky=tk.W, padx=(12, 0)
        )

        self._float_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Float", variable=self._float_var, command=self._recalculate).grid(
            row=row, column=3, sticky=tk.W
        )

        self._byteorder_var = tk.StringVar(value="MSB")
        ttk.Label(frame, text="Byte order:").grid(row=row, column=4, sticky=tk.W, padx=(12, 0))
        ttk.Combobox(
            frame,
            textvariable=self._byteorder_var,
            values=["MSB", "LSB"],
            width=6,
            state="readonly",
        ).grid(row=row, column=5, padx=4)
        self._byteorder_var.trace_add("write", lambda *_a: self._recalculate())

        row += 1
        self._startbit_var = tk.StringVar(value="Startbit: —")
        ttk.Label(frame, textvariable=self._startbit_var).grid(row=row, column=0, columnspan=2, sticky=tk.W)
        self._bitlen_var = tk.StringVar(value="Length: —")
        ttk.Label(frame, textvariable=self._bitlen_var).grid(row=row, column=2, columnspan=2, sticky=tk.W)
        self._raw_var = tk.StringVar(value="Raw: —")
        ttk.Label(frame, textvariable=self._raw_var).grid(row=row, column=4, columnspan=2, sticky=tk.W)

        row += 1
        ttk.Label(frame, text="Decoded value:", font=("", 11, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=(8, 0)
        )
        self._decoded_var = tk.StringVar(value="—")
        ttk.Label(frame, textvariable=self._decoded_var, font=("", 16, "bold")).grid(
            row=row, column=1, columnspan=2, sticky=tk.W, pady=(8, 0)
        )

        row += 1
        ttk.Label(frame, text="VCDS reference:").grid(row=row, column=0, sticky=tk.W, pady=(8, 0))
        self._vcds_ref_var = tk.StringVar(value="")
        ref_entry = ttk.Entry(frame, textvariable=self._vcds_ref_var, width=12)
        ref_entry.grid(row=row, column=1, padx=4, pady=(8, 0), sticky=tk.W)
        ref_entry.bind("<KeyRelease>", lambda _e: self._recalculate())

        self._ratio1_var = tk.StringVar(value="ref/decoded: —")
        ttk.Label(frame, textvariable=self._ratio1_var).grid(row=row, column=2, columnspan=2, sticky=tk.W)
        self._ratio2_var = tk.StringVar(value="decoded/ref: —")
        ttk.Label(frame, textvariable=self._ratio2_var).grid(row=row, column=4, columnspan=2, sticky=tk.W)

    def _build_export_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="Export", padding=8)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        fields = ttk.Frame(frame)
        fields.pack(fill=tk.X)

        ttk.Label(fields, text="Name:").grid(row=0, column=0, sticky=tk.W)
        self._name_var = tk.StringVar(value="unnamed")
        ttk.Entry(fields, textvariable=self._name_var, width=24).grid(row=0, column=1, padx=4)

        ttk.Label(fields, text="Unit:").grid(row=0, column=2, sticky=tk.W, padx=(12, 0))
        self._unit_var = tk.StringVar()
        ttk.Entry(fields, textvariable=self._unit_var, width=10).grid(row=0, column=3, padx=4)

        ttk.Label(fields, text="Req ID #:").grid(row=0, column=4, sticky=tk.W, padx=(12, 0))
        self._reqid_num_var = tk.StringVar(value="0")
        ttk.Entry(fields, textvariable=self._reqid_num_var, width=8).grid(row=0, column=5, padx=4)

        ttk.Label(fields, text="Comment:").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        self._comment_var = tk.StringVar()
        ttk.Entry(fields, textvariable=self._comment_var, width=60).grid(
            row=1, column=1, columnspan=5, padx=4, pady=(4, 0), sticky=tk.W
        )

        self._ids_line_var = tk.StringVar()
        ids_entry = ttk.Entry(frame, textvariable=self._ids_line_var)
        ids_entry.pack(fill=tk.X, pady=(8, 4))

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X)

        ttk.Button(buttons, text="Copy ids.txt line", command=self._copy_ids_line).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Save to signals.yaml", command=self._save_signal).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Load signals.yaml", command=self._load_signals_dialog).pack(side=tk.LEFT, padx=4)

    def _toggle_connection(self) -> None:
        if self._listener.is_connected:
            self._listener.disconnect()
            self._connect_btn.config(text="Connect")
            self._status_var.set("Disconnected")
            return

        try:
            bitrate = int(self._bitrate_var.get())
            self._listener.connect(self._channel_var.get(), bitrate)
            self._connect_btn.config(text="Disconnect")
            self._status_var.set(f"Listening on {self._channel_var.get()} @ {bitrate}")
        except Exception as exc:
            messagebox.showerror("Connection Error", str(exc))

    def _on_can_error(self, message: str) -> None:
        self.after(0, lambda: self._status_var.set(f"Error: {message}"))

    def _poll_can(self) -> None:
        while True:
            try:
                frame = self._frame_queue.get_nowait()
            except queue.Empty:
                break

            self._frame_count_var.set(f"Frames: {self._listener.frame_count}")

            reassembled = self._isotp.feed(
                frame.timestamp,
                frame.arbitration_id,
                frame.is_extended_id,
                frame.data,
            )
            if reassembled is None:
                continue

            # Track requests separately for pairing
            if len(reassembled.data) >= 2:
                svc = reassembled.data[0]
                if svc in (0x22, 0x01):
                    self._matcher.handle_frame(
                        reassembled.arbitration_id,
                        reassembled.is_extended_id,
                        reassembled.data,
                        reassembled.timestamp,
                    )
                    continue

            exchange = self._matcher.handle_frame(
                reassembled.arbitration_id,
                reassembled.is_extended_id,
                reassembled.data,
                reassembled.timestamp,
            )
            if exchange:
                self._apply_exchange(exchange)

        self.after(POLL_MS, self._poll_can)

    def _apply_exchange(self, exchange: UdsExchange) -> None:
        self._current_exchange = exchange
        self._exchange_count += 1
        self._exchange_count_var.set(f"Exchanges: {self._exchange_count}")

        req_fmt = f"0x{exchange.request_id:X}"
        if exchange.is_extended_id:
            req_fmt = f"0x{exchange.request_id:08X} (ext)"
        self._req_id_var.set(req_fmt)

        resp_fmt = f"0x{exchange.response_id:X}"
        if exchange.is_extended_id:
            resp_fmt = f"0x{exchange.response_id:08X} (ext)"
        self._resp_id_var.set(resp_fmt)

        reg_str = " ".join(f"{b:02X}" for b in exchange.reg)
        self._reg_var.set(reg_str)

        if exchange.negative:
            self._negative_var.set(f"Negative response: {exchange.negative_code}")
            return

        self._negative_var.set("")
        self._payload = list(exchange.payload)

        for i in range(PAYLOAD_BYTES):
            val = self._payload[i]
            old = self._byte_vars[i].get()
            new = f"{val:02X}"
            self._byte_vars[i].set(new)
            if old != new and new:
                self._byte_entries[i].config(bg=HIGHLIGHT_COLOR)
            else:
                self._byte_entries[i].config(bg=NORMAL_COLOR)

        self._recalculate()

    def _selected_byte_indices(self) -> list[int]:
        return [i for i, var in enumerate(self._byte_checks) if var.get()]

    def _on_byte_check(self, _idx: int) -> None:
        self._update_bitmask = True
        self._recalculate()

    def _parse_float(self, text: str, default: float = 0.0) -> float:
        try:
            return float(text.strip())
        except ValueError:
            return default

    def _parse_bitmask(self, text: str) -> int:
        text = text.strip()
        if not text:
            return 0
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text, 16) if all(c in "0123456789ABCDEFabcdef" for c in text) else int(text)

    def _recalculate(self) -> None:
        selected = self._selected_byte_indices()
        num_selected = len(selected)

        if self._update_bitmask:
            self._update_bitmask = False
            mask = auto_bitmask(num_selected)
            self._bitmask_var.set(f"0x{mask:X}" if mask else "0x0")

        try:
            bitmask = self._parse_bitmask(self._bitmask_var.get())
        except ValueError:
            bitmask = 0

        sign = SignType.UNSIGNED
        if self._float_var.get():
            sign = SignType.FLOAT
        elif self._signed_var.get():
            sign = SignType.SIGNED

        params = DecodeParams(
            selected_bytes=selected,
            bitmask=bitmask,
            mult=self._parse_float(self._mult_var.get(), 1.0),
            div=self._parse_float(self._div_var.get(), 1.0) or 1.0,
            offset=self._parse_float(self._add_var.get(), 0.0),
            sign=sign,
            byteorder=ByteOrder(self._byteorder_var.get()),
        )

        result = decode_signal(self._payload, params)
        if result is None:
            self._decoded_var.set("—")
            self._startbit_var.set("Startbit: —")
            self._bitlen_var.set("Length: —")
            self._raw_var.set("Raw: —")
            self._update_export_line(0, 0, sign)
            return

        self._decoded_var.set(f"{result.physical_value:.6g}")
        self._startbit_var.set(f"Startbit: {result.startbit}")
        self._bitlen_var.set(f"Length: {result.bitlength}")
        self._raw_var.set(f"Raw: {result.raw_value} (0x{result.raw_value:X})")

        ref_text = self._vcds_ref_var.get().strip()
        if ref_text:
            try:
                ref = float(ref_text)
                if result.physical_value != 0:
                    self._ratio1_var.set(f"ref/decoded: {ref / result.physical_value:.6g}")
                    self._ratio2_var.set(f"decoded/ref: {result.physical_value / ref:.6g}")
                else:
                    self._ratio1_var.set("ref/decoded: —")
                    self._ratio2_var.set("decoded/ref: —")
            except ValueError:
                self._ratio1_var.set("ref/decoded: —")
                self._ratio2_var.set("decoded/ref: —")
        else:
            self._ratio1_var.set("ref/decoded: —")
            self._ratio2_var.set("decoded/ref: —")

        self._update_export_line(result.startbit, result.bitlength, sign)

    def _build_signal_definition(self, startbit: int, bitlen: int, sign: SignType) -> SignalDefinition:
        exchange = self._current_exchange
        req_id = exchange.request_id if exchange else 0
        resp_id = exchange.response_id if exchange else 0
        is_ext = exchange.is_extended_id if exchange else False
        reg = list(exchange.reg) if exchange else []

        try:
            reqid_num = int(self._reqid_num_var.get())
        except ValueError:
            reqid_num = 0

        return SignalDefinition(
            name=self._name_var.get() or "unnamed",
            request_id=req_id,
            response_id=resp_id,
            is_extended_id=is_ext,
            reg=reg,
            sign=sign,
            startbit=startbit,
            bitlen=bitlen,
            byteorder=ByteOrder(self._byteorder_var.get()),
            add=self._parse_float(self._add_var.get(), 0.0),
            mult=self._parse_float(self._mult_var.get(), 1.0),
            div=self._parse_float(self._div_var.get(), 1.0) or 1.0,
            unit=self._unit_var.get(),
            comment=self._comment_var.get(),
            reqid=reqid_num,
        )

    def _update_export_line(self, startbit: int, bitlen: int, sign: SignType) -> None:
        signal = self._build_signal_definition(startbit, bitlen, sign)
        self._ids_line_var.set(signal.to_ids_txt_line())

    def _copy_ids_line(self) -> None:
        line = self._ids_line_var.get()
        if line:
            self.clipboard_clear()
            self.clipboard_append(line)
            self._status_var.set("ids.txt line copied to clipboard")

    def _save_signal(self) -> None:
        selected = self._selected_byte_indices()
        sign = SignType.FLOAT if self._float_var.get() else (
            SignType.SIGNED if self._signed_var.get() else SignType.UNSIGNED
        )
        params = DecodeParams(
            selected_bytes=selected,
            bitmask=self._parse_bitmask(self._bitmask_var.get()),
            mult=self._parse_float(self._mult_var.get(), 1.0),
            div=self._parse_float(self._div_var.get(), 1.0) or 1.0,
            offset=self._parse_float(self._add_var.get(), 0.0),
            sign=sign,
            byteorder=ByteOrder(self._byteorder_var.get()),
        )
        result = decode_signal(self._payload, params)
        startbit = result.startbit if result else 0
        bitlen = result.bitlength if result else 0

        signal = self._build_signal_definition(startbit, bitlen, sign)
        path = Path("signals.yaml")
        append_signal(path, signal)
        messagebox.showinfo("Saved", f"Signal '{signal.name}' saved to {path.resolve()}")

    def _load_signals_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Load signals.yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
            initialfile="signals.yaml",
        )
        if not path:
            return

        signals = load_signals(path)
        if not signals:
            messagebox.showwarning("Load", "No signals found in file.")
            return

        self._show_signal_picker(signals, Path(path))

    def _show_signal_picker(self, signals: list[SignalDefinition], path: Path) -> None:
        picker = tk.Toplevel(self)
        picker.title("Load Signal")
        picker.geometry("400x300")

        listbox = tk.Listbox(picker)
        listbox.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        for sig in signals:
            listbox.insert(tk.END, f"{sig.name} ({sig.unit}) — 0x{sig.response_id:X}")

        def load_selected() -> None:
            sel = listbox.curselection()
            if not sel:
                return
            self._apply_signal_definition(signals[sel[0]])
            picker.destroy()
            self._status_var.set(f"Loaded signal from {path.name}")

        ttk.Button(picker, text="Load", command=load_selected).pack(pady=4)

    def _apply_signal_definition(self, signal: SignalDefinition) -> None:
        self._name_var.set(signal.name)
        self._unit_var.set(signal.unit)
        self._comment_var.set(signal.comment)
        self._reqid_num_var.set(str(signal.reqid))
        self._mult_var.set(str(signal.mult))
        self._div_var.set(str(signal.div))
        self._add_var.set(str(signal.add))
        self._signed_var.set(signal.sign == SignType.SIGNED)
        self._float_var.set(signal.sign == SignType.FLOAT)
        self._byteorder_var.set(signal.byteorder.value)

        self._req_id_var.set(f"0x{signal.request_id:X}")
        self._resp_id_var.set(f"0x{signal.response_id:X}")
        self._reg_var.set(" ".join(f"{b:02X}" for b in signal.reg))

        self._recalculate()

    def _on_close(self) -> None:
        self._listener.disconnect()
        self.destroy()


def main() -> None:
    app = UdsDecoderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
