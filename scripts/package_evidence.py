"""Package only public research files; never include environment variables."""
import hashlib
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    dest = ROOT / "artifacts"
    dest.mkdir(exist_ok=True)
    source = ROOT / "quant-data"
    paths = [p for p in source.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in paths)
    if total > 200 * 1024 * 1024:
        raise RuntimeError("Artifact exceeds preregistered 200 MiB budget")
    hashes = {}
    for p in paths:
        name = p.relative_to(ROOT)
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
        hashes[name.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    for name in ["RELEASE_MANIFEST.json", "FROZEN_PROTOCOL_v1_1.json", "requirements.lock", "docs/CORRECTIONS.md"]:
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    results = list(source.glob("research/results/**/evidence.json"))
    summary = {"commit": os.getenv("GITHUB_SHA"), "run_id": os.getenv("GITHUB_RUN_ID"),
               "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"), "total_dataset_bytes": total,
               "status": "COMPUTED_NOT_YET_REVIEWED" if results else "INCOMPLETE_NO_RESEARCH_RESULT",
               "sha256": hashes}
    (dest / "ARTIFACT_MANIFEST.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "sha256"}, indent=2))


if __name__ == "__main__":
    main()
