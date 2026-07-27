# Dota 2 AI 复盘中心（dota2-dem-review-skill · 跨平台导出版）

基于 [odota/parser](https://github.com/odota/parser) 的 Dota 2 录像（`.dem`）**深度复盘完整工具链**。
本目录是一个**自包含、跨平台**的技能包：所有 Python 脚本与 odota/parser 的编译产物
（`stats-0.1.0.jar`）都已随包提供，**不依赖任何绝对路径**，Windows / Linux / macOS 通用。

只需目标机器满足：
- **JRE 21+**（仅需运行时，无需 JDK / Maven）
- **Python 3.10+**

即可解析录像、生成中英双语英雄名 + 玩家昵称的深度复盘报告，并附带 Web 可视化界面。

---

## 目录结构

```
dota2-dem-review-skill/
├── SKILL.md            # 技能指令（也可作为其他 AI 助手的 system prompt）
├── README.md           # 本文件
├── launch.bat          # Windows 一键启动（双击即可）
├── run.sh              # Linux / macOS 一键启动（先 chmod +x run.sh）
├── scripts/            # 所有 Python 脚本与数据
│   ├── analyze.py          # 解析 .dem → 指标计算 + 基础报告
│   ├── deep_extract.py     # 深度数据提取 → <match>_deep.json
│   ├── dem_playerinfo.py   # 从 .dem 尾部提取玩家昵称与权威英雄对应
│   ├── coach.py            # AI 教练：规则引擎评级 + 可选 LLM
│   ├── run_parser.py       # 启动解析服务（端口 5600，detached）
│   ├── build_parser.py     # 可选：用 Maven 重新构建 odota/parser
│   ├── start_all.py        # 一键启动 解析服务 + Web 服务
│   ├── batch.py            # 批量分析目录下所有 .dem
│   ├── config.json         # 配置（watch_dirs 等）
│   ├── heroes.json         # 英雄 token→英文名 映射（自动缓存）
│   ├── heroes_cn.json      # 英雄 token→中文名 静态表（离线可用）
│   └── webapp/
│       ├── server.py           # stdlib HTTP 服务（零依赖，端口 8642）
│       └── static/index.html   # 单页应用（ECharts 暗色可视化）
└── parser/             # odota/parser（自带源码 + 已编译 jar）
    ├── src/  pom.xml  ...
    └── target/stats-0.1.0.jar   # ⭐ 已编译，离线即可运行
```

---

## 三种使用方式

### 方式 A：作为 WorkBuddy 技能（推荐）

把整个 `dota2-dem-review-skill` 目录复制到 WorkBuddy 的技能目录：

- Windows：`%USERPROFILE%\.workbuddy\skills\`
- Linux / macOS：`~/.workbuddy/skills/`

随后在对话中 `@skill:dota2-dem-review`，或直接说「复盘这个 dem `<你的录像路径>`」。

### 方式 B：纯命令行

```bash
# 1) 进入脚本目录
cd dota2-dem-review-skill/scripts

# 2) 启动解析服务（首次会拉起 5600 端口的 odota/parser）
python run_parser.py          # Windows 用 python；Linux/macOS 用 python3

# 3) 解析某场录像（也可同时用 start_all.py 把 Web 也起了）
python analyze.py --dem "/path/to/your/replay.dem"
python deep_extract.py --match <match_id>

# 4) 查看报告
#    reports/<match>_analysis.md       基础复盘报告
#    reports/<match>_deep.json         深度数据（喂给 AI 写报告用）
#    reports/<match>_deep_review.md    人工/AI 撰写的深度复盘（需按 SKILL.md 第4步撰写）

# 可选：一键启动 解析服务 + Web UI
python start_all.py           # Windows 双击上一级的 launch.bat 也行
# 然后浏览器打开 http://localhost:8642

# 可选：批量分析某个目录下的所有 .dem
python batch.py --dir "/path/to/replays"
```

### 方式 C：在其他 AI 助手（Claude / ChatGPT / 本地模型等）

1. 把 `SKILL.md` 的内容作为系统提示（system prompt）交给模型；
2. 把 `scripts/` 目录提供给模型调用（让模型能执行 `analyze.py` 等）；
3. 模型即可按 SKILL.md 描述的工作流完成「解析 → 提取 → 撰写复盘」。

---

## 首次运行步骤（最小可用）

1. 安装 **JRE 21+** 并确认 `java -version` 可用（或设置 `JAVA_HOME`）。
2. 安装 **Python 3.10+**。
3. 启动服务：`python3 scripts/run_parser.py`（或 `./run.sh`）。
4. 解析录像：`python3 scripts/analyze.py --dem /path/to/replay.dem`。
5. 检查 `scripts/reports/` 下生成的 `*_analysis.md` 与 `*_summary.json`。

> 若 `stats-0.1.0.jar` 缺失或想更新 parser：先装 JDK 21 + Maven 3.9.x，
> 再运行 `python3 scripts/build_parser.py`（会自动探测工具链并从源码构建）。

---

## 跨平台注意事项

| 项目 | 说明 |
|---|---|
| Java 探测 | 脚本按 `JAVA_HOME`/`JDK_HOME` 环境变量 → 系统 PATH 顺序自动找 `java`，无需手动配 |
| parser 位置 | 默认 `$SKILL_DIR/parser`；可用环境变量 `DOTA2_PARSER_DIR` 覆盖 |
| 进程独立化 | 解析/Web 服务均以 detached 进程启动，关掉终端也不退出（跨平台实现） |
| 路径 | 全部相对化，**不要**出现 `C:\` / `/Users/xxx` 之类的绝对路径 |
| 中文英雄名 | `heroes_cn.json` 静态表离线可用；新英雄需手动补 token→中文条目 |
| 英雄英文名 | 首次运行从 dotaconstants/OpenDota 拉取并缓存到 `heroes.json`；无网则回退 |

---

## 常见问题

- **报「未找到 Java」**：装 JRE 21+，加入 PATH，或 `export JAVA_HOME=/path/to/jdk21`。
- **5600 端口被占用**：改 `run_parser.py` / `start_all.py` 里的 `PORT` 常量。
- **英雄只显示 token（如 `npc_dota_hero_xxx`）**：删除 `scripts/heroes.json` 重新跑；
  若仍无网络，`heroes_cn.json` 仅提供中文名，英文名会回退为 token。
- **想分析自己 Steam 里的录像**：默认在
  `Steam/steamapps/common/dota 2 beta/game/dota/replays/` 下找 `.dem` 文件。

---

## 许可

- 本工具链中的 odota/parser 部分遵循其原仓库许可（见 `parser/LICENSE`）。
- 其余脚本与技能定义为本项目成果，可自由使用与二次分发。
