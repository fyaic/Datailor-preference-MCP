# Bondie Preference MCP

本仓库是一个本地优先的个人偏好代言系统 POC：从历史会话中提取用户长期偏好，写入人类可读的 Markdown，并通过 MCP/CLI/本地 UI 让任意 agent 在回复或执行前读取这些偏好。

V0 的重点不是“记忆一切”，而是验证一条可控链路：

- 冷启动时偏好库为空，不伪造用户偏好。
- 只把稳定、可复用、对未来 agent 行为有指导价值的内容写入偏好库。
- 过滤一次性任务、路径、URL、issue 编号、密钥、泛用假条件和用户原文照抄。
- 用 Markdown 作为唯一官方偏好源，便于人和模型共同读取。
- 通过 MCP 工具、CLI 和静态注入文件，把偏好实际应用到 agent 工作流里。

## 当前能力

- `capture-job`：流式扫描大型 JSONL / session 文件，支持 checkpoint 断点恢复。
- `recall-extract`：先召回候选，再调用 OpenAI-compatible 模型精提成可复用偏好。
- `semantic-extract`：支持 embedding 召回 + 模型精提，适合更高质量的长跑。
- `decide`：给定当前任务，返回应应用的偏好和 agent 指令。
- MCP stdio server：暴露偏好决策、捕获、反馈、注入同步和 UI 打开工具。
- 本地 Manifesto UI：以 HTML 面板查看 active / pending / conflict / feedback。
- 注入层：生成 `preference_rules.md`、session prewarm cache 和 fallback rules。
- 反馈闭环：记录用户确认、拒绝、纠正和使用反馈。

## 安装

```powershell
git clone https://github.com/fyaic/bondie-preference-MCP.git
cd bondie-preference-MCP
python -m pip install -e .
```

需要 Python 3.11+。仓库没有强制第三方依赖；云模型、embedding 和本地模型都通过环境变量接入。

## 快速试跑

```powershell
python -m preference_agent.cli init --store ".\tmp-preferences.md"
python -m preference_agent.cli capture --store ".\tmp-preferences.md" --source ".\examples\cold_start_session.md"
python -m preference_agent.cli decide --store ".\tmp-preferences.md" --agent codex --task "代码实现完成后准备回复用户"
```

预期结果：`tmp-preferences.md` 中出现泛化后的偏好句，而不是用户原话。

## 环境变量

复制模板后填写本地配置：

```powershell
Copy-Item .env.example .env.local
notepad .env.local
```

加载环境变量：

```powershell
.\scripts\Load-PreferenceEnv.ps1
```

本地模型示例：

```powershell
$env:PREFERENCE_MODEL_BACKEND = "openai-compatible"
$env:PREFERENCE_MODEL_BASE_URL = "http://localhost:11434/v1"
$env:PREFERENCE_MODEL_API_KEY = "local"
$env:PREFERENCE_MODEL_NAME = "qwen2.5:7b"
```

云模型也使用同一套 OpenAI-compatible 接口。不要提交 `.env.local`，仓库已默认忽略。

## 冷启动捕获

正式冷启动不要用 `recall-only` 直接写库。`recall-only` 只适合离线单元测试或排查召回问题；真实捕获应使用 `recall-extract` 或 `semantic-extract`。

```powershell
$env:PREFERENCE_CAPTURE_MODE = "recall-extract"
$env:PREFERENCE_CAPTURE_AGENT_RULES = "0"
$env:PREFERENCE_RECALL_STRATEGY = "keyword"
python -m preference_agent.cli capture-job --source "C:\path\to\history.jsonl" --max-minutes 10
```

长跑可以反复执行同一命令。runner 会使用 `data\.capture-state\*.checkpoint.json` 断点恢复，只有候选完成精提并写库后才推进 checkpoint，避免中断后漏提取。

## Kimi Code 历史

Kimi Code 的 `user-history` JSONL 常见格式是只有 `content` 字段、没有 `role` 字段。本项目会把这种 content-only JSONL 按用户输入处理。

```powershell
.\scripts\Load-PreferenceEnv.ps1
$env:PREFERENCE_CAPTURE_MODE = "recall-extract"
$env:PREFERENCE_CAPTURE_AGENT_RULES = "0"
$env:PREFERENCE_RECALL_STRATEGY = "keyword"

Get-ChildItem "$HOME\.kimi\user-history\*.jsonl" | ForEach-Object {
  python -m preference_agent.cli capture-job --source $_.FullName --max-minutes 10
}
```

V0 在真实 Kimi Code 历史上的一次验收结果：最大文件完整 completed，最终写入 36 条模型精提偏好，其中 23 条 active、13 条待审；质量检查未发现泛用假条件、路径、URL、issue 编号或一次性任务词。

## MCP 配置

```json
{
  "mcpServers": {
    "bondie-preference": {
      "command": "python",
      "args": ["-m", "preference_agent.mcp_server"],
      "cwd": "C:\\path\\to\\bondie-preference-MCP",
      "env": {
        "PREFERENCE_STORE_PATH": "C:\\path\\to\\bondie-preference-MCP\\data\\个人偏好.md",
        "PREFERENCE_MODEL_BACKEND": "heuristic"
      }
    }
  }
}
```

建议把 [docs/AGENTS-snippet.md](docs/AGENTS-snippet.md) 追加到 Codex、OpenClaw 或其他 agent 的系统规则里，要求 agent 在回复、执行、写入外部系统或询问确认前读取偏好。

## 本地 UI

```powershell
python -m preference_agent.cli ui --no-open
```

默认地址：

```text
http://127.0.0.1:8080
```

UI 只绑定 localhost，不对外暴露。它读取 `data\个人偏好.md`，提供 active/pending/conflict/feedback 统计、Manifesto 视图、主题切换和反馈入口。

## 注入层

```powershell
python -m preference_agent.cli sync-injection
python -m preference_agent.cli prewarm --agent codex --task "代码实现完成后准备回复用户"
```

生成文件：

- `data\.injection\preference_rules.md`
- `data\.injection\fallback_rules.md`
- `data\.injection\fallback_rules.json`
- `data\.injection\snapshots\global-default.md`

这些是生成视图，不是新的偏好源。唯一官方源仍然是 `data\个人偏好.md`。

## 反馈与增量捕获

```powershell
python -m preference_agent.cli feedback `
  --type correction `
  --preference "默认先给大纲" `
  --user-feedback "这次不用大纲，直接给完整方案。"

python -m preference_agent.cli feedback-report
python -m preference_agent.cli incremental-scan --source "C:\path\to\sessions" --mode recall-extract
```

## 数据与隐私

仓库默认忽略运行态数据：

- `.env.local`
- `data\个人偏好.md`
- `data\.capture-state\`
- `data\.debug-capture\`
- `data\.feedback\`
- `data\.injection\`
- `data\.ui\`

本地真实偏好、历史会话、debug 输出和 API key 不应提交到 Git。代码层也会在写入前清理常见密钥形状和替换字符。

## 测试

```powershell
python -m pytest -q
```

当前 V0 回归：25 个测试通过。
