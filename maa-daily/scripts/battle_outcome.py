"""Standard-stage settlement evidence, separate from native execution counts.

Callers must verify the report boundary and Fight chain identity first.
Annihilation has a separate progress contract and does not use this classifier.
"""
from __future__ import annotations


def inspect_outcome(events: list[tuple[str, dict]]) -> dict:
    failures, settlements = [], []
    for kind, value in events:
        detail = value.get("details", {})
        if not isinstance(detail, dict):
            continue
        node = detail.get("task", "").split("@")[-1]
        # Actual recognized/executed nodes only, never candidates in `next`.
        if kind in {"SubTaskStart", "SubTaskCompleted"} and node in {
                "FightMissionFailed", "FightMissionFailedAndStop"}:
            failures.append("mission_failed")
        if kind != "SubTaskExtraInfo" or value.get("what") != "StageDrops":
            continue
        stars, drops = detail.get("stars"), detail.get("drops")
        settlements.append(stars)
        if type(stars) is int and stars == 0:
            failures.append("zero_star_settlement")
        if isinstance(drops, list) and any(
                isinstance(item, dict)
                and (item.get("dropType") == "SANITY_DROP" or item.get("itemId") == "AP_GAMEPLAY")
                and type(item.get("quantity")) is int and item["quantity"] > 0 for item in drops):
            failures.append("sanity_refund")
    if failures:
        return {"status": "failed", "reason": "battle_failed", "signals": sorted(set(failures)),
                "settlement_stars": settlements}
    # Farming completion requires positive three-star evidence for every
    # settlement, including early ones; a later success cannot erase a failure.
    verified = bool(settlements) and all(type(s) is int and s == 3 for s in settlements)
    return {"status": "verified" if verified else "unknown",
            "reason": "three_star_settlements" if verified else "battle_success_unverified",
            "signals": [], "settlement_stars": settlements}


def require_success(events: list[tuple[str, dict]], *, allow_no_battle: bool = False) -> None:
    result = inspect_outcome(events)
    if result["status"] == "failed":
        raise ValueError(result["reason"])
    if allow_no_battle and not result["settlement_stars"]:
        return
    if result["status"] != "verified":
        raise ValueError(result["reason"])


def main(argv=None) -> int:
    import argparse
    import json
    from pathlib import Path
    from drain_sanity import fight_observation
    parser = argparse.ArgumentParser(description="只读复核原始 Fight 报告的结算；不操作游戏，不提供续跑许可。")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = fight_observation(json.loads(args.report.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {"status": "unknown", "reason": str(error), "usable_for_planning": False}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("battle_outcome", {}).get("status") == "verified" else 2


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
