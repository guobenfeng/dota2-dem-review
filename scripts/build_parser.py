#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可选：用 Maven 重新构建 odota/parser（当自带 jar 损坏或需更新时使用）。

跨平台设计：
- JDK 21 与 Maven 自动探测：环境变量 (JAVA_HOME/JDK_HOME, MAVEN_HOME/M2_HOME) > 系统 PATH
- parser 目录默认指向本技能包内的 ../parser（可用 --parser-dir 或 DOTA2_PARSER_DIR 覆盖）
- 不再使用 cmd.exe，跨平台统一走 mvn 命令
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PARSER = os.environ.get("DOTA2_PARSER_DIR") or os.path.join(os.path.dirname(HERE), "parser")


def find_java():
    for envvar in ("JAVA_HOME", "JDK_HOME"):
        v = os.environ.get(envvar)
        if v and os.path.exists(os.path.join(v, "bin", "java")):
            return v, os.path.join(v, "bin", "java")
    j = shutil.which("java")
    if j:
        return os.path.dirname(os.path.dirname(j)), j
    return None, None


def find_mvn():
    for envvar in ("MAVEN_HOME", "M2_HOME"):
        v = os.environ.get(envvar)
        if v and os.path.exists(os.path.join(v, "bin", "mvn")):
            return os.path.join(v, "bin", "mvn")
    return shutil.which("mvn.cmd") or shutil.which("mvn")


def main():
    ap = argparse.ArgumentParser(description="构建 odota/parser")
    ap.add_argument("--parser-dir", default=None,
                    help="odota-parser 目录（默认导出包内 ../parser）")
    args = ap.parse_args()

    parser_dir = os.path.abspath(args.parser_dir) if args.parser_dir else PARSER
    if not os.path.isdir(parser_dir):
        print(f"错误：parser 目录不存在 {parser_dir}")
        sys.exit(1)

    jar = os.path.join(parser_dir, "target", "stats-0.1.0.jar")
    if os.path.exists(jar):
        print(f"已存在构建产物，无需重建：{jar}\n如需强制重建请先删除该文件。")
        return 0

    java_home, java = find_java()
    mvn = find_mvn()
    if not java:
        print("错误：未找到 Java。请安装 JDK 21 并设置 JAVA_HOME，或加入 PATH。")
        sys.exit(1)
    if not mvn:
        print("错误：未找到 Maven。请安装 Maven 3.9.x 并设置 MAVEN_HOME，或加入 PATH。")
        sys.exit(1)

    env = os.environ.copy()
    env["JAVA_HOME"] = java_home
    print(f"JAVA_HOME = {java_home}")
    print(f"PARSER    = {parser_dir}")
    print(">>> mvn clean install ...")
    rc = subprocess.run([mvn, "-B", "clean", "install", "-U"],
                        cwd=parser_dir, env=env).returncode
    print("mvn return code:", rc, "| jar exists:", os.path.exists(jar))
    return rc


if __name__ == "__main__":
    sys.exit(main())
