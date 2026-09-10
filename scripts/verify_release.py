"""Check the preregistered engineering release before reading market data."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / "RELEASE_MANIFEST.json").read_text())
    for name, expected in manifest["files"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Release source hash mismatch: {name}")
    print(f"RELEASE HASH VERIFICATION PASS: {len(manifest['files'])} files")


if __name__ == "__main__":
    main()
