# builtin-agents

cast 平台**内置 agent** 数据 (declare in yaml)。每个 yaml = 一个 builtin agent · 平台启动时 sync 进 cast-api `agents` 表。

跟"真人通过 meta 创建的 agent"区别在 metadata 字段 `builtin: true`。

## 文件结构

每 agent 1 yaml · 文件名 `<slug>.yaml` · slug 即 agent 短名 (英文 + 连字符)。

字段 schema 见 [docs/architecture.md §D-4](../docs/architecture.md)。

## 当前 builtin 清单

| slug | role | 用途 |
|---|---|---|
| `meta-xiaozao` | meta | 阿空小造 · 真人入口 · 帮造其它 agent (per-real-user spawn template) |
| `design-xiaowang` | normal | 小王 · LOGO 设计 (cast 平台 demo · 已在 cast-api seed) |
| `coach-acha` | normal | 阿茶 · 心理树洞 (同上) |
| `dev-xiaodu` | normal | 小度 · 周末码农 (同上) |

## 怎么加新 builtin

1. 写 `<slug>.yaml`
2. push cast-agents
3. 平台重启 (FC 函数下次 cold start) · 启动钩子 sync DB

## 真人 owner 字段

- meta agent template `owner_id: "$REAL_USER_ID"` (启动时按真人 spawn 实例时替换)
- 普通 builtin `owner_id: "u_system"` (cast-api seed 时建 system 用户)

## 老仓迁移路线

- mail-dayou-agent / discovery-xiaoyan / xiaoyan 等独立 agent 仓 · 数据剥离成 yaml 后 · 仓 `gh repo archive` · 留 git history 备查
- 各自独立 FC 部署 + 自定义域名暂保留 · 等 cast 平台 builtin runtime 真接管业务后切 DNS
