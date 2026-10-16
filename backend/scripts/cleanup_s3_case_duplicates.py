"""Clean up case-duplicate prefixes in S3.

S3 keys are case-sensitive and have no real folders, so a bug that once wrote
`industry-docs/Healthcare/...` leaves those keys in the bucket forever even
after the code was fixed to always write lowercase (`industry-docs/healthcare/...`).
This script finds objects whose first path segment after a managed prefix is
NOT all-lowercase and reports (or deletes) them.

Managed prefixes scanned:
    industry-docs/
    context-pack/

Usage:
    # Dry run (default) — lists what WOULD be deleted, changes nothing:
    python scripts/cleanup_s3_case_duplicates.py

    # Actually delete the mixed-case duplicates:
    python scripts/cleanup_s3_case_duplicates.py --apply

    # Only delete if the lowercase equivalent key already exists (safest):
    python scripts/cleanup_s3_case_duplicates.py --apply --require-lowercase-twin

Requires valid AWS credentials (run `aws sso login` / refresh your token first).
Reads S3_BUCKET / AWS_REGION / the prefixes from the backend .env.
"""
from __future__ import annotations

import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

BUCKET = os.getenv("S3_BUCKET", "vertical-adaption-agent")
REGION = os.getenv("AWS_REGION", "us-east-1")
MANAGED_PREFIXES = [
    os.getenv("INDUSTRY_DOCS_PREFIX", "industry-docs/"),
    os.getenv("CONTEXT_PACK_PREFIX", "context-pack/"),
]


def _first_segment_after_prefix(key: str, prefix: str) -> str:
    """Return the first path segment after `prefix` (the industry name)."""
    rest = key[len(prefix):]
    return rest.split("/", 1)[0] if rest else ""


def _list_all_keys(s3, prefix: str) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": BUCKET, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            keys.append(obj["Key"])
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete the mixed-case duplicate objects (default: dry run).",
    )
    parser.add_argument(
        "--require-lowercase-twin",
        action="store_true",
        help="Only delete a mixed-case key if its all-lowercase equivalent also exists.",
    )
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=REGION)

    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError as e:
        print(f"ERROR: cannot access bucket {BUCKET}: {e}")
        print("Refresh your AWS credentials (e.g. `aws sso login`) and retry.")
        return 1

    total_offenders = 0
    total_deleted = 0

    for prefix in MANAGED_PREFIXES:
        all_keys = _list_all_keys(s3, prefix)
        lowercase_keys = set(all_keys)  # for twin existence check

        offenders: list[str] = []
        for key in all_keys:
            segment = _first_segment_after_prefix(key, prefix)
            if segment and segment != segment.lower():
                offenders.append(key)

        if not offenders:
            print(f"[{prefix}] OK — no mixed-case prefixes found ({len(all_keys)} objects).")
            continue

        print(f"\n[{prefix}] Found {len(offenders)} mixed-case object(s):")
        for key in offenders:
            total_offenders += 1
            segment = _first_segment_after_prefix(key, prefix)
            # The lowercase equivalent key (only the industry segment changes).
            twin = key.replace(f"{prefix}{segment}/", f"{prefix}{segment.lower()}/", 1)
            twin_exists = twin in lowercase_keys

            if args.require_lowercase_twin and not twin_exists:
                print(f"    SKIP (no lowercase twin): {key}")
                continue

            marker = "twin✓" if twin_exists else "NO twin"
            if args.apply:
                try:
                    s3.delete_object(Bucket=BUCKET, Key=key)
                    total_deleted += 1
                    print(f"    DELETED [{marker}]: {key}")
                except ClientError as e:
                    print(f"    FAILED  [{marker}]: {key} ({e})")
            else:
                print(f"    WOULD DELETE [{marker}]: {key}")

    print("\n" + "=" * 60)
    print(f"Mixed-case objects found : {total_offenders}")
    if args.apply:
        print(f"Objects deleted          : {total_deleted}")
    else:
        print("Dry run — nothing deleted. Re-run with --apply to delete.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
