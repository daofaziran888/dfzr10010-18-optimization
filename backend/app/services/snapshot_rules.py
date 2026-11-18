"""快照保留共用规则：表快照批算、可恢复快照数据集、回滚清单消费同一份保留口径。

这里只放纯函数，不读写内存仓库：
- `snapshot_status`：既有「创建快照 / 恢复快照 / 清理快照」的状态推导；
- `expires_before`：既有「清理快照」判定快照是否过期的那条口径（快照时间 + 保留天数）。

表快照管理、回滚清单、可恢复快照数据集都从这里取判断，避免各处各写一套。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

SNAPSHOT_STATUSES = ["已创建", "保留中", "已过期", "已清理"]

# 既有动作 -> 目标状态；保持表快照管理原有行为不变。
ACTION_TARGET_STATUS = {"创建快照": "已创建", "恢复快照": "保留中", "清理快照": "已过期"}
NEGATIVE_ACTIONS: list[str] = []


def snapshot_status(action: str) -> str:
    """把动作换算成快照状态；调用方负责先校验动作是否在白名单内。"""
    return ACTION_TARGET_STATUS[action]


def _parse_date(value: str) -> date:
    return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()


def expires_before(snapshot_time: object, retention_days: int, today: date) -> bool:
    """保留阈值判定：快照时间严格早于 (今天 - 保留天数) 即过期。"""
    try:
        created = _parse_date(str(snapshot_time))
    except (TypeError, ValueError):
        return False
    return created < today - timedelta(days=max(int(retention_days), 0))
