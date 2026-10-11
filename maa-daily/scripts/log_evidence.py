"""只读校验 runner 的单段或轮转日志区间，不改写原始日志。"""
from __future__ import annotations

import hashlib
from pathlib import Path

MAX_INTERVAL_BYTES = 64 * 1024 * 1024


def _interval(part: dict) -> tuple[int, int]:
    start, end = part.get("before_size"), part.get("after_size")
    if (type(start) is not int or type(end) is not int
            or not 0 <= start <= end or end - start > MAX_INTERVAL_BYTES):
        raise ValueError("invalid_log_interval")
    return start, end


def _read_part(part: dict) -> bytes:
    start, end = _interval(part)
    path = Path(part["log_file"])
    candidates = [path]
    # 既有报告所指当前日志可能在后续阶段才被轮转。只接受已封存哈希，
    # 不按时间戳或内容关键词猜测归属，也不搜索其它日志。
    if path.name == "asst.log":
        candidates.append(path.with_name("asst.bak.log"))
    for candidate in candidates:
        try:
            with candidate.open("rb") as handle:
                handle.seek(start)
                data = handle.read(end - start)
        except FileNotFoundError:
            continue
        if len(data) == end - start and hashlib.sha256(data).hexdigest() == part.get("interval_sha256"):
            return data
    raise ValueError("log_interval_changed_or_unhashed")


def read_evidence(evidence: dict) -> bytes:
    """返回完整哈希匹配的本轮字节；新旧报告均走同一个读取入口。"""
    if evidence.get("state") != "bounded":
        raise ValueError("unbounded_log_evidence")
    if "segments" not in evidence:
        start, end = _interval(evidence)
        if start == end:
            raise ValueError("empty_log_interval")
        return _read_part(evidence)
    segments = evidence["segments"]
    if not isinstance(segments, list) or len(segments) != 2:
        raise ValueError("invalid_log_segments")
    path = Path(evidence["log_file"])
    first, second = segments
    if (path.name != "asst.log" or Path(first["log_file"]) != path.with_name("asst.bak.log")
            or Path(second["log_file"]) != path or second.get("before_size") != 0
            or first.get("before_size") != evidence.get("before_size")
            or second.get("after_size") != evidence.get("after_size")):
        raise ValueError("invalid_log_segment_order")
    size = sum(end - start for start, end in map(_interval, segments))
    if not 0 < size <= MAX_INTERVAL_BYTES:
        raise ValueError("invalid_log_interval")
    data = b"".join(_read_part(part) for part in segments)
    if hashlib.sha256(data).hexdigest() != evidence.get("interval_sha256"):
        raise ValueError("log_interval_changed_or_unhashed")
    return data
