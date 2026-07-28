#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动 odota/parser 解析服务（端口 5600），以独立(detached)进程运行，
使其在调用方退出后依然存活。

跨平台设计：
- Java 运行时自动探测：环境变量 JAVA_HOME / JDK_HOME > 系统 PATH 中的 java
- parser 目录默认指向 ../odota-parser（或环境变量 DOTA2_PARSER_DIR 覆盖）
- 进程独立化：Windows 用 creationflags，Linux/macOS 用 start_new_session
只需目标机器装有 JRE 21+（无需 JDK / Maven）。
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# parser 目录：优先 env 变量，其次 ../odota-parser，最后 ../parser
PARSER = os.environ.get("DOTA2_PARSER_DIR")
if not PARSER:
    for cand in [os.path.join(os.path.dirname(HERE), "odota-parser"),
                 os.path.join(os.path.dirname(HERE), "parser")]:
        if os.path.exists(cand):
            PARSER = cand
            break
if not PARSER:
    PARSER = os.path.join(os.path.dirname(HERE), "odota-parser")
PORT = 5600
JAR = os.path.join(PARSER, "target", "stats-0.1.0.jar")
LOGS = os.path.join(HERE, "parser.log")


def find_java():
    """返回 java 可执行文件路径，找不到返回 None。"""
    # 1) 环境变量 JAVA_HOME / JDK_HOME（路径存在才信任）
    for envvar in ("JAVA_HOME", "JDK_HOME"):
        v = os.environ.get(envvar)
        if v:
            p = os.path.join(v, "bin", "java")
            if os.path.exists(p):
                return p
            # env 指向的目录里没有 bin/java，不信任，继续探测
    # 2) 系统 PATH
    found = shutil.which("java")
    if found:
        return found
    # 3) 本机 toolchain 兜底（目录不存在则跳过，不影响其他机器）
    # 注意：必须精确匹配 java.exe / java，避免误中 java.dll 等导致 WinError 193
    project_root = os.path.dirname(HERE)
    for base in [os.path.join(project_root, "toolchain"),
                 os.path.join(os.path.dirname(project_root), "toolchain")]:
        if os.path.isdir(base):
            for sub in sorted(os.listdir(base)):
                for exe in ("java.exe", "java"):
                    cand = os.path.join(base, sub, "bin", exe)
                    if os.path.isfile(cand):
                        return cand
    return None


def detached_kwargs(cwd, env, logf):
    kwargs = dict(cwd=cwd, env=env, stdout=logf, stderr=subprocess.STDOUT)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    return kwargs


def main():
    if not os.path.exists(JAR):
        print(f"错误：构建产物不存在 {JAR}\n"
              f"请确认 odota-parser/target/stats-0.1.0.jar 存在，"
              f"或运行 build_parser.py 重新构建。")
        sys.exit(1)
    java = find_java()
    if not java:
        print("错误：未找到 Java 运行时。请安装 JRE 21+ 并加入 PATH，或设置 JAVA_HOME 环境变量。")
        sys.exit(1)

    env = os.environ.copy()
    env["JAVA_HOME"] = os.path.dirname(os.path.dirname(java))

    logf = open(LOGS, "w", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        [java, "-jar", JAR, str(PORT)],
        **detached_kwargs(PARSER, env, logf),
    )
    print(f"[run_parser] 已启动 pid={proc.pid}，日志 → {LOGS}")

    for i in range(60):
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"http://localhost:{PORT}/healthz", timeout=2) as r:
                if r.read().decode().strip() == "ok":
                    print(f"[run_parser] 服务就绪 ✓ http://localhost:{PORT}")
                    return
        except Exception:
            pass
        if i == 59:
            print("[run_parser] 超时仍未就绪，请查看 parser.log")


if __name__ == "__main__":
    main()
