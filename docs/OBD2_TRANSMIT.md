# How OBD-II Transmit works

This describes the **OBD-II Transmit** CAN communications setup (e.g. MoTeC-style **CAN Communications Setup – OBDII_Tx**). The device acts as an **OBD-II requester** (scanner-style), not an ECU emulator: it polls the vehicle ECU over CAN and maps replies into channels.

## What it does on the bus

1. Device sends a **Mode 01 request** to base address **`0x7DF`** (functional broadcast — “any ECU that knows this PID”).
2. ECU answers on its response ID (engine is usually **`0x7E8`**).
3. Payload shape is the usual OBD pair: request `01 PID` → response `41 PID [data…]`.

Example for RPM (`010C`):

| Direction | CAN ID | Typical data |
|-----------|--------|----------------|
| Request | `7DF` | `02 01 0C` |
| Response | `7E8` | `04 41 0C xx yy` → RPM = `((xx<<8)+yy)/4` |

## How the two tabs fit together

### Parameters

- **OBD-II Transmit** + **7DF** + **Standard** = 11-bit OBD request traffic
- **50 Hz** = request cadence (how often it fires the next poll)
- **Receive Timeout 2200 ms** = how long it waits for a reply before giving up that cycle
- **Comms CAN 3 Diag** = which CAN port this device uses

### Transmitted Channels (Compound)

- **Compound** = cycle through the PID list (not one fixed PID forever)
- Table IDs are **Mode + PID**:
  - `010C` — Engine RPM
  - `010B` — Intake manifold absolute pressure (MAP)
  - `0111` — Throttle position
  - `0104` — Calculated engine load
- **Offset 1 / Mask FFFF** = match the 16-bit Mode+PID starting at byte 1 (after the ISO-TP length byte)
- Duplicates of `010C` / `010B` / `0111` raise those PIDs’ share of the 50 Hz budget (more polls → fresher values)

## Incomplete channel mapping

If the selected identifier (e.g. ID 1 = `010C`) shows **“(No Channels selected)”**, the device can still request the data, but nothing is wired into a logged or displayed signal until you **Add…** a channel for each PID.

## End-to-end flow

```
Your device @ 50 Hz
  → CAN 7DF: request next PID in compound list
  → ECU replies (e.g. 7E8)
  → matched by ID/mask
  → value lands in the mapped Channel (once you add one)
```

**Summary:** this setup is “ask the car for these PIDs over and over”; it is not broadcasting sensor data as an ECU.
