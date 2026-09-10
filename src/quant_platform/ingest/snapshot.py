from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json

def freeze_snapshot(manifest_paths, *, snapshot_id, output_path):
    entries=[]
    for p in sorted(Path(x) for x in manifest_paths):
        obj=json.loads(p.read_text(encoding="utf-8"))
        if obj.get("status")!="VALID":
            raise RuntimeError(f"Snapshot refuses non-VALID manifest: {p}")
        entries.append({
            "manifest":str(p),
            "manifest_sha256":hashlib.sha256(p.read_bytes()).hexdigest(),
            "source_sha256":obj["source_sha256"],
            "canonical_sha256":obj["canonical_sha256"],
            "min_open_time_ms":obj["min_open_time_ms"],
            "max_open_time_ms":obj["max_open_time_ms"],
            "rows":obj["rows"],
        })
    payload={
        "snapshot_id":snapshot_id,
        "status":"FROZEN",
        "created_at":datetime.now(timezone.utc).isoformat(),
        "entries":entries,
    }
    canonical=json.dumps(payload,sort_keys=True,separators=(",",":")).encode()
    payload["snapshot_sha256"]=hashlib.sha256(canonical).hexdigest()
    output_path=Path(output_path)
    output_path.parent.mkdir(parents=True,exist_ok=True)
    output_path.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    return payload
