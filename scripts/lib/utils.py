# -*- coding: utf-8 -*-
"""lib/utils.py — 共享工具函数（消除跨脚本重复代码）。

所有 Dota 2 复盘分析脚本的公共函数集中于此：
- find_java / find_mvn: Java/Maven 运行时探测
- fmt_min: 秒 → M:SS 时间格式化
- team_of_slot: 玩家 slot → 阵营名
- player_hero_token: 从 ability_uses 反推英雄 token
- detached_kwargs: 跨平台独立进程启动参数
- locate_parser_dir: 定位 odota/parser 项目目录
- match_id_of: 从文件名提取 match id
- load_heroes / load_heroes_cn / hero_name: 英雄名映射

用法:
    from lib.utils import fmt_min, team_of_slot, find_java
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent          # lib/
ROOT = HERE.parent                               # dem-analyzer/
REPORT_DIR = ROOT / "reports"
HERO_CACHE = ROOT / "heroes.json"
HERO_CN = ROOT / "heroes_cn.json"               # token -> 中文名（静态表）

RADIANT = "天辉"
DIRE = "夜魇"

# ---------------------------------------------------------------------------
# 时间格式化
# ---------------------------------------------------------------------------
def fmt_min(sec):
    """秒 → 'M:SS' 格式。None → '—'。支持负数（如经济差）。"""
    if sec is None:
        return "—"
    sign = "-" if sec < 0 else ""
    sec = abs(int(sec))
    return f"{sign}{sec // 60}:{sec % 60:02d}"


# ---------------------------------------------------------------------------
# 阵营判定
# ---------------------------------------------------------------------------
def team_of_slot(idx):
    """slot 0-4 = 天辉，5-9 = 夜魇。"""
    return RADIANT if idx < 5 else DIRE


def is_radiant(idx):
    """slot 0-4 → True（天辉），5-9 → False（夜魇）。"""
    return idx < 5


# ---------------------------------------------------------------------------
# 英雄 token 反推（odota/parser Blob 不导出 hero_id）
# ---------------------------------------------------------------------------
def player_hero_token(p, hero_tokens):
    """从 ability_uses 的技能名前缀反推英雄 token。

    hero_tokens 可以是 list[str]（token 列表）或 dict（heroes 映射，自动提取非数字键）。
    使用最长前缀匹配避免歧义（如 ogre_magi vs ogre_magi_smash）。
    """
    if isinstance(hero_tokens, dict):
        hero_tokens = [k for k in hero_tokens if not k.isdigit()]
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


# ---------------------------------------------------------------------------
# 英雄名映射
# ---------------------------------------------------------------------------
def load_heroes():
    """返回 {hero_id(str): 名称, hero_token: 名称} 的双键映射。
    优先读本地缓存 heroes.json，缓存不存在时从网络拉取。
    """
    if HERO_CACHE.exists():
        try:
            return json.loads(HERO_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    import urllib.request
    sources = [
        "https://raw.githubusercontent.com/odota/dotaconstants/master/build/heroes.json",
        "https://api.opendota.com/api/heroes",
    ]
    for url in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                arr = json.loads(r.read().decode("utf-8"))
            items = arr.values() if isinstance(arr, dict) else arr
            mapping = {}
            for h in items:
                hid = str(h.get("id"))
                name = h.get("localized_name") or h.get("name")
                token = (h.get("name") or "").replace("npc_dota_hero_", "")
                if hid:
                    mapping[hid] = name
                if token:
                    mapping[token] = name
            HERO_CACHE.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[heroes] 已缓存 {len(mapping)//2} 个英雄名 → {HERO_CACHE.name}")
            return mapping
        except Exception as e:
            print(f"[heroes] 来源 {url} 失败：{e}")
    print("[heroes] 全部来源失败（报告将只显示 hero token）")
    return {}


def load_heroes_cn():
    """加载 token -> 中文名 静态映射（heroes_cn.json，离线可用）。"""
    try:
        return json.loads(HERO_CN.read_text(encoding="utf-8"))
    except Exception:
        return {}


_HEROES_CN = None


def hero_name(heroes, hid):
    """返回「中文名（English Name）」双语英雄名；缺中文时只显示英文。"""
    global _HEROES_CN
    if hid is None:
        return "未知"
    if _HEROES_CN is None:
        _HEROES_CN = load_heroes_cn()
    en = heroes.get(str(hid), f"英雄#{hid}")
    token = str(hid)
    if token.isdigit():
        for k, v in heroes.items():
            if not k.isdigit() and v == en:
                token = k
                break
    cn = _HEROES_CN.get(token)
    return f"{cn}（{en}）" if cn and cn != en else en


# ---------------------------------------------------------------------------
# Java / Maven 探测
# ---------------------------------------------------------------------------
def find_java():
    """返回 java 可执行文件路径，找不到返回 None。
    搜索顺序：JAVA_HOME/JDK_HOME → 系统 PATH → toolchain 目录兜底。
    """
    for envvar in ("JAVA_HOME", "JDK_HOME"):
        v = os.environ.get(envvar)
        if v:
            p = os.path.join(v, "bin", "java")
            if os.path.exists(p):
                return p
    found = shutil.which("java")
    if found:
        return found
    project_root = os.path.dirname(ROOT)
    for base in [str(ROOT / "toolchain"),
                 os.path.join(project_root, "toolchain"),
                 os.path.join(os.path.dirname(project_root), "toolchain")]:
        if os.path.isdir(base):
            for sub in sorted(os.listdir(base)):
                for exe in ("java.exe", "java"):
                    cand = os.path.join(base, sub, "bin", exe)
                    if os.path.isfile(cand):
                        return cand
    return None


def find_java_home():
    """返回 (java_home, java_path)，找不到返回 (None, None)。"""
    java = find_java()
    if not java:
        return None, None
    java_home = os.path.dirname(os.path.dirname(java))
    return java_home, java


def find_mvn():
    """返回 Maven 可执行文件路径，找不到返回 None。"""
    for envvar in ("MAVEN_HOME", "M2_HOME"):
        v = os.environ.get(envvar)
        if v:
            for exe in ("mvn.cmd", "mvn"):
                p = os.path.join(v, "bin", exe)
                if os.path.exists(p):
                    return p
            return os.path.join(v, "bin", "mvn")
    m = shutil.which("mvn.cmd") or shutil.which("mvn")
    if m:
        return m
    project_root = os.path.dirname(ROOT)
    for base in [str(ROOT / "toolchain"),
                 os.path.join(project_root, "toolchain"),
                 os.path.join(os.path.dirname(project_root), "toolchain")]:
        if os.path.isdir(base):
            for sub in sorted(os.listdir(base)):
                for exe in ("mvn.cmd", "mvn"):
                    cand = os.path.join(base, sub, "bin", exe)
                    if os.path.isfile(cand):
                        return cand
    return None


# ---------------------------------------------------------------------------
# 跨平台独立进程启动
# ---------------------------------------------------------------------------
def detached_kwargs(cwd, env, logf):
    """构造 subprocess.Popen 的 kwargs，使进程在父进程退出后独立存活。"""
    import subprocess
    kwargs = dict(cwd=cwd, env=env, stdout=logf, stderr=subprocess.STDOUT)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


# ---------------------------------------------------------------------------
# parser 目录定位
# ---------------------------------------------------------------------------
def locate_parser_dir(parser_dir=None):
    """定位 odota/parser 项目目录。优先级：参数 > env > 常见路径。"""
    if parser_dir:
        return Path(parser_dir).resolve()
    env = os.environ.get("DOTA2_PARSER_DIR")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates += [
        ROOT.parent / "parser",
        ROOT.parent / "odota-parser",
        ROOT / "parser",
        ROOT / "odota-parser",
    ]
    for c in candidates:
        if c and c.exists():
            return c.resolve()
    return None


# ---------------------------------------------------------------------------
# match id 提取
# ---------------------------------------------------------------------------
def match_id_of(dem_path):
    """从文件名提取 match id（如 8701850772.dem → '8701850772'）。"""
    stem = Path(dem_path).stem if not isinstance(dem_path, Path) else dem_path.stem
    m = re.search(r"(\d{6,})", stem)
    return m.group(1) if m else stem
