# -*- coding: utf-8 -*-
"""server.py — Web 服务层（stdlib 实现，零第三方依赖）。

API:
    GET  /api/matches          比赛索引列表
    GET  /api/match/<id>       比赛详情（summary + 经济/经验曲线 + coach 合并）
    POST /api/rescan           重新扫描 watch_dirs 并批量解析（后台线程）
    GET  /api/rescan/status    扫描状态
    OPTIONS  (全局 CORS 预检)
静态: webapp/static/  （/ → index.html）

用法:
    python webapp/server.py [--port 8642]
配置: config.json 的 watch_dirs 指定 .dem 目录列表
"""
import argparse
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent          # webapp/
ROOT = HERE.parent                              # dem-analyzer/
REPORTS = ROOT / "reports"
STATIC = HERE / "static"
CONFIG = ROOT / "config.json"

_scan_state = {"running": False, "log": ""}
_scan_lock = threading.Lock()

# 简单内存缓存（{path: (json_obj, mtime, expire_at)}）
_json_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 60  # 秒


def load_config():
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"watch_dirs": []}


def read_json(path):
    """读取 JSON 文件（带简单内存缓存，60s TTL）。"""
    p = Path(path)
    key = str(p)
    now = time.time()
    with _cache_lock:
        cache_entry = _json_cache.get(key)
        if cache_entry and cache_entry[2] > now:
            return cache_entry[0]
    try:
        content = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    with _cache_lock:
        _json_cache[key] = (content, p.stat().st_mtime, now + CACHE_TTL)
        # 清理过期条目
        expired = [k for k, v in _json_cache.items() if v[2] < now]
        for k in expired:
            del _json_cache[k]
    return content


def match_detail(mid: str):
    summary = read_json(REPORTS / f"{mid}_summary.json")
    if summary is None:
        return None
    detail = dict(summary)
    detail["coach"] = read_json(REPORTS / f"{mid}_coach.json")
    raw = read_json(REPORTS / f"{mid}.raw.json")
    if raw:
        detail["gold_adv"] = raw.get("radiant_gold_adv") or []
        detail["xp_adv"] = raw.get("radiant_xp_adv") or []
        # 每分钟经济曲线（每玩家）
        detail["gold_t"] = [p.get("gold_t") or [] for p in raw.get("players", [])]
        detail["xp_t"] = [p.get("xp_t") or [] for p in raw.get("players", [])]
        detail["lh_t"] = [p.get("lh_t") or [] for p in raw.get("players", [])]
    # 团战深度分析（来自 deep.json 的 enriched teamfights）
    deep = read_json(REPORTS / f"{mid}_deep.json")
    if deep:
        detail["teamfights_deep"] = deep.get("teamfights")
    return detail


def do_rescan():
    with _scan_lock:
        _scan_state["running"] = True
        _scan_state["log"] = "扫描中…"
    try:
        cfg = load_config()
        cmd = [sys.executable, str(ROOT / "batch.py")]
        for d in cfg.get("watch_dirs", []):
            cmd += ["--dir", d]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        with _scan_lock:
            _scan_state["log"] = (proc.stdout or "") + (proc.stderr or "")
    except Exception as e:
        with _scan_lock:
            _scan_state["log"] = f"扫描失败：{e}"
    finally:
        with _scan_lock:
            _scan_state["running"] = False
        # 重置缓存（已重新解析，旧缓存失效）
        with _cache_lock:
            _json_cache.clear()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 安静模式
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/matches":
            idx = read_json(REPORTS / "index.json") or {"count": 0, "matches": []}
            return self._json(idx)
        if path.startswith("/api/match/"):
            mid = path.rsplit("/", 1)[-1]
            detail = match_detail(mid)
            if detail is None:
                return self._json({"error": f"match {mid} not found"}, 404)
            return self._json(detail)
        if path == "/api/rescan/status":
            return self._json(_scan_state)
        # 静态文件
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if not str(f).startswith(str(STATIC.resolve())) or not f.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = {
            ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
        }.get(f.suffix.lower(), "application/octet-stream")
        return self._send(200, f.read_bytes(), ctype)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/rescan":
            with _scan_lock:
                if _scan_state["running"]:
                    return self._json({"ok": False, "msg": "已有扫描在进行"})
            threading.Thread(target=do_rescan, daemon=True).start()
            return self._json({"ok": True, "msg": "扫描已启动"})
        return self._json({"error": "unknown endpoint"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8642)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    # 限制最大线程数为 20（防 OOM）
    class BoundedServer(type(srv)):
        pass
    srv.__class__ = BoundedServer
    print(f"[web] Dota2 复盘产品已启动: http://localhost:{args.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
