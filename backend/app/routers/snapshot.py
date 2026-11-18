"""表快照管理接口：维护快照记录，覆盖创建快照、恢复快照、清理快照等动作。

保留策略、策略纪元与可恢复快照数据集也挂在同一入口下：静态路径必须声明在
`/{entry_id}` 之前，否则会被整数路径参数先匹配走（422）而不是进入对应处理器。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult, PolicyUpdate
from app.services.snapshot import SnapshotService
from app.services.snapshot_audit import audit_service
from app.services.snapshot_policy import policy_service

router = APIRouter(prefix="/api/snapshot", tags=["表快照管理"])

service = SnapshotService()

LIST_FIELDS = ["快照编号", "表名称", "快照时间", "快照版本", "快照大小", "保留天数", "创建方式", "快照状态"]
STATUSES = ["已创建", "保留中", "已过期", "已清理"]


# ---------- 保留策略、策略纪元与可恢复快照数据集 ----------


@router.get("/retention")
def get_retention() -> dict[str, Any]:
    """读取当前保留策略：保留天数、策略纪元与最近一次调整时间。"""
    policy = policy_service.policy()
    latest = policy_service.latest_dataset()
    return {
        "retention_days": policy_service.retention_days(),
        "epoch": int(policy.get("epoch", 0)),
        "revised_at": policy.get("revised_at"),
        "revision_seq": policy_service.revision_seq(),
        "latest_dataset_epoch": int(latest.get("epoch")) if latest else None,
        "stale": policy_service.is_stale(),
        "pending": policy_service.pending(),
        "protected_versions": sorted(policy_service.protected_versions()),
    }


@router.put("/retention")
def put_retention(payload: PolicyUpdate) -> dict[str, Any]:
    """调整保留策略；旧纪元结果保留在历史里，但不会被当作当前结果。"""
    policy, message = policy_service.update_retention(payload.retention_days)
    if policy is None:
        return {"ok": False, "message": message}
    return {
        "ok": True,
        "message": message,
        "retention_days": policy_service.retention_days(),
        "epoch": int(policy.get("epoch", 0)),
        "revision_seq": policy_service.revision_seq(),
    }


@router.post("/batch")
def run_batch(payload: EntryPayload) -> dict[str, Any]:
    """增量批算：只发布一个完整策略纪元；批算中阈值变化时按 strategy 重算或顺延。"""
    values = payload.values
    strategy = str(values.get("strategy") or "defer").strip()
    retention = values.get("retention_days")
    try:
        retention_days = int(retention) if retention not in (None, "") else None
    except (TypeError, ValueError):
        return {"ok": False, "published": False, "message": "retention_days 必须是整数"}
    return policy_service.run_incremental(strategy=strategy, retention_days=retention_days)


@router.get("/dataset")
def get_dataset(
    epoch: int | None = Query(default=None, description="策略纪元，缺省为当前已发布版本"),
) -> dict[str, Any]:
    """读取可恢复快照数据集：当前版本、历史版本与受保护/清理候选明细。"""
    history = policy_service.dataset_history()
    if epoch is None:
        dataset = policy_service.latest_dataset()
    else:
        dataset = policy_service.dataset_for_epoch(epoch)
        if dataset is None:
            raise HTTPException(status_code=404, detail=f"策略纪元 {epoch} 没有可恢复快照数据集")
    if dataset is None:
        return {
            "ok": False,
            "message": "尚无可恢复快照数据集，请先执行一次增量批算",
            "dataset": None,
            "history": [],
        }
    return {
        "ok": True,
        "stale": policy_service.is_stale(),
        "message": (
            f"可恢复快照数据集纪元 {dataset.get('epoch')} 已加载"
            + ("（该纪元基于旧保留阈值，等待下一纪元补算）" if policy_service.is_stale() else "")
        ),
        "dataset": dataset,
        "history": [
            {
                "epoch": row.get("epoch"),
                "retention_days": row.get("retention_days"),
                "published": bool(row.get("published")),
                "published_at": row.get("published_at"),
                "protected_count": len(row.get("protected") or []),
                "candidate_count": len(row.get("candidates") or []),
            }
            for row in history
        ],
    }


@router.get("/audit")
def get_audit(
    epoch: int | None = Query(default=None, description="策略纪元，缺省为当前已发布版本"),
) -> dict[str, Any]:
    """读取快照保留审计清单（表快照列表/策略详情入口）。"""
    return {
        "ok": True,
        "epoch": audit_service.resolve_epoch(epoch),
        "items": audit_service.audit_entries(epoch),
    }


@router.post("/audit/verify")
def post_audit_verify(payload: EntryPayload) -> dict[str, Any]:
    """按策略纪元核对可恢复快照数据集，结论写回快照保留审计清单。"""
    epoch = payload.values.get("epoch")
    if epoch in (None, ""):
        resolved = None
    else:
        try:
            resolved = int(epoch)
        except (TypeError, ValueError):
            return {"ok": False, "message": "epoch 必须是整数"}
    return audit_service.verify(resolved)


@router.post("/audit/sync")
def post_audit_sync() -> dict[str, Any]:
    """另一业务面读取同一份可恢复快照数据集，并把结果同步到快照保留审计清单。"""
    return audit_service.sync_from_dataset()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出表快照管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "snapshot", "total": total, "items": items}


# ---------- 快照记录 CRUD ----------


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按快照编号检索"),
    status: str | None = Query(default=None, description="已创建、保留中、已过期、已清理"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按快照编号与状态过滤表快照管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条快照记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="快照记录已登记", entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条快照记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"快照记录 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条快照记录执行创建快照、恢复快照、清理快照；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
