#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from typing import Iterable


def _has_value(value: str | None) -> bool:
    if value is None:
        return False
    token = str(value).strip()
    if not token:
        return False
    return token.lower() not in {"replace_me", "changeme", "none", "null"}


def _normalized_lower(value: str | None) -> str:
    return str(value or "").strip().lower()


def _detect_cloud_provider() -> str:
    explicit = _normalized_lower(os.getenv("CLOUD_PROVIDER"))
    if explicit in {"aws", "oci", "generic"}:
        return explicit

    if any(
        _has_value(os.getenv(key))
        for key in (
            "OCI_BUCKET_NAME",
            "OCI_NAMESPACE",
            "OCI_ACCESS_KEY_ID",
            "OCI_SECRET_ACCESS_KEY",
            "OCI_OBJECT_STORAGE_ENDPOINT",
        )
    ):
        return "oci"
    if any(
        _has_value(os.getenv(key))
        for key in (
            "AWS_REGION",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
        )
    ):
        return "aws"
    return "generic"


def _print_group(title: str, keys: Iterable[str], *, required: bool) -> int:
    print(f"\n[{title}]")
    missing = 0
    for key in keys:
        present = _has_value(os.getenv(key))
        marker = "OK" if present else ("MISSING" if required else "optional")
        print(f" - {key}: {marker}")
        if required and not present:
            missing += 1
    return missing


def _print_recommendations(provider: str) -> None:
    notes: list[str] = []

    database_url = _normalized_lower(os.getenv("DATABASE_URL"))
    if database_url and not database_url.startswith("postgresql"):
        notes.append(
            "DATABASE_URL is not PostgreSQL. This stack expects PostgreSQL with SQLAlchemy + psycopg."
        )
    if database_url.startswith("postgresql") and "sslmode=require" not in database_url:
        if provider == "aws":
            notes.append(
                "DATABASE_URL is missing 'sslmode=require' (recommended for AWS RDS/Aurora)."
            )
        elif provider == "oci":
            notes.append(
                "DATABASE_URL is missing 'sslmode=require' (recommended for OCI managed PostgreSQL)."
            )
        else:
            notes.append(
                "DATABASE_URL is missing 'sslmode=require' (recommended for managed PostgreSQL)."
            )
    if database_url.startswith("postgresql://"):
        notes.append("DATABASE_URL uses 'postgresql://'; prefer 'postgresql+psycopg://' explicitly.")

    redis_url = _normalized_lower(os.getenv("REDIS_URL"))
    if redis_url.startswith("redis://"):
        notes.append("REDIS_URL uses 'redis://'; prefer 'rediss://' for TLS in production.")

    has_aws_s3 = _has_value(os.getenv("AWS_ACCESS_KEY_ID")) or _has_value(os.getenv("AWS_SECRET_ACCESS_KEY"))
    if has_aws_s3 and not _has_value(os.getenv("S3_BUCKET_NAME")):
        notes.append("AWS S3 credentials are set, but S3_BUCKET_NAME is missing.")
    has_oci_s3 = _has_value(os.getenv("OCI_ACCESS_KEY_ID")) or _has_value(os.getenv("OCI_SECRET_ACCESS_KEY"))
    if has_oci_s3 and not _has_value(os.getenv("OCI_BUCKET_NAME")):
        notes.append("OCI object storage credentials are set, but OCI_BUCKET_NAME is missing.")
    if _has_value(os.getenv("OCI_BUCKET_NAME")) and not _has_value(os.getenv("OCI_NAMESPACE")):
        notes.append("OCI_BUCKET_NAME is set, but OCI_NAMESPACE is missing.")
    if _has_value(os.getenv("OCI_BUCKET_NAME")) and not _has_value(os.getenv("OCI_REGION")):
        notes.append("OCI_BUCKET_NAME is set, but OCI_REGION is missing.")
    if _has_value(os.getenv("OCI_BUCKET_NAME")) and not _has_value(os.getenv("OCI_OBJECT_STORAGE_ENDPOINT")):
        notes.append(
            "OCI_BUCKET_NAME is set, but OCI_OBJECT_STORAGE_ENDPOINT is missing "
            "(example: https://<namespace>.compat.objectstorage.<region>.oraclecloud.com)."
        )

    if not notes:
        return

    print("\n[Recommendations]")
    for note in notes:
        print(f" - {note}")


def main() -> int:
    provider = _detect_cloud_provider()
    print("Cloud preflight check for Scouting backend")
    print("This validates required env values before moving scheduler/automation to cloud.")
    print(f"Detected cloud profile: {provider}")

    required_missing = 0
    required_missing += _print_group(
        "Core Infra (required)",
        ["DATABASE_URL", "REDIS_URL", "ADMIN_API_KEY"],
        required=True,
    )
    required_missing += _print_group(
        "FRC Data Feeds (required for full automation)",
        ["TBA_AUTH_KEY"],
        required=True,
    )

    _print_group(
        "FIRST API (optional fallback)",
        ["FIRST_FRC_API_USERNAME", "FIRST_FRC_API_AUTH_KEY", "FIRST_FRC_API_BASE_URL"],
        required=False,
    )
    _print_group(
        "S3-compatible Object Storage (optional for cloud media)",
        [
            "S3_BUCKET_NAME",
            "S3_ENDPOINT_URL",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
        ],
        required=False,
    )
    _print_group(
        "AWS Object Storage (optional)",
        [
            "AWS_REGION",
            "S3_BUCKET_NAME",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "S3_ENDPOINT_URL",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
        ],
        required=False,
    )
    _print_group(
        "OCI Object Storage (optional)",
        [
            "OCI_REGION",
            "OCI_NAMESPACE",
            "OCI_BUCKET_NAME",
            "OCI_ACCESS_KEY_ID",
            "OCI_SECRET_ACCESS_KEY",
            "OCI_OBJECT_STORAGE_ENDPOINT",
        ],
        required=False,
    )
    _print_recommendations(provider)

    if required_missing > 0:
        print(f"\nResult: FAIL ({required_missing} required env values missing)")
        return 1

    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
