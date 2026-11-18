"""数据回滚恢复业务规则：状态流转、字段校验与筛选口径都收在这里。

回滚清单还负责消费「可恢复快照数据集」：按策略纪元解释受保护版本，
把回滚记录引用的快照版本与数据集核对结果同步到快照保留审计清单。
"""
from __future__ import annotations

from typing import Any

from app.services.snapshot_audit import audit_service
from app.services.snapshot_policy import SNAPSHOT_MODULE, policy_service
from app.store import store

MODULE = "rollback"
REQUIRED_FIELDS = ["记录编号", "表名称", "回滚版本"]
STATUS_ORDER = ["待回滚", "回滚中", "已回滚", "已恢复"]
ACTION_RULES = {"执行回滚": "待回滚", "确认恢复": "回滚中", "归档记录": "已回滚"}
NEGATIVE_ACTIONS = []


def _int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class RollbackService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"回滚记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据回滚恢复可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"回滚记录已{action}"

    # ---------- 消费可恢复快照数据集 ----------
    def interpret_dataset(self, epoch: int | None = None) -> dict[str, Any]:
        """按策略纪元读取同一份可恢复快照数据集，解释受保护版本与回滚引用。"""
        resolved = audit_service.resolve_epoch(epoch)
        if resolved is None:
            return {"ok": False, "message": "尚无可恢复快照数据集", "epoch": None, "protected": [], "rollback": []}
        dataset = policy_service.dataset_for_epoch(resolved)
        if dataset is None:
            return {
                "ok": False,
                "message": f"策略纪元 {resolved} 没有可恢复快照数据集",
                "epoch": resolved,
                "protected": [],
                "rollback": [],
            }
        protected_ids = {str(item.get("快照编号") or "") for item in dataset.get("protected") or []}
        snapshots = {str(row.get("快照编号") or ""): row for row in store.rows(SNAPSHOT_MODULE)}
        interpreted = []
        for item in dataset.get("protected") or []:
            version = str(item.get("快照编号") or "")
            row = snapshots.get(version) or {}
            interpreted.append({
                "快照编号": version,
                "表名称": item.get("表名称"),
                "受保护原因": item.get("受保护原因"),
                "快照状态": row.get("status"),
            })
        rollback_rows = [
            {
                "记录编号": row.get("记录编号"),
                "表名称": row.get("表名称"),
                "回滚版本": row.get("回滚版本"),
                "回滚状态": row.get("回滚状态") or row.get("status"),
                "受保护": str(row.get("回滚版本") or "") in protected_ids,
                "策略纪元": resolved,
            }
            for row in store.rows(MODULE)
        ]
        return {
            "ok": True,
            "epoch": resolved,
            "stale": policy_service.is_stale(),
            "retention_days": dataset.get("retention_days"),
            "dataset_version": _int(dataset.get("epoch")),
            "protected": interpreted,
            "rollback": rollback_rows,
        }
