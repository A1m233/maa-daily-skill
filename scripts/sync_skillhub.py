"""Publish tested Git content once; persist uncertain submissions in GitHub Releases."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error

from package_skillhub import ROOT, build

MARKER = "<!-- skillhub-sync-v1 -->\n"
VERSION = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or not token:
            raise ValueError("GitHub repository and token required")
        self.base = f"https://api.github.com/repos/{repository}"
        self.token = token

    def request(self, url, method="GET", data=None, binary=False):
        # Only GitHub receives this token, including release asset uploads.
        if urllib.parse.urlparse(url).hostname not in {"api.github.com", "uploads.github.com"}:
            raise ValueError("unexpected GitHub endpoint")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "maa-daily-skill-sync"}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream" if binary else "application/json"
            if not binary:
                data = json.dumps(data).encode()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)

    def releases(self):
        result, page = [], 1
        while True:
            rows = self.request(f"{self.base}/releases?per_page=100&page={page}")
            result.extend(rows)
            if len(rows) < 100:
                return result
            page += 1

    def create(self, manifest):
        tag = "v" + manifest["version"]
        try:
            self.request(self.base + "/git/ref/tags/" + tag)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        else:
            raise ValueError("version tag already exists without a release; reconcile before publishing")
        return self.request(self.base + "/releases", "POST", {
            "tag_name": tag, "target_commitish": manifest["commit"],
            "name": "MAA 日常 Skill v" + manifest["version"], "make_latest": "false",
            "body": body(manifest, "prepared")})

    def update(self, release, manifest, status, **extra):
        values = {"body": body(manifest, status, **extra)}
        if status == "submitted":
            values["make_latest"] = "true"
        return self.request(f"{self.base}/releases/{release['id']}", "PATCH", values)

    def upload(self, release, file):
        names = {asset["name"] for asset in release.get("assets", [])}
        if file.name in names:
            return  # The saved manifest/package hash is checked before resuming.
        url = release["upload_url"].split("{")[0] + "?" + urllib.parse.urlencode({"name": file.name})
        self.request(url, "POST", file.read_bytes(), binary=True)


def body(manifest, status, **extra):
    state = {key: manifest[key] for key in ("commit", "version", "source_digest", "sha256")}
    state.update(status=status, **extra)
    return MARKER + json.dumps(state, ensure_ascii=False, sort_keys=True) + (
        "\n\n自动同步记录；submitted 仅表示平台已接收，不表示审核上架。"
        "\nsubmitting/unknown 必须先核对平台版本，禁止直接重传。")


def state(release):
    text = release.get("body") or ""
    if not text.startswith(MARKER):
        return None
    result = json.loads(text[len(MARKER):].splitlines()[0])
    if (result.get("status") not in {"prepared", "submitting", "unknown", "submitted"}
            or release["tag_name"] != "v" + result["version"]):
        raise ValueError("invalid publication record; reconcile before publishing")
    return result


def select(releases):
    stable = []
    for release in releases:
        record = state(release)
        if record and record["status"] in {"submitting", "unknown"}:
            raise ValueError(f"Uncertain SkillHub submission {release['tag_name']}; check platform before retry")
        match = VERSION.fullmatch(release.get("tag_name", ""))
        if match and not release.get("prerelease"):
            stable.append((tuple(map(int, match.groups())), release, record))
    if not stable:
        return "0.1.0", None, None
    version, latest, record = max(stable, key=lambda item: item[0])
    return f"{version[0]}.{version[1]}.{version[2] + 1}", latest, record


def cli_run(cli, archive, *, dry=False, commit=""):
    command = [sys.executable, str(cli), "--skip-self-upgrade", "publish", str(archive), "--json"]
    command += ["--dry-run"] if dry else ["--host", "https://api.skillhub.cn", "--changelog", "GitHub commit: " + commit]
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=300)


def acceptance(response, version):
    if (not isinstance(response, dict) or response.get("success") is False or response.get("ok") is False
            or not response.get("skillId") or response.get("version", version) != version
            or response.get("slug", "maa-daily") != "maa-daily"):
        raise ValueError("publish response does not confirm this skill/version")
    # Community publishing returns ok/reviewStatus; the older API returns status.
    if response.get("ok") is True:
        status = response.get("reviewStatus")
        if response.get("version") != version or response.get("slug") != "maa-daily" or not response.get("versionId"):
            raise ValueError("incomplete community publication receipt")
    else:
        status = response.get("status")
    if not isinstance(status, str) or not status:
        raise ValueError("publish review status missing")
    return status


def sync(repo, output, cli, *, github=None, dry_version="0.1.0", run_cli=cli_run):
    output = Path(output)
    next_version, latest, record = select(github.releases()) if github else (dry_version, None, None)
    # Probe package fixes the content identity independently of its version.
    probe = build(repo, "HEAD", next_version, output / "candidate")
    same = record and record["source_digest"] == probe["source_digest"]
    if same and record["status"] == "submitted":
        return {"status": "unchanged", "version": record["version"], "commit": probe["commit"]}
    manifest = probe
    package_dir = output / "candidate"
    if same and record["status"] == "prepared":
        package_dir = output / "resume"
        manifest = build(repo, "HEAD", record["version"], package_dir)
        if manifest["sha256"] != record["sha256"]:
            raise ValueError("prepared package changed; reconcile publication record")
        # Keep the original source anchor when a later commit contains identical content.
        manifest["commit"] = record["commit"]
        (package_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive = package_dir / manifest["archive"]
    dry = run_cli(cli, archive, dry=True)
    (output / "dry-run.txt").write_text(dry.stdout + dry.stderr, encoding="utf-8")
    if dry.returncode != 0 or json.loads(dry.stdout).get("dryRun") is not True:
        raise ValueError("official package dry-run failed")
    if github is None:
        return {"status": "dry_run", "version": manifest["version"], "commit": manifest["commit"]}
    release = latest if same and record["status"] == "prepared" else github.create(manifest)
    github.upload(release, archive)
    github.upload(release, package_dir / "manifest.json")
    # Persist BEFORE the external request: cancellation or lost response must not cause a duplicate.
    github.update(release, manifest, "submitting")
    try:
        result = run_cli(cli, archive, commit=manifest["commit"])
        (output / "response.json").write_text(result.stdout, encoding="utf-8")
        (output / "publish-stderr.txt").write_text(result.stderr, encoding="utf-8")
        response = json.loads(result.stdout)
        if result.returncode:
            raise ValueError("publish not confirmed; reconcile with platform before retry")
        platform_status = acceptance(response, manifest["version"])
        github.update(release, manifest, "submitted", platform_status=platform_status)
        return {"status": "submitted", "version": manifest["version"], "commit": manifest["commit"],
                "platform_status": platform_status, "release": release.get("html_url")}
    except Exception:
        # If this update fails, the persisted 'submitting' state still blocks blind retries.
        github.update(release, manifest, "unknown")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--version", default="0.1.0", help="dry-run only")
    args = parser.parse_args()
    github = None
    if args.publish:
        if os.environ.get("GITHUB_REF") != "refs/heads/main" or not os.environ.get("SKILLHUB_TOKEN"):
            parser.error("publishing requires main and SKILLHUB_TOKEN")
        github = GitHub(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""))
    result = sync(ROOT, args.output, args.cli, github=github, dry_version=args.version)
    print(json.dumps(result, ensure_ascii=False))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
            file.write("## SkillHub 同步\n\n```json\n" + json.dumps(result, ensure_ascii=False, indent=2)
                       + "\n```\n\nsubmitted 表示已提交平台，审核与上架状态以平台为准。\n")


if __name__ == "__main__":
    main()
