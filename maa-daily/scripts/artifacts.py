#!/usr/bin/env python3
"""仅管理显式登记的组件产物。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

ENV_NAME = "MAA_DAILY_ARTIFACT_RUN"
MANIFEST_NAME = ".maa-daily-run.json"
DEFAULT_POLICY = {"keep_days": 14, "max_bytes": 128 * 1024 * 1024}
VERSION = 1


class ArtifactError(RuntimeError):
    """产物归属、锁或容量门禁未通过。"""


def _absolute(path: Path) -> Path:
    path = Path(path)
    if ".." in path.parts:
        raise ArtifactError(f"artifact_parent_traversal: {path}")
    return Path(os.path.abspath(path))


def _safe(path: Path, *, missing: bool = False) -> Path:
    """逐级 lstat，拒绝所有 symlink/junction/reparse，包括路径祖先。"""
    path = _absolute(path)
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ArtifactError(f"artifact_link_refused: {part}")
    if path.resolve(strict=not missing) != path:
        raise ArtifactError(f"artifact_noncanonical_path: {path}")
    return path


def safe_path(path: Path, *, missing: bool = False) -> Path:
    """供调用方在创建文件前复用同一套路径/祖先链接检查。"""
    try:
        return _safe(path, missing=missing)
    except OSError as exc:
        raise ArtifactError(f"artifact_path_unavailable: {path}: {exc}") from exc


def _read(path: Path) -> dict:
    _safe(path)
    if not path.is_file():
        raise ArtifactError(f"artifact_metadata_not_file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ArtifactError(f"artifact_metadata_not_object: {path}")
    return value


def _write(path: Path, value: dict) -> None:
    _safe(path, missing=True)
    temporary = path.with_name(path.name + ".tmp")
    _safe(temporary, missing=True)
    created = False
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _safe(path, missing=True)
        os.replace(temporary, path)
    except BaseException:
        # 既有 .tmp 不属于本次写入，不能擅自删除。
        if created:
            try:
                _safe(temporary)
                temporary.unlink()
            except (OSError, ArtifactError):
                pass
        raise


@contextmanager
def _lock(store: Path):
    _safe(store)
    path = store / "store.lock"
    _safe(path, missing=True)
    token = uuid.uuid4().hex
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "token": token}))
    except FileExistsError as exc:
        raise ArtifactError(f"artifact_store_locked: {path}") from exc
    try:
        yield
    finally:
        # 锁永不按 PID/mtime 抢占；仅释放本次创建且仍保持原归属的锁。
        if _read(path).get("token") == token:
            path.unlink()


def _load(store: Path) -> tuple[dict, dict]:
    policy = _read(store / "policy.json")
    if (set(policy) != set(DEFAULT_POLICY)
            or any(type(policy[k]) is not int or policy[k] < 1 for k in DEFAULT_POLICY)):
        raise ArtifactError("artifact_invalid_policy")
    index = _read(store / "index.json")
    if (index.get("version") != VERSION or index.get("store") != str(store)
            or not re.fullmatch(r"[0-9a-f]{32}", str(index.get("store_id", "")))
            or not isinstance(index.get("runs"), list)):
        raise ArtifactError("artifact_invalid_index")
    ids = [r.get("run_id") if isinstance(r, dict) else None for r in index["runs"]]
    if any(not isinstance(run_id, str) for run_id in ids) or len(ids) != len(set(ids)):
        raise ArtifactError("artifact_duplicate_or_invalid_run_id")
    return policy, index


def _store(config: Path) -> Path:
    return _safe(_absolute(config) / "maa-daily-artifacts", missing=True)


def _initialize(store: Path) -> None:
    _write(store / "policy.json", dict(DEFAULT_POLICY))
    _write(store / "index.json", {"version": VERSION, "store": str(store),
                                 "store_id": uuid.uuid4().hex, "runs": []})


def _validate(record: dict, store: Path, index: dict) -> Path:
    if (not isinstance(record, dict) or record.get("version") != VERSION
            or record.get("store") != str(store) or record.get("store_id") != index["store_id"]
            or not re.fullmatch(r"[0-9a-f]{32}", str(record.get("run_id", "")))
            or record.get("state") not in {"active", "finished", "uncertain"}
            or type(record.get("pid")) is not int or record["pid"] < 1
            or type(record.get("created_at")) not in {int, float}
            or not math.isfinite(record["created_at"])
            or not isinstance(record.get("files"), list)):
        raise ArtifactError("artifact_invalid_run_record")
    path = _absolute(Path(record["path"]))
    parent = _absolute(Path(record["output_parent"]))
    if str(path) != record["path"] or str(parent) != record["output_parent"] or path.parent != parent:
        raise ArtifactError("artifact_run_parent_mismatch")
    _safe(path)
    if not path.is_dir() or path == store or path in store.parents:
        raise ArtifactError("artifact_invalid_run_path")
    manifest = _read(path / MANIFEST_NAME)
    if manifest != record:
        raise ArtifactError(f"artifact_manifest_mismatch: {path}")
    if record["state"] != "active":
        if (type(record.get("finished_at")) not in {int, float}
                or not math.isfinite(record["finished_at"])):
            raise ArtifactError("artifact_invalid_finished_time")
    return path


def _tree_bytes(path: Path) -> int:
    _safe(path)
    total = 0
    with os.scandir(path) as entries:
        for entry in entries:
            child = Path(entry.path)
            _safe(child)
            info = child.lstat()
            if stat.S_ISDIR(info.st_mode):
                total += _tree_bytes(child)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
            else:
                raise ArtifactError(f"artifact_special_file_refused: {child}")
    return total


def _digest(path: Path) -> tuple[int, str]:
    _safe(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ArtifactError(f"artifact_external_not_file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    _safe(path)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ArtifactError(f"artifact_file_changed_during_read: {path}")
    return after.st_size, digest.hexdigest()


def _external(record: dict) -> tuple[int, list[Path]]:
    total, paths = 0, []
    for item in record["files"]:
        if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                or type(item.get("size")) is not int or item["size"] < 0
                or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))):
            raise ArtifactError("artifact_invalid_external_record")
        path = _safe(Path(item["path"]), missing=True)
        if str(path) != item["path"] or path.is_relative_to(Path(record["path"])):
            raise ArtifactError("artifact_external_path_mismatch")
        try:
            size, digest = _digest(path)
        except FileNotFoundError:
            continue
        if size != item["size"] or digest != item["sha256"]:
            raise ArtifactError(f"artifact_external_modified: {path}")
        if path in paths:
            raise ArtifactError("artifact_duplicate_external_path")
        total += size
        paths.append(path)
    return total, paths


def _remove_tree(path: Path, *, root: bool = True) -> None:
    """保留顶层归属文件到最后；局部删除失败后登记与标记仍可供后续检查。"""
    _safe(path)
    with os.scandir(path) as entries:
        children = [Path(entry.path) for entry in entries]
    for child in children:
        if root and child.name == MANIFEST_NAME:
            continue
        _safe(child)
        info = child.lstat()
        if stat.S_ISDIR(info.st_mode):
            _remove_tree(child, root=False)
        elif stat.S_ISREG(info.st_mode):
            child.unlink()
        else:
            raise ArtifactError(f"artifact_special_file_refused: {child}")
    if root:
        marker = path / MANIFEST_NAME
        data = _read(marker)
        marker.unlink()
        try:
            path.rmdir()
        except OSError:
            if not marker.exists():
                _write(marker, data)
            raise
    else:
        path.rmdir()


def _cleanup(store: Path, policy: dict, index: dict, *, apply: bool,
             protected: set[str] | None = None, reserve: int = 0) -> dict:
    result = {"store": str(store), "policy": policy, "registered_bytes": 0,
              "remaining_bytes": 0, "size_complete": True, "budget_ok": True,
              "runs": [], "would_delete": [], "deleted": [], "warnings": [], "applied": apply}
    protected = protected or set()
    candidates = []
    cutoff = time.time() - policy["keep_days"] * 86400
    for record in index["runs"]:
        row = {"run_id": record.get("run_id"), "path": record.get("path"),
               "state": record.get("state"), "bytes": None, "protected": True}
        result["runs"].append(row)
        try:
            path = _validate(record, store, index)
            extra, _ = _external(record)
            row["bytes"] = _tree_bytes(path) + extra
            result["registered_bytes"] += row["bytes"]
            row["protected"] = record["state"] != "finished" or record["run_id"] in protected
            if not row["protected"]:
                candidates.append((record["finished_at"], record, row))
        except (OSError, ValueError, KeyError, TypeError, ArtifactError) as exc:
            result["size_complete"] = False
            row["warning"] = str(exc)
            result["warnings"].append(f"artifact_protected: {record.get('path')}: {exc}")
    remaining = result["registered_bytes"]
    for ended, record, row in sorted(candidates, key=lambda value: value[0]):
        if ended > cutoff and remaining + reserve <= policy["max_bytes"]:
            continue
        result["would_delete"].append(record["path"])
        if apply:
            try:
                path = _validate(record, store, index)
                _tree_bytes(path)  # 删除前再检查整棵树，遇到链接或 ACL 失败不动该包。
                _, external = _external(record)
                for target in external:
                    _safe(target)
                    target.unlink()
                _remove_tree(path)
                index["runs"].remove(record)
                _write(store / "index.json", index)
                result["deleted"].append(record["path"])
            except (OSError, ValueError, KeyError, TypeError, ArtifactError) as exc:
                result["warnings"].append(f"artifact_cleanup_failed: {record['path']}: {exc}")
                result["size_complete"] = False
                continue
        remaining -= row["bytes"]
    result["remaining_bytes"] = remaining
    result["budget_ok"] = result["size_complete"] and remaining + reserve <= policy["max_bytes"]
    if not result["budget_ok"]:
        result["warnings"].append("artifact_budget_unavailable: protected or unmeasurable artifacts prevent a new run")
    return result


def _failure(store: Path, exc: Exception, *, apply: bool) -> dict:
    return {"store": str(store), "policy": None, "registered_bytes": None, "remaining_bytes": None,
            "size_complete": False, "budget_ok": False, "runs": [], "would_delete": [], "deleted": [],
            "warnings": [str(exc)], "applied": apply}


def manage(config: Path, apply: bool = False) -> dict:
    """只读预览不初始化目录/策略、不创建锁，也不会跟随已有链接。"""
    store = _absolute(config) / "maa-daily-artifacts"
    try:
        _safe(store, missing=True)
        if not store.exists():
            return {"store": str(store), "policy": dict(DEFAULT_POLICY), "registered_bytes": 0,
                    "remaining_bytes": 0, "size_complete": True, "budget_ok": True,
                    "runs": [], "would_delete": [], "deleted": [], "warnings": [], "applied": apply}
        if apply:
            with _lock(store):
                policy, index = _load(store)
                return _cleanup(store, policy, index, apply=True)
        if (store / "store.lock").exists():
            raise ArtifactError(f"artifact_store_locked: {store / 'store.lock'}")
        policy, index = _load(store)
        result = _cleanup(store, policy, index, apply=False)
        if (store / "store.lock").exists() or _read(store / "index.json") != index:
            raise ArtifactError("artifact_store_changed_during_inspection")
        return result
    except (OSError, ValueError, KeyError, TypeError, ArtifactError) as exc:
        return _failure(store, exc, apply=apply)


class ArtifactRun:
    def __init__(self, path: Path, top: Path, store: Path, run_id: str, owner: bool):
        self.path, self.top, self.store, self.run_id, self.owner = path, top, store, run_id, owner
        self.warnings: list[str] = []

    @classmethod
    def begin(cls, config: Path, output: Path | None, kind: str, prefix: str = "drain-",
              parent: ArtifactRun | None = None) -> ArtifactRun:
        try:
            return cls._begin(config, output, kind, prefix, parent)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ArtifactError(f"artifact_begin_failed: {exc}") from exc

    @classmethod
    def current(cls) -> ArtifactRun | None:
        context = os.environ.get(ENV_NAME)
        if context is None:
            return None
        try:
            top = _safe(Path(context))
            manifest = _read(top / MANIFEST_NAME)
            store = _safe(Path(manifest["store"]))
            with _lock(store):
                _, index = _load(store)
                record = next((r for r in index["runs"] if r.get("path") == str(top)), None)
                if record is None or _validate(record, store, index) != top or record["state"] != "active":
                    raise ArtifactError("artifact_parent_not_active_or_registered")
                return cls(top, top, store, record["run_id"], False)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ArtifactError(f"artifact_parent_invalid: {exc}") from exc

    @classmethod
    def _begin(cls, config: Path, output: Path | None, kind: str, prefix: str,
               parent: ArtifactRun | None) -> ArtifactRun:
        if not isinstance(kind, str) or not kind or not re.fullmatch(r"[A-Za-z0-9_-]+", prefix):
            raise ArtifactError("artifact_invalid_kind_or_prefix")
        context = parent.top if parent is not None else os.environ.get(ENV_NAME)
        if context is not None:
            top = _safe(Path(context))
            manifest = _read(top / MANIFEST_NAME)
            store = _safe(Path(manifest["store"]))
            with _lock(store):
                _, index = _load(store)
                record = next((r for r in index["runs"] if r.get("path") == str(top)), None)
                if record is None or _validate(record, store, index) != top or record["state"] != "active":
                    raise ArtifactError("artifact_parent_not_active_or_registered")
                if parent is not None and (parent.run_id != record["run_id"] or parent.store != store):
                    raise ArtifactError("artifact_parent_handle_mismatch")
                base = _safe(output if output is not None else (parent.path if parent else top), missing=True)
                if not base.is_relative_to(top):
                    raise ArtifactError("artifact_child_outside_parent")
                if parent is not None and not base.is_relative_to(parent.path):
                    raise ArtifactError("artifact_child_outside_parent")
                base.mkdir(parents=True, exist_ok=True)
                path = base / (prefix + uuid.uuid4().hex)
                path.mkdir()
                return cls(path, top, store, record["run_id"], False)
        store = _store(config)
        try:
            store.mkdir(parents=True)
            created = True
        except FileExistsError:
            created = False
        with _lock(store):
            if created:
                _initialize(store)
            policy, index = _load(store)
            base = _safe(output if output is not None else store / "runs", missing=True)
            # 不允许把新顶层包放入另一个已登记包，避免双计数和父子生命周期分裂。
            for existing in index["runs"]:
                previous = Path(existing["path"])
                if base.is_relative_to(previous):
                    raise ArtifactError("artifact_nested_run_requires_parent_context")
            run_id = uuid.uuid4().hex
            path = base / (prefix + run_id)
            record = {"version": VERSION, "store": str(store), "store_id": index["store_id"],
                      "run_id": run_id, "path": str(path), "output_parent": str(base),
                      "pid": os.getpid(), "state": "active", "created_at": time.time(),
                      "finished_at": None, "kind": kind, "status": None, "files": []}
            # 最小归属文件也占空间；旧包恰好用满预算时不能再无限创建空运行。
            reserve = len((json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            report = _cleanup(store, policy, index, apply=True, reserve=reserve)
            if not report["budget_ok"]:
                raise ArtifactError("; ".join(report["warnings"]))
            base.mkdir(parents=True, exist_ok=True)
            # 先登记再创建：登记写入失败不遗留未登记包；创建中断时保留 active
            # 记录，后续统计 fail closed，避免连续失败产生无法治理的空目录。
            index["runs"].append(record)
            _write(store / "index.json", index)
            path.mkdir()
            _write(path / MANIFEST_NAME, record)
            handle = cls(path, path, store, run_id, True)
            handle.warnings = report["warnings"]
            return handle

    def environment(self, env: dict | None = None) -> dict:
        result = dict(os.environ if env is None else env)
        result[ENV_NAME] = str(self.top)
        return result

    def _record(self, index: dict) -> dict:
        record = next((r for r in index["runs"] if r.get("run_id") == self.run_id), None)
        if record is None or _validate(record, self.store, index) != self.top:
            raise ArtifactError("artifact_run_not_registered")
        return record

    def register_file(self, path: Path) -> None:
        """调用方必须先确认包外文件不存在，再生成并登记；从不接管其父目录。"""
        try:
            self._register_file(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ArtifactError(f"artifact_file_registration_failed: {exc}") from exc

    def _register_file(self, path: Path) -> None:
        path = _safe(path)
        if path.is_relative_to(self.top):
            return
        with _lock(self.store):
            _, index = _load(self.store)
            record = self._record(index)
            if record["state"] != "active":
                raise ArtifactError("artifact_run_not_active")
            for other in index["runs"]:
                if path.is_relative_to(Path(other["path"])) or any(f["path"] == str(path) for f in other["files"]):
                    raise ArtifactError("artifact_file_already_owned")
            if path.is_relative_to(self.store):
                raise ArtifactError("artifact_store_metadata_not_external")
            size, digest = _digest(path)
            record["files"].append({"path": str(path), "size": size, "sha256": digest})
            _write(self.top / MANIFEST_NAME, record)
            _write(self.store / "index.json", index)

    def remove_registered_file(self, path: Path) -> dict:
        """进程已退出后回收一个精确临时文件；失败只返回警告，保留登记。"""
        try:
            path = _safe(path, missing=True)
            with _lock(self.store):
                _, index = _load(self.store)
                record = self._record(index)
                if record["state"] != "active":
                    raise ArtifactError("artifact_run_not_active")
                item = next((f for f in record["files"] if f["path"] == str(path)), None)
                if item is None:
                    raise ArtifactError("artifact_external_not_registered")
                _, targets = _external({**record, "files": [item]})
                for target in targets:
                    _safe(target)
                    target.unlink()
                record["files"].remove(item)
                _write(self.top / MANIFEST_NAME, record)
                _write(self.store / "index.json", index)
                return {"deleted": [str(path)] if targets else [], "warnings": []}
        except (OSError, ValueError, KeyError, TypeError, ArtifactError) as exc:
            return {"deleted": [], "warnings": [str(exc)]}

    def finish(self, status: str = "finished", uncertain: bool = False) -> dict:
        if not self.owner and not uncertain:
            return {"owner": False, "warnings": list(self.warnings), "deleted": []}
        try:
            with _lock(self.store):
                policy, index = _load(self.store)
                record = self._record(index)
                # 子进程只可传播不确定性；普通完成不能替父进程结束整轮。
                # 一旦 uncertain，后续任何正常 finish 都不能解除保护。
                if record["state"] == "active" or (uncertain and record["state"] == "finished"):
                    record.update(state="uncertain" if uncertain else "finished", status=str(status),
                                  finished_at=time.time())
                    _write(self.top / MANIFEST_NAME, record)
                    _write(self.store / "index.json", index)
                result = _cleanup(self.store, policy, index, apply=True, protected={self.run_id})
                result["warnings"] = [*self.warnings, *result["warnings"]]
                return result
        except (OSError, ValueError, KeyError, TypeError, ArtifactError) as exc:
            return _failure(self.store, exc, apply=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="仅管理显式登记的组件产物")
    parser.add_argument("command", choices=("inspect", "cleanup"))
    parser.add_argument("--maa", default="maa")
    parser.add_argument("--artifact-config", type=Path, help="直接指定配置目录，离线检查或清理产物")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "inspect" and args.apply:
        parser.error("--apply 只可与 cleanup 一起使用")
    try:
        config = args.artifact_config
        if config is None:
            discovered = subprocess.run([args.maa, "dir", "config", "--batch"], capture_output=True,
                                        text=True, encoding="utf-8", check=True, timeout=30)
            lines = [line.strip() for line in discovered.stdout.splitlines() if line.strip()]
            if not lines or not Path(lines[-1]).is_absolute():
                raise ArtifactError("artifact_invalid_config_discovery")
            config = Path(lines[-1])
        result = manage(config, apply=args.command == "cleanup" and args.apply)
    except (OSError, ValueError, subprocess.SubprocessError, ArtifactError) as exc:
        print(json.dumps({"warnings": [str(exc)], "budget_ok": False}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["budget_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
