#!/usr/bin/env python3
"""从日常结果生成用户简报；只读，不启动 MAA，不代替来源/账号/游戏日核验。"""
import argparse
import json
import datetime as dt
from pathlib import Path
import sys

# 可由启用 safe_path 的宿主直接调用；只加入本脚本所属的受信任组件目录。
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from recruit_check import inspect_report
from reward_check import game_day

DONE = {"completed", "completed_with_reminder", "not_scheduled"}
LABELS = {"pre": "前段", "priority": "优先任务", "drain": "清体力", "award": "领奖", "checks": "收尾检查"}


def summarize(result):
    notices, facts = [], []
    def note(code, text):
        if not any(n["code"] == code for n in notices):
            notices.append({"code": code, "text": text})
    steps = result.get("steps", {})
    for key, label in LABELS.items():
        if steps.get(key, {}).get("status") not in DONE:
            note("step_" + key, label + "未完成或未核验。")
    for i, item in enumerate(steps.get("pre", {}).get("tasks", [])):
        recruit = item.get("recruitment")
        if item.get("type") == "Recruit":
            if not recruit or recruit.get("status") != "evaluated":
                note(f"recruit_{i}", "公招结果尚未核验，不能只凭任务完成认定全部开招。")
            else:
                facts.append(f"公招确认开招 {recruit['confirmed_actions']} 次，刷新 {recruit['refresh_actions']} 次")
                for j, pending in enumerate(recruit.get("pending", [])):
                    if pending["outcome"] == "preserved":
                        text = f"公招保留了「{pending['preserved_tag']}」标签，未开招；请决定是否手动处理或调整保留策略。"
                    else:
                        text = "公招有一组标签未确认开招，原因未知；请查看该槽位，不自动补招。"
                    note(f"recruit_{i}_{j}", text)
                if recruit.get("reminder_required") and not recruit.get("pending"):
                    note(f"recruit_{i}_review", "公招存在待核实异常，已确认的动作不代表全部槽位处理完。")
        if item.get("internal_error_lines") or item.get("subtask_error_lines"):
            note("execution_warnings", "运行存在告警，具体记录已保留；不据此猜测商品缺货、余额不足或其它业务原因。")
    for item in steps.get("priority", {}).get("results", []):
        p = item.get("weekly_progress")
        if item.get("status") == "completed" and p:
            facts.append(f"剿灭周进度 {p[0]}/{p[1]}")
    drain = steps.get("drain", {})
    if drain.get("status") in DONE and type(drain.get("remaining_sanity")) is int:
        facts.append(f"{drain.get('stage', '目标关卡')} 完成 {drain.get('completed_runs', '未知')} 场，剩余理智 {drain['remaining_sanity']}")
    medicine = drain.get("medicine_check", {})
    if medicine.get("status") == "detected":
        note("medicine", "仍检测到临期药，请在到期前处理；本轮不自动扩大用药范围。")
    elif medicine.get("status") == "unknown":
        note("medicine", "临期药库存未确认，不能认定已经用光。")
    if drain.get("reminder_required") or drain.get("status") == "completed_with_reminder":
        if not medicine.get("reminder_required"):
            note("drain_review", "清体力组件仍有未解决提醒，详见证据。")
    if type(drain.get("medicine_used")) is int and drain["medicine_used"] > 0:
        facts.append(f"使用理智药 {drain['medicine_used']} 瓶")
    checks = steps.get("checks", {})
    for i, base in enumerate(checks.get("infrastructure", [])):
        if base.get("all_work_completed") != "completed":
            observed = any(c.get("collection_status") == "action_observed" for c in base.get("chains", []))
            note(f"base_{i}", ("基建已观察到收取动作，但未确认全部待办清空。" if observed
                                 else "基建收取结果未确认，请检查是否仍有待收产物。"))
    rewards = checks.get("rewards", {})
    claimed = []
    for key, label in (("daily_orundum", "每日合成玉"), ("daily_annihilation_ticket", "剿灭扫荡券")):
        value = rewards.get(key)
        if rewards.get("status") == "evaluated" and value == "claimed":
            claimed.append(label)
        elif value in {"not_claimed", "claimable", "not_reached"}:
            note(key, label + "尚未领取。")
        else:
            note(key, "无法确认" + label + "是否已领取。")
    if claimed:
        facts.append("、".join(claimed) + "已领")
    # 保留任何尚未翻译的上游提醒，不能让奖励局部 false 清空其它组件提示。
    for key, step in steps.items():
        if step.get("reminder_required") or step.get("status") == "completed_with_reminder":
            prefixes = {"pre": ("recruit_", "execution_warnings"),
                        "drain": ("medicine", "drain_review"),
                        "checks": ("base_", "daily_orundum", "daily_annihilation_ticket")}.get(key, ())
            if not any(n["code"].startswith(prefixes) for n in notices):
                note("review_" + key, LABELS.get(key, key) + "仍有未解决提醒。")
    if result.get("status") not in DONE:
        note("run_incomplete", "本轮流程未完整完成，不能概括为日常全部完成。")
    if result.get("reminder_required") and not notices:
        note("run_review", "本轮仍有未解决提醒，详见证据。")
    incomplete = result.get("status") not in DONE or any(steps.get(k, {}).get("status") not in DONE for k in LABELS)
    status = "incomplete" if incomplete else "completed_with_reminder" if notices else "completed"
    headline = {"incomplete": "日常部分完成", "completed_with_reminder": "日常流程已结束，有事项需要关注", "completed": "日常流程已完成"}[status]
    text = f"{result.get('account', '当前账号')}：{headline}（游戏日 {result.get('game_day', '未确认')}）。"
    if notices:
        text += "\n\n" + "\n".join("- " + n["text"] for n in notices)
    if facts:
        text += "\n\n" + "；".join(facts) + "。"
    return {"status": status, "reminder_required": bool(notices), "notices": notices,
            "facts": facts, "text": text, "source_identity": "caller_must_verify_account_and_game_day"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-result", type=Path, required=True)
    parser.add_argument("--recruit-report", type=Path, action="append", default=[],
                        help="只读回放旧结果缺少的公招报告；调用者先核验同账号同游戏日，不用于自动恢复")
    parser.add_argument("--output-dir", type=Path, help="新建本地简报目录，已存在则拒绝，不改原报告")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        source = json.loads(args.daily_result.read_text(encoding="utf-8"))
        tasks = source.get("steps", {}).get("pre", {}).get("tasks", [])
        if args.recruit_report and any(t.get("type") == "Recruit" for t in tasks):
            raise ValueError("recruit_results_already_present")
        for path in args.recruit_report:
            report = json.loads(path.read_text(encoding="utf-8"))
            if game_day(dt.datetime.fromisoformat(report["ended_at"])) != source.get("game_day"):
                raise ValueError("different_game_day")
            source.setdefault("steps", {}).setdefault("pre", {}).setdefault("tasks", []).append(
                {"type": "Recruit", "recruitment": inspect_report(report)})
        result = summarize(source)
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=False)
            (args.output_dir / "brief.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            (args.output_dir / "brief.md").write_text(result["text"] + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result = {"status": "unknown", "reminder_required": True, "text": "结果文件无法解析，日常完成情况未知。"}
    print(json.dumps(result, ensure_ascii=False) if args.json else result["text"])
    return 2 if result["status"] in {"unknown", "incomplete"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
