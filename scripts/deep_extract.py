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

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.utils import (
    ROOT as HERE, REPORT_DIR, HERO_CACHE,
    RADIANT, DIRE, fmt_min, is_radiant, player_hero_token,
    load_heroes, load_heroes_cn, hero_name,
)

# deep_extract 内部用英文阵营名（保持输出 JSON 兼容）
RADIANT, DIRE = "radiant", "dire"
REPORTS = REPORT_DIR


def team_of_slot(idx):
    """slot 0-4 = radiant，5-9 = dire。"""
    return RADIANT if is_radiant(idx) else DIRE

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


def _infer_winner(blob: dict):
    """从 raw blob 推断胜者（当 summary.json 不可用时）。返回 English radiant/dire。"""
    # 扫描建筑击杀中的遗迹
    for o in blob.get("objectives", []) or []:
        if o.get("type") == "building_kill":
            key = o.get("key") or ""
            if "goodguys_fort" in key:
                return DIRE  # 夜魇推掉天辉遗迹
            elif "badguys_fort" in key:
                return RADIANT
    # 回退用最终经济差符号
    adv = blob.get("radiant_gold_adv") or []
    if adv:
        return RADIANT if adv[-1] > 0 else DIRE
    return None


def _teamfight_winner(r_deaths, d_deaths, net_gold):
    """死亡少的一方获胜；死亡相同则看净经济差。"""
    if r_deaths < d_deaths:
        return RADIANT
    if d_deaths < r_deaths:
        return DIRE
    return RADIANT if net_gold >= 0 else DIRE


def enrich_teamfights(blob, slot_display, npc_to_slot):
    """从 raw blob 的 teamfights（含 per-player 细节）重建团战深度分析。

    返回 enriched 列表，每个元素含：
    - 时间 / 阵营死亡 / 胜方 / 净经济·经验差
    - quality_score（一边倒度 0-100，基于击杀差 + 净经济）
    - kill_chain（击杀链：谁杀谁）
    - participants（每人伤害/治疗/经济差/击杀/死亡/买活/活跃度）
    - suspected_initiator（全场最活跃开团候选，低置信启发式）
    - death_positions（各阵营死亡位置质心，来自 deaths_pos）
    """
    raw_tfs = blob.get("teamfights") or []
    if not raw_tfs:
        return []
    out = []
    for tf in raw_tfs:
        pls = tf.get("players") or []
        participants = {}
        if len(pls) >= 10:
            for i, pl in enumerate(pls):
                killed = pl.get("killed") or {}
                participants[i] = {
                    "slot": i,
                    "hero": slot_display.get(i, f"slot{i}"),
                    "damage": pl.get("damage") or 0,
                    "healing": pl.get("healing") or 0,
                    "gold_delta": pl.get("gold_delta") or 0,
                    "xp_delta": pl.get("xp_delta") or 0,
                    "kills": sum(killed.values()),
                    "deaths": pl.get("deaths") or 0,
                    "buybacks": pl.get("buybacks") or 0,
                    "ability_uses": sum((pl.get("ability_uses") or {}).values()),
                    "item_uses": sum((pl.get("item_uses") or {}).values()),
                }
        # 击杀链
        kill_chain = []
        for i, pl in enumerate(pls):
            for victim_npc, cnt in (pl.get("killed") or {}).items():
                vslot = npc_to_slot.get(victim_npc)
                kill_chain.append({
                    "killer_slot": i,
                    "killer": slot_display.get(i, f"slot{i}"),
                    "victim_slot": vslot,
                    "victim": slot_display.get(vslot, (victim_npc or "").replace("npc_dota_hero_", "")),
                    "count": cnt,
                })
        kill_chain.sort(key=lambda e: -(e["count"] or 0))
        # 死亡位置（deaths_pos: {x_str:{y_str:count}}，0-255 小地图坐标）
        death_pos = {"radiant": [], "dire": []}
        for i, pl in enumerate(pls):
            for xs, ymap in (pl.get("deaths_pos") or {}).items():
                for ys, c in (ymap or {}).items():
                    try:
                        x, y = int(xs), int(ys)
                    except ValueError:
                        continue
                    death_pos["radiant" if i < 5 else "dire"].append({"x": x, "y": y, "c": c})

        r_deaths = sum(p["deaths"] for p in participants.values() if p["slot"] < 5)
        d_deaths = sum(p["deaths"] for p in participants.values() if p["slot"] >= 5)
        net_gold = (sum(p["gold_delta"] for p in participants.values() if p["slot"] < 5)
                    - sum(p["gold_delta"] for p in participants.values() if p["slot"] >= 5))
        net_xp = (sum(p["xp_delta"] for p in participants.values() if p["slot"] < 5)
                  - sum(p["xp_delta"] for p in participants.values() if p["slot"] >= 5))
        winner = _teamfight_winner(r_deaths, d_deaths, net_gold)

        # 疑似先手：全场 ability_uses+item_uses 最高者（启发式，低置信）
        engager = None
        eng_best = -1
        for p in participants.values():
            act = p["ability_uses"] + p["item_uses"]
            if act > eng_best:
                eng_best, engager = act, p["slot"]
        # 一边倒度评分：0-100
        kill_diff = d_deaths - r_deaths  # 天辉击杀优势（正=天辉赢）
        quality = 50 + kill_diff * 6 + max(-20, min(20, net_gold // 300))
        quality = max(0, min(100, quality))

        def _centroid(lst):
            if not lst:
                return None
            n = sum(d["c"] for d in lst)
            if n == 0:
                return None
            return {"x": round(sum(d["x"] * d["c"] for d in lst) / n, 1),
                    "y": round(sum(d["y"] * d["c"] for d in lst) / n, 1),
                    "n": n}

        out.append({
            "start": tf.get("start"),
            "end": tf.get("end"),
            "duration": (tf.get("end") or 0) - (tf.get("start") or 0),
            "last_death": tf.get("last_death"),
            "radiant_deaths": r_deaths,
            "dire_deaths": d_deaths,
            "winner": winner,
            "net_gold_delta": net_gold,
            "net_xp_delta": net_xp,
            "quality_score": quality,
            "kill_chain": kill_chain,
            "participants": [participants[i] for i in sorted(participants)],
            "suspected_initiator": (
                {"slot": engager, "hero": slot_display.get(engager), "confidence": "low"}
                if engager is not None else None),
            "death_positions": {
                "radiant_centroid": _centroid(death_pos["radiant"]),
                "dire_centroid": _centroid(death_pos["dire"]),
                "radiant_deaths": len(death_pos["radiant"]),
                "dire_deaths": len(death_pos["dire"]),
            },
        })
    return out


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
    summary = {}
    summary_missing = False
    if sum_p.exists():
        summary = json.loads(sum_p.read_text(encoding="utf-8"))
    else:
        summary_missing = True
        print("[deep_extract] 警告：_summary.json 不存在（可能只跑了 --raw-only），"
              "winner 与 duration 将从 raw blob 推断，部分分析可能不完整。")

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

    # 团战深度分析（基于 raw blob 的 per-player 细节重建）
    slot_display = {q["slot"]: q["hero_display"] for q in out_players}
    enriched_tfs = enrich_teamfights(blob, slot_display, npc_to_slot)

    # 建筑摧毁时间线
    buildings = []
    for o in blob.get("objectives", []) or []:
        if o.get("type") == "building_kill":
            buildings.append({"time": o.get("time"), "t": fmt_min(o.get("time")), "key": o.get("key")})
    buildings.sort(key=lambda x: x["time"] or 0)

    # 肉山击杀时间线（从 objectives 中的 CHAT_MESSAGE_ROSHAN_KILL 事件）
    roshan_kills = []
    for o in blob.get("objectives", []) or []:
        if o.get("type") == "CHAT_MESSAGE_ROSHAN_KILL":
            roshan_kills.append({"time": o.get("time"), "t": fmt_min(o.get("time")),
                                 "player": o.get("player1", -1)})
    # 买活时间线（每位玩家，slot 统一用 0-9 与 deep.json 其余字段一致；
    # 注：odota 的 player_slot 夜魇编码为 128-132，此处改用枚举索引避免不一致）
    buyback_timeline = []
    for i, p in enumerate(players):
        bb_log = p.get("buyback_log") or []
        for bb_time in bb_log:
            _t = bb_time if isinstance(bb_time, (int, float)) else bb_time.get("time")
            buyback_timeline.append({"time": _t, "t": fmt_min(_t), "slot": i})
    buyback_timeline.sort(key=lambda x: x["time"] or 0)

    deep = {
        "match_id": args.match,
        "duration_min": duration_min,
        "winner": summary.get("winner") or _infer_winner(blob),
        "radiant_gold_adv": blob.get("radiant_gold_adv"),
        "radiant_xp_adv": blob.get("radiant_xp_adv"),
        "buildings_timeline": buildings,
        "roshan_timeline": roshan_kills,
        "buyback_timeline": buyback_timeline,
        "teamfights": enriched_tfs,
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
