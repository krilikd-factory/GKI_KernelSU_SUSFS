#!/usr/bin/env python3
"""Apply NoMount's upstream commit-count version scheme fail-closed.

The source module.prop intentionally has an empty versionCode. Official CI uses
`git rev-list --count HEAD`; update.json is a lower bound because a newer master
commit can legitimately precede the next published release metadata refresh.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def replace_one(text: str, key: str, value: str) -> str:
    updated, count = re.subn(rf"(?m)^{re.escape(key)}=.*$", f"{key}={value}", text, count=1)
    if count != 1:
        raise ValueError(f"expected exactly one {key}= entry in module.prop")
    return updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module-prop", required=True, type=Path)
    parser.add_argument("--update-json", required=True, type=Path)
    parser.add_argument("--commit-count", required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"[1-9][0-9]*", args.commit_count):
        raise ValueError("commit count must be a positive decimal integer")
    count = int(args.commit_count)
    source = args.module_prop.read_text(encoding="utf-8")
    versions = re.findall(r"(?m)^version=(.*)$", source)
    if len(versions) != 1:
        raise ValueError("expected exactly one version= entry in module.prop")
    base_version = versions[0].strip()
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", base_version):
        raise ValueError(f"unsupported NoMount base version: {base_version!r}")

    update = json.loads(args.update_json.read_text(encoding="utf-8"))
    update_code = update.get("versionCode")
    if type(update_code) is not int or update_code < 1:
        raise ValueError("update.json versionCode must be a positive integer")
    if update.get("version") != base_version:
        raise ValueError("module.prop and update.json base versions differ")
    if count < update_code:
        raise ValueError(
            f"selected NoMount history is older than its update metadata: {count} < {update_code}"
        )

    rendered = replace_one(source, "version", f"{base_version} ({count})")
    rendered = replace_one(rendered, "versionCode", str(count))
    args.module_prop.write_text(rendered, encoding="utf-8")
    print(f"NoMount metadata: version={base_version} ({count}), versionCode={count}")
    if count > update_code:
        print(f"NoMount selected commit is newer than published update metadata ({update_code}).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: NoMount metadata validation failed: {exc}") from exc
