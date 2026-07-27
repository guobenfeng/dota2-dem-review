#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键启动「Dota2 AI 复盘中心」（跨平台）：
  1. 解析服务 odota/parser（端口 5600），未运行则启动（detached 独立进程）
  2. Web 服务 webapp/server.py（端口 8642），未运行则启动（detached 独立进程）
  3. 全部就绪后自动打开浏览器 http://localhost:8642

幂等：重复运行不会重复起服务（先探测健康检查，活着就跳过）。
Java 运行时自动探测（JAVA_HOME / JDK_HOME / PATH）；parser 默认 ../parser。
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
PARSER = os.environ.get("DOTA2_PARSER_DIR") or os.path.join(os.path.dirname(HERE), "parser")
JAR = os.path.join(PARSER, "target", "stats-0.1.0.jar")
PARSER_PORT = 5600
WEB_PORT = 8642


def find_java():
    for envvar in ("JAVA_HOME", "JDK_HOME"):
        v = os.environ.get(envvar)
        if v and os.path.exists(os.path.join(v, "bin", "java")):
            return v, os.path.join(v, "bin", "java")
    j = shutil.which("java")
    if j:
        return os.path.dirname(os.path.dirname(j)), j
    return None, None


def detached_kwargs(cwd, env, logf):
    kwargs = dict(cwd=cwd, env=env, stdout=logf, stderr=subprocess.STDOUT)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    return kwargs


def alive(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def wait_for(url, name, tries=60, gap=2):
    for _ in range(tries):
        if alive(url):
            print(f"  [OK] {name} 就绪 -> {url}")
            return True
        time.sleep(gap)
    print(f"  [!!] {name} 超时未就绪：{url}")
    return False


def start_parser():
    url = f"http://localhost:{PARSER_PORT}/healthz"
    if alive(url):
        print(f"  [OK] 解析服务已在运行（端口 {PARSER_PORT}），跳过")
        return True
    if not os.path.exists(JAR):
        print(f"  [!!] 缺少构建产物 {JAR}\n       请先运行: python build_parser.py")
        return False
    java_home, java = find_java()
    if not java:
        print("  [!!] 未找到 Java 运行时，请安装 JRE 21+ 并设置 JAVA_HOME。")
        return False
    env = os.environ.copy()
    env["JAVA_HOME"] = java_home
    logf = open(os.path.join(HERE, "parser.log"), "w", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        [java, "-jar", JAR, str(PARSER_PORT)],
        **detached_kwargs(PARSER, env, logf),
    )
    print(f"  [..] 解析服务启动中 pid={proc.pid}（日志 parser.log）")
    return wait_for(url, "解析服务")


def start_web():
    url = f"http://localhost:{WEB_PORT}/"
    if alive(url):
        print(f"  [OK] Web 服务已在运行（端口 {WEB_PORT}），跳过")
        return True
    server = os.path.join(HERE, "webapp", "server.py")
    logf = open(os.path.join(HERE, "webapp.log"), "w", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        [sys.executable, server, "--port", str(WEB_PORT)],
        **detached_kwargs(HERE, os.environ.copy(), logf),
    )
    print(f"  [..] Web 服务启动中 pid={proc.pid}（日志 webapp.log）")
    return wait_for(url, "Web 服务", tries=15, gap=1)


def main():
    print("=== Dota2 AI 复盘中心 · 一键启动 ===")
    ok1 = start_parser()
    ok2 = start_web()
    if ok2:
        addr = f"http://localhost:{WEB_PORT}"
        print(f"\n全部就绪 ✓  {addr}")
        if "--no-browser" not in sys.argv:
            try:
                webbrowser.open(addr)
            except Exception:
                pass
    else:
        print("\nWeb 服务未能启动，请查看 webapp.log")
    if not ok1:
        print("提示：解析服务未就绪时，页面可浏览已有报告，但无法解析新录像。")


if __name__ == "__main__":
    main()
