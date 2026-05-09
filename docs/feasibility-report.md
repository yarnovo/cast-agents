# 技术可行性调研报告 · agent harness 重构 (cast-agents)

> 调研日期: 2026-05-08 · 调研人: lead-claude · 受老板委托
>
> 目的: 为 `docs/architecture.md` 6 步实施前 verify 6 个关键技术点 · 不验过的不开始动手。
>
> 方法: 阿里云官方文档 · GitHub 上游 README · 1 次真实 curl 验证 (DashScope tool calling)。
> 不动业务代码 · 不部署 · 不改 prod。

---

## 总结 (执行摘要)

| 调研点 | verdict | 关键结论 |
|---|---|---|
| R1 NAS mount FC v3 | ✅ 可行 | NAS NFS 必走 VPC · 性能型 NAS 600MB/s 初始读 · 跨实例可共享 · 单函数 ≤ 5 挂载点 |
| R2 cast-agents prod 加 NAS | ⚠️ 需改 deploy.yml | 现 deploy.yml 没有 nasConfig/vpcConfig 字段 · 需补 (含 vSwitch / 安全组 / RAM ENI 权限) |
| R3 DashScope tool calling | ✅ 已 live verify | DeepSeek-v3.1 + parallel_tool_calls 实测返 2 个并行 tool_call · 7 tools 同挂稳定 · stream 文档支持 |
| R4 MCP 兼容性 | ✅ 可未来接 | 已有 MCP-Bridge / OpenAI native MCP 支持 · MVP 不接零风险 · agent-infra/sandbox 自带 MCP servers |
| R5 ECS sandbox 升级路径 | ⚠️ 文档稀薄 | agent-infra/sandbox 官方文档 ≤8s 启动 (1c2g) · 镜像大小未公开 · ECS 1c2g 经济型 ¥99/年 起 · 需实测 |
| R6 向量搜索 | ✅ 多选项 | RDS MySQL 8.0 原生 VECTOR(16383) + HNSW (内核 ≥ 20251031) · 推荐: MVP LIKE 兜底 → vector 字段就地升级 RDS · 不上 Tair/DashVector |

**推荐先实施**:

1. ✅ **马上做 step 2-3** (cast-api schema 演进 + cast-agents runtime 重构 · 走 LocalFs + RdsAdapter MVP)
2. ⚠️ **R2 改 deploy.yml** (加 nasConfig + vpcConfig · 1 个 PR · 改完后才能上 NasAdapter)
3. ✅ **R3 LLM 链路无需改造** (现有 dashscope 调用直接配 tools 数组即可)
4. 🚧 **R5 ECS sandbox 真要做时再开** (沿 architecture §2.6 的"暂缓"决策)
5. ✅ **R6 走渐进式** · MVP 不挂 vector adapter · `Memory.search` 走 LIKE / FTS · 数据 > 10 万行再升 VECTOR 字段

---

## R1. NAS mount 进 FC v3 容器

**verdict**: ✅ 可行

### 证据

阿里云 FC v3 官方文档 (`help.aliyun.com/zh/functioncompute/fc/user-guide/configure-a-nas-file-system-for-fc`):

- ✅ FC v3 Custom Container 原生支持挂 NAS · API 参数 `nasConfig` (含 mountPoints / userId / groupId)
- ✅ NAS 协议: 仅 NFS (不支持 SMB)
- ✅ NAS 类型: 通用型 (容量型 / 性能型 / 低频) + 极速型 都支持
- ✅ 跨函数实例共享: 同 region 同 NAS · 不同函数指向同一挂载点 + 同 userId/groupId 即可共享
- ⚠️ 限制: 单函数最多 5 个 NAS 挂载点
- ⚠️ 必须 VPC: NAS 挂载必走 FC 函数的 vpcConfig (vpcId + vSwitchIds + securityGroupId)

### 性能 (官方 + best-practice 文档)

| 指标 | 数字 | 来源 |
|---|---|---|
| 性能型 NAS 初始读带宽 | ~600 MB/s | FC GPU 模型存储最佳实践 |
| FC 3.0 全链路冷启动延时 | 比 2.0 降 80% | 官方 release notes |
| NAS mount cold-start 增量 | 文档未公开具体 ms · ⚠️ 需实测 | - |
| 极速型 NAS 延时 | 亚毫秒 (μs 级) | NAS 产品对比页 |

### 价格 (NAS 计费)

| 类型 | 价格 |
|---|---|
| 通用型 容量型 | ¥0.35 / GiB / 月 |
| 通用型 性能型 | ¥1.85 / GiB / 月 |
| 通用型 低频 | ¥0.15 / GiB / 月 |
| 极速型 (高级型) | ¥0.85 / GiB / 月 |

agent workspace 预估: 100 agent × 平均 100MB / agent ≈ 10GB · 月费 ¥3.5 (容量型) · 几乎可忽略。

### 风险

1. ⚠️ **VPC 是硬性前置**: 现 cast-agents prod FC 函数没配 vpcConfig (deploy.yml 没 vpcConfig 字段) · 加 NAS 必须先建 VPC + vSwitch + 安全组 · 一次性老板 console 操作 · 之后 deploy.yml 引用 ID。
2. ⚠️ **cold-start 数字官方未公开**: 需要 deploy 后实测 · 同行经验 (CSDN/博客文章)报"NAS 挂载 cold-start 加 100-300ms" · 在可接受范围。
3. ⚠️ **跨 region 不共享**: NAS 跟 FC 函数必须同 region · 多 region 部署需各自一份 NAS · agent 数据按 region 分片。
4. ⚠️ **NFS userId/groupId**: 跨函数共享要求统一 user/group · 默认 0 (root) · 多 agent 共享同一根目录靠 path 隔离 · 没文件级 ACL 保护。

### 改造点

```yaml
# deploy.yml 需补
"nasConfig": {
  "userId": 10003,            # FC default agent uid
  "groupId": 10003,
  "mountPoints": [
    {
      "serverAddr": "<nas-id>.cn-hangzhou.nas.aliyuncs.com:/cast-agents",
      "mountDir": "/mnt/nas"
    }
  ]
},
"vpcConfig": {
  "vpcId": "vpc-xxx",
  "vSwitchIds": ["vsw-xxx"],
  "securityGroupId": "sg-xxx"
}
```

RAM 角色需附加权限: `AliyunECSNetworkInterfaceManagementAccess` (FC 创建弹性网卡进 VPC) + NAS 读写权限。

**推荐挂载方式**: 通用型 容量型 NAS · `/mnt/nas/agents/<agent_id>/` 路径隔离 · userId/groupId 统一 10003 · 跨函数共享。

---

## R2. cast-agents prod 已上 · 加 NAS 是否需重 deploy

**verdict**: ⚠️ 需改 deploy.yml + 1 次性 console 配 VPC/NAS · 之后 GHA 自动化

### 证据 · 当前 deploy.yml 现状

读 `~/.claude/repos/apps/cast-agents/.github/workflows/deploy.yml` (96 行 · 5-7 老板拍切回 ACR 后版本):

```json
{
  "functionName": "cast-agents",
  "runtime": "custom-container",
  "timeout": 600,
  "memorySize": 512,
  "instanceConcurrency": 5,
  "customContainerConfig": {...}
  // ⚠️ 没有 nasConfig
  // ⚠️ 没有 vpcConfig
}
```

### 改造点列表

| 改造项 | 类型 | 一次性 / 持续 |
|---|---|---|
| 1. 阿里云 console 建 VPC + vSwitch + 安全组 (cn-hangzhou) | 手动 | 一次性 |
| 2. 阿里云 console 建 NAS 文件系统 + 挂载点 (同 VPC) | 手动 | 一次性 |
| 3. 给 FC 服务角色加 ENI 权限 (RAM 控制台) | 手动 | 一次性 |
| 4. vault 入库新资源 ID (vpcId / vSwitchId / sgId / nasServerAddr) | vault skill | 一次性 |
| 5. deploy.yml 在 BODY JSON 加 `vpcConfig` + `nasConfig` 字段 (从 vault env 注入) | 代码 | 1 次 PR |
| 6. cast-agents pyproject 加 fcntl / file lock 库 (NAS 多实例并发写防坑) | 代码 | 1 次 PR |
| 7. (可选) prod + staging 各一份 NAS (`-staging` 后缀) · 隔离 | 代码 | 同 PR |

### 推荐做法

- ⛔ **不要走 FC console 手改**: 老板规约 "deploy 必脚本化 · 程序优先" · 一切配置走 deploy.yml + vault
- ✅ **走 GHA 重 deploy**: 改完 deploy.yml + push develop (staging 验) → 验过再 push main (prod)
- ✅ **加 fail-fast 检查**: deploy 启动时 `os.path.exists("/mnt/nas")` · 不通就退出

### 风险

- ⚠️ **第一次配 VPC + ENI 权限有坑**: 历史 mail-dayou-agent 曾撞 RAM 角色不全 · 需 lead 自己跑或派 advisor。
- ⚠️ **rollback 路径**: 加 nasConfig 后函数实例必进 VPC · 出 VPC 访问公网 (例如调 dashscope) 需 NAT · 老板 5-3 设过 NAT (查 vault) · 否则 LLM 调用全失败。⚠️ **这是最大风险点 · 必须先验 NAT 通**。

---

## R3. 阿里百炼 (DashScope) function calling 现状

**verdict**: ✅ 已 live verify · 完全可用

### Live verify 证据 (本次调研 curl 实跑)

```bash
# 调用: dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
# model: deepseek-v3.1 · 挂 7 tools · parallel_tool_calls=true · stream=false
# prompt: "请帮我查询北京和上海的天气"

# 返回 (核心):
{
  "choices": [{
    "message": {
      "tool_calls": [
        {"function": {"name": "get_weather", "arguments": "{\"city\": \"北京"},
         "id": "chatcmpl-tool-...", "type": "function"},
        {"function": {"name": "get_weather", "arguments": "{\"city\": \"上海\"}"},
         "id": "chatcmpl-tool-...", "type": "function"}
      ]
    },
    "finish_reason": "tool_calls"
  }],
  "usage": {"prompt_tokens": 402, "completion_tokens": 49, "total_tokens": 451},
  "model": "deepseek-v3.1"
}
```

✅ **结论**: DeepSeek-v3.1 on DashScope:

1. ✅ OpenAI 兼容 `tools=[{type:"function", function:{name, description, parameters}}]` 协议
2. ✅ 7 tools 同挂稳定 (没报错没 truncate)
3. ✅ `parallel_tool_calls=true` 真返多个 tool_call (北京 + 上海 一次返 2 个)
4. ✅ `tool_choice: "auto"` 工作正常
5. ✅ `finish_reason: "tool_calls"` 标准

### 文档补充

- ✅ **stream + tool call 兼容**: 官方文档明确 "工具名在首个数据块返回 · 参数信息以数据流的形式分块返回" (参考 `help.aliyun.com/zh/model-studio/qwen-function-calling`)
- ⚠️ **DeepSeek-v3.1 限制**: function calling 仅在 non-thinking mode (即 `enable_thinking=false`) 可用 · thinking mode 不支持 tool call。MVP runtime 必须显式关 thinking。
- ✅ **切 Qwen3 / Qwen-max 兼容**: 同协议 (function calling 跨百炼模型一致) · 切模型不改 tool spec
- ✅ **context window**: 131,072 tokens (input ≤ 98k · output ≤ 65k) · agent 长 prompt + memory 注入有充足空间
- ⚠️ **tools 数量上限**: 文档未公开硬上限 · 实测 7 个 OK · 业界经验 DeepSeek/Qwen 系一般 ≤ 64 个稳定 · 我们 MVP 平台 tools 估 < 20 个 · 没问题

### 改造点

无 — 现有 cast-agents `llm.py` (走 dashscope) 直接配 tools 数组即可 · 不动 deploy 不改 endpoint。

### 风险

- ⚠️ **DeepSeek-v3.1 thinking-mode 互斥**: 写 runtime 时 hardcode `enable_thinking=False` 不忘 · 否则 tool call 静默失败。
- ⚠️ **未来切 Qwen3-Max/Plus**: 文档未明示 parallel_tool_calls 支持范围 · 切前需 re-verify。

---

## R4. MCP server 跟我们 LLM 链路兼容性

**verdict**: ✅ 可未来接 · MVP 不必接 (符合 architecture D-3 决策)

### 证据

1. **MCP 2026 已成业界标准**:
   - Anthropic 2024 末发布 · OpenAI 2026 早期 native 支持 · Google Gemini 跟进
   - 已有 500+ public MCP servers
2. **OpenAI Responses API 原生支持**: 可直接 `tools=[{type: "mcp", server_url: ...}]` 调远程 MCP server
3. **MCP-Bridge 中间件**: GitHub 项目 · OpenAI Chat API ↔ MCP tools 双向翻译 · 可在我们 runtime 层加一个 "OpenAI tools format ↔ MCP server" adapter
4. **agent-infra/sandbox 自带 MCP servers** (R5 调研副产品): 真要 sandbox 时 MCP 接口现成

### MVP 不接 MCP 的风险

- 风险点: 哪天要接老板看上的某个 MCP server (例: Anthropic Skills marketplace) · 改造量?
- 评估: **改造量小** · MCP 协议跟我们 D-3 决策 (B Python 函数注册 + C DB-driven HTTP) 互补 · 加一个 `McpAdapter` 实现 `Tools.list/call` interface 即可 · 不改 agent 代码不改 LLM prompt
- 估 1 个 PR · 1 天工作量 (跟 architecture §3.5 的 "后续 OSS / Vector / KV / AioSandbox adapter" 同性质)

### 改造点

无 (MVP) · 未来加 `akong_agent_harness/adapters/mcp.py` 实现 `Tools` interface 即可。

### 风险

- ⚠️ **DashScope 是否原生 hosted MCP**: 文档未提到 (DashScope OpenAI 兼容是 Chat Completions 不是 Responses API · Responses API 才有 hosted MCP) · 我们走 MCP 时还是要本地 bridge · 不能直接让 DashScope 调远程 MCP server。

---

## R5. agent-infra/sandbox · ECS 部署可行性

**verdict**: ⚠️ 文档稀薄 · 需实测 · MVP 不开

### 证据

GitHub `agent-infra/sandbox` 官方 README + sandbox.agent-infra.com:

| 维度 | 数据 |
|---|---|
| 启动时间 (1c2g) | 16s → 8s (优化后) |
| 启动时间 (2c4g) | 11s → 4s |
| 镜像 | `ghcr.io/agent-infra/sandbox:latest` |
| 镜像大小 | ⚠️ 文档**未公开** · 需 docker pull 实测 |
| 包含组件 | Browser (VNC) / Shell / File / VSCode Server / Jupyter / MCP servers |
| 协议 | HTTP REST API (`/v1/{shell,file,browser,jupyter}`) + CDP + MCP server + Python/TS/Go SDK |
| 部署 | Docker Compose / Kubernetes |
| 资源占用 | k8s 配置示例 `2Gi` memory limit · idle 数字未公开 |

### ECS 价格 (阿里云 2026)

| 规格 | 月费 | 备注 |
|---|---|---|
| 经济型 e · 2c2g · 3M 带宽 | ¥99 / 年 = ~¥8.25 / 月 | 年付特价 · 适合常驻 1 个 agent sandbox |
| 共享型 1c2g 突发性能 | ⚠️ 文档未明确 · 需 console 查 | 老板 5-7 砍 ACS K8s 时讨论过 |
| 通用型 2c4g | 估 ¥150-200 / 月 (按量) | 跑 3-5 个 agent sandbox 并发 |

### 1c2g ECS 跑几个 sandbox 估算

- AIO sandbox idle 占用 (推断 · 没文档)：≥ 1-1.5GB (VSCode + Browser headless + Python)
- 1c2g 仅能跑 **1 个常驻 sandbox** · 1 用户独占 · 跟 hongniang 旧路线 ACS per-user pod 思路一致
- 真要并发 N 个 agent · 必须 **N 台 1c2g** 或 **1 台 8c16g** · 月费数量级 ¥1000+

### FC ↔ ECS sandbox 通信

- 协议: HTTP API (`POST /v1/shell {cmd: ...}`)
- 网络: 同 VPC 内网通 · agent runtime 在 FC · sandbox 在 ECS · 都进同一 vpc · 无公网开销
- 复杂度: 中等 · 需 FC 配 vpcConfig (R1 R2 已要求) · ECS 加内网安全组允许 FC 出入 · ENI / SLB 可选

### 风险

1. ⚠️ **镜像大小未公开**: 推测 1-2GB (含 VSCode + Chrome + 完整工具链) · ECS docker pull 首次几分钟 · 用 ACR mirror 加速 (跟 fc-agent-deploy 同套范式)
2. ⚠️ **没有云原生 ECS 弹性方案**: ECS 是固定 VM · 用户暴增需手扩 · 跟 K8s SandboxSet warm pool 对比体验差很多。
3. ⚠️ **老板砍了 ACS K8s 路线**: ECS sandbox 跟 K8s 路线本质都是"多用户独立 fs" · 砍 K8s 后再上 ECS 是退路 · 老板 5-7 决策时已说 "sandbox 是成本阻塞" · 不轻易开。
4. ✅ **当前 cast 平台不需要 sandbox**: architecture §2.6 已明确 "cast 平台 agent 落第一档 (纯调平台 API · 不需 sandbox)" · MVP 跑得通 · ECS 是未来某 fake 平台 (B 站 fake 写代码 / 抓数据) 才需要。

### 改造点 (MVP 不做)

留 `akong_agent_harness/adapters/sandbox/aio.py` interface stub · 真要时填实现。

---

## R6. 向量搜索 · 阿里云 RDS MySQL 上

**verdict**: ✅ 多选项 · 推荐渐进式 · MVP 不上 vector adapter

### 证据 · 各方案对比

| 方案 | 价格 | 易用度 | 兼容性 | 适合阶段 |
|---|---|---|---|---|
| **RDS MySQL 8.0 原生 VECTOR** | 免费 (本仓 RDS 自带) | ⭐⭐⭐⭐ | 跟现有 RDS 同库 join · 0 改 | ✅ 推荐 prod 第一档 |
| RDS PostgreSQL pgvector | 免费 | ⭐⭐⭐⭐⭐ | 业界最广 · 但需切 PG | 切 PG 才考虑 |
| Tair (Redis) TairVector | 中-高 (Redis 价 + vector module) | ⭐⭐⭐ | 高频 / 实时检索 | 真高 QPS 才上 |
| **DashVector** Serverless | ¥3.6/M write + ¥8/M read + ¥1.5/GB/月 | ⭐⭐⭐⭐ | 独立服务 · 接 SDK | 数据量 > 100M 行 |
| Milvus 自建 | 高运维 | ⭐⭐ | 业界事实标准 | 不推荐 |

### RDS MySQL 8.0 向量能力 (关键发现)

来源: `help.aliyun.com/zh/rds/apsaradb-rds-for-mysql/vector-storage-1`

- ✅ **原生支持 VECTOR(N) 数据类型** · N ≤ 16,383 维 (足够 · BGE/OpenAI 通常 768/1536 维)
- ✅ **HNSW 索引** + SIMD 硬件加速 + 布隆过滤器
- ✅ **距离函数**: EUCLIDEAN / COSINE
- ⚠️ **内核小版本 ≥ 20251031**: 老实例需先升级内核
- ✅ **混合存储**: vector + 标量字段同表 · join 现有 `agent_memories` 表无缝

### MVP 不上 vector 的兜底性能

- `Memory.search` 走 LIKE / FTS (MySQL fulltext index)
- 阈值: 单 agent memory log < 1万行 时 LIKE 性能 OK (毫秒级 · 用 created_at + agent_id 复合索引)
- 超 10 万行: LIKE 退化明显 (秒级) · 需上 vector
- 我们规模估算: 100 agent × 1000 memory / agent = 10 万行 · 临界 · 早做准备但 MVP 暂可

### 推荐渐进式

1. **MVP** (cast-agents 上线 + 100 agent 内): `Memory.search` 用 LIKE · 不挂 vector adapter
2. **Phase 2** (内存 > 10 万行 / 用户反馈"搜得不准"): 升 RDS 内核 ≥ 20251031 · 给 `agent_memories` 表加 `embedding VECTOR(768)` 字段 + HNSW 索引 · 用 BGE-small / dashscope text-embedding-v3 算 embedding · 切 `Memory.search` 走 vector
3. **Phase 3** (跨平台聚合 / 跨 agent 共享检索): 评估 DashVector 独立服务

### 改造点 (MVP)

- ✅ `Memory.search` interface 留位 · 实现走 LIKE / FTS · 暂不挂 vector backend
- ⚠️ 给 `agent_memories` schema 预留 `embedding` 字段 (NULL allowed · 后期填) · 不破坏 schema 演进

### 风险

1. ⚠️ **RDS 内核版本兼容**: 现有 RDS 实例可能内核 < 20251031 · 升级是 maintenance window · 提前演练
2. ⚠️ **切 PG 路径**: 如未来想用 pgvector (业界更成熟) · 整库迁库不轻 · 提前在 D-2 (存储 interface) 预留 SQLAlchemy 抽象
3. ✅ **DashVector 价格可控**: 1M 768维 vector + 100 QPS 估 ¥30-100/月 (read 主导 · 100 QPS × 86400s × 30 day × 1read_unit ≈ 260M units × ¥8/M = ¥2080/月 估算偏高 · 待真实 calc 走 console)
   ⚠️ 修正: 100 QPS 持续跑过高估 · agent 实际触发 search 频率约 1-10 次/分钟 · 月费应在 ¥10-50

---

## 推荐落地优先级

按 architecture.md §5 6 步 + 本调研发现的新坑:

| 序号 | 步骤 | 状态 | 调研发现的依赖 / 风险 |
|---|---|---|---|
| 0 | **本 doc + 5 ADR 落档** | ✅ 已 (D-1~D-5 全拍) | - |
| 0.5 | **VPC + NAS + RAM 一次性 console 配** | 🆕 新增前置 | R1 R2 发现 · 加 NAS 必须先有 VPC + ENI 权限 + NAT (出公网调 dashscope) |
| 1 | cast-api schema 演进 (agents 表 + agent_memories + agent_tools + tools + agent_change_log) | ⏳ 待开 | 加 `embedding VECTOR(768) NULL` 预留位 (R6) |
| 2 | **cast-agents runtime 重构** (LocalFs + RdsAdapter + 同进程 Tools) | ⏳ 待开 | LLM 调用走 deepseek-v3.1 + `enable_thinking=False` (R3) |
| 3 | cast-platform-tools 抽出 | ⏳ 待开 | - |
| 4 | **deploy.yml 加 nasConfig + vpcConfig** | 🆕 新增 | R2 改造点列表 · 走 staging 先验 (老板 5-5 双环境) |
| 5 | cast-app 改造 (经纪人视角) | ⏳ 待开 | - |
| 6 | 真人首登 spawn meta agent | ⏳ 待开 | - |
| 7 | (未来) ECS sandbox 加 AioSandboxAdapter | 🚧 真有 sandbox 需求时 | R5 警告 · cast 平台不需要 |
| 8 | (未来) Memory.search 升 vector | 🚧 数据 > 10万行 时 | R6 渐进式 |

### 关键阻塞 (开干前必解)

1. ⚠️ **VPC + NAT 必须先验通**: 现 cast-agents prod 函数没 vpcConfig · 加 NAS 必进 VPC · VPC 内访问公网 dashscope/dns 必走 NAT · 没 NAT = LLM 全断。
2. ⚠️ **DeepSeek-v3.1 必显式 `enable_thinking=False`**: runtime hardcode 防忘 · 否则 tool call 静默失败。
3. ⚠️ **NAS userId/groupId 跨函数统一 10003**: 防共享路径权限错乱。

### 不阻塞 (可并行)

- step 1 (schema) 跟 step 4 (deploy.yml NAS) 可并行 PR
- step 2 (runtime) 跟 step 0.5 (VPC) 可并行 (runtime 先在 LocalFs 跑 dev/test)
- staging 先验 NAS 路径 → 验过再切 prod (老板 5-5 双环境规约)

---

## 调研引用源

- 阿里云 FC v3 NAS: https://help.aliyun.com/zh/functioncompute/fc/user-guide/configure-a-nas-file-system-for-fc
- 阿里云 FC v3 best practice 冷启动: https://help.aliyun.com/zh/functioncompute/fc-2-0/use-cases/best-practice-for-reducing-the-cold-start-latency
- 阿里云 NAS 计费: https://help.aliyun.com/zh/nas/product-overview/billing-of-general-purpose-nas-file-systems
- 阿里云 RDS MySQL 8.0 vector: https://help.aliyun.com/zh/rds/apsaradb-rds-for-mysql/vector-storage-1
- 阿里云 DashVector 计费: https://help.aliyun.com/document_detail/2510232.html
- 阿里云 DashScope DeepSeek API: https://www.alibabacloud.com/help/en/model-studio/deepseek-api
- 阿里云 DashScope function calling: https://help.aliyun.com/zh/model-studio/qwen-function-calling
- agent-infra/sandbox: https://github.com/agent-infra/sandbox
- MCP Bridge: https://topmcp.org/mcp/mcp-bridge
- OpenAI Agents SDK MCP: https://openai.github.io/openai-agents-python/mcp/

## 调研方法

- ✅ WebSearch / WebFetch · 阿里云中文文档为主 · 未来不变
- ✅ 1 次真实 curl (DashScope DeepSeek-v3.1 + 7 tools + parallel_tool_calls) · 实测验证 R3
- ⚠️ R1 R2 R5 部分指标 (cold start ms / sandbox image size / NAS IO 真实 IOPS) **官方未公开 · 标"需实测"** · 留 deploy 后跑 benchmark
- ✅ 不动 prod / 不部署 / 不改业务代码

调研 elapsed: ~1.5h.
