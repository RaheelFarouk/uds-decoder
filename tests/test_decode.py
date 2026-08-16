"""Unit tests for ISO-TP reassembly and signal decoding."""

import unittest

from uds_decode.decoder import (
    ByteOrder,
    DecodeParams,
    SignType,
    auto_bitmask,
    convert_data_legacy,
    decode_signal,
)
from uds_decode.isotp import IsotpReassembler
from uds_decode.uds_parser import (
    UdsTrafficMatcher,
    derive_request_id,
    parse_response,
)


class TestIsotpReassembler(unittest.TestCase):
    def setUp(self) -> None:
        self.reassembler = IsotpReassembler()

    def test_single_frame(self) -> None:
        # ISO-TP single frame: 4 bytes UDS payload 62 1E 40 12
        data = bytes([0x04, 0x62, 0x1E, 0x40, 0x12, 0x00, 0x00, 0x00])
        result = self.reassembler.feed(1.0, 0x7AE, False, data)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.data, bytes([0x62, 0x1E, 0x40, 0x12]))
        self.assertEqual(result.arbitration_id, 0x7AE)

    def test_multi_frame(self) -> None:
        # First frame: 10 bytes total payload
        ff = bytes([0x10, 0x0A, 0x62, 0x1E, 0x40, 0x01, 0x02, 0x03])
        r1 = self.reassembler.feed(1.0, 0x7AE, False, ff)
        self.assertIsNone(r1)

        # Consecutive frame 1
        cf1 = bytes([0x21, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A])
        r2 = self.reassembler.feed(1.1, 0x7AE, False, cf1)
        self.assertIsNotNone(r2)
        assert r2 is not None
        self.assertEqual(r2.data, bytes([0x62, 0x1E, 0x40, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]))

    def test_extended_id(self) -> None:
        data = bytes([0x03, 0x62, 0xAB, 0xCD, 0x00, 0x00, 0x00, 0x00])
        result = self.reassembler.feed(1.0, 0x17FC007B, True, data)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.is_extended_id)
        self.assertEqual(result.arbitration_id, 0x17FC007B)


class TestDecoder(unittest.TestCase):
    def test_auto_bitmask(self) -> None:
        self.assertEqual(auto_bitmask(0), 0)
        self.assertEqual(auto_bitmask(1), 0xFF)
        self.assertEqual(auto_bitmask(2), 0xFFFF)

    def test_decode_two_bytes_msb_scale(self) -> None:
        # Payload bytes 0,1 = 0x1A, 0xF0 -> raw 0x1AF0 = 6896, RPM formula /4
        payload = [0x1A, 0xF0] + [0] * 22
        params = DecodeParams(
            selected_bytes=[0, 1],
            bitmask=0xFFFF,
            mult=1.0,
            div=4.0,
            offset=0.0,
            sign=SignType.UNSIGNED,
            byteorder=ByteOrder.MSB,
        )
        result = decode_signal(payload, params)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.raw_value, 0x1AF0)
        self.assertAlmostEqual(result.physical_value, 6896 / 4)

    def test_decode_signed(self) -> None:
        payload = [0xFF, 0xFE] + [0] * 22
        params = DecodeParams(
            selected_bytes=[0, 1],
            bitmask=0xFFFF,
            mult=1.0,
            div=1.0,
            offset=0.0,
            sign=SignType.SIGNED,
            byteorder=ByteOrder.MSB,
        )
        result = decode_signal(payload, params)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.raw_value, 0xFFFE)

    def test_legacy_convert_data(self) -> None:
        # Match uds-decoder: startbyte=0, startbit=0, length=16, MSB, mult=1, div=1000, add=1
        data = [0x0E, 0xD8, 0x00, 0x00]
        val = convert_data_legacy(data, 0, 0, 16, False, False, 1, 1000, 1, "MSB")
        self.assertAlmostEqual(val, 0x0ED8 / 1000 + 1, places=4)


class TestUdsParser(unittest.TestCase):
    def test_derive_request_id_obd(self) -> None:
        self.assertEqual(derive_request_id(0x7E8, False), 0x7E0)

    def test_derive_request_id_vag(self) -> None:
        self.assertEqual(derive_request_id(0x77A, False), 0x710)

    def test_derive_request_id_extended(self) -> None:
        self.assertEqual(derive_request_id(0x17FC007B, True), 0x17FA007B)

    def test_parse_mode22_response(self) -> None:
        data = bytes([0x62, 0x1E, 0x40, 0x0E, 0xD8])
        exchange = parse_response(0x17FC007B, True, data, 1.0)
        self.assertIsNotNone(exchange)
        assert exchange is not None
        self.assertFalse(exchange.negative)
        self.assertEqual(exchange.reg, [0x22, 0x1E, 0x40])
        self.assertEqual(exchange.payload[0], 0x0E)
        self.assertEqual(exchange.payload[1], 0xD8)

    def test_parse_mode01_response(self) -> None:
        data = bytes([0x41, 0x0C, 0x1A, 0xF0])
        exchange = parse_response(0x7E8, False, data, 1.0)
        self.assertIsNotNone(exchange)
        assert exchange is not None
        self.assertEqual(exchange.reg, [0x01, 0x0C])
        self.assertEqual(exchange.request_id, 0x7E0)
        self.assertEqual(exchange.payload[0], 0x1A)
        self.assertEqual(exchange.payload[1], 0xF0)

    def test_matcher_request_response_pair(self) -> None:
        matcher = UdsTrafficMatcher()
        # Reassembled ISO-TP payloads (no PCI byte)
        req = bytes([0x22, 0x1E, 0x40])
        resp = bytes([0x62, 0x1E, 0x40, 0xAB])

        matcher.handle_frame(0x17FA007B, True, req, 1.0)
        exchange = matcher.handle_frame(0x17FC007B, True, resp, 1.1)

        self.assertIsNotNone(exchange)
        assert exchange is not None
        self.assertEqual(exchange.request_id, 0x17FA007B)
        self.assertEqual(exchange.payload[0], 0xAB)

    def test_negative_response(self) -> None:
        data = bytes([0x7F, 0x22, 0x31])
        exchange = parse_response(0x7AE, False, data, 1.0)
        self.assertIsNotNone(exchange)
        assert exchange is not None
        self.assertTrue(exchange.negative)
        self.assertEqual(exchange.negative_code, "Out of range")


class TestConfigExport(unittest.TestCase):
    def test_ids_txt_line(self) -> None:
        from uds_decode.config_export import SignalDefinition

        signal = SignalDefinition(
            name="test_signal",
            request_id=0x712,
            response_id=0x77A,
            reg=[0x22, 0x12, 0x34],
            sign=SignType.UNSIGNED,
            startbit=40,
            bitlen=16,
            add=0,
            mult=1,
            div=100,
            reqid=12345,
        )
        line = signal.to_ids_txt_line()
        self.assertIn("[CCAN=UDS,12345,0x712", line)
        self.assertIn("REG={0x22,0x12,0x34}", line)
        self.assertIn("CONV={0,1,100}", line)


if __name__ == "__main__":
    unittest.main()
