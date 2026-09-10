import hashlib,json

def stable_snapshot_hash(snapshot):
    base={k:v for k,v in snapshot.items() if k!="snapshot_sha256"}
    payload=json.dumps(base,sort_keys=True,separators=(",",":")).encode()
    return hashlib.sha256(payload).hexdigest()

def main():
    s={
      "snapshot_id":"DS-X",
      "status":"FROZEN",
      "entries":[
        {"year":2020,"month":1,"canonical_sha256":"a"*64},
        {"year":2020,"month":2,"canonical_sha256":"b"*64},
      ],
    }
    h1=stable_snapshot_hash(s)
    h2=stable_snapshot_hash(dict(reversed(list(s.items()))))
    assert h1==h2
    s2=json.loads(json.dumps(s))
    s2["entries"][1]["canonical_sha256"]="c"*64
    assert stable_snapshot_hash(s2)!=h1
    print("DatasetSnapshot deterministic-hash self-test: PASS")

if __name__=="__main__":
    main()
