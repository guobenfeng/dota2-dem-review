#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 odota/parser（跨平台）：自动探测 JDK + Maven，调用 mvn clean install。
若 parser 目录自带预编译 jar（stats-0.1.0.jar），则跳过构建。

跨平台：Windows 用 mvn.cmd，Linux/macOS 用 mvn；Java 从 JAVA_HOME/JDK_HOME/PATH 探测。
"""
import os
import shutil
import subprocess
import sys

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
JAR = os.path.join(PARSER, "target", "stats-0.1.0.jar")


def find_java():
    for envvar in ("JAVA_HOME", "JDK_HOME"):
        v = os.environ.get(envvar)
        if v and os.path.exists(os.path.join(v, "bin", "java")):
            return v, os.path.join(v, "bin", "java")
    j = shutil.which("java")
    if j:
        return os.path.dirname(os.path.dirname(j)), j
    # 本机 toolchain 兜底（目录不存在则跳过，不影响其他机器）
    project_root = os.path.dirname(HERE)
    for base in [os.path.join(project_root, "toolchain"),
                 os.path.join(os.path.dirname(project_root), "toolchain")]:
        if os.path.isdir(base):
            for sub in sorted(os.listdir(base)):
                for exe in ("java.exe", "java"):
                    cand = os.path.join(base, sub, "bin", exe)
                    if os.path.isfile(cand):
                        return os.path.dirname(os.path.dirname(cand)), cand
    return None, None


def find_mvn():
    """优先使用 env 变量，其次系统 PATH，最后本机 toolchain 兜底。"""
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
    # 本机 toolchain 兜底（目录不存在则跳过，不影响其他机器）
    project_root = os.path.dirname(HERE)
    for base in [os.path.join(project_root, "toolchain"),
                 os.path.join(os.path.dirname(project_root), "toolchain")]:
        if os.path.isdir(base):
            for sub in sorted(os.listdir(base)):
                for exe in ("mvn.cmd", "mvn"):
                    cand = os.path.join(base, sub, "bin", exe)
                    if os.path.isfile(cand):
                        return cand
    return None


def build():
    if not os.path.isdir(PARSER):
        print(f"错误：odota/parser 源码目录不存在：{PARSER}")
        return 1
    java_home, java = find_java()
    if not java:
        print("错误：未找到 JDK（需要 javac 编译）。请安装 JDK 21+ 并设置 JAVA_HOME 环境变量。")
        return 1
    mvn = find_mvn()
    if not mvn:
        print("错误：未找到 Maven。请安装 Maven 3.9+ 或设置 MAVEN_HOME 环境变量。")
        return 1

    env = os.environ.copy()
    env["JAVA_HOME"] = java_home
    # 把 JDK 和 Maven 的 bin 加到 PATH 最前面
    sep = ";" if sys.platform.startswith("win") else ":"
    extra = sep.join([os.path.join(java_home, "bin"), os.path.dirname(mvn), env.get("PATH", "")])
    env["PATH"] = extra

    print(f"JAVA_HOME = {java_home}")
    print(f"mvn       = {mvn}")
    print(f"cwd       = {PARSER}")
    print(f">>> mvn clean install -U ...")

    # Windows 上用 cmd.exe /c 执行 mvn.cmd；Linux/macOS 直接执行
    if sys.platform.startswith("win") and mvn.endswith(".cmd"):
        rc = subprocess.run(
            ["cmd.exe", "/c", mvn, "-B", "clean", "install", "-U"],
            cwd=PARSER, env=env,
        ).returncode
    else:
        rc = subprocess.run(
            [mvn, "-B", "clean", "install", "-U"],
            cwd=PARSER, env=env,
        ).returncode
    print(f"mvn return code: {rc}")

    if os.path.exists(JAR):
        print(f"构建成功 ✓  {JAR}")
    else:
        print(f"构建完成但未找到 {JAR}，请检查 POM 和依赖是否正确。")
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(build())
