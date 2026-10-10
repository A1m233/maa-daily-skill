"""组件编排测试隔离 CLI 层；CLI 证据/重试在 test_cli_recovery 中单独验证。"""
import subprocess


def dry(command, managed, report_file, *, env=None, timeout=120):
    result = subprocess.run(command, check=True, env=env or managed.environment())
    return getattr(result, "stdout", None) or ""
