# -*- coding: utf-8 -*-
"""deep_extract.py — 从 raw Blob 提取深度复盘素材 → reports/<match>_deep.json

为 AI 深度复盘（阵容/分阶段/每人多维分析）准备结构化素材：
- 分路推断（lane_pos，前10分钟位置热区）
- 位置推断（1-5号位：经济排名 + 眼位数量）
- 出装时间线（purchase_log，标注关键装备时间）
- 击杀/死亡时间线（kills_log 交叉重建每人死亡记录）
- 分阶段数据（前期0-10 / 中期10-25 / 后期25+：经济、补刀、击杀、死亡）
- APM（actions）、经济来源（gold_reasons）、死亡损失时长（life_state）
- 神符、堆野、买活、一血、建筑摧毁时间线

用法: python deep_extract.py --match 8701850772
"""
import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"
HERO_CACHE = HERE / "heroes.json"

RADIANT, DIRE = "radiant", "dire"

GOLD_REASONS = {
    "0": "其他", "1": "死亡惩罚", "2": "买活", "5": "逃跑惩罚", "6": "卖装备",
    "11": "建筑", "12": "英雄击杀", "13": "补刀", "14": "肉山", "15": "信使击杀",
    "16": "神符", "17": "排眼", "19": "赏金符", "20": "自然增长",
}
RUNE_NAMES = {
    "0": "双倍伤害", "1": "急速", "2": "幻象", "3": "隐身", "4": "回复",
    "5": "赏金", "6": "奥术", "7": "水符", "8": "智慧",
}
# 关键装备（用于出装节奏点评）
KEY_ITEMS = {
    "blink", "black_king_bar", "aghanims_shard", "ultimate_scepter", "radiance",
    "battle_fury", "desolator", "mjollnir", "greater_crit", "abyssal_blade",
    "satanic", "butterfly", "monkey_king_bar", "assault", "heart", "skadi",
    "manta", "diffusal_blade", "echo_sabre", "harpoon", "bloodthorn",
    "orchid", "cyclone", "sheepstick", "refresher", "octarine_core",
    "aether_lens", "glimmer_cape", "force_staff", "ghost", "lotus_orb",
    "pipe", "crimson_guard", "shivas_guard", "heavens_halberd", "blade_mail",
    "guardian_greaves", "mekansm", "solar_crest", "spirit_vessel", "vladmir",
    "hand_of_midas", "mask_of_madness", "sange_and_yasha", "kaya_and_sange",
    "yasha_and_kaya", "eternal_shroud", "bloodstone", "dagon", "dagon_5",
    "ethereal_blade", "nullifier", "silver_edge", "swift_blink", "overwhelming_blink",
    "arcane_blink", "travel_boots", "hurricane_pike", "gungir", "disperser",
    # 游戏内代码别名（purchase_log 实际使用的键）
    "bfury", "basher", "invis_sword", "sphere", "maelstrom", "armlet", "gem",
    "rapier", "lesser_crit", "meteor_hammer", "rod_of_atos", "witch_blade",
    "hood_of_defiance", "vanguard", "crimson_guard", "aeon_disk", "wind_waker",
    "power_treads", "phase_boots", "arcane_boots", "sange", "revenants_brooch",
}


def fmt_min(sec):
    if sec is None:
        return "?"
    sign = "-" if sec < 0 else ""
    sec = abs(int(sec))
    return f"{sign}{sec // 60}:{sec % 60:02d}"


def team_of_slot(idx):
    return RADIANT if idx < 5 else DIRE


def load_heroes():
    if HERO_CACHE.exists():
        return json.loads(HERO_CACHE.read_text(encoding="utf-8"))
    return {}


def player_hero_token(p, hero_tokens):
    au = p.get("ability_uses") or {}
    counts = {}
    for k, v in au.items():
        best = None
        for tok in hero_tokens:
            if k.startswith(tok + "_") or k == tok:
                if best is None or len(tok) > len(best):
                    best = tok
        if best:
            counts[best] = counts.get(best, 0) + v
    if counts:
        return max(counts, key=counts.get)
    return None


def infer_lane(p):
    """由 lane_pos 推断分路。坐标约 64~192，中心对角线为中路。"""
    lp = p.get("lane_pos") or {}
    votes = {"top": 0, "mid": 0, "bot": 0, "jungle": 0}
    for xs, ys in lp.items():
        try:
            x = int(xs) - 64
        except ValueError:
            continue
        for yv, w in (ys or {}).items():
            try:
                y = int(yv) - 64
            except ValueError:
                continue
            # 128x128 网格：左上=top，右下=bot，对角线=mid
            if abs(x - y) < 18:
                votes["mid"] += w
            elif y > x:
                # 上半区：靠边算 top，中间算野区
                votes["top" if (x < 40 or y > 88) else "jungle"] += w
            else:
                votes["bot" if (y < 40 or x > 88) else "jungle"] += w
    if not any(votes.values()):
        return "unknown", votes
    return max(votes, key=votes.get), votes


def infer_positions(team_players):
    """按 全场经济 排位 1-5 号位；眼数多者优先归为辅助(4/5)。"""
    by_gold = sorted(team_players, key=lambda q: -q["gold"])
    by_wards = sorted(team_players, key=lambda q: -(q["obs"] + q["sen"]))
    support_slots = {q["slot"] for q in by_wards[:2] if (q["obs"] + q["sen"]) >= 3}
    cores = [q for q in by_gold if q["slot"] not in support_slots]
    sups = [q for q in by_gold if q["slot"] in support_slots]
    result = {}
    pos = 1
    for q in cores:
        result[q["slot"]] = pos
        pos += 1
    # 辅助按经济高的是4号位
    for i, q in enumerate(sups):
        result[q["slot"]] = 4 + i if 4 + i <= 5 else 5
    return result


def phase_slice(arr, lo, hi):
    """arr 为每分钟采样；返回 [lo,hi) 分钟区间增量。"""
    if not arr:
        return 0
    lo = min(lo, len(arr) - 1)
    hi = min(hi, len(arr) - 1)
    return arr[hi] - arr[lo]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", required=True, help="match id（对应 reports/<match>.raw.json）")
    args = ap.parse_args()

    raw_p = REPORTS / f"{args.match}.raw.json"
    sum_p = REPORTS / f"{args.match}_summary.json"
    if not raw_p.exists():
        raise SystemExit(f"未找到 {raw_p}，请先运行 analyze.py")
    blob = json.loads(raw_p.read_text(encoding="utf-8"))
    summary = json.loads(sum_p.read_text(encoding="utf-8")) if sum_p.exists() else {}

    heroes = load_heroes()
    hero_tokens = [k for k in heroes if not k.isdigit()]
    # 中文名映射（token -> 中文），用于双语显示
    try:
        heroes_cn = json.loads((HERE / "heroes_cn.json").read_text(encoding="utf-8"))
    except Exception:
        heroes_cn = {}
    players = blob.get("players", [])
    duration_min = summary.get("duration_min") or len(blob.get("radiant_gold_adv", []) or [1])

    # 玩家昵称与权威英雄对应（analyze.py 已从 dem 提取并缓存）
    players_p = REPORTS / f"{args.match}_players.json"
    slot_pname = {}
    slot_auth_token = {}
    if players_p.exists():
        try:
            plist = json.loads(players_p.read_text(encoding="utf-8"))
            r_idx, d_idx = 0, 5
            for pl in plist:
                tok = (pl.get("hero") or "").replace("npc_dota_hero_", "")
                idx = None
                if pl.get("team") == 2 and r_idx < 5:
                    idx = r_idx; r_idx += 1
                elif pl.get("team") == 3 and d_idx < 10:
                    idx = d_idx; d_idx += 1
                if idx is not None:
                    slot_pname[idx] = pl.get("name") or ""
                    slot_auth_token[idx] = tok
        except Exception:
            pass

    # 每人 npc 名 → slot（用于交叉重建死亡时间线）：优先 dem 权威对应，缺失才反推
    slot_token = {}
    for i, p in enumerate(players):
        tok = slot_auth_token.get(i) or player_hero_token(p, hero_tokens)
        slot_token[i] = tok
    npc_to_slot = {f"npc_dota_hero_{tok}": i for i, tok in slot_token.items() if tok}

    # 全部击杀事件（带时间）→ 每人死亡时间线
    deaths_of = {i: [] for i in range(len(players))}
    kills_of = {i: [] for i in range(len(players))}
    for i, p in enumerate(players):
        for ev in p.get("kills_log") or []:
            victim = npc_to_slot.get(ev.get("key"))
            kills_of[i].append({"time": ev.get("time"), "victim": ev.get("key", "").replace("npc_dota_hero_", "")})
            if victim is not None:
                deaths_of[victim].append({"time": ev.get("time"), "killer": slot_token.get(i)})

    out_players = []
    tmp_for_pos = []
    for i, p in enumerate(players):
        gold_t = p.get("gold_t") or [0]
        tmp_for_pos.append({
            "slot": i, "team": team_of_slot(i), "gold": gold_t[-1],
            "obs": p.get("obs_placed") or 0, "sen": p.get("sen_placed") or 0,
        })
    positions = {}
    for team in (RADIANT, DIRE):
        positions.update(infer_positions([q for q in tmp_for_pos if q["team"] == team]))

    for i, p in enumerate(players):
        tok = slot_token.get(i)
        gold_t = p.get("gold_t") or [0]
        xp_t = p.get("xp_t") or [0]
        lh_t = p.get("lh_t") or [0]
        dn_t = p.get("dn_t") or [0]
        lane, lane_votes = infer_lane(p)

        # 出装时间线（过滤消耗品噪音，保留关键装备+全列表）
        purchase_log = [{"time": e.get("time"), "t": fmt_min(e.get("time")), "item": e.get("key")}
                        for e in (p.get("purchase_log") or [])]
        key_items = [e for e in purchase_log if e["item"] in KEY_ITEMS]

        # 分阶段
        end = len(gold_t) - 1
        mid_hi = min(25, end)
        phases = {}
        for name, lo, hi in (("early_0_10", 0, min(10, end)), ("mid_10_25", min(10, end), mid_hi), ("late_25_end", mid_hi, end)):
            span = max(hi - lo, 1)
            k_in = [e for e in kills_of[i] if lo * 60 <= (e["time"] or 0) < hi * 60]
            d_in = [e for e in deaths_of[i] if lo * 60 <= (e["time"] or 0) < hi * 60]
            phases[name] = {
                "gold_gain": phase_slice(gold_t, lo, hi),
                "xp_gain": phase_slice(xp_t, lo, hi),
                "lh_gain": phase_slice(lh_t, lo, hi),
                "gpm": round(phase_slice(gold_t, lo, hi) / span),
                "kills": len(k_in),
                "deaths": len(d_in),
            }

        actions_total = sum((p.get("actions") or {}).values())
        life = p.get("life_state") or {}
        dead_sec = life.get("2", 0)
        gr = {GOLD_REASONS.get(k, k): v for k, v in (p.get("gold_reasons") or {}).items()}
        runes = {RUNE_NAMES.get(k, k): v for k, v in (p.get("runes") or {}).items()}

        _en = heroes.get(tok or "", tok)
        _cn = heroes_cn.get(tok or "")
        out_players.append({
            "slot": i,
            "team": team_of_slot(i),
            "player": slot_pname.get(i, ""),
            "hero_token": tok,
            "hero": _en,
            "hero_cn": _cn or _en,
            "hero_display": f"{_cn}（{_en}）" if _cn and _cn != _en else _en,
            "position": positions.get(i),
            "lane": lane,
            "lane_votes": lane_votes,
            "gold_at_10": gold_t[min(10, len(gold_t) - 1)],
            "lh_at_10": lh_t[min(10, len(lh_t) - 1)],
            "final_gold": gold_t[-1],
            "final_xp": xp_t[-1],
            "final_lh": lh_t[-1],
            "final_dn": dn_t[-1],
            "kills_log": kills_of[i],
            "deaths_log": deaths_of[i],
            "phases": phases,
            "purchase_log": purchase_log,
            "key_items": key_items,
            "neutral_items": [e.get("item_name") for e in (p.get("neutral_item_history") or [])],
            "apm": round(actions_total / max(duration_min, 1)),
            "dead_seconds": dead_sec,
            "dead_pct": round(dead_sec / max(duration_min * 60, 1) * 100, 1),
            "gold_reasons": gr,
            "runes": runes,
            "rune_pickups": p.get("rune_pickups", 0),
            "camps_stacked": p.get("camps_stacked", 0),
            "buybacks": len(p.get("buyback_log") or []),
            "firstblood": bool(p.get("firstblood_claimed")),
            "obs_placed": p.get("obs_placed", 0),
            "sen_placed": p.get("sen_placed", 0),
            "stuns": round(p.get("stuns") or 0, 1),
            "tf_participation": p.get("teamfight_participation"),
            "pings": sum((p.get("pings") or {}).values()),
        })

    # 建筑摧毁时间线
    buildings = []
    for o in blob.get("objectives", []) or []:
        if o.get("type") == "building_kill":
            buildings.append({"time": o.get("time"), "t": fmt_min(o.get("time")), "key": o.get("key")})
    buildings.sort(key=lambda x: x["time"] or 0)

    deep = {
        "match_id": args.match,
        "duration_min": duration_min,
        "winner": summary.get("winner"),
        "radiant_gold_adv": blob.get("radiant_gold_adv"),
        "radiant_xp_adv": blob.get("radiant_xp_adv"),
        "buildings_timeline": buildings,
        "teamfights": summary.get("teamfights"),
        "players": out_players,
    }
    out_p = REPORTS / f"{args.match}_deep.json"
    out_p.write_text(json.dumps(deep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[deep] 已生成 {out_p} ({out_p.stat().st_size // 1024} KB)")
    # 控制台摘要
    for q in out_players:
        print(f"  slot{q['slot']} {q['team'][:1].upper()} pos{q['position']} {q['lane']:6s} {q['hero']}: "
              f"10分钟{q['gold_at_10']}金/{q['lh_at_10']}刀 APM{q['apm']} 死亡占比{q['dead_pct']}% "
              f"关键装备{[e['item'] for e in q['key_items']][:4]}")


if __name__ == "__main__":
    main()
