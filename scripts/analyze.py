#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dota 2 .dem 复盘分析器 —— 基于 odota/parser 的实战项目

工作流程
========
1. (可选) 自动定位/构建 odota/parser，并启动 5600 端口解析服务
2. 把 .dem 文件 POST 到 http://localhost:5600/?blob
3. 取回结构化 Blob JSON（玩家快照/战斗日志/眼位/BP/团战/经济曲线…）
4. 计算关键指标，生成：
      reports/<match>.raw.json      原始 Blob（缓存，便于二次分析）
      reports/<match>_analysis.md   人类可读复盘报告
      reports/<match>_summary.json 机器可读摘要（喂给 LLM 做 AI 叙事）

用法
====
  # 最简：假设 parser 已在 5600 端口运行
  python analyze.py --dem ./replays/match.dem

  # 一键：自动构建并启动 parser（需先下载好 JDK / Maven 到 toolchain 目录）
  python analyze.py --dem <文件.dem> --start-parser --parser-dir <odota-parser路径>

  # 只解析不生成报告（仅保存 raw.json）
  python analyze.py --dem <文件.dem> --raw-only

依赖：pip install requests
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# 共享工具模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.utils import (
    ROOT as HERE, REPORT_DIR, HERO_CACHE, HERO_CN,
    RADIANT, DIRE,
    fmt_min, team_of_slot, player_hero_token,
    load_heroes, load_heroes_cn, hero_name,
    find_java, locate_parser_dir, match_id_of,
)

# 兼容旧引用
PARSER_PORT = 5600


# ---------------------------------------------------------------------------
# 1. 工具链定位（JDK / Maven）— 使用 lib.utils.find_java
# ---------------------------------------------------------------------------
def locate_toolchain(toolchain: Path):
    """
    定位 JAVA_HOME 与 MVN。
    依次搜索：--toolchain 参数 > 环境变量 DOTA2_TOOLCHAIN >
    <项目>/toolchain > <项目上级>/toolchain > 系统 PATH。
    """
    from lib.utils import find_java, find_mvn as _find_mvn
    java = find_java()
    java_home = None
    if java:
        java_home = Path(java).parent.parent
    mvn = _find_mvn()
    return java_home, (Path(java) if java else None), (Path(mvn) if mvn else None)


# ---------------------------------------------------------------------------
# 2. 启动 odota/parser
# ---------------------------------------------------------------------------
def build_parser(parser_dir: Path, java_home, mvn) -> Path:
    jar = parser_dir / "target" / "stats-0.1.0.jar"
    if jar.exists():
        print(f"[parser] 已存在构建产物：{jar}")
        return jar
    if not mvn:
        raise RuntimeError("找不到 Maven，无法构建 parser。请先下载 Maven 到 toolchain 目录。")
    print(f"[parser] 开始构建（mvn clean install），这可能需要 1-3 分钟…")
    env = dict(os.environ)
    if java_home:
        env["JAVA_HOME"] = str(java_home)
    subprocess.run([str(mvn), "-q", "clean", "install", "-U"],
                 cwd=str(parser_dir), env=env, check=True)
    if not jar.exists():
        raise RuntimeError(f"构建完成但未找到 {jar}")
    print(f"[parser] 构建成功：{jar}")
    return jar


def start_parser_service(jar: Path, java) -> subprocess.Popen:
    print(f"[parser] 启动服务：java -jar {jar.name} {PARSER_PORT}")
    logf = open(HERE / "parser.log", "w", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        [str(java), "-jar", str(jar), str(PARSER_PORT)],
        stdout=logf, stderr=subprocess.STDOUT,
    )
    # 轮询健康检查
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://localhost:{PARSER_PORT}/healthz", timeout=2) as r:
                if r.read().decode().strip() == "ok":
                    print("[parser] 服务就绪 ✓")
                    return proc
        except Exception:
            time.sleep(2)
    raise RuntimeError("parser 服务启动超时（60s 内未响应 /healthz）")


def ensure_parser(args, parser_dir):
    """根据参数决定是否构建并启动 parser，返回 (是否由本脚本启动)。"""
    toolchain = (HERE / "toolchain").resolve()
    java_home, java, mvn = locate_toolchain(toolchain)
    if not java:
        raise RuntimeError("未找到 Java。请下载 JDK 21 并放到 toolchain/ 目录。")

    # 先探测是否已有服务在跑
    try:
        with urllib.request.urlopen(f"http://localhost:{PARSER_PORT}/healthz", timeout=2) as r:
            if r.read().decode().strip() == "ok":
                print("[parser] 检测到已有 parser 服务在运行，复用之。")
                return False
    except Exception:
        pass

    if not args.start_parser:
        raise RuntimeError(
            f"localhost:{PARSER_PORT} 无服务，且未指定 --start-parser。"
            "请先启动 odota/parser，或使用 --start-parser 自动构建。"
        )

    if not parser_dir or not parser_dir.exists():
        raise RuntimeError(f"parser 目录不存在：{parser_dir}")
    jar = build_parser(parser_dir, java_home, mvn)
    start_parser_service(jar, java)
    return True


# ---------------------------------------------------------------------------
# 3. 调用 parser 解析 .dem
# ---------------------------------------------------------------------------
def parse_dem(dem_path: Path) -> dict:
    url = f"http://localhost:{PARSER_PORT}/?blob"
    with open(dem_path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    print(f"[parse] POST {dem_path.name} ({len(data)/1024/1024:.1f} MB) → parser …")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise SystemExit(
            f"[parse] 无法连接 parser 服务（http://localhost:{PARSER_PORT}）。\n"
            f"请先启动解析服务：python run_parser.py\n原始错误：{e}")
    except Exception as e:
        raise SystemExit(f"[parse] 请求 parser 失败：{e}")
    print(f"[parse] 耗时 {time.time()-t0:.1f}s，返回 {len(body)/1024:.1f} KB")
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        # parser 返回了非 JSON（如 Java 报错栈），写出前 500 字符便于排查
        snippet = body[:500].replace("\n", " ")
        raise SystemExit(f"[parse] parser 返回的不是合法 JSON（可能被 .dem 损坏或 parser 报错）：{e}\n"
                         f"响应片段：{snippet}")


# ---------------------------------------------------------------------------
# 4. 玩家昵称提取
# ---------------------------------------------------------------------------


def load_player_names(dem_path=None, match_id=None):
    """获取 slot -> {'name', 'hero_token'}。

    优先从 .dem 的 CDemoFileInfo 提取（dem_playerinfo.py，同时给出权威英雄对应）；
    已提取过则读缓存 reports/<match>_players.json。
    """
    cache = REPORT_DIR / f"{match_id}_players.json" if match_id else None
    players = None
    if cache and cache.exists():
        try:
            players = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            players = None
    if players is None and dem_path and Path(dem_path).exists():
        try:
            from dem_playerinfo import extract_players
            players = extract_players(dem_path)
            if cache:
                cache.write_text(json.dumps(players, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[players] 玩家名单 → {cache.name}")
        except Exception as e:
            print(f"[players] 从 dem 提取玩家名失败（报告将不含玩家名）：{e}")
            players = None
    if not players:
        return {}
    # dem 内顺序：team2(天辉) 按 slot0-4，team3(夜魇) 按 slot5-9
    result = {}
    r_idx, d_idx = 0, 5
    for pl in players:
        tok = (pl.get("hero") or "").replace("npc_dota_hero_", "")
        entry = {"name": pl.get("name") or "", "hero_token": tok}
        if pl.get("team") == 2 and r_idx < 5:
            result[r_idx] = entry
            r_idx += 1
        elif pl.get("team") == 3 and d_idx < 10:
            result[d_idx] = entry
            d_idx += 1
    return result


# ---------------------------------------------------------------------------
# 5. 指标计算
# ---------------------------------------------------------------------------


def compute(blob: dict, match_id, heroes: dict, player_names: dict = None) -> dict:
    player_names = player_names or {}
    players = blob.get("players", [])
    duration_min = len(blob.get("radiant_gold_adv", [])) or 0

    # --- 胜负判定（以遗迹被推为权威信号） ---
    winner = None
    first_blood = None
    roshan_kills = []
    aegis_events = []
    objectives = blob.get("objectives", [])
    for o in objectives:
        t = o.get("type")
        if t == "building_kill" and o.get("key"):
            key = o["key"]
            if "goodguys_fort" in key:
                winner = DIRE  # 夜魇推掉天辉遗迹
            elif "badguys_fort" in key:
                winner = RADIANT
        elif t == "CHAT_MESSAGE_FIRSTBLOOD" and first_blood is None:
            first_blood = o.get("time")
        elif t == "CHAT_MESSAGE_ROSHAN_KILL":
            roshan_kills.append(o.get("time"))
        elif t in ("CHAT_MESSAGE_AEGIS", "CHAT_MESSAGE_AEGIS_STOLEN", "CHAT_MESSAGE_DENIED_AEGIS"):
            aegis_events.append((o.get("time"), t))

    # 回退：用最终经济差符号推断
    gold_adv = blob.get("radiant_gold_adv", [])
    if winner is None and gold_adv:
        winner = RADIANT if gold_adv[-1] > 0 else DIRE

    # --- BP / 阵容 ---
    picks = {RADIANT: [], DIRE: []}
    bans = {RADIANT: [], DIRE: []}
    for d in blob.get("draft_timings", []):
        team = RADIANT if d.get("draft_active_team") == 2 else (DIRE if d.get("draft_active_team") == 3 else None)
        hid = d.get("hero_id")
        if not team or not hid:
            continue
        (picks if d.get("pick") else bans)[team].append(hid)
    # 把英雄 id 映射到位置（天辉 0-4，夜魇 5-9，按选取顺序）
    slot_to_hero = {}
    for i, hid in enumerate(picks[RADIANT]):
        slot_to_hero[i] = hid
    for i, hid in enumerate(picks[DIRE]):
        slot_to_hero[5 + i] = hid

    # --- 每个玩家的英雄：优先用 dem 尾部 CDemoFileInfo 的权威对应，缺失时用 ability_uses 反推 ---
    slot_to_token = {}
    for idx, p in enumerate(players):
        auth = (player_names.get(idx) or {}).get("hero_token")
        slot_to_token[idx] = auth or player_hero_token(p, heroes)

    # --- 每玩家汇总 ---
    psummary = []
    for idx, p in enumerate(players):
        gold_t = p.get("gold_t", []) or [0]
        xp_t = p.get("xp_t", []) or [0]
        lh_t = p.get("lh_t", []) or [0]
        dn_t = p.get("dn_t", []) or [0]
        # 只统计英雄击杀（killed 里还包含小兵/野怪）
        killed = p.get("killed", {}) or {}
        kills = sum(v for k, v in killed.items() if str(k).startswith("npc_dota_hero_"))
        deaths = sum((p.get("killed_by", {}) or {}).values())
        damage = sum((p.get("damage", {}) or {}).values())
        healing = sum((p.get("healing", {}) or {}).values())
        gpm = round(gold_t[-1] / duration_min) if duration_min else 0
        xpm = round(xp_t[-1] / duration_min) if duration_min else 0
        psummary.append({
            "slot": idx,
            "team": team_of_slot(idx),
            "player": (player_names.get(idx) or {}).get("name", ""),
            "hero": hero_name(heroes, slot_to_token.get(idx)) or hero_name(heroes, slot_to_hero.get(idx)),
            "hero_id": slot_to_token.get(idx),
            "kills": kills,
            "deaths": deaths,
            "kda": round(kills / max(deaths, 1), 2) if deaths else kills,
            "damage": damage,
            "healing": healing,
            "gold": gold_t[-1],
            "xp": xp_t[-1],
            "lh": lh_t[-1],
            "dn": dn_t[-1],
            "gpm": gpm,
            "xpm": xpm,
            "obs": p.get("obs_placed", 0),
            "sen": p.get("sen_placed", 0),
            "towers": p.get("towers_killed", 0),
            "roshans": p.get("roshans_killed", 0),
            "stuns": p.get("stuns", 0),
            "tf_participation": p.get("teamfight_participation", 0),
        })

    # --- 经济/经验曲线 ---
    gold_adv = blob.get("radiant_gold_adv", [])
    xp_adv = blob.get("radiant_xp_adv", [])
    max_lead = max(gold_adv) if gold_adv else 0
    max_lead_min = gold_adv.index(max_lead) + 1 if gold_adv and max_lead > 0 else 0
    min_lead = min(gold_adv) if gold_adv else 0
    min_lead_min = gold_adv.index(min_lead) + 1 if gold_adv and min_lead < 0 else 0
    # 反转检测：从正到负或从负到正
    reversal = None
    if gold_adv:
        prev = 0 if gold_adv[0] >= 0 else 1
        for i, v in enumerate(gold_adv):
            cur = 0 if v >= 0 else 1
            if cur != prev and i > 0:
                reversal = i + 1
                break
            prev = cur

    # --- 团战 ---
    fights = []
    for tf in blob.get("teamfights", []):
        start = tf.get("start", 0)
        end = tf.get("end") or tf.get("last_death", 0)
        tf_players = tf.get("players", [])
        r_d = d_d = 0
        top_dmg = ("", 0)
        for sidx, tp in enumerate(tf_players):
            if sidx < 5:
                r_d += tp.get("deaths", 0)
            else:
                d_d += tp.get("deaths", 0)
            if tp.get("damage", 0) > top_dmg[1]:
                top_dmg = (hero_name(heroes, slot_to_token.get(sidx)) or hero_name(heroes, slot_to_hero.get(sidx)), tp.get("damage", 0))
        fights.append({
            "start": start,
            "end": end,
            "duration": max(end - start, 0),
            "deaths": tf.get("deaths", 0),
            "radiant_deaths": r_d,
            "dire_deaths": d_d,
            "top_damage": top_dmg,
        })
    fights.sort(key=lambda x: x["start"])

    # --- 眼位 ---
    total_obs = sum(p.get("obs_placed", 0) for p in players)
    total_sen = sum(p.get("sen_placed", 0) for p in players)

    summary = {
        "match_id": match_id,
        "duration_min": duration_min,
        "winner": winner,
        "pauses": len(blob.get("pauses", [])),
        "first_blood": first_blood,
        "roshan_kills": roshan_kills,
        "aegis_events": aegis_events,
        "draft": {"picks": picks, "bans": bans},
        "players": psummary,
        "economy": {
            "final_gold_adv": gold_adv[-1] if gold_adv else 0,
            "max_radiant_lead": max_lead, "max_lead_min": max_lead_min,
            "max_dire_lead": -min_lead, "min_lead_min": min_lead_min,
            "reversal_min": reversal,
        },
        "teamfights": fights,
        "teamfights_count": len(fights),
        "wards": {"total_obs": total_obs, "total_sen": total_sen},
    }
    return summary


# ---------------------------------------------------------------------------
# 6. 报告渲染
# ---------------------------------------------------------------------------
def render_report(summary: dict, match_id, heroes: dict) -> str:
    w = summary["winner"] or "未知"
    L = []
    L.append(f"# Dota 2 复盘分析 — Match {match_id}\n")
    L.append("> 由 odota/parser 解析 .dem 自动生成\n")

    L.append("## 一、比赛概览\n")
    L.append(f"- **比赛时长**：约 {summary['duration_min']} 分钟")
    L.append(f"- **胜利方**：**{w}**")
    L.append(f"- **暂停次数**：{summary['pauses']}")
    L.append(f"- **一血（First Blood）**：{fmt_min(summary['first_blood'])}")
    if summary["roshan_kills"]:
        L.append(f"- **肉山击杀**：第 {', '.join(fmt_min(t) for t in summary['roshan_kills'])} 分钟")
    L.append("")

    L.append("## 二、英雄阵容（BP）\n")
    picks_r = summary['draft']['picks'][RADIANT]
    picks_d = summary['draft']['picks'][DIRE]
    if picks_r or picks_d:
        heroes_fmt = lambda lst: "、".join([hero_name(heroes, h) for h in lst]) or "（数据缺失）"
        L.append(f"- **天辉（ Radiant ）选取**：{heroes_fmt(picks_r)}")
        L.append(f"- **夜魇（ Dire ）选取**：{heroes_fmt(picks_d)}")
        if summary['draft']['bans'][RADIANT] or summary['draft']['bans'][DIRE]:
            L.append(f"- **天辉禁用**：{heroes_fmt(summary['draft']['bans'][RADIANT])}")
            L.append(f"- **夜魇禁用**：{heroes_fmt(summary['draft']['bans'][DIRE])}")
    else:
        # 用 ability_uses 反推的阵容兜底展示
        lineup = []
        for p in summary["players"]:
            pn = f"（玩家：{p['player']}）" if p.get("player") else ""
            lineup.append(f"{p['team']} #{p['slot']} {p['hero']}{pn}")
        L.append("- 本局录像未记录 BP / 选英雄阶段数据（draft_timings 为空）。")
        L.append("- 以下阵容取自录像文件信息 / 对局内技能反推：")
        for line in lineup:
            L.append(f"  - {line}")
    L.append("")

    L.append("## 三、玩家数据总览\n")
    L.append("| # | 队伍 | 玩家 | 英雄 | K | D | 伤害 | 治疗 | GPM | XPM | 正补 | 眼(侦/哨) | 参战率 |")
    L.append("|---|------|------|------|---|---|------|------|-----|-----|------|-----------|--------|")
    for p in summary["players"]:
        pname = (p.get("player") or "—").replace("|", "丨")  # 防止昵称里的竖线破坏表格
        L.append(
            f"| {p['slot']} | {p['team']} | {pname} | {p['hero']} | {p['kills']} | {p['deaths']} | "
            f"{p['damage']:,} | {p['healing']:,} | {p['gpm']} | {p['xpm']} | {p['lh']} | "
            f"{p['obs']}/{p['sen']} | {round(p['tf_participation']*100)}% |"
        )
    L.append("")

    L.append("## 四、经济与经验曲线\n")
    ec = summary["economy"]
    L.append(f"- **最终经济差（天辉视角）**：{ec['final_gold_adv']:,} 金")
    if ec["max_lead_min"]:
        L.append(f"- **天辉最大领先**：{ec['max_radiant_lead']:,} 金 @ 第 {ec['max_lead_min']} 分钟")
    if ec["min_lead_min"]:
        L.append(f"- **夜魇最大领先**：{ec['max_dire_lead']:,} 金 @ 第 {ec['min_lead_min']} 分钟")
    if ec["reversal_min"]:
        L.append(f"- **经济差反转点**：第 {ec['reversal_min']} 分钟（领先方易主）")
    L.append("")

    L.append("## 五、团战分析\n")
    L.append(f"- **团战总数**：{summary['teamfights_count']} 场（≥3 人死亡）")
    top_tf = sorted(summary["teamfights"], key=lambda x: -x["deaths"])[:8]
    top_tf.sort(key=lambda x: x["start"])
    for i, f in enumerate(top_tf, 1):
        L.append(
            f"  {i}. `{fmt_min(f['start'])}` 持续 {f['duration']}s，"
            f"{f['deaths']} 人死亡（天辉 {f['radiant_deaths']} / 夜魇 {f['dire_deaths']}），"
            f"最高伤害：{f['top_damage'][0]}（{f['top_damage'][1]:,}）"
        )
    if not summary["teamfights"]:
        L.append("  （未检测到大规模团战）")
    L.append("")

    L.append("## 六、视野控制\n")
    L.append(f"- **总侦查眼**：{summary['wards']['total_obs']} ｜ **总岗哨眼**：{summary['wards']['total_sen']}")
    L.append("")

    L.append("## 七、AI 复盘建议\n")
    L.append("- 以上为结构化数据自动汇总。可把同目录的 `<match>_summary.json` 喂给 LLM，")
    L.append("  生成自然语言复盘、关键失误检测与针对性教练建议。")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 7. 主流程
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Dota 2 .dem 复盘分析器")
    ap.add_argument("--dem", required=True, help=".dem 文件路径")
    ap.add_argument("--parser-dir", default=None, help="odota/parser 项目目录")
    ap.add_argument("--start-parser", action="store_true", help="自动构建并启动 parser 服务")
    ap.add_argument("--raw-only", action="store_true", help="只解析并保存 raw.json，不生成报告")
    ap.add_argument("--toolchain", default=None, help="JDK/Maven 所在目录（默认自动搜索）")
    args = ap.parse_args()

    parser_dir = locate_parser_dir(args.parser_dir)

    dem = Path(args.dem).resolve()
    if not dem.exists():
        print(f"错误：.dem 文件不存在 {dem}")
        sys.exit(1)

    # match_id 取自文件名（如 8701850772.dem -> 8701850772）
    match_id = match_id_of(dem)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # 启动/复用 parser
    own = ensure_parser(args, parser_dir) if (args.start_parser or not args.raw_only) else False

    try:
        blob = parse_dem(dem)
    finally:
        pass

    # 保存原始 Blob
    raw_path = REPORT_DIR / f"{match_id}.raw.json"
    raw_path.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
    print(f"[output] 原始 Blob → {raw_path}")

    if args.raw_only:
        print("raw-only 完成。")
        return

    heroes = load_heroes()
    player_names = load_player_names(dem, match_id)
    summary = compute(blob, match_id, heroes, player_names)

    md_path = REPORT_DIR / f"{match_id}_analysis.md"
    md_path.write_text(render_report(summary, match_id, heroes), encoding="utf-8")
    json_path = REPORT_DIR / f"{match_id}_summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[output] 复盘报告 → {md_path}")
    print(f"[output] 机器摘要 → {json_path}")


if __name__ == "__main__":
    main()
