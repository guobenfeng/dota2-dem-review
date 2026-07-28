# -*- coding: utf-8 -*-
"""lib.utils 单元测试（纯函数，无需网络/外部文件）。"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from lib.utils import (  # noqa: E402
    is_radiant, fmt_min, match_id_of, hero_name, player_hero_token,
)


class TestIsRadiant(unittest.TestCase):
    def test_slots(self):
        self.assertTrue(is_radiant(0))
        self.assertTrue(is_radiant(4))
        self.assertFalse(is_radiant(5))
        self.assertFalse(is_radiant(9))


class TestFmtMin(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(fmt_min(0), "0:00")

    def test_normal(self):
        self.assertEqual(fmt_min(125), "2:05")
        self.assertEqual(fmt_min(60), "1:00")

    def test_none(self):
        self.assertEqual(fmt_min(None), "—")


class TestMatchIdOf(unittest.TestCase):
    def test_dem(self):
        self.assertEqual(match_id_of(Path("8701850772.dem")), "8701850772")

    def test_short(self):
        # 至少 6 位数字才被识别为 match id
        self.assertEqual(match_id_of(Path("123456_x.dem")), "123456")


class TestHeroName(unittest.TestCase):
    def test_english_only(self):
        import lib.utils as U
        cn_path = HERE / "heroes_cn.json"
        backup = cn_path.read_text(encoding="utf-8") if cn_path.exists() else None
        if cn_path.exists():
            cn_path.unlink()
        U._HEROES_CN = None
        try:
            heroes = {"87": "Lina", "lina": "Lina"}
            self.assertEqual(hero_name(heroes, "87"), "Lina")
        finally:
            if backup is not None:
                cn_path.write_text(backup, encoding="utf-8")
            U._HEROES_CN = None

    def test_bilingual(self):
        heroes = {"87": "Lina", "lina": "Lina"}
        cn_path = HERE / "heroes_cn.json"
        backup = cn_path.read_text(encoding="utf-8") if cn_path.exists() else None
        cn_path.write_text('{"lina": "莉娜"}', encoding="utf-8")
        try:
            self.assertEqual(hero_name(heroes, "lina"), "莉娜（Lina）")
        finally:
            if backup is not None:
                cn_path.write_text(backup, encoding="utf-8")
            else:
                cn_path.unlink()


class TestPlayerHeroToken(unittest.TestCase):
    def test_prefix_match(self):
        # 已知英雄 token 集合（非数字键）
        heroes = {"slark": "Slark", "slark_tail": "SlarkTail"}
        ability_uses = {"slark_dark_pact": 5, "slark_pounce": 3, "tidehunter_ravage": 1}
        # 前缀匹配应选出 slark（而非更长的 slark_tail，因为键是 slark_ 开头）
        self.assertEqual(player_hero_token({"ability_uses": ability_uses}, heroes), "slark")

    def test_compound_token(self):
        heroes = {"shadow_demon": "Shadow Demon", "spirit_breaker": "Spirit Breaker"}
        ability_uses = {"spirit_breaker_charge_of_darkness": 4}
        self.assertEqual(player_hero_token({"ability_uses": ability_uses}, heroes), "spirit_breaker")

    def test_no_match(self):
        heroes = {"slark": "Slark"}
        ability_uses = {"something_else": 3}
        self.assertIsNone(player_hero_token({"ability_uses": ability_uses}, heroes))


if __name__ == "__main__":
    unittest.main()
