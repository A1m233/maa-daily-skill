#!/usr/bin/env python3
"""解析本机账号配置清单；可选按共用配置分组预检，不切号或执行游戏业务。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys
import tomllib

from daily_run import load_config, main as daily_main
from reward_check import game_day


def text_field(value, field: str, *, token: bool = False) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or (token and not re.fullmatch(r"[\w-]+", value))):
        raise ValueError("invalid_" + field)
    return value


def resolve_config(root: Path, value) -> str:
    path = (root / text_field(value, "config_path")).resolve()
    # 复用单账号 schema 与用药策略验证；不解析原生 task，不做 dry-run。
    load_config(path)
    return str(path)


def load_plan(path: Path) -> dict:
    path = path.resolve()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    allowed = {"version", "profile", "client", "default_config", "accounts"}
    if set(data) - allowed or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("unsupported_account_plan")
    profile = text_field(data.get("profile"), "profile")
    client = text_field(data.get("client"), "client")
    entries = data.get("accounts")
    if not isinstance(entries, list) or not entries:
        raise ValueError("accounts_required")
    default = None
    if "default_config" in data:
        try:
            default = resolve_config(path.parent, data["default_config"])
        except (OSError, ValueError, TypeError):
            raise ValueError("invalid_default_config") from None
    aliases, identities, resolved = set(), [], []
    for index, entry in enumerate(entries, 1):
        try:
            if not isinstance(entry, dict) or set(entry) - {"alias", "account_name", "config"}:
                raise ValueError("invalid_account_fields")
            alias = text_field(entry.get("alias"), "alias", token=True)
            identity = text_field(entry.get("account_name"), "account_name")
            if alias.casefold() in aliases:
                raise ValueError("duplicate_alias")
            # 登录名可为片段；清单内互相包含的片段不能用于唯一匹配。
            key = identity.casefold()
            if any(key in previous or previous in key for previous in identities):
                raise ValueError("overlapping_account_names")
            config = resolve_config(path.parent, entry["config"]) if "config" in entry else default
            if config is None:
                raise ValueError("account_config_required")
            aliases.add(alias.casefold())
            identities.append(key)
            resolved.append({"alias": alias, "account_name": identity, "config": config})
        except (OSError, ValueError, TypeError) as error:
            # 不把完整登录名、TOML 原文或路径相关系统错误带入对外错误消息。
            reason = str(error) if type(error) is ValueError and re.fullmatch(r"[a-z_]+", str(error)) else "invalid_config"
            raise ValueError(f"account_{index}: {reason}") from None
    return {"profile": profile, "client": client, "accounts": resolved}


def preflight_groups(entries: list[dict]) -> list[dict]:
    """只合并同一规范路径，不合并内容相似但独立的配置。"""
    groups = {}
    for entry in entries:
        key = Path(entry["config"]).resolve()
        groups.setdefault(key, {"config": str(key), "accounts": []})["accounts"].append(entry["alias"])
    return list(groups.values())


def preflight_plan(path: Path, plan: dict, entries: list[dict], maa: str, *, verbose=False) -> dict:
    """本次调用内去重；不缓存或签发运行许可，run 仍自行即时预检。"""
    day = game_day(dt.datetime.now(dt.timezone.utc))
    groups = [{**g, "status": "pending"} for g in preflight_groups(entries)]
    result = {"status": "incomplete", "game_day": day, "profile": plan["profile"],
              "accounts": [e["alias"] for e in entries], "groups": groups,
              "game_operated": False, "identity_verified": False, "run_preflight_required": True}
    # 清单和执行配置/药物策略若在本批检查期间改变，不返回成功。
    paths = {path.resolve()}
    for group in groups:
        config = Path(group["config"])
        paths.update((config, Path(load_config(config)["medicine_policy"])))
    snapshots = {p: p.read_bytes() for p in paths}
    def unchanged():
        if game_day(dt.datetime.now(dt.timezone.utc)) != day:
            raise ValueError("game_day_changed")
        if any(p.read_bytes() != data for p, data in snapshots.items()) or load_plan(path) != plan:
            raise ValueError("account_plan_changed_during_preflight")
    try:
        for group in groups:
            unchanged()
            print("预检账号配置组：" + ", ".join(group["accounts"]), flush=True)
            code = daily_main(["preflight", "--config", group["config"], "--maa", maa,
                               "--profile", plan["profile"], "--account", group["accounts"][0],
                               *(["--verbose"] if verbose else [])])
            group.update(status="prepared" if code == 0 else "failed", exit_code=code)
            if code:
                result["reason"] = "group_preflight_failed"
                return result
            unchanged()
        result["status"] = "prepared"
    except (OSError, ValueError, TypeError) as error:
        result["reason"] = str(error) if type(error) is ValueError else "preflight_input_unreadable"
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True, help="本机账号清单 TOML")
    parser.add_argument("--account", help="精确别名；只在选中时输出该账号的本地登录标识")
    parser.add_argument("--preflight", action="store_true", help="对所选目标按配置路径去重后执行提前预检；有本地写入和可能的热更新，不操作游戏")
    parser.add_argument("--maa", default="maa", help="仅 --preflight 使用的 maa-cli 路径")
    parser.add_argument("--verbose", action="store_true", help="预检诊断用详细输出")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.file)
        if args.account is not None:
            entries = [item for item in plan["accounts"] if item["alias"] == args.account]
            if not entries:
                raise ValueError("account_not_found")
        else:
            entries = [{k: v for k, v in item.items() if k != "account_name"} for item in plan["accounts"]]
        if args.preflight:
            result = preflight_plan(args.file, plan, entries, args.maa, verbose=args.verbose)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] == "prepared" else 2
        print(json.dumps({"status": "resolved", "profile": plan["profile"], "client": plan["client"],
                          "accounts": entries, "identity_verified": False,
                          "tasks_validated": False, "preflight_groups": preflight_groups(entries)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError) as error:
        reason = str(error) if type(error) is ValueError else "account_plan_unreadable_or_invalid"
        print(json.dumps({"status": "invalid", "reason": reason}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
