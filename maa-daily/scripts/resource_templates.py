#!/usr/bin/env python3
"""Bind reviewed derived tasks to installed MAA templates; never operate the game."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
import uuid

ASSETS = Path(__file__).resolve().parents[1] / "assets"
MANIFEST = "policies/resource-templates.json"
SOURCES = ("daily-checks/tasks.json", "strict-credit-recruit-permit/tasks/tasks.json")
# Only template identity is supplied by MAA. Actions, branches, ROIs explicitly
# set by the skill and user business preferences are never imported here.
BINDINGS = {
    "MaaDailyCheck@RewardNavHome": "Award",
    "MaaDailyCheck@RewardNavMenuOpen": "QuickSwitch@ToHome@Open",
    "MaaDailyCheck@RewardNavMenuEntry": "QuickSwitch@ToHome@Entry",
    "MaaDailyCheck@RewardNavConfirm": "QuickSwitch@Confirm",
    "MaaDailyCheck@DailyPage": "DailyTask",
    "MaaDailyCredit@CreditShop-Bought": "CreditShop-Bought",
    "MaaDailyCredit@CrisisPopup": "CrisisPopup",
}


def read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"resource_object_required: {path}")
    return value


def baseline() -> dict:
    return {k: v for source in SOURCES for k, v in read(ASSETS / source).items() if k in BINDINGS}


def templates(roots: list[Path], bases=None) -> dict:
    """Read verified resource layers in load order; reject ambiguous definitions.

    This is deliberately not a ProcessTask interpreter. Bound bases must be
    explicitly defined MatchTemplate nodes. Other inheritance needs review.
    """
    if not roots or len(set(roots)) != len(roots):
        raise ValueError("resource_layers_missing_or_duplicate")
    definitions, ambiguous = {}, set()
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"resource_layer_missing: {root}")
        layer = {}
        for path in sorted((root / "tasks").rglob("*.json")):
            for name, value in read(path).items():
                if not isinstance(value, dict):
                    continue
                if name in layer:
                    ambiguous.add(name)
                layer[name] = value
        for name, value in layer.items():
            # MaaCore replaces a previous same-name definition with baseTask;
            # ordinary overlay fields are merged.
            definitions[name] = value if "baseTask" in value else {**definitions.get(name, {}), **value}
    def algorithm(name, seen=()):
        if name in seen or name in ambiguous:
            raise ValueError(f"ambiguous_base_definition: {name}")
        if name not in definitions:
            for index in range(1, len(name.split("@"))):
                suffix = "@".join(name.split("@")[index:])
                if suffix in definitions:
                    return algorithm(suffix, (*seen, name))
            raise ValueError(f"official_base_missing: {name}")
        node = definitions[name]
        if "algorithm" in node:
            return node["algorithm"]
        parent = node.get("baseTask")
        if parent:
            return algorithm(parent, (*seen, name))
        for index in range(1, len(name.split("@"))):
            suffix = "@".join(name.split("@")[index:])
            if suffix in definitions:
                return algorithm(suffix, (*seen, name))
        return "MatchTemplate"

    result = {}
    for name in set(BINDINGS.values()) if bases is None else set(bases):
        if name not in definitions:
            raise ValueError(f"official_base_missing: {name}")
        node = definitions[name]
        if algorithm(name) != "MatchTemplate":
            raise ValueError(f"official_algorithm_changed: {name}")
        value = node.get("template", name + ".png")
        files = [value] if isinstance(value, str) else value
        if not isinstance(files, list) or not files or any(not isinstance(x, str) or not x for x in files):
            raise ValueError(f"invalid_official_template: {name}")
        for filename in files:
            relative = Path(filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe_template_path: {name}")
            # MAA template resources are recursively indexed by filename/path.
            if not any((root / "template" / relative).is_file() or
                       (len(relative.parts) == 1 and any(p.is_file() for p in (root / "template").rglob(filename))) for root in roots):
                raise ValueError(f"official_template_missing: {name}: {filename}")
        result[name] = value
    return result


def manifest(config: Path) -> dict | None:
    path = config / MANIFEST
    if not path.exists():
        return None
    value = read(path)
    if (value.get("version") != 1 or not isinstance(value.get("roots"), list)
            or not value["roots"] or any(not isinstance(p, str) or not Path(p).is_absolute() for p in value["roots"])
            or not isinstance(value.get("nodes"), dict) or not value["nodes"]
            or not set(value["nodes"]).issubset(BINDINGS)):
        raise ValueError("invalid_resource_template_manifest")
    return value


def expected_nodes(config: Path, nodes: dict) -> dict:
    saved = manifest(config)
    if saved is None:
        return nodes
    resolved = templates([Path(p) for p in saved["roots"]], [BINDINGS[n] for n in saved["nodes"]])
    result = copy.deepcopy(nodes)
    for name in result.keys() & saved["nodes"].keys():
        result[name]["template"] = resolved[BINDINGS[name]]
    return result


def check(config: Path) -> dict:
    reject_user_base_overrides(config)
    actual = read(config / "resource/tasks/tasks.json")
    saved = manifest(config)
    if saved is None:
        raise ValueError("resource_template_binding_required: run resource_templates.py bind")
    if set(saved["nodes"]) != set(BINDINGS) & set(actual):
        raise ValueError("resource_template_binding_targets_changed: review and bind before running")
    expected = expected_nodes(config, baseline())
    for name in saved["nodes"]:
        if actual.get(name) != expected[name]:
            raise ValueError(f"resource_template_stale_or_changed: {name}; review and bind before running")
    return {"status": "current", "bound_nodes": len(saved["nodes"])}


def reject_user_base_overrides(config: Path) -> None:
    for path in (config / "resource/tasks").rglob("*.json"):
        overlap = set(read(path)) & set(BINDINGS.values())
        if overlap:
            raise ValueError("user_overrides_official_binding_base: " + ", ".join(sorted(overlap)))


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        f.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        temp = Path(f.name)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def bind(config: Path, roots: list[Path]) -> dict:
    reject_user_base_overrides(config)
    target = config / "resource/tasks/tasks.json"
    original = target.read_bytes()
    actual = read(target)
    saved = manifest(config)
    roots = [p.resolve(strict=True) for p in roots]
    if any(p == (config / "resource").resolve() for p in roots):
        raise ValueError("user_resource_is_not_an_official_layer")
    resolved = templates(roots, [BINDINGS[n] for n in actual.keys() & BINDINGS.keys()])
    owned = {}
    for name, node in baseline().items():
        if name not in actual:
            continue  # optional credit example is never installed implicitly
        if (not isinstance(actual[name], dict)
                or {k: v for k, v in actual[name].items() if k != "template"}
                != {k: v for k, v in node.items() if k != "template"}):
            raise ValueError(f"resource_policy_conflict: {name}; bind changes templates only")
        previous = saved["nodes"].get(name) if saved else None
        if actual[name] != node and (previous is None or actual[name] != previous):
            raise ValueError(f"resource_conflict: {name}; user changes preserved")
        owned[name] = {**actual[name], "template": resolved[BINDINGS[name]]}
    if not owned:
        raise ValueError("no_deployed_binding_targets: run daily_checks.py prepare first")
    new_manifest = {"version": 1, "roots": [str(p) for p in roots], "nodes": owned}
    merged = {**actual, **owned}
    if saved == new_manifest and merged == actual:
        return {"status": "current", "changed": False, "bound_nodes": len(owned)}
    backup = target.with_name(target.name + ".bak-" + uuid.uuid4().hex)
    with backup.open("xb") as f:
        f.write(original)
    # All validation precedes writes; a torn pair is rejected by check(), not
    # silently accepted or repaired during game execution.
    atomic(target, merged)
    atomic(config / MANIFEST, new_manifest)
    return {"status": "bound", "changed": True, "bound_nodes": len(owned), "backup": str(backup)}


def main() -> int:
    parser = argparse.ArgumentParser(description="核对或绑定已部署派生节点的官方模板；不运行 MAA，不改业务参数。")
    parser.add_argument("command", choices=("check", "bind"))
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--resource-dir", type=Path, action="append", help="已核实的官方资源层，按实际加载顺序重复传入；不包含用户资源")
    args = parser.parse_args()
    if args.command == "check" and args.resource_dir:
        parser.error("--resource-dir is only accepted by bind; check uses the saved verified layers")
    try:
        result = bind(args.config_dir, args.resource_dir or []) if args.command == "bind" else check(args.config_dir)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "not_ready", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
