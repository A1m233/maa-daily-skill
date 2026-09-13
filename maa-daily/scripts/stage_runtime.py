"""按关卡生成只读核验资源；仅临时配置中的原生导航禁用战斗。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import shutil

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


def probe_resource(stage: str) -> dict:
    return {VERIFY: {"algorithm": "OcrDetect", "text": [normalize_stage(stage)], "fullMatch": True,
                    "isAscii": True, "roi": [845, 72, 220, 50], "action": "DoNothing",
                    "next": ["MaaDailyCheck@StagePage"], "maxTimes": 1}}


def stop_resource() -> dict:
    stop = {"algorithm": "JustReturn", "action": "Stop", "next": [], "sub": [],
            "onErrorNext": [], "exceededNext": [], "reduceOtherTimes": [], "maxTimes": 1}
    return {name: copy.deepcopy(stop) for node in STOP_NODES for name in (node, "Fight@" + node)}


def isolated_config(source: Path, target: Path, stage: str, *, navigation: bool) -> Path:
    """新目录快照，不改用户 profile/resource。守卫仅注入导航快照。"""
    normalize_stage(stage)
    target.mkdir(parents=True, exist_ok=False)
    for folder in ("profiles", "resource"):
        if (source / folder).is_dir():
            shutil.copytree(source / folder, target / folder)
    for file in ("asst.toml", "asst.json", "asst.yaml", "asst.yml", "cli.toml", "cli.json", "cli.yaml", "cli.yml"):
        if (source / file).is_file():
            shutil.copy2(source / file, target / file)
    (target / "tasks").mkdir()
    task_root = target / "resource/tasks"
    task_root.mkdir(parents=True, exist_ok=True)
    path = task_root / "tasks.json"
    nodes = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    nodes.update(probe_resource(stage))
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
