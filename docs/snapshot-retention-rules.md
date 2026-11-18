# 表快照保留：共用口径与校验说明

本说明对应「表快照管理」与「数据回滚恢复」两个模块共用的快照保留逻辑。
代码位置：

| 关注点 | 文件 |
| --- | --- |
| 保留判断纯函数（共用口径） | `backend/app/services/snapshot_rules.py` |
| 保留策略、策略纪元、增量批算、可恢复快照数据集发布 | `backend/app/services/snapshot_policy.py` |
| 快照保留审计清单、核对与同步 | `backend/app/services/snapshot_audit.py` |
| 回滚清单消费数据集 | `backend/app/services/rollback.py` |
| 表快照入口路由 | `backend/app/routers/snapshot.py` |
| 回滚入口路由 | `backend/app/routers/rollback.py` |
| 示例场景数据 | `backend/app/seed.py` |

## 1. 共用保留口径

`snapshot_rules.py` 只放纯函数，不读写内存仓库：

- `snapshot_status(action)`：动作 → 快照状态。取值仍是
  `创建快照 → 已创建`、`恢复快照 → 保留中`、`清理快照 → 已过期`。
- `expires_before(snapshot_time, retention_days, today)`：快照时间严格早于
  `today - retention_days` 即过期；时间解析不了时返回 `False`（不过期）。

表快照管理的动作流转、批算清理候选、回滚清单解释都从这里取同一份口径，
没有第二套过期判断。

**行为保持**：`SnapshotService` 的动作白名单、状态序列、`pending`/`abnormal`
推导与改动前一致；这次只是把状态映射抽到共用模块，并补了批算/核对入口。

## 2. 一次批算只发布一个完整策略纪元

`POST /api/snapshot/batch`

请求体：

```json
{ "values": { "strategy": "defer", "retention_days": 7 } }
```

- `strategy`：`defer`（默认）或 `recompute`。
- `retention_days`：可选。传入表示「本轮扫描完成后、发布之前」观察到保留阈值被改成该值，
  用于复现阈值变化与已扫描清单冲突。

冲突处理：

| 场景 | 结果 |
| --- | --- |
| 阈值未变 | 发布新纪元；阈值与上一纪元一致时才增量复用旧候选 |
| 阈值变化 + `defer` | 本轮按旧阈值完成扫描，**不发布**，登记为下一纪元待办；当前数据集标记 `stale` |
| 阈值变化 + `recompute` | 立即按新阈值整轮重算，只发布新纪元结果 |
| 上一纪元留有不同阈值的待办 | 本轮不复用旧候选，整轮按当前阈值重算 |

硬约束：

1. 历史纪元数据集只读，发布新纪元时旧纪元 `published` 置为 `false`；
   任一时刻只有一个「当前」数据集版本。
2. 恢复链（`restore_chain`）与分支引用（`branch_ref`）每轮重新解释，
   命中引用的快照永远进受保护集合，不会被清理候选覆盖。
3. 增量重算只判断「上一纪元之后新增」的版本；旧版本沿用上一纪元判断，
   但保护结论每轮重新解释。

## 3. 可恢复快照数据集与审计清单

`GET /api/snapshot/dataset[?epoch=N]`：读取当前/指定纪元的可恢复快照数据集，
附历史版本列表。历史纪元可以读，只用于追溯；`stale=true` 表示当前纪元基于旧阈值。

`GET /api/snapshot/audit[?epoch=N]` 与 `GET /api/rollback/audit[?epoch=N]`：
两个入口返回**同一批审计记录**（同一份数据、同一字段），审计记录包含：

- `epoch`、`retention_days`、`dataset_version`
- `protected_versions`、`candidate_versions`
- `diff`（新增/解除受保护、新增/移出清理候选）与 `summary`
- `conclusion`、`verified`、`anomalies`、`rollback_references`、`checked_at`

`POST /api/snapshot/audit/verify` 与 `POST /api/rollback/audit/verify`：
围绕可恢复快照数据集核对指定纪元（缺省当前纪元），结论写回同一条审计记录。
核对项：

- 受保护快照是否仍在快照清单里、是否被标记为已清理；
- 受保护快照是否仍被恢复链/分支引用；
- 清理候选是否仍被引用、回滚记录是否落入清理候选；
- 当前数据集是否基于最新保留阈值（`stale`）。

`POST /api/snapshot/audit/sync` 与 `POST /api/rollback/audit/sync`：
另一业务面读取同一份可恢复快照数据集，把受保护/清理候选刷新进审计清单，
不新造数据集版本；没有审计记录时按数据集补录（兼容已有明细）。

## 4. 业务场景示例

`customer_events` 共 120 个快照，上一纪元已扫描 82 个，本次清单增量 38 个；
`SNAP-CE-20261112-07` 被回滚记录 `RB-20261113-21` 引用（恢复链 + 分支引用）。

预期：

1. 执行增量批算：发布新纪元，`delta=38`，受保护集合包含
   `SNAP-CE-20261112-07`、`SNAP-CE-20261105-02`、`SNAP-CE-20261118-11`。
2. 表快照列表/策略详情与回滚入口的审计清单条目一致。
3. 把保留阈值从 30 天改为 7 天并选 `defer` 再批算：不发布，当前数据集 `stale=true`；
   下一轮批算发布新纪元（7 天），旧纪元不再作为当前结果。
4. 回滚清单读取数据集时，`RB-20261113-21` 的 `受保护=true`，策略纪元与审计清单一致。

## 5. 接口清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/snapshot/retention` | 当前保留天数、纪元、修订序号、`stale`、待办 |
| `PUT` | `/api/snapshot/retention` | 调整保留天数（`{ "retention_days": N }`） |
| `POST` | `/api/snapshot/batch` | 增量批算，发布一个完整策略纪元 |
| `GET` | `/api/snapshot/dataset` | 可恢复快照数据集 + 历史版本 |
| `GET` | `/api/snapshot/audit` | 快照保留审计清单（表快照入口） |
| `POST` | `/api/snapshot/audit/verify` | 核对并回写结论（表快照入口） |
| `POST` | `/api/snapshot/audit/sync` | 读取数据集并同步审计清单（表快照入口） |
| `GET` | `/api/rollback/dataset` | 回滚清单按纪元解释受保护版本 |
| `GET` | `/api/rollback/audit` | 快照保留审计清单（回滚入口） |
| `POST` | `/api/rollback/audit/verify` | 比对当前数据集并回写结论（回滚入口） |
| `POST` | `/api/rollback/audit/sync` | 读取数据集并同步审计清单（回滚入口） |

静态路径都声明在 `/{entry_id}` 之前，避免被整数路径参数抢先匹配。

## 6. 快速自检

启动后端后按顺序调用：

```bash
curl -s -X POST localhost:8000/api/snapshot/batch -H 'Content-Type: application/json' -d '{"values":{}}'
#   -> published=true, delta=38，受保护集合含 SNAP-CE-20261112-07
curl -s localhost:8000/api/snapshot/audit
curl -s localhost:8000/api/rollback/audit          # 与上一条逐字一致
curl -s -X POST localhost:8000/api/snapshot/batch -H 'Content-Type: application/json' -d '{"values":{"retention_days":7}}'
#   -> published=false，旧纪元结果未发布
curl -s localhost:8000/api/snapshot/retention       # -> stale=true
curl -s -X POST localhost:8000/api/snapshot/batch -H 'Content-Type: application/json' -d '{"values":{}}'
#   -> 新纪元，retention_days=7，stale=false
```
