#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动 odota/parser 解析服务（端口 5600），以独立(detached)进程运行，
使其在调用方退出后依然存活。

跨平台设计：
- Java 运行时自动探测：环境变量 JAVA_HOME / JDK_HOME > 系统 PATH 中的 java
- parser 目录默认指向本技能包内的 ../parser（自带 odota-parser 源码与 jar）
  （可用环境变量 DOTA2_PARSER_DIR 覆盖）
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
# parser 目录：scripts/ 的兄弟目录 parser/（导出包自带 odota-parser 源码与 jar）
PARSER = os.environ.get("DOTA2_PARSER_DIR") or os.path.join(os.path.dirname(HERE), "parser")
PORT = 5600
JAR = os.path.join(PARSER, "target", "stats-0.1.0.jar")
LOGS = os.path.join(HERE, "parser.log")


def find_java():
    """返回 java 可执行文件路径，找不到返回 None。"""
    for envvar in ("JAVA_HOME", "JDK_HOME"):
        v = os.environ.get(envvar)
        if v:
            p = os.path.join(v, "bin", "java")
            if os.path.exists(p):
                return p
            # Windows 上也可能无扩展名即可执行
            return p
    return shutil.which("java")


def detached_kwargs(cwd, env, logf):
    kwargs = dict(cwd=cwd, env=env, stdout=logf, stderr=subprocess.STDOUT)
    if sys.platform.startswith("win"):
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    return kwargs


def main():
    if not os.path.exists(JAR):
        print(f"错误：构建产物不存在 {JAR}\n"
              f"请确认导出包内 parser/target/stats-0.1.0.jar 存在，"
              f"或运行 build_parser.py 重新构建。")
        sys.exit(1)
    java = find_java()
    if not java:
        print("错误：未找到 Java 运行时。请安装 JRE 21+ 并加入 PATH，或设置 JAVA_HOME 环境变量。")
        sys.exit(1)

    env = os.environ.copy()
    # 把 java 所属的 home 透传给 JVM 子进程
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
