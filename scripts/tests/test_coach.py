# -*- coding: utf-8 -*-
"""coach 单元测试：规则引擎评分 / MVP 评选 / LLM 事实块。"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import coach  # noqa: E402


def _player(slot, team, **kw):
    base = {
        "slot": slot, "team": team, "hero": f"Hero{slot}",
        "player": f"P{slot}", "kills": 0, "deaths": 0, "assists": 0,
        "gpm": 300, "xpm": 400, "kda": 0.0, "damage": 0, "lh": 0, "dn": 0,
        "obs": 0, "sen": 0, "stuns": 0, "towers": 0, "key_items": [],
        "tf_participation": 0.5,
    }
    base.update(kw)
    return base


class TestGradePlayer(unittest.TestCase):
    def test_strong_core(self):
        p = _player(0, "天辉", kills=23, deaths=4, assists=10, gpm=600, xpm=700,
                    kda=23 / 4, lh=300, obs=2, sen=2, stuns=40, towers=4,
                    tf_participation=0.8, key_items=[{"item": "blink", "time": 600}])
        grade, score, hl, ad = coach.grade_player(p, 31, True)
        self.assertIn(grade, ("A", "S"))
        self.assertGreater(score, 70)

    def test_weak_player(self):
        p = _player(1, "夜魇", kills=0, deaths=10, gpm=200, xpm=250, kda=0.0,
                    lh=20, obs=0, sen=0)
        grade, score, hl, ad = coach.grade_player(p, 31, False)
        self.assertEqual(grade, "D")
        self.assertLess(score, 45)

    def test_long_game_item_not_flagged_late(self):
        # 87 分钟长局：blink 在第 20 分钟购买，基准按时长缩放后不应报"过晚"
        p = _player(2, "天辉", kills=5, deaths=5, gpm=400, xpm=500, kda=1.0,
                    lh=200, key_items=[{"item": "blink", "time": 1200}])
        grade, score, hl, ad = coach.grade_player(p, 87, True)
        # 不应出现"过晚"建议（长局基准被放大）
        self.assertFalse(any("过晚" in a for a in ad))


class TestRuleCoach(unittest.TestCase):
    def _summary(self):
        players = [
            _player(0, "天辉", kills=20, deaths=3, kda=20 / 3, gpm=600, xpm=700, lh=300, obs=2, sen=2),
            _player(1, "天辉", kills=2, deaths=1, kda=2.0, gpm=300, xpm=400, lh=30, obs=20, sen=20),
            _player(5, "夜魇", kills=1, deaths=12, kda=1 / 12, gpm=200, xpm=250, lh=20, obs=0, sen=0),
            _player(6, "夜魇", kills=5, deaths=4, kda=1.25, gpm=400, xpm=500, lh=150, obs=2, sen=2),
        ]
        return {
            "match_id": "999", "winner": "天辉", "duration_min": 40,
            "economy": {}, "teamfights": [], "first_blood": 100,
            "players": players,
        }

    def test_mvp_per_team(self):
        r = coach.rule_coach(self._summary())
        self.assertIsNotNone(r["mvp"]["radiant"])
        self.assertIsNotNone(r["mvp"]["dire"])
        # 天辉 MVP 应是击杀最多的 slot0
        self.assertEqual(r["mvp"]["radiant"]["slot"], 0)

    def test_no_crash(self):
        r = coach.rule_coach(self._summary())
        self.assertEqual(r["engine"], "rule")
        self.assertEqual(len(r["players"]), 4)

    def test_short_game_skips(self):
        s = self._summary()
        s["duration_min"] = 3
        r = coach.rule_coach(s)
        self.assertEqual(r["players"][0]["grade"], "—")


class TestBuildFactBlock(unittest.TestCase):
    def test_winner_stated(self):
        s = {"winner": "天辉", "teamfights": [
            {"start": 1200, "radiant_deaths": 1, "dire_deaths": 5},
            {"start": 1600, "radiant_deaths": 3, "dire_deaths": 1},
        ]}
        block = coach.build_fact_block(s)
        self.assertIn("比赛最终胜方：天辉", block)
        self.assertIn("天辉胜（天辉阵亡 1 / 夜魇阵亡 5）", block)
        self.assertIn("夜魇胜（天辉阵亡 3 / 夜魇阵亡 1）", block)


if __name__ == "__main__":
    unittest.main()
