# -*- coding: utf-8 -*-
"""coach.py — AI 自动教练（分析层的智能输出）。

两级引擎：
    1. 规则引擎（内置，离线可用）：从 summary.json 提取战术信号，生成结构化教练建议
    2. LLM 增强（可选）：设置环境变量后，把结构化摘要喂给 OpenAI 兼容 API 生成自然语言深度复盘
       - LLM_API_KEY    : API Key（必需，否则只用规则引擎）
       - LLM_BASE_URL   : 默认 https://api.openai.com/v1（可指向任何兼容端点，如 deepseek/qwen）
       - LLM_MODEL      : 默认 gpt-4o-mini

输出:
    reports/<match>_coach.json  — 结构化建议（Web UI 数据源）
    reports/<match>_coach.md    — 可读版教练报告

用法:
    python coach.py --match 8701850772
"""
import argparse
import json
import os
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"
RADIANT, DIRE = "天辉", "夜魇"

# 关键装备时效基准（核心位常见出装路线的黄金窗口期，以秒计）
ITEM_BENCHMARKS = {
    "blink":           720,   # 跳刀 12 分钟
    "battle_fury":     840,   # 狂战 14 分钟（含草鞋）
    "bfury":           840,   # 狂战（别名）
    "radiance":        1020,  # 辉耀 17 分钟
    "black_king_bar":  1320,  # BKB 22 分钟
    "hand_of_midas":   480,   # 点金 8 分钟
    "desolator":       840,   # 黯灭 14 分钟
    "maelstrom":       720,   # 电锤 12 分钟
    "diffusal_blade":  720,   # 散失 12 分钟
    "manta":           1200,  # 分身 20 分钟
    "orchid":          960,   # 紫苑 16 分钟
    "aghanims_shard":  900,   # A杖碎片 15 分钟（白嫖/购买）
    "ultimate_scepter":1500,  # A杖 25 分钟
    "butterfly":       1800,  # 蝴蝶 30 分钟
    "satanic":         1980,  # 撒旦 33 分钟
    "abyssal_blade":   2100,  # 大晕锤 35 分钟
    "assault":         1920,  # 强袭 32 分钟
}


def check_item_timing(p, duration_min):
    """对比关键装备购买时间与元游戏基准，生成建议。返回 (highlights, advices)。"""
    hl, ad = [], []
    key_items = p.get("key_items") or []
    for ki in key_items:
        item = ki.get("item") or ki.get("name") or ""
        purchase_time = ki.get("time", 0)
        if not item or item not in ITEM_BENCHMARKS:
            continue
        benchmark = ITEM_BENCHMARKS[item]
        if purchase_time <= benchmark:
            hl.append(f"{item} {fmt_min(purchase_time)} 准时（基准{fmt_min(benchmark)}）")
        elif purchase_time > benchmark * 1.5:
            ad.append(f"{item} {fmt_min(purchase_time)} 过晚（基准{fmt_min(benchmark)}）——节奏严重落后，需检视打钱路线和团战收益")
        elif purchase_time > benchmark:
            ad.append(f"{item} {fmt_min(purchase_time)} 稍晚（基准{fmt_min(benchmark)}）——可优化前期发育节奏")
    return hl, ad


def fmt_min(sec):
    return f"{int(sec)//60}:{int(sec)%60:02d}"


# ---------------------------------------------------------------- 规则引擎

def grade_player(p, duration_min, team_won):
    """给单个玩家打分并生成建议。返回 (grade, highlights, advices)。"""
    highlights, advices = [], []
    score = 50.0

    # 位置粗判：依据 obs+sen 与 lh
    is_support = (p["obs"] + p["sen"]) >= 5 or (p["lh"] < duration_min * 2 and p["gpm"] < 350)

    # KDA 信号
    if p["kills"] >= 10:
        highlights.append(f"击杀 {p['kills']} 次，输出核心表现")
        score += 12
    if p["deaths"] >= 8:
        advices.append(f"死亡 {p['deaths']} 次过多——平均每 {duration_min // max(p['deaths'],1)} 分钟阵亡一次，注意地图信息与站位，减少无视野游走")
        score -= 14
    elif p["deaths"] <= 2 and duration_min >= 20:
        highlights.append(f"仅死亡 {p['deaths']} 次，生存意识优秀")
        score += 8
    if p["kda"] >= 4:
        score += 8
    elif p["kda"] < 0.7 and p["deaths"] >= 5:
        advices.append(f"KDA 仅 {p['kda']}（{p['kills']}杀/{p['deaths']}死）——参战收益为负，优先在有视野、有队友跟进时才接团，避免单带被抓")
        score -= 8

    # 发育信号（核心位）
    lh_per_min = p["lh"] / max(duration_min, 1)
    if not is_support:
        if lh_per_min >= 6:
            highlights.append(f"正补 {p['lh']}（{lh_per_min:.1f}/分钟），打钱效率高")
            score += 8
        elif lh_per_min < 3.5:
            advices.append(f"正补仅 {p['lh']}（{lh_per_min:.1f}/分钟），核心位应保证 5+/分钟——练习卡兵与补刀节奏，空窗期多刷野")
            score -= 10
        if p["dn"] == 0 and duration_min >= 15:
            advices.append("全场 0 反补——对线期反补能直接压制对手经验，建议刻意练习")
            score -= 3

    # 视野信号（辅助位）
    if is_support:
        wpm = (p["obs"] + p["sen"]) / max(duration_min, 1)
        if wpm >= 0.6:
            highlights.append(f"插眼 {p['obs']} 侦 + {p['sen']} 哨，视野贡献突出")
            score += 10
        elif (p["obs"] + p["sen"]) <= 2:
            advices.append(f"全场仅 {p['obs']} 侦 / {p['sen']} 哨——辅助位视野是第一职责，目标每 2 分钟至少 1 个眼位")
            score -= 8

    # 团战信号
    tf = p.get("tf_participation") or 0
    if tf >= 0.65:
        highlights.append(f"团战参与率 {tf:.0%}，节奏跟进积极")
        score += 6
    elif tf < 0.4 and duration_min >= 20:
        advices.append(f"团战参与率仅 {tf:.0%}——注意 TP 支援时机与地图动向，别错过关键团")
        score -= 6

    # 控制信号
    if p["stuns"] >= 30:
        highlights.append(f"累计控制 {p['stuns']:.0f} 秒，先手/反手价值高")
        score += 5

    # 推进
    if p["towers"] >= 3:
        highlights.append(f"拆塔 {p['towers']} 座，推进转化出色")
        score += 5

    # 物品时效基准（对标元游戏黄金窗口）
    ih, ia = check_item_timing(p, duration_min)
    highlights += ih; advices += ia
    score += len(ih) * 3 - len(ia) * 4

    if team_won:
        score += 5

    score = max(0, min(100, score))
    grade = ("S" if score >= 85 else "A" if score >= 70 else
             "B" if score >= 55 else "C" if score >= 40 else "D")
    if not advices:
        advices.append("整体表现均衡，保持当前思路即可")
    return grade, round(score), highlights, advices


def team_analysis(s):
    """全队层面的战术分析。"""
    econ = s.get("economy", {})
    tfs = s.get("teamfights", [])
    players = s.get("players", [])
    winner = s.get("winner")
    duration = s.get("duration_min", 0)
    notes = []

    # 经济走势
    max_lead = econ.get("max_radiant_lead", 0)
    reversal = econ.get("reversal_min")
    if reversal:
        notes.append(f"第 {reversal} 分钟出现经济反转——注意顺风局的决策纪律：领先时避免无意义浪，落后时抓敌方核心打钱空档开雾。")
    elif max_lead and winner == RADIANT:
        notes.append(f"天辉全程滚雪球（峰值 +{max_lead:,} 金），夜魇没有等到翻盘窗口。落后方应更早认清局势：要么 15 分钟前抱团抓单打出节奏，要么收缩守高换时间。")

    # 团战胜率
    r_win = sum(1 for f in tfs if f["dire_deaths"] > f["radiant_deaths"])
    d_win = sum(1 for f in tfs if f["radiant_deaths"] > f["dire_deaths"])
    if tfs:
        notes.append(f"团战战绩：天辉 {r_win} 胜 / 夜魇 {d_win} 胜 / {len(tfs)-r_win-d_win} 平。")
        worst = max(tfs, key=lambda f: abs(f["radiant_deaths"] - f["dire_deaths"]))
        loser = DIRE if worst["dire_deaths"] > worst["radiant_deaths"] else RADIANT
        notes.append(
            f"最一边倒的团发生在 {fmt_min(worst['start'])}（{loser}阵亡 "
            f"{max(worst['radiant_deaths'], worst['dire_deaths'])} 人）——复盘这波开团前的视野布置与技能交换顺序。")

    # 视野对比
    r_ward = sum(p["obs"] + p["sen"] for p in players if p["team"] == RADIANT)
    d_ward = sum(p["obs"] + p["sen"] for p in players if p["team"] == DIRE)
    if r_ward and d_ward and max(r_ward, d_ward) >= min(r_ward, d_ward) * 2:
        rich, poor = (RADIANT, DIRE) if r_ward > d_ward else (DIRE, RADIANT)
        notes.append(f"视野差距悬殊（{RADIANT} {r_ward} vs {DIRE} {d_ward}）——{poor}的辅助需要大幅提高做眼频率，视野劣势会放大一切决策错误。")

    # 一血
    fb = s.get("first_blood")
    if fb is not None:
        notes.append(f"一血发生在 {fmt_min(fb)}，{'开局节奏极快，前期对抗激烈' if fb < 120 else '前期相对平稳'}。")
    return notes


def rule_coach(s):
    duration = s.get("duration_min", 0)
    winner = s.get("winner")
    players = s.get("players", [])

    # 超短局（<5 分钟：放弃/秒退）：跳过评分，避免失真
    if duration < 5:
        return {
            "match_id": s.get("match_id"),
            "engine": "rule",
            "winner": winner,
            "duration_min": duration,
            "team_notes": ["比赛过早结束（<5 分钟），无法做有意义的评分与分析。"],
            "players": [{
                "slot": p["slot"], "team": p["team"], "hero": p["hero"],
                "player": p.get("player", ""), "grade": "—", "score": 0,
                "highlights": [], "advices": ["比赛过早结束"],
                "kda": f"{p['kills']}/{p['deaths']}", "gpm": p["gpm"], "xpm": p["xpm"],
            } for p in players],
            "mvp": None, "needs_improvement": None,
        }

    result = {
        "match_id": s.get("match_id"),
        "engine": "rule",
        "winner": winner,
        "duration_min": duration,
        "team_notes": team_analysis(s),
        "players": [],
    }
    for p in players:
        grade, score, highlights, advices = grade_player(p, duration, p["team"] == winner)
        result["players"].append({
            "slot": p["slot"], "team": p["team"], "hero": p["hero"], "player": p.get("player", ""),
            "grade": grade, "score": score,
            "highlights": highlights, "advices": advices,
            "kda": f"{p['kills']}/{p['deaths']}", "gpm": p["gpm"], "xpm": p["xpm"],
        })
    # 按队伍分别评选 MVP 和最需改进（而非全局最高/最低分）
    r_players = [x for x in result["players"] if x["team"] == RADIANT]
    d_players = [x for x in result["players"] if x["team"] == DIRE]
    r_mvp = max(r_players, key=lambda x: x["score"]) if r_players else None
    d_mvp = max(d_players, key=lambda x: x["score"]) if d_players else None
    r_worst = min(r_players, key=lambda x: x["score"]) if r_players else None
    d_worst = min(d_players, key=lambda x: x["score"]) if d_players else None
    result["mvp"] = {"radiant": r_mvp, "dire": d_mvp}
    result["needs_improvement"] = {"radiant": r_worst, "dire": d_worst}
    return result


# ---------------------------------------------------------------- LLM 增强

def llm_coach(summary, rule_result):
    """可选：调用 OpenAI 兼容 API 生成自然语言深度复盘。失败返回 None。"""
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return None
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    prompt = (
        "你是一名职业 Dota 2 教练。基于以下比赛结构化数据（JSON），写一篇 500 字左右的中文复盘：\n"
        "1) 胜负手是什么 2) 双方最关键的 2-3 个转折点 3) 给表现最差的两名玩家各一条具体可执行的改进建议。\n"
        "语气专业、直接，不要客套。\n\n比赛数据：\n" + json.dumps(summary, ensure_ascii=False)
    )
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        print(f"[coach] LLM（{model}）复盘生成成功")
        return text
    except Exception as e:
        print(f"[coach] LLM 调用失败，回退规则引擎：{e}")
        return None


# ---------------------------------------------------------------- 报告渲染

def render_md(r, llm_text):
    L = [f"# AI 教练报告 — Match {r['match_id']}", ""]
    mvp = r.get("mvp")
    ni = r.get("needs_improvement")
    if mvp and ni:
        # 新格式（按队伍 MVP）
        r_mvp = mvp.get("radiant")
        d_mvp = mvp.get("dire")
        mvp_line = []
        if r_mvp: mvp_line.append(f"天辉 MVP：{r_mvp['hero']}({r_mvp['score']}分)")
        if d_mvp: mvp_line.append(f"夜魇 MVP：{d_mvp['hero']}({d_mvp['score']}分)")
        L.append(f"> **{r['winner']} 获胜** · 时长 {r['duration_min']} 分钟 · "
                 f"{' · '.join(mvp_line)}")
    else:
        L.append(f"> **{r.get('winner','?')}** · 时长 {r['duration_min']} 分钟")
    L.append("")
    L.append("## 全队战术分析\n")
    for n in r["team_notes"]:
        L.append(f"- {n}")
    L.append("\n## 玩家评级与建议\n")
    L.append("| 评级 | 玩家 | 英雄 | 阵营 | KDA | GPM | 亮点 | 改进建议 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for p in sorted(r["players"], key=lambda x: -x["score"]):
        hl = "；".join(p["highlights"]) or "—"
        ad = "；".join(p["advices"])
        pname = (p.get("player") or "—").replace("|", "丨")
        L.append(f"| **{p['grade']}**({p['score']}) | {pname} | {p['hero']} | {p['team']} | {p['kda']} | {p['gpm']} | {hl} | {ad} |")
    if llm_text:
        L.append("\n## LLM 深度复盘\n")
        L.append(llm_text)
    L.append("\n---\n*由 dem-analyzer AI 教练生成（规则引擎" + ("+LLM" if llm_text else "") + "）*")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="AI 自动教练")
    ap.add_argument("--match", required=True, help="match id（对应 reports/<id>_summary.json）")
    args = ap.parse_args()

    summary_file = REPORTS / f"{args.match}_summary.json"
    if not summary_file.exists():
        print(f"[coach] 找不到 {summary_file}，请先运行 analyze.py")
        return 1
    s = json.loads(summary_file.read_text(encoding="utf-8"))

    # 装备时效基准对比需要 deep.json 里的 key_items（含购买时间）。
    # coach 默认只读 summary.json，后者不含该字段——这里把 deep 的关键装备
    # 按 slot 合并进 summary 的玩家数据，让 check_item_timing 真正生效。
    deep_file = REPORTS / f"{args.match}_deep.json"
    if deep_file.exists():
        try:
            d = json.loads(deep_file.read_text(encoding="utf-8"))
            slot_items = {}
            for dp in d.get("players", []):
                slot_items[dp.get("slot")] = dp.get("key_items") or []
            for p in s.get("players", []):
                if p.get("slot") in slot_items:
                    p["key_items"] = slot_items[p["slot"]]
        except Exception as e:
            print(f"[coach] 合并 deep.json 装备时间失败（跳过基准对比）：{e}")
    else:
        print(f"[coach] 未找到 {deep_file}，装备时效基准对比将不生效（请先运行 deep_extract.py）")

    r = rule_coach(s)
    llm_text = llm_coach(s, r)
    if llm_text:
        r["engine"] = "rule+llm"
        r["llm_recap"] = llm_text

    (REPORTS / f"{args.match}_coach.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS / f"{args.match}_coach.md").write_text(
        render_md(r, llm_text), encoding="utf-8")
    print(f"[coach] 教练报告已生成：{args.match}_coach.json / {args.match}_coach.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
