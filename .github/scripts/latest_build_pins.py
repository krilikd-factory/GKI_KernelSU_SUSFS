#!/usr/bin/env python3
"""Find matching pins from the latest successful complete kernel workflow."""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import update_check

def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)

def main():
    known = json.loads(Path(".github/known-good.json").read_text())
    selected = update_check.select_automatic_candidate_composition(known, "")
    if not selected:
        return 0
    key, features, _ = selected
    runs = gh("run", "list", "--workflow", "main.yml", "--branch", "main", "--limit", "40",
              "--json", "databaseId,status,conclusion")
    for run in runs:
        if run["status"] != "completed" or run["conclusion"] != "success":
            continue
        run_id = str(run["databaseId"])
        artifacts = gh("api", f"repos/{__import__('os').environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}/artifacts")
        for item in artifacts.get("artifacts", []):
            name = item["name"]
            if not name.endswith("-Pins") or item.get("expired"):
                continue
            with tempfile.TemporaryDirectory() as folder:
                result = subprocess.run(["gh", "run", "download", run_id, "--name", name, "--dir", folder],
                                        capture_output=True)
                path = Path(folder) / "known-good-candidate.json"
                if result.returncode or not path.is_file():
                    continue
                try:
                    data = json.loads(path.read_text())
                    pins = data[key]
                except (ValueError, KeyError, TypeError):
                    continue
                if not isinstance(pins, dict) or pins.get("feature_set") != features:
                    continue
                required = [field for field, value in known[key].items()
                            if field.endswith("_commit") and re.fullmatch(r"[0-9a-f]{40}", str(value))]
                if any(not re.fullmatch(r"[0-9a-f]{40}", str(pins.get(field, ""))) for field in required):
                    continue
                Path("latest-built-pins.json").write_text(json.dumps({key: pins}))
                print(f"Matched successful build {run_id}")
                return 0
    print("No matching successful kernel build pins; using known-good")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Could not read build history: {error}", file=sys.stderr)
        sys.exit(1)
