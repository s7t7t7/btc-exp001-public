import argparse
from quant_platform.storage import open_store
from quant_platform.ingest.remote_snapshot import freeze_discovery_snapshot

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--store-uri",required=True)
    a=p.parse_args()
    key,s=freeze_discovery_snapshot(open_store(a.store_uri))
    print("FROZEN",key,s["snapshot_sha256"],"rows=",s["row_count"])

if __name__=="__main__":
    main()
