from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from urllib.parse import urlparse

import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq


@dataclass(frozen=True)
class StoreLocation:
    fs: pafs.FileSystem
    root: str

    def key(self, relative: str) -> str:
        rel = relative.lstrip("/")
        return f"{self.root.rstrip('/')}/{rel}" if self.root else rel

    def relative(self, absolute_key: str) -> str:
        prefix = self.root.rstrip("/") + "/" if self.root else ""
        if prefix and absolute_key.startswith(prefix):
            return absolute_key[len(prefix):]
        return absolute_key


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def resolve_store_uri(explicit_uri: str | None = None) -> str:
    """
    Resolution order:
      1) explicit --store-uri
      2) QUANT_STORE_URI
      3) Railway Bucket variable `BUCKET` -> s3://BUCKET/quant-data
      4) AWS_S3_BUCKET_NAME -> s3://bucket/quant-data
    """
    if explicit_uri:
        return explicit_uri

    uri = os.getenv("QUANT_STORE_URI")
    if uri:
        return uri

    bucket = _first_env("BUCKET", "AWS_S3_BUCKET_NAME")
    if bucket:
        return f"s3://{bucket}/quant-data"

    raise ValueError(
        "No storage configured. Set QUANT_STORE_URI, or inject Railway Bucket variables."
    )


def open_store(uri: str | None = None) -> StoreLocation:
    uri = resolve_store_uri(uri)
    parsed = urlparse(uri)

    if parsed.scheme in ("", "file"):
        path = parsed.path if parsed.scheme == "file" else uri
        return StoreLocation(pafs.LocalFileSystem(), path.rstrip("/"))

    if parsed.scheme == "s3":
        bucket = parsed.netloc
        prefix = parsed.path.strip("/")
        root = f"{bucket}/{prefix}" if prefix else bucket

        access_key = _first_env("AWS_ACCESS_KEY_ID", "ACCESS_KEY_ID")
        secret_key = _first_env("AWS_SECRET_ACCESS_KEY", "SECRET_ACCESS_KEY")
        session_token = os.getenv("AWS_SESSION_TOKEN")
        region = _first_env("AWS_REGION", "AWS_DEFAULT_REGION", "REGION") or "auto"
        endpoint_raw = _first_env("AWS_ENDPOINT_URL", "ENDPOINT")

        scheme = "https"
        endpoint_override = None
        force_virtual = False

        if endpoint_raw:
            ep = urlparse(endpoint_raw)
            if ep.scheme:
                scheme = ep.scheme
                endpoint_override = ep.netloc
            else:
                endpoint_override = endpoint_raw
            # Railway Buckets use virtual-hosted-style URLs for current buckets.
            force_virtual = True

        fs = pafs.S3FileSystem(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            region=region,
            scheme=scheme,
            endpoint_override=endpoint_override,
            force_virtual_addressing=force_virtual,
        )
        return StoreLocation(fs, root)

    raise ValueError(f"Unsupported storage scheme: {parsed.scheme!r}")


def exists(store: StoreLocation, relative: str) -> bool:
    return store.fs.get_file_info(store.key(relative)).type != pafs.FileType.NotFound


def read_bytes(store: StoreLocation, relative: str) -> bytes:
    with store.fs.open_input_file(store.key(relative)) as f:
        return f.read()


def sha256_object(store: StoreLocation, relative: str) -> str:
    return hashlib.sha256(read_bytes(store, relative)).hexdigest()


def write_bytes(
    store: StoreLocation,
    relative: str,
    payload: bytes,
    overwrite: bool = False,
) -> str:
    key = store.key(relative)
    if not overwrite and exists(store, relative):
        raise FileExistsError(f"Object already exists: {key}")

    parent = key.rsplit("/", 1)[0] if "/" in key else ""
    if parent and isinstance(store.fs, pafs.LocalFileSystem):
        store.fs.create_dir(parent, recursive=True)

    with store.fs.open_output_stream(key) as f:
        f.write(payload)
    return key


def parquet_bytes_from_polars(df) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(df.to_arrow(), sink, compression="zstd")
    return sink.getvalue().to_pybytes()


def read_parquet_table(
    store: StoreLocation,
    relatives: list[str],
    columns: list[str] | None = None,
):
    keys = [store.key(x) for x in relatives]
    return pq.read_table(keys, filesystem=store.fs, columns=columns)
