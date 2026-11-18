"""快照保留审计：核对可恢复快照数据集、按策略纪元解释受保护版本，并回写审计清单。

两个业务面（表快照列表/策略详情、回滚清单）读的是同一份可恢复快照数据集与同一批审计记录：
- `snapshot` 入口： `GET /api/snapshot/audit`
- `rollback` 入口：`GET /api/rollback/audit`
两边返回的条目逐字一致；核对结论写回同一条审计记录。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.snapshot_policy import (
    AUDIT_MODULE,
    REFERENCE_MODULE,
    SNAPSHOT_MODULE,
    policy_service,
)
from app.store import store

ROLLBACK_MODULE = "rollback"


def _int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class SnapshotAuditService:
    # ---------- 数据集与纪元 ----------
    def datasets(self) -> list[dict[str, Any]]:
        return policy_service.dataset_history()

    def dataset_for_epoch(self, epoch: int) -> dict[str, Any] | None:
        return policy_service.dataset_for_epoch(epoch)

    def latest_published_dataset(self) -> dict[str, Any] | None:
        return policy_service.latest_dataset()

    def resolve_epoch(self, epoch: int | None) -> int | None:
        if epoch is not None:
            return epoch
        latest = policy_service.latest_dataset()
        return _int(latest.get("epoch")) if latest else None

    def is_current_epoch(self, epoch: int) -> bool:
        latest = policy_service.latest_dataset()
        return latest is not None and _int(latest.get("epoch")) == epoch

    # ---------- 审计清单 ----------
    def audit_entries(self, epoch: int | None = None) -> list[dict[str, Any]]:
        rows = sorted(store.rows(AUDIT_MODULE), key=lambda row: _int(row.get("epoch")), reverse=True)
        if epoch is not None:
            rows = [row for row in rows if _int(row.get("epoch")) == epoch]
        return [dict(row) for row in rows]

    def audit_entry(self, epoch: int) -> dict[str, Any] | None:
        rows = self.audit_entries(epoch)
        return rows[0] if rows else None

    def _write_conclusion(self, epoch: int, conclusion: dict[str, Any]) -> None:
        entry = next((row for row in store.rows(AUDIT_MODULE) if _int(row.get("epoch")) == epoch), None)
        if entry is None:
            entry = {
                "id": max((_int(row.get("id")) for row in store.rows(AUDIT_MODULE)), default=0) + 1,
                "epoch": epoch,
                "protected_versions": [],
                "candidate_versions": [],
            }
            store.rows(AUDIT_MODULE).append(entry)
        entry.update({
            "conclusion": conclusion["conclusion"],
            "verified": conclusion["verified"],
            "anomalies": conclusion["anomalies"],
            "rollback_references": conclusion["rollback_references"],
            "current": conclusion["current"],
            "stale": conclusion["stale"],
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "checked_epoch": conclusion["epoch"],
        })

    # ---------- 核对 ----------
    def verify(self, epoch: int | None = None) -> dict[str, Any]:
        """围绕可恢复快照数据集核对指定纪元（默认最新已发布纪元）。

        结论同时覆盖：数据集是否为当前已发布版本、受保护版本是否仍被恢复链/分支引用、
        清理候选是否仍被引用、回滚记录是否落到清理候选里。
        """
        resolved = self.resolve_epoch(epoch)
        if resolved is None:
            return {
                "ok": False,
                "verified": False,
                "epoch": None,
                "message": "尚无可恢复快照数据集，请先完成一次策略纪元批算",
                "anomalies": [],
                "conclusion": "缺少可恢复快照数据集，无法核对",
            }
        dataset = self.dataset_for_epoch(resolved)
        if dataset is None:
            return {
                "ok": False,
                "verified": False,
                "epoch": resolved,
                "message": f"策略纪元 {resolved} 没有可恢复快照数据集",
                "anomalies": [f"纪元 {resolved} 缺少数据集"],
                "conclusion": f"策略纪元 {resolved} 缺少可恢复快照数据集",
            }

        snapshots = {str(row.get("快照编号") or ""): row for row in store.rows(SNAPSHOT_MODULE)}
        references = store.rows(REFERENCE_MODULE)
        reference_versions = {str(ref.get("version") or ref.get("snapshot_id") or "") for ref in references}
        protected_ids = {str(item.get("快照编号") or "") for item in dataset.get("protected") or []}
        candidate_ids = {str(item.get("快照编号") or "") for item in dataset.get("candidates") or []}

        rollback_rows = store.rows(ROLLBACK_MODULE)
        rollback_refs = [
            {
                "记录编号": row.get("记录编号"),
                "回滚版本": row.get("回滚版本"),
                "回滚状态": row.get("回滚状态") or row.get("status"),
            }
            for row in rollback_rows
            if str(row.get("回滚版本") or "") in protected_ids
        ]
        # 回滚引用的版本若落入清理候选，数据集会把它当可清理对象——必须逐条核对。
        rollback_candidate_refs = [
            {
                "记录编号": row.get("记录编号"),
                "回滚版本": row.get("回滚版本"),
            }
            for row in rollback_rows
            if str(row.get("回滚版本") or "") in candidate_ids
        ]

        anomalies: list[str] = []
        for version in sorted(protected_ids):
            row = snapshots.get(version)
            if row is None:
                anomalies.append(f"受保护快照 {version} 已不在快照清单中")
                continue
            if str(row.get("status") or "") == "已清理":
                anomalies.append(f"受保护快照 {version} 被标记为已清理")
            if version not in reference_versions:
                anomalies.append(f"受保护快照 {version} 已失去恢复链/分支引用")
        for version in sorted(candidate_ids & reference_versions):
            anomalies.append(f"清理候选 {version} 仍被恢复链/分支引用")
        for ref in rollback_candidate_refs:
            anomalies.append(f"回滚记录 {ref['记录编号']} 引用的 {ref['回滚版本']} 落入清理候选")
        if not protected_ids and references:
            anomalies.append("数据集未解释出任何受保护版本，但引用表不为空")
        current = self.is_current_epoch(resolved)
        if not current and epoch is None:
            anomalies.append(f"纪元 {resolved} 不是当前已发布数据集版本")
        stale = current and policy_service.is_stale()
        if stale:
            anomalies.append("可恢复快照数据集基于旧保留阈值，等待下一纪元按新阈值补算")

        verified = not anomalies
        if verified:
            detail = (
                f"策略纪元 {resolved} 核对通过：受保护 {len(protected_ids)} 个，"
                f"清理候选 {len(candidate_ids)} 个，回滚清单引用 {len(rollback_refs)} 条"
            )
            detail += "（当前纪元）" if current else "（历史纪元，仅供追溯）"
        else:
            detail = f"策略纪元 {resolved} 核对发现 {len(anomalies)} 项异常"
        conclusion = {
            "ok": True,
            "verified": verified,
            "epoch": resolved,
            "current": current,
            "stale": stale,
            "retention_days": dataset.get("retention_days"),
            "protected_versions": sorted(protected_ids),
            "candidate_versions": sorted(candidate_ids),
            "rollback_references": rollback_refs,
            "anomalies": anomalies,
            "conclusion": detail,
            "message": "核对结论已写入快照保留审计清单",
        }
        self._write_conclusion(resolved, conclusion)
        return conclusion

    # ---------- 另一个业务面 ----------
    def sync_from_dataset(self) -> dict[str, Any]:
        """另一业务面读取同一份可恢复快照数据集，把结果同步进快照保留审计清单。

        同步只刷新当前纪元的受保护/清理候选视图，不新造数据集版本。
        """
        latest = policy_service.latest_dataset()
        if latest is None:
            return {"ok": False, "message": "尚无可恢复快照数据集，无法同步", "entry": None}
        epoch = _int(latest.get("epoch"))
        entry = self.audit_entry(epoch)
        protected_versions = sorted(item.get("快照编号") for item in latest.get("protected") or [])
        candidate_versions = sorted(item.get("快照编号") for item in latest.get("candidates") or [])
        if entry is None:
            # 数据集存在但没有审计记录（历史数据兼容）：按数据集补一条。
            entry = {
                "id": max((_int(row.get("id")) for row in store.rows(AUDIT_MODULE)), default=0) + 1,
                "epoch": epoch,
                "retention_days": latest.get("retention_days"),
                "batch_date": latest.get("batch_date"),
                "dataset_version": epoch,
                "protected_versions": protected_versions,
                "candidate_versions": candidate_versions,
                "diff": {
                    "added_protected": [],
                    "removed_protected": [],
                    "added_candidates": [],
                    "removed_candidates": [],
                },
                "summary": "按可恢复快照数据集补录",
                "published_at": latest.get("published_at"),
            }
            store.rows(AUDIT_MODULE).append(entry)
        else:
            entry["protected_versions"] = protected_versions
            entry["candidate_versions"] = candidate_versions
        return {
            "ok": True,
            "stale": policy_service.is_stale(),
            "message": f"可恢复快照数据集纪元 {epoch} 已同步到快照保留审计清单",
            "entry": dict(entry),
        }


audit_service = SnapshotAuditService()
