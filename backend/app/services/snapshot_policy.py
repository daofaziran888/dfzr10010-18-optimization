"""表快照保留策略纪元：保留阈值变更、增量批算与可恢复快照数据集的唯一发布口。

核心约束（缺一项就可能让旧候选覆盖新快照）：
1. 一次批算只发布一个完整的策略纪元；批算期间保留阈值发生变化时，旧阈值结果作废：
   `defer` 策略按旧阈值完成本轮扫描、只登记为下一纪元的待办，不发布；
   `recompute` 策略立即按新阈值整轮重算，只发布新纪元结果。
2. 增量重算只对上一纪元之后的清单增量做判断；复用旧候选的前提是保留阈值与上一纪元一致，
   且期间没有待发布冲突。恢复链与分支引用每轮都重新解释，始终受保护。
3. 提交时只让一个可恢复快照数据集版本落库（按纪元合并明细，兼容已有明细）。
4. 可恢复快照数据集的差异写入回滚模块的快照保留审计清单。

状态全部落在内存仓库里（与平台其它模块一致），服务重启后从 seed 重新生成。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from app.services.snapshot_rules import expires_before
from app.store import store

POLICY_MODULE = "snapshot_policy"
DATASET_MODULE = "snapshot_dataset"
AUDIT_MODULE = "snapshot_audit"
CURSOR_MODULE = "snapshot_batch_cursor"
REFERENCE_MODULE = "snapshot_reference"
SNAPSHOT_MODULE = "snapshot"

DEFAULT_RETENTION_DAYS = 30
STRATEGIES = {"defer", "recompute"}


def _today() -> date:
    return date.today()


def _row_id(rows: Iterable[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _snapshot_key(row: dict[str, Any]) -> str:
    return str(row.get("快照编号") or "").strip()


def _reason_label(kind: str) -> str:
    return {"restore_chain": "恢复链", "branch_ref": "分支引用"}.get(kind, kind)


class SnapshotPolicyService:
    """保留策略与批算纪元。"""

    # ---------- 策略 ----------
    def policy(self) -> dict[str, Any]:
        rows = store.rows(POLICY_MODULE)
        if not rows:
            rows.append({
                "id": 1,
                "retention_days": DEFAULT_RETENTION_DAYS,
                "epoch": 0,
                "revised_at": "",
                "revision_seq": 0,
                "stale": False,
            })
        return rows[0]

    def retention_days(self) -> int:
        return _int(self.policy().get("retention_days"), DEFAULT_RETENTION_DAYS)

    def revision_seq(self) -> int:
        return _int(self.policy().get("revision_seq"))

    def is_stale(self) -> bool:
        """当前发布的数据集是否落后于保留策略（旧纪元结果不再代表当前结论）。

        两种情况都算落后：策略调整后还没按新阈值批算；或者批算冲突按 defer 顺延。
        """
        if self.policy().get("stale"):
            return True
        latest = self.latest_dataset()
        return latest is not None and _int(latest.get("retention_days")) != self.retention_days()

    def pending(self) -> dict[str, Any] | None:
        return self._cursor().get("pending") or None

    def update_retention(self, retention_days: int) -> tuple[dict[str, Any] | None, str]:
        if retention_days <= 0:
            return None, "保留天数必须是正整数"
        policy = self.policy()
        policy["retention_days"] = int(retention_days)
        policy["revision_seq"] = self.revision_seq() + 1
        policy["revised_at"] = datetime.now().isoformat(timespec="seconds")
        return policy, f"保留策略已调整为 {retention_days} 天，下一个批算纪元生效"

    # ---------- 受保护集合 ----------
    def references(self) -> list[dict[str, Any]]:
        return store.rows(REFERENCE_MODULE)

    def protected_versions(self, rows: list[dict[str, Any]] | None = None) -> dict[str, list[str]]:
        """按快照编号解释受保护版本：恢复链（restore_chain）与分支引用（branch_ref）。"""
        snapshot_ids = {_snapshot_key(row) for row in (rows if rows is not None else store.rows(SNAPSHOT_MODULE))}
        protected: dict[str, list[str]] = {}
        for ref in self.references():
            version = str(ref.get("version") or ref.get("snapshot_id") or "").strip()
            if not version or version not in snapshot_ids:
                continue
            kind = str(ref.get("kind") or "branch_ref")
            bucket = protected.setdefault(version, [])
            if kind not in bucket:
                bucket.append(kind)
        return protected

    def resolve_protected(self, snapshot_id: str) -> list[str]:
        return self.protected_versions().get(snapshot_id, [])

    def _protected_item(self, row: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
        return {
            "快照编号": _snapshot_key(row),
            "表名称": row.get("表名称"),
            "快照时间": row.get("快照时间"),
            "保留天数": row.get("保留天数"),
            "受保护原因": "、".join(_reason_label(item) for item in reasons),
        }

    def _candidate_item(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "快照编号": _snapshot_key(row),
            "表名称": row.get("表名称"),
            "快照时间": row.get("快照时间"),
            "保留天数": row.get("保留天数"),
        }

    # ---------- 批算 ----------
    def _cursor(self) -> dict[str, Any]:
        rows = store.rows(CURSOR_MODULE)
        if not rows:
            rows.append({"id": 1, "scanned": [], "epoch": 0})
        return rows[0]

    def _decide(
        self,
        rows: list[dict[str, Any]],
        retention_days: int,
        today: date,
        protected: dict[str, list[str]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        keep: list[dict[str, Any]] = []
        clean: list[dict[str, Any]] = []
        for row in rows:
            snapshot_id = _snapshot_key(row)
            reasons = protected.get(snapshot_id)
            if reasons:
                keep.append(self._protected_item(row, reasons))
            elif expires_before(row.get("快照时间"), retention_days, today):
                clean.append(self._candidate_item(row))
        return keep, clean

    def run_incremental(
        self,
        *,
        strategy: str = "defer",
        retention_days: int | None = None,
        today: date | None = None,
    ) -> dict[str, Any]:
        """增量批算：只对清单增量做判断，但每次发布都必须是完整策略纪元。

        `retention_days` 传入时表示本轮批算进行中观察到的保留阈值调整（扫描完成后、发布前生效），
        用于复现「阈值变化与已扫描清单冲突」的场景；不传则按当前策略执行。
        """
        if strategy not in STRATEGIES:
            return {"ok": False, "published": False, "message": f"未知批算策略「{strategy}」"}

        today = today or _today()
        snapshot_rows = store.rows(SNAPSHOT_MODULE)
        row_by_id = {_snapshot_key(row): row for row in snapshot_rows}
        scanned = set(row_by_id)
        cursor = self._cursor()
        previous = self.latest_dataset()
        previous_scanned = {str(item) for item in (cursor.get("scanned") or [])}
        start_retention = self.retention_days()

        # 批算进行中变更保留阈值：在扫描完清单增量之后、发布之前生效。
        mid_batch_change = retention_days is not None and int(retention_days) != start_retention
        if mid_batch_change:
            _, change_message = self.update_retention(int(retention_days))
            if change_message.startswith("保留天数必须"):
                return {"ok": False, "published": False, "message": change_message}
        effective_retention = self.retention_days()
        protected = self.protected_versions(snapshot_rows)

        delta = scanned - previous_scanned
        pending = cursor.get("pending") or None
        pending_conflict = bool(
            pending and _int(pending.get("retention_days")) != effective_retention
        )

        if mid_batch_change and strategy == "defer":
            # 按旧阈值把本轮扫描完成，但结果不发布，只登记为下一纪元待办。
            old_protected, old_candidates = self._decide(snapshot_rows, start_retention, today, protected)
            cursor["pending"] = {
                "scanned": sorted(scanned),
                "retention_days": start_retention,
                "next_retention_days": effective_retention,
                "deferred": True,
                "scanned_at": datetime.now().isoformat(timespec="seconds"),
            }
            self.policy()["stale"] = True
            return {
                "ok": True,
                "published": False,
                "message": "保留阈值在本轮批算中发生变化，旧阈值结果未发布；下一纪元将按新阈值重算",
                "epoch": _int(cursor.get("epoch")),
                "retention_days": start_retention,
                "next_retention_days": effective_retention,
                "strategy": strategy,
                "delta": len(delta),
                "dataset": None,
                "protected": old_protected,
                "candidates": old_candidates,
            }

        # 是否复用上一纪元的候选：阈值一致、无待办冲突、本次没有中途变更。
        reuse = bool(
            previous
            and not mid_batch_change
            and not pending_conflict
            and _int(previous.get("retention_days")) == effective_retention
        )
        if reuse:
            keep, clean, reused = self._incremental_result(
                row_by_id=row_by_id,
                previous=previous,
                previous_scanned=previous_scanned,
                delta=delta,
                retention_days=effective_retention,
                today=today,
                protected=protected,
            )
        else:
            keep, clean = self._decide(snapshot_rows, effective_retention, today, protected)
            reused = 0

        epoch = max(_int(cursor.get("epoch")), _int((previous or {}).get("epoch"))) + 1
        cursor.update({"epoch": epoch, "scanned": sorted(scanned), "pending": None})
        policy = self.policy()
        policy["epoch"] = epoch
        policy["retention_days"] = effective_retention
        policy["stale"] = False

        # 「一个完整策略纪元」：历史纪元不再标记 published，只保留作恢复链。
        for previous_row in store.rows(DATASET_MODULE):
            if _int(previous_row.get("epoch")) != epoch:
                previous_row["published"] = False

        dataset = {
            "id": _row_id(store.rows(DATASET_MODULE)),
            "epoch": epoch,
            "retention_days": effective_retention,
            "batch_date": today.isoformat(),
            "protected": keep,
            "candidates": clean,
            "scanned_total": len(snapshot_rows),
            "delta": len(delta),
            "reused": reused,
            "incremental": reuse,
            "strategy": strategy,
            "revision_seq": self.revision_seq(),
        }
        published = self.publish_dataset(dataset)
        return {
            "ok": True,
            "published": True,
            "message": (
                f"策略纪元 {epoch} 已发布：受保护快照 {len(keep)} 个、清理候选 {len(clean)} 个"
                f"（增量 {len(delta)} 个，复用旧候选 {reused} 个）"
            ),
            "epoch": epoch,
            "retention_days": effective_retention,
            "next_retention_days": effective_retention,
            "strategy": strategy,
            "delta": len(delta),
            "dataset": published,
            "protected": keep,
            "candidates": clean,
        }

    def _incremental_result(
        self,
        *,
        row_by_id: dict[str, dict[str, Any]],
        previous: dict[str, Any],
        previous_scanned: set[str],
        delta: set[str],
        retention_days: int,
        today: date,
        protected: dict[str, list[str]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """增量重算：只判断新增版本，旧版本沿用上一纪元判断，但受保护结论每轮重新解释。"""
        keep: dict[str, dict[str, Any]] = {}
        clean: dict[str, dict[str, Any]] = {}

        for item in previous.get("protected") or []:
            snapshot_id = str(item.get("快照编号") or "")
            if snapshot_id in row_by_id:
                keep[snapshot_id] = dict(item)
        for item in previous.get("candidates") or []:
            snapshot_id = str(item.get("快照编号") or "")
            if snapshot_id in row_by_id:
                clean[snapshot_id] = dict(item)

        for snapshot_id in sorted(delta):
            row = row_by_id[snapshot_id]
            reasons = protected.get(snapshot_id)
            if reasons:
                keep[snapshot_id] = self._protected_item(row, reasons)
                clean.pop(snapshot_id, None)
            elif expires_before(row.get("快照时间"), retention_days, today):
                clean[snapshot_id] = self._candidate_item(row)
                keep.pop(snapshot_id, None)

        # 保护结论每轮重新解释：新引用的版本从候选移回受保护，失去引用的旧版本按阈值重判。
        for snapshot_id, reasons in protected.items():
            if snapshot_id not in keep:
                keep[snapshot_id] = self._protected_item(row_by_id[snapshot_id], reasons)
                clean.pop(snapshot_id, None)
        for snapshot_id in list(keep):
            if snapshot_id not in protected:
                keep.pop(snapshot_id)
                row = row_by_id[snapshot_id]
                if expires_before(row.get("快照时间"), retention_days, today):
                    clean.setdefault(snapshot_id, self._candidate_item(row))

        judged = set(keep) | set(clean)
        carried = {
            str(item.get("快照编号") or "")
            for item in (previous.get("protected") or []) + (previous.get("candidates") or [])
        }
        reused = len(judged & (carried | previous_scanned))
        return (
            [keep[name] for name in sorted(keep)],
            [clean[name] for name in sorted(clean)],
            max(reused, 0),
        )

    # ---------- 可恢复快照数据集 ----------
    def published_datasets(self) -> list[dict[str, Any]]:
        return sorted(
            (row for row in store.rows(DATASET_MODULE) if row.get("published")),
            key=lambda row: _int(row.get("epoch")),
        )

    def latest_dataset(self) -> dict[str, Any] | None:
        rows = self.published_datasets()
        return rows[-1] if rows else None

    def dataset_for_epoch(self, epoch: int) -> dict[str, Any] | None:
        """按纪元读取数据集；历史纪元只读，是否当前版本由 `latest_dataset` 判断。"""
        return next((row for row in self.dataset_history() if _int(row.get("epoch")) == epoch), None)

    def dataset_history(self) -> list[dict[str, Any]]:
        return sorted(store.rows(DATASET_MODULE), key=lambda row: _int(row.get("epoch")))

    def publish_dataset(self, dataset: dict[str, Any]) -> dict[str, Any]:
        """只让一个可恢复快照数据集版本落库：同纪元合并明细，兼容已有明细。"""
        rows = store.rows(DATASET_MODULE)
        epoch = _int(dataset["epoch"])
        existing = next((row for row in rows if _int(row.get("epoch")) == epoch), None)
        merged_protected = _merge_by_key(
            (existing or {}).get("protected") or [],
            dataset.get("protected") or [],
            "快照编号",
        )
        merged_candidates = _merge_by_key(
            (existing or {}).get("candidates") or [],
            dataset.get("candidates") or [],
            "快照编号",
        )
        record = dict(existing or {})
        record.update(dataset)
        if existing is not None:
            # 同纪元重复发布：保留原记录 id，避免明细合并时主键漂移。
            record["id"] = existing.get("id")
        record["protected"] = merged_protected
        record["candidates"] = merged_candidates
        record["published"] = True
        record["published_at"] = datetime.now().isoformat(timespec="seconds")

        previous_audit = self._last_audit(epoch)
        previous_protected = set(previous_audit.get("protected_versions", []) if previous_audit else [])
        previous_candidates = set(previous_audit.get("candidate_versions", []) if previous_audit else [])
        protected_versions = sorted(item["快照编号"] for item in merged_protected)
        candidate_versions = sorted(item["快照编号"] for item in merged_candidates)
        diff = {
            "added_protected": sorted(set(protected_versions) - previous_protected),
            "removed_protected": sorted(previous_protected - set(protected_versions)),
            "added_candidates": sorted(set(candidate_versions) - previous_candidates),
            "removed_candidates": sorted(previous_candidates - set(candidate_versions)),
        }

        if existing is None:
            rows.append(record)
        else:
            existing.clear()
            existing.update(record)

        audit = {
            "id": _row_id(store.rows(AUDIT_MODULE)),
            "epoch": epoch,
            "retention_days": record["retention_days"],
            "batch_date": record["batch_date"],
            "dataset_version": epoch,
            "protected_versions": protected_versions,
            "candidate_versions": candidate_versions,
            "diff": diff,
            "summary": _diff_summary(diff),
            "published_at": record["published_at"],
            "revision_seq": self.revision_seq(),
        }
        audit_rows = store.rows(AUDIT_MODULE)
        current = next((row for row in audit_rows if _int(row.get("epoch")) == epoch), None)
        if current is None:
            audit_rows.append(audit)
        else:
            current.clear()
            current.update(audit)
        return record

    def _last_audit(self, epoch: int) -> dict[str, Any] | None:
        audits = [row for row in store.rows(AUDIT_MODULE) if _int(row.get("epoch")) != epoch]
        return max(audits, key=lambda row: _int(row.get("epoch"))) if audits else None


def _merge_by_key(left: list[dict[str, Any]], right: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {str(item.get(key)): dict(item) for item in left}
    for item in right:
        merged[str(item.get(key))] = dict(item)
    return [merged[name] for name in sorted(merged)]


def _diff_summary(diff: dict[str, list[str]]) -> str:
    parts = []
    if diff["added_protected"]:
        parts.append(f"新增受保护 {len(diff['added_protected'])} 个")
    if diff["removed_protected"]:
        parts.append(f"解除保护 {len(diff['removed_protected'])} 个")
    if diff["added_candidates"]:
        parts.append(f"新增清理候选 {len(diff['added_candidates'])} 个")
    if diff["removed_candidates"]:
        parts.append(f"移出清理候选 {len(diff['removed_candidates'])} 个")
    return "、".join(parts) or "与上一纪元相比无差异"


policy_service = SnapshotPolicyService()
