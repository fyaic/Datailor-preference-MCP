# 大规模本地会话捕获策略

## 背景

POC 的默认冷启动偏好库是空的。真实用户新安装后，不应该由系统猜测偏好，而应该从两类本地证据中捕获：

1. 用户已经写过的 agent 规则文件。
2. 用户历史会话 JSONL 中的用户输入部分。

这两类证据的优先级不同：agent 规则是显式偏好，历史用户输入是行为反馈和隐式偏好。第一次 capture 应该先读规则，再读历史。

## 总策略

```text
capture runner 启动
  -> 读取 env 配置
  -> 发现 agent 规则文件
  -> 提取显式偏好与硬约束
  -> 写入候选偏好
  -> 流式扫描 JSONL
  -> 只读取 user/human 输入
  -> 召回可能包含偏好的片段
  -> 分批模型精提，改写成可复用偏好
  -> 过滤一次性任务、路径、URL、issue 编号和泛用假条件
  -> 合并、去重、冲突标记
  -> 直接维护唯一 Markdown 偏好库
```

## 谁来执行

不要让 agent 自己读大 JSONL，也不要让单次对话承担捕获任务。

| 角色 | 职责 |
|------|------|
| 人 / agent | 启动任务、查看最终 Markdown，必要时查看 debug 输出 |
| capture runner | 扫描文件、召回用户输入、维护 `个人偏好.md` 和 checkpoint |
| model backend | 正式冷启动必需；负责对候选片段做语义精提 |
| Markdown store | 唯一产品输出，存放人类可读偏好 |

`recall-only` 只适合离线测试和召回灾难排查，不适合作为正式偏好输出。正式冷启动应使用 `recall-extract` 或 `semantic-extract`，否则会把一次性任务和用户原文误写入偏好库。

## Env 设计

最小正式配置：

```powershell
$env:PREFERENCE_PROJECT_ROOT = "."
$env:PREFERENCE_STORE_PATH = "data\个人偏好.md"
$env:PREFERENCE_CHECKPOINT_DIR = "data\.capture-state"
$env:PREFERENCE_CAPTURE_DEBUG = "0"
$env:PREFERENCE_CAPTURE_MODE = "recall-extract"
$env:PREFERENCE_CAPTURE_BATCH_SIZE = "24"
$env:PREFERENCE_CAPTURE_MAX_MINUTES = "0"
$env:PREFERENCE_MODEL_TIMEOUT = "120"
$env:PREFERENCE_MODEL_BACKEND = "openai-compatible"
```

接云模型或本地模型时，只替换模型后端：

```powershell
$env:PREFERENCE_MODEL_BACKEND = "openai-compatible"
$env:PREFERENCE_MODEL_BASE_URL = "https://api.example.com/v1"
$env:PREFERENCE_MODEL_API_KEY = "replace-me"
$env:PREFERENCE_MODEL_NAME = "replace-me"
```

未来切本地模型：

```powershell
$env:PREFERENCE_MODEL_BASE_URL = "http://localhost:11434/v1"
$env:PREFERENCE_MODEL_API_KEY = "local"
$env:PREFERENCE_MODEL_NAME = "qwen2.5:7b"
```

## 第一步：先读取 agent 已写规则

### 目标

先把用户已经明确写过的规则读进来，作为最高置信度的初始偏好来源。

### 候选路径

捕获器不应只假设存在 `agents/claude.md`，而应按优先级搜索：

1. 当前项目根目录：
   - `AGENTS.md`
   - `agents.md`
   - `CLAUDE.md`
   - `claude.md`
2. 当前项目 `agents/` 目录：
   - `agents/claude.md`
   - `agents/codex.md`
   - `agents/openclaw.md`
   - `agents/*.md`
   - `agents/*.yaml`
   - `agents/*.yml`
3. 仓库子项目目录：
   - `<subproject>/AGENTS.md`
   - `<subproject>/agents/*.md`
   - `<subproject>/agents/*.yaml`

### 当前仓库观察

捕获器要支持“多位置规则发现”，不能把 `agents/claude.md` 写死为唯一入口。

### 规则文件提取方式

规则文件中的内容分三类：

| 类型 | 处理方式 |
|------|----------|
| 硬约束 | 直接作为高置信偏好候选 |
| 工作习惯 | 作为中高置信偏好候选 |
| 项目局部规则 | 标记适用项目路径，避免全局污染 |

示例：

```json
{
  "source_type": "agent_rule",
  "source": "C:\\path\\to\\project\\AGENTS.md",
  "scope": "project:project-name",
  "preference": "完成阶段性成果后询问是否更新 Linear issue",
  "confidence": "high"
}
```

## 第二步：JSONL 只读取用户输入

### 核心判断

对庞大的本地 JSONL，不应默认把用户输入和模型输出都送进模型。第一次捕获只读用户输入，理由是：

- token 成本大幅下降。
- 用户输入更直接包含要求、反馈、纠错、偏好变化。
- 模型输出大量是执行过程和解释，容易稀释偏好信号。
- 偏好系统要学习“用户怎样要求 agent”，而不是学习“agent 怎样回答”。

### 保留什么

只保留 role 为以下值的消息：

- `user`
- `human`
- `用户`
- 其他可映射为用户的 sender/author 字段

忽略：

- `assistant`
- `model`
- `ai`
- `tool`
- `system`

### 例外

少数情况下可以保留用户输入前后的轻量上下文，但不能把完整模型输出送入提取模型：

| 场景 | 可保留上下文 |
|------|--------------|
| 用户说“按你刚才那个方案” | 保留上一条 assistant 输出的摘要，不保留全文 |
| 用户说“这个不对” | 保留上一条 assistant 输出的 200-500 字截断摘要 |
| 用户回答“要/不要/可以” | 保留上一条 assistant 问句 |

这类上下文应由本地预处理器提取，作为 `context_hint`，而不是完整塞进模型。

## 大 JSONL 捕获流水线

### Phase 1：Profile

先抽样前 1000 行，识别：

- JSONL 每行结构。
- role 字段路径。
- content 字段路径。
- timestamp/session_id/conversation_id 字段。
- 是否存在嵌套 message 数组。

输出：

```json
{
  "format": "jsonl",
  "role_path": "message.role",
  "content_path": "message.content",
  "session_id_path": "conversation_id",
  "timestamp_path": "created_at"
}
```

### Phase 2：Stream

对文件逐行读取：

- 不把整个 JSONL 加载进内存。
- 每处理 N 行写一次 checkpoint。
- 坏行记录到错误日志，跳过并继续。
- 默认没有全局时间上限，会全量遍历到完成。
- `PREFERENCE_CAPTURE_MAX_MINUTES` 只作为 debug 或灾难止损开关；默认 `0` 表示不启用。

checkpoint 示例：

```json
{
  "file": "kimi-history.jsonl",
  "line": 120000,
  "last_session_id": "abc",
  "updated_at": "2026-05-23T12:00:00+08:00"
}
```

### Phase 3：User-only Recall

先用低成本规则从用户输入中召回候选片段，不直接全量调用模型。

召回信号：

- 显式偏好：`以后`、`默认`、`每次`、`总是`、`我希望`、`我偏好`
- 禁止/约束：`不要`、`别`、`禁止`、`必须`、`不能`
- 反馈纠错：`不对`、`不是这个意思`、`你应该`、`下次`
- 工作流：`测试`、`验证`、`review`、`回读`、`Linear`、`沉淀`、`文档`
- 偏好变化：`改成`、`从现在开始`、`以后默认`

召回输出是候选片段，不是正式偏好。

### Phase 3.5：四路融合召回

当前实现新增 `PREFERENCE_RECALL_STRATEGY=multi`，在原有关键词召回外增加三条路径：

| 路径 | 作用 | 默认输出 |
|------|------|----------|
| 语义召回 | 用 GLM `embedding-3` 或 hash fallback 对用户输入与偏好表达示例库做相似度搜索 | 只进入内存候选，debug 模式才落盘 |
| 扩展关键词召回 | 覆盖正向、否定、条件、比较、指令型表达 | 只进入内存候选，debug 模式才落盘 |
| 行为模式召回 | 统计“先给我...”“测试/验证/回读/沉淀”等跨 session 高频模板 | 只进入内存候选，debug 模式才落盘 |
| 对话结构召回 | 识别“不对”“你理解错了”“重申”“我说过”等纠正/强调信号 | 只进入内存候选，debug 模式才落盘 |

POC 阶段使用内存向量索引，不强引入 Chroma/FAISS。后续如果真实数据规模验证通过，再把 embedding cache 和候选索引迁移到本地向量库。

GLM embedding 配置：

```powershell
$env:PREFERENCE_RECALL_STRATEGY = "multi"
$env:PREFERENCE_EMBEDDING_BACKEND = "glm"
$env:PREFERENCE_EMBEDDING_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
$env:PREFERENCE_EMBEDDING_MODEL = "embedding-3"
$env:PREFERENCE_EMBEDDING_DIMENSIONS = "1024"
```

API key 只写入 `.env.local`，不要写入文档或日志。

### Phase 4：Batch Extract

把候选片段按主题和时间切批：

- 每批 20-100 条用户输入。
- 同一 session 的相邻用户反馈尽量放在同批。
- 每批只要求模型输出结构化候选偏好。
- 模型必须输出证据 quote。

### Phase 5：Markdown Store

大规模捕获的默认产品输出只有一份 Markdown：

```text
data/
  个人偏好.md
  .capture-state/
    kimi-history.checkpoint.json
```

`个人偏好.md` 只保留人类可读 bullet：

```md
# 个人偏好

## 已确认偏好

- 当 AI 完成代码修改时，默认运行相关测试；如果测试跑不了，要说明原因和替代验证。
- 当讨论进入方案设计、决策推理或复杂问题拆解时，先询问是否需要沉淀成 Markdown 文档。

## 待观察偏好

暂无待观察偏好。
```

metadata、candidate id、source quote、confidence 等信息不进入默认偏好库，避免破坏人类可读性和增加后续 token 成本。

### Phase 5.5：Loop、Timeout 与恢复

长任务必须可中断、可恢复、可重复执行：

- 每批写 checkpoint。
- 每个 candidate 有稳定 `candidate_id`。
- 外层 loop 默认持续到文件遍历完成，不依赖 agent 手动维护。
- API 失败只影响当前 batch，按 `PREFERENCE_CAPTURE_MAX_RETRIES` 重试；重试后仍失败则记录错误并跳过该 batch。
- 默认不写 candidate/extracted/summary 多份重复文件。
- 设置 `PREFERENCE_CAPTURE_DEBUG=1` 时，失败批次才写入 `data\.debug-capture\*-failures.jsonl`，保留错误、候选 ID 和来源，方便复盘真实灾难现场。
- `PREFERENCE_MODEL_TIMEOUT` 控制单次模型请求超时，防止卡死在某个请求上。
- 显式设置 `PREFERENCE_CAPTURE_MAX_MINUTES` 或传 `--max-minutes` 时，到点写 checkpoint 后正常退出。
- 下一次启动从 checkpoint 的 `line` 继续。

`recall-extract` 模式会在每个 batch 召回候选后调用模型后端，把结构化精提结果合并进 `个人偏好.md`。这一步只处理候选片段，不处理完整 JSONL。

`semantic-extract` 模式等价于“多路召回 + 模型精提”。它仍然只维护唯一官方 Markdown；candidate、extracted、failure、summary 都必须显式开启 `PREFERENCE_CAPTURE_DEBUG=1` 才会写入。

命令示例：

```powershell
python -m preference_agent.cli capture-job `
  --source "C:\path\kimi-history.jsonl" `
  --project-root "." `
  --mode recall-extract
```

### Phase 6：Merge

候选偏好进入 `个人偏好.md` 前做合并：

| 动作 | 条件 |
|------|------|
| new | 没有同类偏好 |
| merge | 同类且一致，追加证据 |
| replace | 用户明确表达偏好变化 |
| conflict | 同类但方向矛盾，需要人工观察 |
| reject | 一次性任务、事实陈述、无复用价值 |

## 为什么先读规则，再读用户输入

agent 规则文件通常是用户已经沉淀过的“显式偏好”，置信度高，但覆盖不全。

JSONL 用户输入覆盖面广，但噪声大。它更适合补充：

- 用户反复纠正 agent 的地方。
- 用户频繁要求的工作流。
- 用户后来改变过的标准。
- 没有写进规则文件但实际一直坚持的习惯。

因此顺序应该是：

```text
规则文件建立初始偏好锚点
  -> JSONL 用户输入补充证据
  -> 新证据强化、更新或冲突标记
```

## token 控制原则

1. 不读模型输出全文。
2. 不把完整 JSONL 全量喂给模型；只把召回后的候选片段分批喂给模型。
3. 先本地召回，再模型精提。
4. 一批只处理候选用户输入。
5. 证据 quote 保留原文，但每条偏好只保留少量高质量证据。
6. 默认只维护一份 Markdown；debug 输出必须显式打开。

## 验收标准

- 能发现并读取已有 agent 规则文件。
- 能流式处理大 JSONL，不一次性加载全文件。
- 默认只读取用户输入。
- 能在必要时保留上一条 assistant 问句或摘要作为轻量上下文。
- 能直接维护一份人类可读 Markdown 偏好库。
- 能通过 checkpoint 断点续跑。
- 默认不生成多份重复语义文件；debug 模式才生成故障排查材料。
