"""Build a SkillHub distribution from a Git commit; never copy local runtime data."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def build(repo, ref, version, output):
    # Initial distribution supports stable releases only, deliberately excluding aliases.
    if not SEMVER.fullmatch(version):
        raise ValueError("version must be a stable SemVer, e.g. 0.1.0")
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    entries = git(repo, "ls-tree", "-r", "-z", commit, "--", "maa-daily/", "LICENSE")
    files = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        header, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        path = raw_path.decode("utf-8")
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"unsupported Git entry: {path}")
        relative = path.removeprefix("maa-daily/")
        if relative == "LICENSE" and relative in files:
            raise ValueError("duplicate LICENSE; resolve source ownership before publishing")
        files[relative] = git(repo, "cat-file", "blob", oid)
    if "LICENSE" not in files or "SKILL.md" not in files:
        raise ValueError("source must include SKILL.md and LICENSE")
    if "LICENSE.txt" in files:
        raise ValueError("LICENSE.txt collides with the packaged repository license")
    # SkillHub rejects extensionless uploads; preserve the license bytes verbatim.
    files["LICENSE.txt"] = files.pop("LICENSE")
    source = files["SKILL.md"].decode("utf-8")
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md frontmatter missing")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("SKILL.md frontmatter incomplete")
    extra = {
        "slug": "maa-daily",
        "version": version,
        "displayName": "MAA 明日方舟日常",
        "summary": "通过 maa-cli 运行单账号或多账号日常，支持清理理智与结果核验。",
        "license": "MIT",
        "homepage": "https://github.com/A1m233/maa-daily-skill",
    }
    existing = {line.split(":", 1)[0].strip() for line in lines[1:end] if ":" in line}
    if existing & extra.keys():
        raise ValueError("source contains distribution metadata; do not maintain two owners")
    injected = "".join(f"{key}: {value}\n" for key, value in extra.items())
    files["SKILL.md"] = ("".join(lines[:end]) + injected + "".join(lines[end:])).encode("utf-8")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    archive = output / f"maa-daily-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(info, content)
    manifest = {
        "source": extra["homepage"], "commit": commit, "version": version,
        "archive": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(ROOT, args.ref, args.version, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
