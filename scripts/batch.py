# -*- coding: utf-8 -*-
"""batch.py — 批量分析目录下的所有 .dem 录像（数据采集层 + 解析层调度）。

用法:
    python batch.py --dir "/path/to/your/replays" [--dir 其他目录 ...] [--force] [--no-coach]

流程:
    1. 扫描目录下所有 *.dem
    2. 对每个未解析过的录像调用 analyze.py（复用 5600 端口的 parser 服务）
    3. 对每个已有 summary 的比赛调用 coach.py 生成 AI 教练报告
    4. 汇总生成 reports/index.json 比赛索引（Web UI 的数据源）
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"


def match_id_of(dem: Path) -> str:
    m = re.search(r"(\d{6,})", dem.stem)
    return m.group(1) if m else dem.stem


def run_script(script: str, *args) -> int:
    """用当前 Python 解释器运行子脚本（列表传参，天然免疫中文路径问题）。"""
    cmd = [sys.executable, str(HERE / script), *args]
    proc = subprocess.run(cmd, cwd=str(HERE))
    return proc.returncode


def build_index() -> dict:
    """扫描 reports/ 下全部 *_summary.json，生成 index.json。"""
    items = []
    for f in sorted(REPORTS.glob("*_summary.json")):
        mid = f.name.replace("_summary.json", "")
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        players = s.get("players", [])
        radiant_kills = sum(p["kills"] for p in players if p["team"] == "天辉")
        dire_kills = sum(p["kills"] for p in players if p["team"] == "夜魇")
        mvp = max(players, key=lambda p: (p["kda"], p["damage"])) if players else None
        items.append({
            "match_id": mid,
            "winner": s.get("winner"),
            "duration_min": s.get("duration_min"),
            "score": f"{radiant_kills}:{dire_kills}",
            "radiant_heroes": [p["hero"] for p in players if p["team"] == "天辉"],
            "dire_heroes": [p["hero"] for p in players if p["team"] == "夜魇"],
            "mvp": mvp["hero"] if mvp else None,
            "teamfights": s.get("teamfights_count", 0),
            "has_coach": (REPORTS / f"{mid}_coach.json").exists(),
            "mtime": f.stat().st_mtime,
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    index = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(items), "matches": items}
    (REPORTS / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def main():
    ap = argparse.ArgumentParser(description="批量分析 .dem 录像")
    ap.add_argument("--dir", action="append", default=[], help="包含 .dem 的目录（可多次）")
    ap.add_argument("--force", action="store_true", help="已解析过的也重新解析")
    ap.add_argument("--no-coach", action="store_true", help="跳过 AI 教练报告生成")
    ap.add_argument("--index-only", action="store_true", help="只重建索引，不解析")
    args = ap.parse_args()

    REPORTS.mkdir(exist_ok=True)

    if not args.index_only:
        dems = []
        for d in args.dir or []:
            p = Path(d)
            if p.is_dir():
                dems += sorted(p.glob("*.dem"))
            elif p.suffix == ".dem" and p.exists():
                dems.append(p)
        print(f"[batch] 发现 {len(dems)} 个 .dem 文件")

        ok, skip, fail = 0, 0, 0
        for dem in dems:
            mid = match_id_of(dem)
            summary = REPORTS / f"{mid}_summary.json"
            if summary.exists() and not args.force:
                print(f"[batch] 跳过（已解析）: {dem.name}")
                skip += 1
            else:
                print(f"[batch] 解析: {dem.name} …")
                t0 = time.time()
                rc = run_script("analyze.py", "--dem", str(dem))
                if rc == 0 and summary.exists():
                    print(f"[batch] 完成 {mid}（{time.time()-t0:.1f}s）")
                    ok += 1
                else:
                    print(f"[batch] 失败 {dem.name}（rc={rc}）")
                    fail += 1
                    continue
            if not args.no_coach:
                coach_file = REPORTS / f"{mid}_coach.json"
                if not coach_file.exists() or args.force:
                    run_script("coach.py", "--match", mid)

        print(f"[batch] 解析 {ok} 成功 / {skip} 跳过 / {fail} 失败")

    index = build_index()
    print(f"[batch] 索引已更新：{index['count']} 场比赛 → reports/index.json")


if __name__ == "__main__":
    main()
