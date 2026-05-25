# Personal Preference Agent — 功能全景

> 本文档汇总 preference-agent-poc 仓库的**已实现功能**与**设计规划中功能**，按模块分层展开。

---

## 一、定位与架构概览

Personal Preference Agent 是一个**本地优先、Markdown 存储、MCP 协议**的个人偏好代言系统。核心目标：让 AI Agent（Kimi Code、Codex、OpenClaw 等）在回复或执行动作前，自动读取并遵守用户的长期偏好。

```
┌─────────────────────────────────────────────────────────────┐
│                        Agent 交互层                          │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│   │ get_preference_decision       │  │ report_preference_feedback │
│   │ capture_preferences_from_session│  │ prewarm_preferences       │
│   └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
                              │ MCP stdio (JSON-RPC 2.0)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        注入层 (Injection)                    │
│   Layer 1: 静态规则  →  preference_rules.md                  │
│   Layer 2: Session 预热 → session-cache/*.md                 │
│   Layer 3: 本地兜底  → fallback_rules.md/json                │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      偏好引擎 (Engine)                       │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│   │ 召回层    │ →│ 精提层    │ →│ 合并层    │ →│ 决策层    │   │
│   │ Recall   │  │ Refine   │  │ Merge    │  │ Decide   │   │
│   └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      存储层 (Store)                          │
│   data/个人偏好.md  ← 唯一官方源，人机可读                    │
│   data/.capture-state/  ← checkpoint & 增量状态               │
│   data/.feedback/       ← 反馈日志 JSONL                     │
│   data/.injection/      ← 注入产物（生成视图，非源）          │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、MCP Server — 5 个暴露工具

`preference_agent/mcp_server.py` 实现标准 **stdio MCP**，协议版本 `2024-11-05`。

### 2.1 `get_preference_decision`

**用途**：Agent 回复或执行动作前，查询当前任务应应用的用户偏好。

**输入**：
- `task` — 当前任务/计划动作/用户问题
- `agent` — 调用方名称（如 `codex` / `openclaw`）
- `context` — 项目、语言、风险、文件等上下文

**输出**：
```json
{
  "decision": "apply|no_preference|escalate",
  "agent_instruction": "在回复或执行前应用这些用户偏好：...",
  "matched_preferences": [...],
  "escalate": false
}
```

### 2.2 `capture_preferences_from_session`

**用途**：从单个 session 文件或目录中提取偏好，增量合并到 Markdown 库。

**输入**：
- `source_path` — session 文件/目录路径（JSONL / Markdown / 文本）
- `dry_run` — 只分析不写入

### 2.3 `prewarm_preferences`

**用途**：Session 开始时预热偏好，生成 session 级缓存，减少每轮实时 MCP 调用。

**输入**：`task`, `agent`, `context`, `output_dir`

**输出**：`agent_instruction`, `session_cache_file`, `matched_preferences`

### 2.4 `sync_preference_injection`

**用途**：生成静态规则、快照和本地兜底文件；可选同步到 `AGENTS.md` / `.cursorrules` managed block。

**输入**：`output_dir`, `target_files`（规则文件列表）

**输出**：`static_rules_file`, `fallback_json_file`, `fallback_md_file`, `snapshot_file`

### 2.5 `report_preference_feedback`

**用途**：用户纠正、确认、拒绝或使用偏好时记录反馈，驱动偏好进化。

**输入**：
- `feedback_type` — `correction` / `confirmation` / `usage` / `rejection`
- `user_feedback` — 用户原始反馈文本
- `preference_id` / `preference_text` — 相关偏好

**输出**：`ok`, `recommendation`（`observe` / `promote` / `stable` / `review_needed`）

---

## 三、召回层（Recall）— 四路融合召回

`preference_agent/recall.py` 实现从原始对话中召回候选偏好的多路融合引擎。

### 3.1 四路召回架构

| 召回路 | 权重 | 覆盖场景 | 状态 |
|--------|------|---------|------|
| **语义召回** | 40% | "短一点"、"别啰嗦"、"先给大纲" 等非关键词表达 | ✅ 已实现（embedding 相似度 vs 示例库） |
| **关键词召回** | 30% | 正向/否定/条件/比较/指令型表达 | ✅ 已实现（7 组 marker + 加权评分） |
| **行为模式召回** | 20% | 跨 session 反复出现的请求模板 | ✅ 已实现（模板提取 + 频率统计） |
| **对话结构召回** | 10% | 纠正、强调、对比信号 | ✅ 已实现（CORRECTION / EMPHASIS marker） |

### 3.2 融合公式

```
final_score = 0.40 × semantic + 0.30 × keyword + 0.20 × behavior + 0.10 × structure
```

**通过条件**：`final_score ≥ 0.35` 或任意单路 score ≥ 0.55

### 3.3 关键词 Marker 体系

- **正向**：我喜欢、我习惯、我希望、优先、默认、每次、总是
- **否定**：不要、别、不用、避免、禁止、不能、我讨厌
- **条件**：如果、除非、否则、当...情况下
- **比较**：比起、相比、更喜欢、宁愿、优于
- **指令**：先给、分步骤、回读、验收、Linear、沉淀
- **纠正**：不对、错了、你应该、我的意思是
- **强调**：重申、记住、一定要、必须
- **临时降级**：这次、今天、临时（score × 0.65）

### 3.4 Embedding 后端

支持可插拔 embedding 后端：
- `glm` — 智谱 Embedding-3（云端）
- `openai` — OpenAI / 兼容 API
- `hash` — 确定性 fallback（无 API key 时使用）

---

## 四、精提层（Refinement）— 结构化提炼与质量控制

`preference_agent/refinement.py` 实现从召回候选到结构化 PreferenceRecord 的精提 Pipeline。

### 4.1 质量评分体系

```python
overall = 0.45 × confidence + 0.35 × clarity + 0.20 × evidence
```

| 维度 | 计算方式 |
|------|---------|
| confidence | high=0.9, medium=0.65, low=0.38 |
| clarity | 长度≥8(+0.25)、applies_to(+0.20)、triggers(+0.10)、条件词(+0.15) |
| evidence | 0.35 + 0.2 × evidence 条数 |

### 4.2 冲突检测

`conflict_likely()` 检测语义相近但方向相反的偏好：
- 语义相似度 ≥ 0.24 且否定词不一致 → **冲突**
- 一方含"详细/展开"、另一方含"简洁/短一点" → **冲突**

### 4.3 归一化合并

- 语义相似度 ≥ 0.82 且无冲突 → 合并证据
- 保留更简洁的 preference 文本
- 置信度升级规则：`score ≥ 0.82 → high`，`score ≥ 0.58 → medium`

### 4.4 四轮精提 Pipeline（设计文档规划）

| 轮次 | 功能 | 状态 |
|------|------|------|
| 粗提 | 从召回候选提取结构化 preference JSON | ✅ 已实现（LLM / Heuristic） |
| 归一化 | 去重、合并、升级置信度 | ✅ 已实现 |
| 冲突检测 | 识别同类场景下的不一致偏好 | ✅ 已实现 |
| 质量评分 | 计算 confidence / clarity / evidence | ✅ 已实现 |

---

## 五、注入层（Injection）— 三层硬保障

`preference_agent/injection.py` 实现偏好生效的多层保障，解决"Agent 不听话"问题。

### 5.1 三层架构

| 层 | 机制 | 可靠性 | 延迟 | 状态 |
|----|------|--------|------|------|
| **Layer 1 静态规则** | 写入 AGENTS.md / .cursorrules managed block | ~99% | 0ms | ✅ 已实现 |
| **Layer 2 Session 预热** | `prewarm_preferences` 生成 session cache | ~85% | 1次 MCP | ✅ 已实现 |
| **Layer 3 本地兜底** | fallback_rules.md/json，MCP 不可用时读取 | ~100% | 0ms | ✅ 已实现 |

### 5.2 静态规则生成

`sync_injection_artifacts()` 生成：
- `preference_rules.md` — 按分类（沟通风格 / 工作流 / 安全约束 / 通用偏好）组织的规则
- `fallback_rules.json` — 结构化兜底规则（含 priority、always_apply）
- `fallback_rules.md` — 人可读兜底规则
- `snapshots/global-default.md` — 偏好库快照

### 5.3 Managed Block 同步

支持向目标文件写入自动管理的偏好块：
```markdown
<!-- personal-preference-agent:start -->
... 自动生成的规则 ...
<!-- personal-preference-agent:end -->
```

更新时自动替换块内内容，不破坏用户自定义内容。

---

## 六、增量捕获（Incremental Capture）

`preference_agent/incremental.py` 实现持续增量捕获。

### 6.1 增量扫描

`scan_incremental()`：
- 维护 JSON 状态文件（文件路径 → `{size, mtime_ns}`）
- 只处理新增或修改的文件
- 支持 `--dry-run` 预览
- 支持 `--max-files` 限制

### 6.2 批量捕获 Runner

`capture_runner.py` 中的 `CaptureRunner`：
- **断点续传**：checkpoint JSON 保存行号、计数、完成状态
- **紧急止损**：`--max-lines`, `--max-candidates`, `--max-minutes`
- **批量提取**：按 `batch_size`（默认 50）批量调用后端 LLM
- **自动重试**：指数退避，最多 `max_retries`（默认 3）次
- **Agent 规则导入**：自动发现项目中的 `AGENTS.md`、`.cursorrules` 等规则文件

### 6.3 四重增量触发（设计规划）

| 触发方式 | 机制 | 状态 |
|---------|------|------|
| **Watchdog** | 文件系统监听 session 目录，实时捕获 | 📋 规划中 |
| **定时扫描** | cron / 定时任务调用 `incremental-scan` | ✅ CLI 已实现，需外部调度 |
| **显式触发** | 用户命令或 Agent 调用 `capture_preferences_from_session` | ✅ 已实现 |
| **Hook 触发** | Agent session 结束时自动提交 | 📋 规划中 |

---

## 七、反馈闭环（Feedback Loop）

`preference_agent/feedback.py` 实现偏好进化的数据基础设施。

### 7.1 反馈类型

- `correction` — 用户纠正 Agent 行为
- `confirmation` — 用户确认偏好正确
- `usage` — 记录偏好被使用（隐式正反馈）
- `rejection` — 用户明确拒绝某条偏好

### 7.2 进化推荐

`feedback_report()` 按偏好聚合反馈，输出进化建议：

| 条件 | 推荐 |
|------|------|
| rejections ≥ 1 或 corrections ≥ 3 | `review_needed` — 需人工审阅 |
| confirmations ≥ 2 且 corrections = 0 | `promote` — 提升置信度 |
| usage ≥ 5 且 corrections = 0 | `stable` — 标记稳定 |
| 其他 | `observe` — 继续观察 |

### 7.3 反馈日志

- 存储：`data/.feedback/feedback-log.jsonl`
- 格式：每行一个 JSON，含 feedback_type、user_feedback、preference_id、agent、task、timestamp

---

## 八、存储层（Store）— Markdown 双轨

`preference_agent/store.py` 实现人机可读的 Markdown 偏好库。

### 8.1 存储格式

```markdown
# 个人偏好

## 已确认偏好

- 默认用中文回复。
- 代码修改完成后默认运行相关测试，不要每次询问。

## 待观察偏好

- 遇到高风险操作先确认，不要直接执行。
```

### 8.2 双轨存储

| 轨 | 用途 | 格式 |
|----|------|------|
| **人类可读轨** | 用户直接浏览、编辑 | Markdown 列表 |
| **机器可读轨** | Agent 解析、结构化处理 | ` ```json preference-record {...} ``` ` |

加载时同时解析两种格式；保存时以人类可读为主，机器可读块通过 `PREF_START/PREF_END` 标记嵌入。

### 8.3 Preference Schema

```python
@dataclass
class PreferenceRecord:
    id: str               # pref-{uuid8}
    title: str            # 简短标题
    summary: str          # 一句话概括
    applies_to: str       # 适用场景（"当...时"）
    preference: str       # 可执行偏好陈述
    triggers: list[str]   # 触发意图
    exceptions: list[str] # 例外情况
    confidence: str       # high / medium / low
    status: str           # active / needs_review
    evidence: list[Evidence]  # 证据链（source, quote, role, observed_at）
    conflict_notes: list[str] # 冲突记录
    version: int          # 版本号
```

---

## 九、后端层（Backend）— 双后端架构

`preference_agent/backends.py` 实现可插拔的模型后端。

### 9.1 Heuristic Backend（离线默认）

- **零依赖**：不调用任何外部 API
- **基于规则**：marker 匹配 + 语义相似度（SequenceMatcher + char-ngram + keyword overlap）
- **用途**：POC 自测、无网络环境、兜底 fallback

### 9.2 OpenAI-Compatible Backend（生产级）

- 支持任意 OpenAI-compatible API（本地 Ollama、云端 GLM、OpenAI 等）
- 三个结构化 prompt：
  - `EXTRACT_SYSTEM_PROMPT` — 从 session 提取 preference JSON
  - `MERGE_SYSTEM_PROMPT` — 判断 candidate 与 existing 的合并策略
  - `DECIDE_SYSTEM_PROMPT` — 给定任务和偏好库，输出决策 JSON
- 响应格式强制 `json_object`，自动从文本中提取 JSON

### 9.3 Fallback 机制

Engine 层自动 fallback：
- 主 backend（如 OpenAI）失败 → 自动切到 Heuristic Backend
- 决策结果标记 `backend_error` 字段

---

## 十、CLI 命令

`preference_agent/cli.py` 提供完整 CLI：

| 命令 | 用途 |
|------|------|
| `init` | 创建空偏好库 |
| `capture` | 从 session 文件/目录捕获偏好 |
| `capture-job` | 大规模可恢复捕获（JSONL 长文件） |
| `decide` | 查询当前任务应应用的偏好 |
| `sync-injection` | 生成注入产物 + 可选同步到规则文件 |
| `prewarm` | Session 预热，生成缓存 |
| `feedback` | 记录偏好反馈 |
| `feedback-report` | 汇总反馈日志 |
| `incremental-scan` | 增量扫描新增/修改的 session 文件 |

---

## 十一、设计文档中的规划功能

以下功能已在设计文档中详细定义，部分代码已有框架，待后续迭代：

### 11.1 召回层优化（02-召回层设计文档）

- [ ] 向量索引持久化（当前 embedding 每次重新计算示例库）
- [ ] 对话切分策略优化（基于 session_id 的跨行合并）
- [ ] 覆盖率目标：从 <1% 提升至 10%+
- [ ] 语义召回覆盖率权重从 40% 调整至 70%（设计目标）

### 11.2 注入层瘦身（03-注入层设计文档）

- [ ] 信任架构后兜底文件从 10 条减至 3 条
- [ ] 预计算从 daily 改为按需
- [ ] 多级缓存缩为单层

### 11.3 产品架构与 UX（04-产品架构设计文档）

- [ ] LaTeX 学术风格 UI（白纸黑字 Light / 黑纸白字 Dark）
- [ ] 用户分层：Type A（撒手不管型）/ Type B（时不时检查型）
- [ ] 本地 Web 面板（`localhost:8080/preferences`）
- [ ] Obsidian 插件集成
- [ ] 斜杠命令触发（`/preference`, `/capture`, `/review`）

### 11.4 生命周期管理

- [ ] 偏好自动过期（30天无证据 → needs_review）
- [ ] 冷启动偏好模板库
- [ ] 跨设备同步（Git / 云同步）

---

## 十二、数据流示例

### 12.1 冷启动 → 首次捕获 → 决策

```
1. python -m preference_agent.cli init
   → 创建 data/个人偏好.md（空）

2. python -m preference_agent.cli capture --source session.md
   → 召回候选 → 精提 → 合并 → 写入 Markdown

3. Agent 调用 get_preference_decision(task="修改代码")
   → 查询偏好库 → 返回 "代码修改后默认运行测试"
   → Agent 遵守

4. 用户纠正："这次不用测试"
   → report_preference_feedback(type=correction)
   → 记录到 feedback-log.jsonl
   → 该偏好标记为 review_needed
```

### 12.2 大规模历史捕获

```
91,974 行 JSONL
    ↓
MultiRouteRecallEngine（四路召回）
    ↓
质量闸门过滤一次性任务、路径、URL、issue 编号和泛用假条件
    ↓
模型精提（recall-extract / semantic-extract）
    ↓
人工审阅 → 合并入库
    ↓
data/个人偏好.md
```

---

## 十三、环境变量速查

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PREFERENCE_STORE_PATH` | `data/个人偏好.md` | 偏好库路径 |
| `PREFERENCE_MODEL_BACKEND` | `heuristic` | 模型后端 |
| `PREFERENCE_MODEL_BASE_URL` | `http://localhost:11434/v1` | API 地址 |
| `PREFERENCE_MODEL_NAME` | `gpt-4.1-mini` | 模型名 |
| `PREFERENCE_CAPTURE_MODE` | `recall-extract` | 捕获模式；`recall-only` 仅用于 debug |
| `PREFERENCE_RECALL_STRATEGY` | `keyword` | 召回策略 |
| `PREFERENCE_EMBEDDING_BACKEND` | — | embedding 后端 |
| `PREFERENCE_INJECTION_DIR` | `data/.injection` | 注入产物目录 |
| `PREFERENCE_INCREMENTAL_STATE` | `data/.capture-state/incremental-scan.json` | 增量状态 |
| `PREFERENCE_FEEDBACK_LOG` | `data/.feedback/feedback-log.jsonl` | 反馈日志 |

---

*最后更新：2026-05-25*
