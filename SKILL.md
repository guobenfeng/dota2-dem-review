---
name: dota2-dem-review
description: >
  Dota 2 录像深度复盘。用户提供 .dem 文件路径（或说"复盘/分析这局录像/这个dem"）时使用。
  自动完成：启动 odota/parser 解析服务 → 解析 .dem → 提取深度数据 → AI 撰写全维度复盘报告
  （阵容优劣势、前中后期打法、每位玩家的位置/打法/出装/意识/打钱/失误清单与建议）。
  触发词：dem、复盘、录像分析、回放分析、demo review。
agent_created: true
---

# Dota 2 录像深度复盘技能（跨平台版）

> 本技能为**自包含、跨平台**版本：所有脚本与 odota/parser 编译产物（`stats-0.1.0.jar`）
> 已随包提供。目标机器只需装有 **JRE 21+** 与 **Python 3.10+**，无需 JDK / Maven，
> 也不依赖任何绝对路径。Windows / Linux / macOS 通用。

## 目录约定（关键）

设本技能根目录为 `$SKILL_DIR`（即 SKILL.md 所在目录）：

- 脚本目录：`$SKILL_DIR/scripts/`（analyze.py / deep_extract.py / dem_playerinfo.py / coach.py / run_parser.py / build_parser.py / start_all.py / batch.py）
- 解析器：`$SKILL_DIR/parser/`（odota/parser 源码 + 已编译 `target/stats-0.1.0.jar`）
- 英雄数据：`$SKILL_DIR/scripts/heroes.json`、`heroes_cn.json`
- 报告输出：`$SKILL_DIR/scripts/reports/`

`PY` 代指系统 Python 命令（Linux/macOS 通常为 `python3`，Windows 为 `python` 或 `py`）。
所有命令都先 `cd "$SKILL_DIR/scripts"` 再执行。

## 前置条件

- **Java 21+ 运行时（JRE 即可）**：脚本自动探测 `JAVA_HOME` / `JDK_HOME` 或系统 PATH 中的 `java`。
  若未安装，到 https://adoptium.net 下载 Temurin 21 JRE 并加入 PATH（或设置 `JAVA_HOME`）。
- **Python 3.10+**：脚本、数据提取与 Web 服务均依赖它（`requests` 非必需，代码已尽量零依赖）。
- 解析服务默认 **端口 5600**，Web 服务默认 **端口 8642**（如被占用可在脚本中改）。

## 执行流程（严格按顺序）

### 第 1 步：确保解析服务在线

```bash
curl -s -m 3 http://localhost:5600/healthz   # 返回 "ok" 则跳过启动
PY scripts/run_parser.py                     # 未在线才执行；detached 启动，轮询 healthz 至就绪
```

> 也可一键启动解析+Web：`PY scripts/start_all.py`（Windows 双击 `launch.bat`，Linux/macOS 运行 `./run.sh`）。

### 第 2 步：解析 .dem 并生成基础报告

```bash
cd "$SKILL_DIR/scripts"
PY analyze.py --dem "<用户给的.dem绝对路径>"
```

产出 `reports/<match>.raw.json`、`<match>_summary.json`、`<match>_analysis.md`。
match id 取自文件名中的连续数字（≥6位）。约 55MB 的 dem 解析约 3-6 秒。

> analyze.py 会同时从 .dem 尾部 CDemoFileInfo 提取**玩家昵称与权威英雄对应**
> （`dem_playerinfo.py`，纯 Python 零依赖），缓存为 `reports/<match>_players.json`。
> summary 里每个玩家含 `player`（昵称）字段，`hero` 为「中文名（English）」双语。

### 第 3 步：提取深度数据

```bash
PY deep_extract.py --match <match_id>
```

产出 `reports/<match>_deep.json`，包含每位玩家：昵称（player）、双语英雄名
（hero_display=「中文名（English）」，hero=英文，hero_cn=中文）、分路推断（lane）、
1-5号位推断（position）、带时间出装（purchase_log/key_items）、击杀与死亡时间线
（kills_log/deaths_log 含凶手）、分阶段数据（phases: early_0_10 / mid_10_25 / late_25_end
的 GPM/击杀/死亡/补刀）、APM、死亡损失时长占比（dead_pct）、经济来源（gold_reasons）、
神符、堆野、买活、眼位。比赛级：radiant_gold_adv/xp_adv 每分钟曲线、buildings_timeline、
teamfights。

### 第 4 步：读取数据，AI 撰写深度复盘报告

用 Bash+Python 打印 deep.json 的紧凑摘要（不要直接 Read 整个 raw.json，太大），
然后**由 AI 亲自撰写** `reports/<match>_deep_review.md`，必须包含以下结构：

1. **对局概览**：胜方、时长、经济曲线关键节点（5/10/15/20/25/终局分钟的 gold_adv）
2. **阵容分析**：双方阵容表（位置/英雄/分路）→ 各自优势、劣势、克制关系、发力期、结论
3. **分阶段复盘**（前期 0-10 / 中期 10-25 / 后期 25+）：每阶段先写「实际发生了什么」
   （用 deaths_log、buildings_timeline、gold_adv 佐证），再写「应该怎么打」
4. **每位玩家深度分析**（10 人逐一）：位置履职 / 打法 / 出装 / 意识 / 打钱 /
   **失误清单（编号列出）** / 建议。每条结论必须引用具体数据。
   **标题格式**：`### 玩家昵称 — 中文英雄名（English Hero）· X号位`，全文英雄一律双语显示
5. **总结**：双方各三条最优先改进项

> 完整报告样板见 `reports/` 下任意 `_deep_review.md`（首次实际运行后生成；亦可见于原项目）。

### 第 5 步：交付

展示：`<match>_deep_review.md`（第一位）、`<match>_analysis.md`、`<match>_deep.json`。
最终回复给出核心结论摘要（胜负手、MVP、最大问题玩家）。

## 数据口径备忘（写报告时必须遵守）

- `killed` 字典混含小兵/野怪 —— 击杀数只统计 `npc_dota_hero_*` 键
- Blob 不导出 `player.hero_id` 与玩家昵称 —— **权威来源是 .dem 尾部 CDemoFileInfo**
  （`dem_playerinfo.py` 提取，含 hero_name/player_name/team；team2=天辉 slot0-4，
  team3=夜魇 slot5-9，与 Blob players 顺序一致）；`ability_uses` 技能前缀反推仅作兜底
- 中文英雄名来自静态表 `heroes_cn.json`（token → 中文，离线可用，新英雄需手动补）
- `purchase_log` 用游戏内代码：`bfury`=狂战斧、`basher`=碎颅锤、`sphere`=林肯、`invis_sword`=隐刀
- `draft_timings` 本地录像通常为空（无 BP 数据），阵容从反推结果获得
- `buildings_timeline` 的 key：`badguys_*`=夜魇建筑被拆（天辉推进），`goodguys_*`=天辉建筑被拆；
  `goodguys_fort` 被拆=夜魇胜，`badguys_fort` 被拆=天辉胜
- position 推断是「全场经济排名+眼数」的近似，与 lane 结合判断真实定位
  （如天辉优势路=下路 bot，夜魇优势路=上路 top），报告中以 lane 为准修正 position
- deaths_log 由全体玩家 kills_log 交叉重建，只含英雄互杀
- match_id 本地上传录像在 Blob 里为 0，一律用文件名数字
- gold_adv 每分钟一个采样点，`len(radiant_gold_adv)` ≈ 比赛分钟数

## 常见故障（跨平台）

- 5600 端口无响应：`PY scripts/run_parser.py`；若报 jar 缺失则 `PY scripts/build_parser.py` 重建
- 报「未找到 Java」：安装 JRE 21+ 并加入 PATH，或设置 `JAVA_HOME` 环境变量
- 英雄显示为 token/ID：删除 `heroes.json` 缓存重跑（自动从 dotaconstants/OpenDota 拉取；
  无网络时回退到 `heroes_cn.json` 的 token 映射）
- 想换 odota/parser 目录：设置环境变量 `DOTA2_PARSER_DIR` 指向你的 parser 目录
- 想指定 JDK/Maven 目录：设 `JAVA_HOME` / `MAVEN_HOME`，或用 `analyze.py --toolchain <dir>`

## 在其他平台 / AI 助手使用

- **WorkBuddy**：把整个 `dota2-dem-review-skill` 目录放到 WorkBuddy 的 skills 目录
  （如 `~/.workbuddy/skills/` 或 Windows `%USERPROFILE%\.workbuddy\skills\`），
  对话中 `@skill:dota2-dem-review` 或直接说"复盘这个 dem <路径>"。
- **纯命令行**：见 README.md 的「纯命令行用法」。
- **其他 AI 助手（Claude / ChatGPT / 本地模型等）**：把本 SKILL.md 作为系统提示，
  并把 `scripts/` 目录交给模型调用，即可复刻相同工作流。
