# -*- coding: utf-8 -*-
"""deep_extract 单元测试：分路推断 / 团战胜者 / 团战深度重建。"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import deep_extract  # noqa: E402


class TestInferLane(unittest.TestCase):
    def test_mid(self):
        # 对角线附近
        p = {"lane_pos": {"160": {"160": 10}}}
        self.assertEqual(deep_extract.infer_lane(p)[0], "mid")

    def test_top(self):
        # 上半区靠边
        p = {"lane_pos": {"80": {"180": 5}}}
        self.assertEqual(deep_extract.infer_lane(p)[0], "top")

    def test_bot(self):
        p = {"lane_pos": {"180": {"80": 5}}}
        self.assertEqual(deep_extract.infer_lane(p)[0], "bot")

    def test_empty(self):
        p = {"lane_pos": {}}
        lane, votes = deep_extract.infer_lane(p)
        self.assertEqual(lane, "unknown")


class TestTeamfightWinner(unittest.TestCase):
    def test_radiant_fewer_deaths(self):
        self.assertEqual(deep_extract._teamfight_winner(1, 5, -100), "radiant")

    def test_dire_fewer_deaths(self):
        self.assertEqual(deep_extract._teamfight_winner(5, 1, 100), "dire")

    def test_tie_uses_gold(self):
        self.assertEqual(deep_extract._teamfight_winner(3, 3, 500), "radiant")
        self.assertEqual(deep_extract._teamfight_winner(3, 3, -500), "dire")


def _make_blob():
    """构造一个最小 raw blob：1 场团战，slot0(天辉) 击杀 slot5(夜魇)。"""
    players = []
    for i in range(10):
        players.append({
            "killed": {"npc_dota_hero_enemy": 1} if i == 0 else {},
            "deaths": 0 if i != 5 else 1,
            "damage": 500 if i == 0 else 100,
            "deaths_pos": {"100": {"150": 1}} if i == 5 else {},
            "gold_delta": 200 if i == 0 else -50,
            "xp_delta": 100 if i == 0 else -20,
            "ability_uses": {"a": 3} if i == 0 else {},
            "item_uses": {},
            "buybacks": 0,
        })
    # 修正 victim 指向 slot5 的英雄 token
    players[0]["killed"] = {"npc_dota_hero_enemy": 1}
    return {
        "teamfights": [{
            "start": 1200, "end": 1247, "last_death": 1245,
            "players": players,
        }]
    }


class TestEnrichTeamfights(unittest.TestCase):
    def test_basic(self):
        blob = _make_blob()
        slot_display = {i: f"P{i}" for i in range(10)}
        slot_display[0] = "斧王（Axe）"
        slot_display[5] = "敌法师（Anti-Mage）"
        npc_to_slot = {"npc_dota_hero_enemy": 5}
        tfs = deep_extract.enrich_teamfights(blob, slot_display, npc_to_slot)
        self.assertEqual(len(tfs), 1)
        tf = tfs[0]
        self.assertEqual(tf["winner"], "radiant")
        self.assertEqual(tf["radiant_deaths"], 0)
        self.assertEqual(tf["dire_deaths"], 1)
        self.assertGreaterEqual(tf["quality_score"], 0)
        self.assertLessEqual(tf["quality_score"], 100)
        # 击杀链应记录 slot0 -> slot5
        self.assertTrue(any(k["killer_slot"] == 0 and k["victim_slot"] == 5 for k in tf["kill_chain"]))
        # 死亡位置质心（夜魇 slot5 死亡在 100,150）
        self.assertEqual(tf["death_positions"]["dire_centroid"], {"x": 100.0, "y": 150.0, "n": 1})
        # 参与者数量 = 10
        self.assertEqual(len(tf["participants"]), 10)
        # 疑似先手应为最活跃者（slot0）
        self.assertEqual(tf["suspected_initiator"]["slot"], 0)


if __name__ == "__main__":
    unittest.main()
