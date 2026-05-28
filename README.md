<p align="center">
  <img src="assets/datailor-github-card.png" alt="Datailor - local-first personal preference memory for agents" width="920">
</p>

# Datailor Preference MCP

Datailor 是本地优先的个人偏好代言系统 POC。它从历史会话和实时 hook 中提取长期、稳定、可复用的用户偏好，写入人类可读的 `个人偏好.md`，并通过 MCP、CLI 和本地 Manifesto UI 让 agent 在回复或执行前读取这些偏好。

V0 的目标不是“记住一切”，而是验证一条可控链路：

- 冷启动时偏好库可以为空，不伪造用户偏好。
- 只沉淀稳定、可复用、对未来 agent 行为有指导价值的偏好。
- 过滤一次性任务、路径、URL、issue 编号、密钥、泛用假条件和用户原文照抄。
- `个人偏好.md` 是唯一官方偏好源，Executive Summary 和 UI 都是派生视图。
- 通过 MCP 工具、CLI、AGENTS managed block 和注入日志，把偏好真正接入 agent 工作流。

## 安装

面向普通用户，推荐用 `pipx` 从 GitHub 安装。安装后 `datailor` 和 `datailor-mcp` 都会进入独立命令行环境，不依赖本地源码目录。

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
pipx install git+https://github.com/fyaic/Datailor-preference-MCP.git
```

检查安装：

```powershell
datailor doctor --agent codex
```

开发者安装：

```powershell
git clone https://github.com/fyaic/Datailor-preference-MCP.git datailor-preference-mcp
cd datailor-preference-mcp
python -m pip install -e .
```

升级和卸载：

```powershell
pipx upgrade datailor-preference-mcp
pipx uninstall datailor-preference-mcp
```

## 默认数据目录

Datailor 默认把运行数据写到用户级目录，而不是仓库内的 `data\`。这让 pipx 安装、源码安装和 MCP 客户端调用都使用同一份偏好库。

| 系统 | 默认目录 |
| --- | --- |
| Windows | `%APPDATA%\Datailor` |
| macOS | `~/Library/Application Support/Datailor` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/datailor` |

默认官方偏好源：

```text
%APPDATA%\Datailor\个人偏好.md
```

可以用环境变量覆盖：

```powershell
$env:DATAILOR_DATA_DIR = "D:\Datailor"
$env:PREFERENCE_STORE_PATH = "D:\Datailor\个人偏好.md"
```

## 首次启动

先看当前状态：

```powershell
datailor doctor --agent codex
```

冷启动扫描本地 agent 历史：

```powershell
datailor onboard --agent codex --mode recall-extract
```

`onboard` 会创建默认 `个人偏好.md`、发现 Claude / Codex / Kimi 历史、按最近活跃和当前 agent 排序、执行增量扫描，并给出下一步命令。

把 Datailor 调用规则写入全局 `AGENTS.md`：

```powershell
datailor install-agent-rules
```

打开本地 Manifesto UI：

```powershell
datailor ui
```

`/preferences` 只是支持 MCP prompts 的客户端上的可选增强。Codex CLI、Kimi CLI 等通常只识别自己的命令系统，所以通用入口是 `datailor doctor`、`datailor onboard` 和 `datailor ui`。

## MCP 配置

生成当前 agent 的 MCP 配置：

```powershell
datailor mcp-config --agent codex
```

输出示例：

```json
{
  "mcpServers": {
    "datailor-preference": {
      "command": "datailor-mcp",
      "args": ["--agent", "codex"],
      "env": {
        "PREFERENCE_MODEL_BACKEND": "heuristic"
      }
    }
  }
}
```

如果你显式传入 `--store`，生成结果会包含 `PREFERENCE_STORE_PATH`：

```powershell
datailor mcp-config --agent kimi --store "D:\Datailor\个人偏好.md"
```

源码开发时也推荐使用 `datailor-mcp`，不要让 MCP 配置绑定仓库 `cwd` 或 `python -m preference_agent.mcp_server`。

## 冷启动扫描

独立运行冷启动扫描：

```powershell
datailor cold-start-scan --agent codex --mode recall-extract
```

默认输出面向人类，会实时显示发现了哪些 sources、正在扫描哪个 source、候选数、新增数、合并数、冲突数和跳过原因。自动化脚本使用 JSON：

```powershell
datailor cold-start-scan --agent codex --mode recall-extract --json
```

只看最终状态：

```powershell
datailor cold-start-scan --agent codex --quiet
```

支持参数：

- `--dry-run`：只演练，不写入偏好库。
- `--max-files`：调试或应急时限制文件数，默认不限制。
- `--max-minutes`：限制单个 source 的运行时间，默认不限制。

## Fitting 离线巩固

`fitting` 用来做 review-first 的离线巩固：它会读取当前偏好库和可选历史源，按自然语言 instructions 提取长期模式、生成 memory rot 建议，并写出一份 Fitting Report。默认不会覆盖 `个人偏好.md`。

```powershell
datailor fitting --agent codex --instructions "focus on UI writing preferences; ignore one-off install commands"
```

扫描指定历史源：

```powershell
datailor fitting --source "C:\path\to\history.jsonl" --instructions-file ".\fitting-instructions.txt"
```

查看和应用：

```powershell
datailor fitting-list
datailor fitting-show fitting-20260527-173000 --report
datailor fitting-apply fitting-20260527-173000 --accept chg-001
```

Fitting 会生成本地 artifacts：

- `report.md`：给用户审查的巩固报告。
- `result.json`：给 CLI/MCP/UI 使用的结构化结果。
- `draft-insights.jsonl`：preference、workflow、error_pattern、tool_quirk 等候选模式。
- `rot-suggestions.jsonl`：重复、覆盖、冲突、过时、负反馈压低等清理建议。
- `apply-plan.json`：可显式接受的变更计划。

MCP 也提供 `start_fitting_consolidation`、`get_fitting_status` 和 `apply_fitting_plan`，供 agent 在不依赖 `/preferences` 的情况下启动和查看巩固任务。

## 真实捕获

正式捕获不建议用 `recall-only` 写库。`recall-only` 只适合离线单测和排查召回问题。真实捕获优先使用 `recall-extract` 或 `semantic-extract`。

```powershell
$env:PREFERENCE_CAPTURE_MODE = "recall-extract"
$env:PREFERENCE_CAPTURE_AGENT_RULES = "0"
datailor capture-job --source "C:\path\to\history.jsonl" --max-minutes 10
```

长跑可以反复执行同一命令。runner 会使用用户数据目录下的 `.capture-state\*.checkpoint.json` 断点恢复，只有候选完成精提并写库后才推进 checkpoint，避免中断后漏提取。

Kimi Code 的 `user-history` JSONL 常见格式是只有 `content` 字段、没有 `role` 字段。Datailor 会把这种 content-only JSONL 按用户输入处理。

## 模型配置

默认后端是 `heuristic`，不依赖 API。接云模型或本地模型时使用 OpenAI-compatible 配置：

```powershell
$env:PREFERENCE_MODEL_BACKEND = "openai-compatible"
$env:PREFERENCE_MODEL_BASE_URL = "http://localhost:11434/v1"
$env:PREFERENCE_MODEL_API_KEY = "local"
$env:PREFERENCE_MODEL_NAME = "qwen2.5:7b"
```

云模型示例：

```powershell
$env:PREFERENCE_MODEL_BACKEND = "openai-compatible"
$env:PREFERENCE_MODEL_BASE_URL = "https://api.moonshot.ai/v1"
$env:PREFERENCE_MODEL_API_KEY = "replace-me"
$env:PREFERENCE_MODEL_NAME = "Kimi-K2.5"
```

Embedding 可选，用于语义召回：

```powershell
$env:PREFERENCE_EMBEDDING_BACKEND = "glm"
$env:PREFERENCE_EMBEDDING_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
$env:PREFERENCE_EMBEDDING_API_KEY = "replace-me"
$env:PREFERENCE_EMBEDDING_MODEL = "embedding-3"
```

不要提交 `.env.local` 或任何 API key。

## 本地 UI

```powershell
datailor ui --no-open
```

默认地址：

```text
http://127.0.0.1:8080
```

UI 只绑定 localhost。它读取官方 `个人偏好.md`，展示：

- **Dashboard**：active / pending / conflict / feedback 统计与 Executive Summary
- **Manifesto**：完整偏好列表（active / pending / conflict / feedback）
- **Injection Log**：每次偏好注入的时间线——命中了哪些偏好、生成了什么 `agent_instruction`、是否真正注入
- **冲突 A/B 对比**：左右分栏查看冲突双方
- **反馈入口**：Confirm / Reject / Correct
- **中英文切换**

所有 `decide` / hook / prewarm 调用都会写入 `.injection-log.jsonl`，Injection Log 标签页将其解析为可读时间线，用于验证偏好是否真的进入 agent 工作流。

UI 反馈会真实修改官方偏好源：

- Confirm：把待观察偏好提升为 `active`。
- Reject：从 `个人偏好.md` 中移除该偏好。
- Correct：用用户输入改写偏好文本并标记为已确认。

每次覆盖 Markdown store 前会自动生成快照，变更后会刷新 Executive Summary。feedback JSONL 只作为审计和演化记录。

## 注入与可观测性

MCP 暴露了偏好决策、冷启动、捕获、反馈、hook、冲突处理和 UI 打开工具。完整工具列表：

| 工具 | 说明 |
|------|------|
| `get_onboarding_status` | 检查首次配置状态 |
| `get_preference_decision` | 在 agent 行动前读取用户偏好 |
| `start_cold_start_capture` | 启动冷启动捕获（等价于 CLI `onboard`） |
| `capture_preferences_from_session` | 从指定 session 文件捕获偏好 |
| `discover_agents` | 检测本机已安装的 agent 历史源 |
| `prewarm_preferences` | 预热 session 级偏好缓存 |
| `hook_session_start` | H1：session 开始 hook |
| `hook_user_message` | H2：用户消息到达 hook |
| `hook_turn_complete` | H3：turn 完成 hook |
| `hook_action_executed` | H4：行为信号 hook |
| `hook_session_end` | H5：session 结束 hook |
| `sync_preference_injection` | 生成静态规则并同步到 AGENTS.md |
| `report_preference_feedback` | 记录用户反馈（确认/纠正/拒绝/使用） |
| `resolve_preference_conflict` | 标记冲突已解决并更新偏好状态 |
| `open_preference_panel` | 打开本地 Manifesto UI 面板 |

所有 `decide` / hook / prewarm 调用都会写入注入日志。Manifesto UI 的 Injection Log 会展示时间、agent、session、命中的偏好和实际注入的 `agent_instruction`，用于验证偏好是否真的进入 agent 工作流。

静态注入产物可以手动同步：

```powershell
datailor sync-injection
datailor prewarm --agent codex --task "代码实现完成后准备回复用户"
```

这些产物是生成视图，不是新的偏好源。唯一官方源仍然是 `个人偏好.md`。

## 冲突处理

`decide` 在返回 agent 指令前会检查本轮命中的 active 偏好是否互相冲突。如果冲突不能同时成立，系统不会把矛盾规则拼接给 agent，而是返回 `decision=escalate`，要求 agent 简短反问用户本次采用哪条偏好，或是否两条都不适用。

Datailor 会记录已询问的冲突组合，并按冷却时间避免同一个冲突反复问。用户回答后，agent 调用 `resolve_preference_conflict` 更新偏好状态并标记该冲突已处理。

## 版本与恢复

```powershell
datailor snapshots
datailor restore-snapshot --snapshot "C:\path\to\.snapshots\20260525-120000-000000-pre-save.md"
```

`个人偏好.md` 仍然是唯一官方偏好源；自动快照只用于恢复。默认每次覆盖已存在的 Markdown store 前，系统会在同目录的 `.snapshots\` 下保存一份旧版本。

## 数据与隐私

仓库默认忽略运行态数据和本地配置：

- `.env.local`
- `data\`
- `.capture-state\`
- `.debug-capture\`
- `.feedback\`
- `.hooks\`
- `.injection\`
- `.snapshots\`
- `.summary\`
- `.ui\`

真实偏好、历史会话、debug 输出和 API key 不应提交到 Git。代码层在写入前会清理常见密钥形状和替换字符。

## 测试

```powershell
python -m pytest -q
```

当前回归：`76 passed`。
