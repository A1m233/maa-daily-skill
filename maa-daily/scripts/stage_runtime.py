"""按关卡生成只读核验资源；仅临时配置中的原生导航禁用战斗。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import shutil

from resource_templates import MANIFEST, manifest, read

VERIFY = "MaaDailyCheck@VerifyStage"
STOP_NODES = {"FightBegin", "StartButton1", "StartButton2", "MedicineConfirm",
              "ExpiringMedicineConfirm", "StoneConfirm"}


def normalize_stage(stage: str) -> str:
    value = stage.strip().upper()
    # 普通数字关卡代码，不接受资源表达式、难度后缀或剿灭等特殊任务。
    if value.startswith("SSREOPEN-") or not re.fullmatch(r"(?:[A-Z]{0,4}\d{1,2}|[A-Z]{1,8}(?:-[A-Z]{1,4})?)-\d{1,2}", value):
        raise ValueError("unsupported_standard_stage_code")
    return value


def probe_task(stage: str) -> dict:
    normalize_stage(stage)
    return {"type": "Custom", "params": {"task_names": [VERIFY]}}


def stage_ocr_overrides(config: Path) -> dict:
    """在已绑定资源的规则之前补齐标题大小写；不复制固定版本的规则表。"""
    binding = manifest(config)
    if binding is None:
        # 旧的独立调用仍交给 MaaCore 继承；完整日常要求先完成资源绑定。
        return {}
    base = "ClickedCorrectStage"
    definition = {}
    for root in [*(Path(p) for p in binding["roots"]), config / "resource"]:
        if not root.is_dir():
            raise ValueError(f"stage_ocr_resource_layer_missing: {root}")
        matches = [data[base] for path in sorted((root / "tasks").rglob("*.json"))
                   if base in (data := read(path))]
        if len(matches) > 1:
            raise ValueError("ambiguous_stage_ocr_definition")
        if matches:
            node = matches[0]
            if not isinstance(node, dict):
                raise ValueError("invalid_stage_ocr_definition")
            definition = node if "baseTask" in node else {**definition, **node}
    # 此处只读直接 OCR 定义及普通字段覆盖，不自行模拟 MaaCore 的任务继承。
    if (definition.get("baseTask") not in (None, "#none")
            or definition.get("algorithm") != "OcrDetect"
            or definition.get("replaceFull", False) is not False):
        raise ValueError("unsupported_stage_ocr_definition")
    rules = definition.get("ocrReplace")
    if not isinstance(rules, list) or any(
            not isinstance(pair, list) or len(pair) != 2
            or any(not isinstance(value, str) for value in pair) for pair in rules):
        raise ValueError("invalid_stage_ocr_replacements")
    # 显式字符类兼容原生正则；只删除开头的完整标题，不改关卡代码或匹配方式。
    return {"ocrReplace": [["^[Oo][Pp][Ee][Rr][Aa][Tt][Ii][Oo][Nn]", ""], *rules],
            "replaceFull": False}


def probe_resource(stage: str, ocr_overrides: dict | None = None) -> dict:
    # 继承当前官方准备页的 OCR 归一化，兼容连读的 OPERATION 前缀与标点。
    return {VERIFY: {"baseTask": "ClickedCorrectStage", **(ocr_overrides or {}),
                    "algorithm": "OcrDetect", "text": [normalize_stage(stage)], "fullMatch": True,
                    "isAscii": True, "roi": [845, 72, 220, 50], "action": "DoNothing",
                    "next": ["MaaDailyCheck@StagePage"], "maxTimes": 1}}


def stop_resource() -> dict:
    stop = {"algorithm": "JustReturn", "action": "Stop", "next": [], "sub": [],
            "onErrorNext": [], "exceededNext": [], "reduceOtherTimes": [], "maxTimes": 1}
    return {name: copy.deepcopy(stop) for node in STOP_NODES for name in (node, "Fight@" + node)}


def _snapshot_backups(source: Path, directory: str, names: list[str]) -> set[str]:
    relative = Path(directory).relative_to(source).parts
    if relative[:1] == ("profiles",):
        extension = r"(?:toml|json|yaml|yml)"
    elif relative[:2] == ("resource", "tasks"):
        extension = "json"
    else:
        return set()
    # 只识别配置文件的显式备份；模板名和目录可以合法包含 .bak。
    suffix = r"\.bak(?:-(?:[0-9a-f]{32}|\d{8}(?:-\d{4,6})?))?"
    return {name for name in names
            if re.fullmatch(r".+\." + extension + suffix, name, re.IGNORECASE)
            and (Path(directory) / name).is_file()}


def isolated_config(source: Path, target: Path, stage: str, *, navigation: bool) -> Path:
    """新目录快照，不改用户 profile/resource。守卫仅注入导航快照。"""
    normalize_stage(stage)
    ocr_overrides = stage_ocr_overrides(source)
    target.mkdir(parents=True, exist_ok=False)
    # 日常执行器会把此快照交给清体力子进程再次快照；绑定必须随资源一起传递。
    # 只带入资源元数据，其它业务策略仍由调用方显式传参，不复制整个 policies。
    if (source / MANIFEST).is_file():
        (target / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / MANIFEST, target / MANIFEST)
    for folder in ("profiles", "resource"):
        if (source / folder).is_dir():
            shutil.copytree(source / folder, target / folder,
                            ignore=lambda directory, names: _snapshot_backups(source, directory, names))
    for file in ("asst.toml", "asst.json", "asst.yaml", "asst.yml", "cli.toml", "cli.json", "cli.yaml", "cli.yml"):
        if (source / file).is_file():
            shutil.copy2(source / file, target / file)
    (target / "tasks").mkdir()
    task_root = target / "resource/tasks"
    task_root.mkdir(parents=True, exist_ok=True)
    path = task_root / "tasks.json"
    nodes = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    nodes.update(probe_resource(stage, ocr_overrides))
    if navigation:
        guards = stop_resource()
        # 显式用户命名空间可能遮蔽基础节点：仅在快照内一起阻断。
        for file in task_root.rglob("*.json"):
            if file == path:
                continue
            data = json.loads(file.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                for name in list(data):
                    if name.split("@")[-1] in STOP_NODES:
                        data[name] = copy.deepcopy(guards[name.split("@")[-1]])
                file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        for name in list(nodes):
            if name.split("@")[-1] in STOP_NODES:
                nodes[name] = copy.deepcopy(guards[name.split("@")[-1]])
        nodes.update(guards)
    path.write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
    return target
