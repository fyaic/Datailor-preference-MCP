# 用 Kimi 跑大规模捕获

本文档说明如何用 Moonshot/Kimi 的 OpenAI-compatible API 跑真实历史精提。不要把 `.env.local` 提交到 Git。

## 1. 准备环境变量

```powershell
Copy-Item .env.example .env.local
notepad .env.local
```

在 `.env.local` 中填写：

```powershell
PREFERENCE_MODEL_BACKEND=openai-compatible
PREFERENCE_MODEL_BASE_URL=https://api.moonshot.ai/v1
PREFERENCE_MODEL_API_KEY=replace-me
PREFERENCE_MODEL_NAME=replace-me
PREFERENCE_MODEL_TEMPERATURE=1
PREFERENCE_CAPTURE_MODE=recall-extract
```

加载：

```powershell
.\scripts\Load-PreferenceEnv.ps1
```

## 2. 先做无模型试跑

只验证文件读取、召回闸门和 checkpoint，不调用 API：

```powershell
$env:PREFERENCE_CAPTURE_MODE = "recall-only"
python -m preference_agent.cli capture-job --source ".\examples\large_history_sample.jsonl" --project-root "."
```

`recall-only` 不适合正式写库；它会跳过模型精提，只能用于 debug。

## 3. 跑真实 JSONL

```powershell
$env:PREFERENCE_CAPTURE_MODE = "recall-extract"
$env:PREFERENCE_CAPTURE_AGENT_RULES = "0"
$env:PREFERENCE_RECALL_STRATEGY = "keyword"
python -m preference_agent.cli capture-job --source "<JSONL_PATH>" --max-minutes 10
```

长跑可以重复执行同一命令。checkpoint 只在候选完成精提并写库后推进，所以中断后可以继续。

## 4. Kimi Code user-history

```powershell
Get-ChildItem "$HOME\.kimi\user-history\*.jsonl" | ForEach-Object {
  python -m preference_agent.cli capture-job --source $_.FullName --max-minutes 10
}
```

## 5. 输出位置

- 正式偏好库：`data\个人偏好.md`
- 断点状态：`data\.capture-state\*.checkpoint.json`
- 调试候选与失败日志：`data\.debug-capture\`，仅在 `PREFERENCE_CAPTURE_DEBUG=1` 时生成

默认会全量遍历到完成。只有 debug 或灾难止损时，才临时加 `--max-minutes`、`--max-lines` 或 `--max-candidates`。
