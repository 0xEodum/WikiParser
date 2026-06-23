from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class S3UploadConfig:
    bucket: str
    prefix: str = ""
    endpoint_url: str | None = None
    region_name: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


@dataclass(frozen=True)
class S3UploadSummary:
    files_uploaded: int
    bucket: str
    prefix: str


def normalize_endpoint(url: str | None, port: str | int | None = None) -> str | None:
    if not url:
        return None
    endpoint = str(url).strip()
    if not endpoint:
        return None
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    if port and ":" not in endpoint.rsplit("/", 1)[-1]:
        endpoint = f"{endpoint}:{port}"
    return endpoint.rstrip("/")


def load_s3_config(
    *,
    bucket: str | None = None,
    prefix: str = "",
    endpoint_url: str | None = None,
    region_name: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    cred_json: str | Path | None = None,
) -> S3UploadConfig:
    credentials: dict[str, object] = {}
    if cred_json:
        credentials = json.loads(Path(cred_json).read_text(encoding="utf-8"))

    resolved_bucket = bucket or str(credentials.get("bucket_name") or os.environ.get("AWS_S3_BUCKET") or "")
    if not resolved_bucket:
        raise ValueError("S3 bucket is required. Pass --s3-bucket, --s3-cred-json, or AWS_S3_BUCKET.")

    return S3UploadConfig(
        bucket=resolved_bucket,
        prefix=prefix.strip("/"),
        endpoint_url=normalize_endpoint(
            endpoint_url or credentials.get("endpoint_url") or credentials.get("url"),
            credentials.get("port"),
        ),
        region_name=region_name or credentials.get("region_name") or credentials.get("reg") or os.environ.get("AWS_REGION"),
        access_key_id=access_key_id or credentials.get("access_key") or os.environ.get("AWS_ACCESS_KEY_ID"),
        secret_access_key=secret_access_key or credentials.get("secret_key") or os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )


def build_s3_client(config: S3UploadConfig):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("S3 upload requires boto3. Install with: pip install 'wiki-dump-converter[s3]'") from exc

    kwargs: dict[str, object] = {}
    if config.endpoint_url:
        kwargs["endpoint_url"] = config.endpoint_url
    if config.region_name:
        kwargs["region_name"] = config.region_name
    if config.access_key_id:
        kwargs["aws_access_key_id"] = config.access_key_id
    if config.secret_access_key:
        kwargs["aws_secret_access_key"] = config.secret_access_key
    return boto3.client("s3", **kwargs)


def s3_key(prefix: str, relative_path: Path) -> str:
    relative = relative_path.as_posix()
    return f"{prefix.strip('/')}/{relative}".strip("/")


def upload_directory_to_s3(local_root: str | Path, config: S3UploadConfig) -> S3UploadSummary:
    root = Path(local_root)
    if not root.exists():
        raise FileNotFoundError(f"Cannot upload missing directory: {root}")

    client = build_s3_client(config)
    uploaded = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        key = s3_key(config.prefix, path.relative_to(root))
        client.upload_file(str(path), config.bucket, key)
        uploaded += 1

    return S3UploadSummary(files_uploaded=uploaded, bucket=config.bucket, prefix=config.prefix)
