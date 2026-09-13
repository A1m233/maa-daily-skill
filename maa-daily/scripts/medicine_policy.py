"""Strict, persistent expiring-medicine policy; no game or filesystem writes."""
from __future__ import annotations

from pathlib import Path
import tomllib

MODES = {"off", "use_and_drain"}


def validate_policy(policy: dict) -> dict:
    if not isinstance(policy, dict) or set(policy) != {"mode", "medicine_expire_days"}:
        raise ValueError("invalid_expiring_medicine_fields")
    mode, days = policy["mode"], policy["medicine_expire_days"]
    if not isinstance(mode, str) or mode not in MODES or type(days) is not int or days < 1:
        raise ValueError("policy_requires_off_or_use_and_drain_and_positive_check_days")
    return dict(policy)


def load_policy(path: Path) -> dict:
    data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    if set(data) != {"expiring_medicine"}:
        raise ValueError("policy_requires_expiring_medicine_table_only")
    return validate_policy(data["expiring_medicine"])


def recovery_task(stage: str, policy: dict, maximum: int, max_runs: int) -> dict:
    if policy["mode"] != "use_and_drain" or policy["medicine_expire_days"] <= 0:
        raise ValueError("medicine_use_not_authorized_by_policy")
    if not 1 <= maximum <= 10 or max_runs < 1:
        raise ValueError("positive_recovery_bounds_required")
    return {"type": "Fight", "params": {
        "stage": stage, "series": maximum, "times": max_runs,
        "medicine": 0, "medicine_expire_days": policy["medicine_expire_days"], "stone": 0,
    }}


def inspect_recovery(report: dict, stage: str, days: int, max_runs: int) -> dict:
    """Validate MAA's recovery phase independently of the no-recovery drain loop."""
    from drain_sanity import callbacks
    events = callbacks(report)
    chains = [(k, v.get("taskchain"), v.get("taskid")) for k, v in events if k.startswith("TaskChain")]
    if (len(chains) != 2 or chains[0][:2] != ("TaskChainStart", "Fight")
            or chains[1][:2] != ("TaskChainCompleted", "Fight") or chains[0][2] != chains[1][2]):
        raise ValueError("ambiguous_recovery_chain")
    counts, drops, uses, confirmed = [], [], [], False
    for kind, value in events:
        if not kind.startswith("SubTask"):
            continue
        if value.get("taskchain") != "Fight" or value.get("taskid") != chains[0][2]:
            raise ValueError("unexpected_recovery_origin")
        detail = value.get("details", {})
        if detail.get("task", "").split("@")[-1] == "StoneConfirm" and detail.get("action") not in {None, "DoNothing"}:
            raise ValueError("unexpected_stone_use")
        if detail.get("task", "").split("@")[-1] in {"MedicineConfirm", "ExpiringMedicineConfirm"} and detail.get("action") not in {None, "DoNothing"}:
            confirmed = True
        if kind != "SubTaskExtraInfo":
            continue
        what = value.get("what")
        if what == "FightTimes":
            count = detail.get("times_finished")
            if type(count) is not int or not 0 <= count <= max_runs:
                raise ValueError("invalid_recovery_fight_count")
            counts.append(count)
        elif what == "StageDrops":
            drops.append(detail.get("stage", {}).get("stageCode"))
        elif what == "UseMedicine":
            medicines = detail.get("medicines")
            if detail.get("is_expiring") is not True or not isinstance(medicines, list) or not medicines:
                raise ValueError("unverified_or_nonexpiring_medicine_use")
            for item in medicines:
                if (not isinstance(item, dict) or any(type(item.get(k)) is not int for k in ("use", "inventory", "expire_days"))
                        or not 0 < item["use"] <= item["inventory"] or not 1 <= item["expire_days"] <= days):
                    raise ValueError("medicine_use_outside_policy")
            if type(detail.get("count")) is not int or detail["count"] != sum(m["use"] for m in medicines):
                raise ValueError("medicine_count_mismatch")
            uses.append(detail)
    completed = max(counts, default=0)
    if not counts or (completed > 0 and not drops) or any(s != stage for s in drops) or (completed == 0 and drops):
        raise ValueError("recovery_fights_unverified")
    if confirmed and not uses:
        raise ValueError("medicine_confirmation_without_use_evidence")
    return {"completed_runs": completed, "medicine_used": sum(u["count"] for u in uses),
            "uses": uses, "medicine_goal": "unknown", "run_budget_reached": completed >= max_runs}
