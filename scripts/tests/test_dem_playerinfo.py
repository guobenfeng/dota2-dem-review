# -*- coding: utf-8 -*-
"""dem_playerinfo 单元测试：snappy 解压 / protobuf 边界 / 损坏文件防御。"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import dem_playerinfo  # noqa: E402


class TestSnappy(unittest.TestCase):
    def test_literal_roundtrip(self):
        # 手工构造 raw snappy 流：result_len=3 + 3 个长度1字面量 (A,B,C)
        stream = bytes([0x03, 0x00, 0x41, 0x00, 0x42, 0x00, 0x43])
        self.assertEqual(dem_playerinfo.snappy_decompress(stream), b"ABC")

    def test_truncated_returns_bytes(self):
        # result_len=5 但无数据：不崩溃，返回截断结果
        out = dem_playerinfo.snappy_decompress(bytes([0x05]))
        self.assertIsInstance(out, bytes)


class TestPbFields(unittest.TestCase):
    def test_simple_field(self):
        # field 1, wire type 0, varint 5  → key=0x08, value=0x05
        fields = list(dem_playerinfo.pb_fields(b"\x08\x05"))
        self.assertEqual(fields, [(1, 0, 5)])

    def test_truncated_varint_raises(self):
        # key 后 varint 被截断（0x80 需要续字节）→ 应抛 ValueError 而非 IndexError
        with self.assertRaises(ValueError):
            list(dem_playerinfo.pb_fields(b"\x08\x80"))


class TestExtractPlayersGuard(unittest.TestCase):
    def test_bad_magic(self):
        with tempfile.NamedTemporaryFile(suffix=".dem", delete=False) as f:
            f.write(b"NOTADEM\x00rest...")
            path = f.name
        try:
            with self.assertRaises(ValueError):
                dem_playerinfo.extract_players(path)
        finally:
            Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
