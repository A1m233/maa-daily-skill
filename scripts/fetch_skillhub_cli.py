"""Fetch the official pinned CLI without installing plugins or global preferences."""

import argparse
import hashlib
from pathlib import Path
import tarfile
import io
import urllib.request


URL = "https://skillhub-1388575217.cos.ap-guangzhou.myqcloud.com/releases/skillhub-cli-2026.8.5.tar.gz"
SHA256 = "3bbe2ba15ada2eb7a94a2b760fead83be5f4164ab28a6c8b0944dbc539f7e236"


def fetch(output):
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("official CLI archive hash mismatch; review upstream before updating pin")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for name in ("skills_store_cli.py", "skills_upgrade.py", "version.json", "metadata.json"):
            member = archive.getmember("cli/" + name)
            if not member.isfile():
                raise ValueError("CLI source is not a regular file")
            files[name] = archive.extractfile(member).read()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, source in files.items():
        (output / name).write_bytes(source)
    print(f"Verified SkillHub CLI 2026.8.5: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    fetch(parser.parse_args().output)
