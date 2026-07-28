#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 .dem 文件尾部的 CDemoFileInfo (epilogue) 提取玩家昵称与英雄对应关系。

odota/parser 的 Blob 不导出玩家昵称，但 Source2 .dem 的文件尾 CDemoFileInfo
包含明文 CDotaGameInfo.player_info: hero_name / player_name / steamid / game_team。
本模块零第三方依赖：自带 snappy 解压 + 最小 protobuf 解析。

用法:
    python dem_playerinfo.py --dem <path.dem> [--out reports/<match>_players.json]
或被 analyze.py import 调用 extract_players(dem_path)。
"""
import json
import struct
import sys
from pathlib import Path


# ---------- snappy 解压（纯 Python，raw format） ----------
def snappy_decompress(data: bytes) -> bytes:
    # 读取前置 varint = 解压后长度
    pos = 0
    result_len = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result_len |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    out = bytearray()
    n = len(data)
    while pos < n:
        tag = data[pos]
        pos += 1
        t = tag & 3
        if t == 0:  # literal
            ln = (tag >> 2) + 1
            if ln > 60:
                extra = ln - 60
                ln = int.from_bytes(data[pos:pos + extra], "little") + 1
                pos += extra
            out += data[pos:pos + ln]
            pos += ln
        else:
            if t == 1:  # copy, 1-byte offset
                ln = ((tag >> 2) & 7) + 4
                off = ((tag >> 5) << 8) | data[pos]
                pos += 1
            elif t == 2:  # copy, 2-byte offset
                ln = (tag >> 2) + 1
                off = int.from_bytes(data[pos:pos + 2], "little")
                pos += 2
            else:  # copy, 4-byte offset
                ln = (tag >> 2) + 1
                off = int.from_bytes(data[pos:pos + 4], "little")
                pos += 4
            start = len(out) - off
            # 切片拷贝（比逐字节 append 快数个量级）
            if start + ln <= len(out):
                out.extend(out[start:start + ln])
            else:
                # 重叠拷贝（数据跨新旧边界）：逐字节复制，回退逐 byte
                for i in range(ln):
                    out.append(out[start + i])
    return bytes(out[:result_len])


# ---------- 最小 protobuf 解析 ----------
def _read_varint(buf, pos):
    r = 0
    s = 0
    while True:
        b = buf[pos]
        pos += 1
        r |= (b & 0x7F) << s
        if not (b & 0x80):
            return r, pos
        s += 7


def pb_fields(buf):
    """遍历一层 protobuf，yield (field_no, wire_type, value)。"""
    pos = 0
    n = len(buf)
    while pos < n:
        key, pos = _read_varint(buf, pos)
        fno, wt = key >> 3, key & 7
        if wt == 0:
            v, pos = _read_varint(buf, pos)
        elif wt == 2:
            ln, pos = _read_varint(buf, pos)
            v = buf[pos:pos + ln]
            pos += ln
        elif wt == 5:
            v = buf[pos:pos + 4]
            pos += 4
        elif wt == 1:
            v = buf[pos:pos + 8]
            pos += 8
        else:
            raise ValueError(f"unsupported wire type {wt}")
        yield fno, wt, v


def extract_players(dem_path):
    """返回 [{'hero': 'npc_dota_hero_x', 'name': 昵称, 'steamid': str, 'team': 2/3}]，按 dem 内顺序。"""
    p = Path(dem_path)
    with open(p, "rb") as f:
        magic = f.read(8)
        if magic != b"PBDEMS2\x00":
            raise ValueError(f"不是 Source2 dem 文件: {magic!r}")
        off = struct.unpack("<I", f.read(4))[0]
        if off == 0:
            raise ValueError("dem 未收尾（无 CDemoFileInfo），可能是损坏/未打完的录像")
        f.seek(off)
        # demo message: cmd(varint) tick(varint) size(varint) data
        def rv():
            r = 0
            s = 0
            while True:
                b = f.read(1)[0]
                r |= (b & 0x7F) << s
                if not (b & 0x80):
                    return r
                s += 7
        cmd = rv()
        rv()  # tick
        size = rv()
        data = f.read(size)
    if cmd & 64:  # DEM_IsCompressed
        data = snappy_decompress(data)
    # CDemoFileInfo: field 4 = game_info (CGameInfo)；CGameInfo.dota = field 4；
    # CDotaGameInfo.player_info = field 4（repeated CPlayerInfo），field 6 才是 picks_bans
    players = []
    for fno, wt, v in pb_fields(data):
        if fno == 4 and wt == 2:  # game_info
            for f2, w2, v2 in pb_fields(v):
                if f2 == 4 and w2 == 2:  # dota (CDotaGameInfo)
                    for f3, w3, v3 in pb_fields(v2):
                        if f3 == 4 and w3 == 2:  # player_info (repeated CPlayerInfo)
                            info = {"hero": None, "name": None, "steamid": None, "team": None}
                            for f4, w4, v4 in pb_fields(v3):
                                if f4 == 1:
                                    info["hero"] = v4.decode("utf-8", "replace")
                                elif f4 == 2:
                                    info["name"] = v4.decode("utf-8", "replace")
                                elif f4 == 4:
                                    info["steamid"] = str(v4)
                                elif f4 == 5:
                                    info["team"] = v4
                            players.append(info)
    return players


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    players = extract_players(args.dem)
    for pl in players:
        print(f"team={pl['team']} hero={pl['hero']:<38} name={pl['name']}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(players, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[players] 已保存 → {args.out}")


if __name__ == "__main__":
    main()
