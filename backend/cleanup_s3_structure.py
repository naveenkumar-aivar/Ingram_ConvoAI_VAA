#!/usr/bin/env python3
"""
Clean up S3 bucket - remove all objects and recreate only base folders
Base folders: industry-docs/, context-pack/, generated-packs/, sessions/, logs/
Runtime folders created on demand
"""

import boto3
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET", "vertical-adaption-agent")

# S3 client
s3_client = boto3.client("s3", region_name=AWS_REGION)

def delete_all_objects(bucket):
    """Delete all objects from bucket"""
    print(f"🗑️  Deleting all objects from {bucket}...\n")
    
    paginator = s3_client.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket)
    
    deleted_count = 0
    for page in pages:
        if 'Contents' in page:
            for obj in page['Contents']:
                s3_client.delete_object(Bucket=bucket, Key=obj['Key'])
                deleted_count += 1
    
    print(f"✓ Deleted {deleted_count} objects\n")

def create_base_folders(bucket):
    """Create only base folders"""
    base_folders = [
        "industry-docs/",
        "context-pack/",
        "sessions/",
        "logs/",
    ]
    
    print("📁 Creating base folders only:\n")
    
    for folder in base_folders:
        try:
            s3_client.put_object(Bucket=bucket, Key=folder + ".keep", Body=b"")
            print(f"✓ {folder}")
        except Exception as e:
            print(f"✗ Error: {e}")

if __name__ == "__main__":
    print(f"🚀 Cleaning up S3 bucket: {S3_BUCKET}\n")
    
    # Delete all objects
    delete_all_objects(S3_BUCKET)
    
    # Create only base folders
    create_base_folders(S3_BUCKET)
    
    print("\n✅ Cleanup complete! Only base folders exist.")
    print("   • industry-docs/     (user input + generator details)")
    print("   • context-pack/      (approved final packs)")
    print("   • sessions/          (session data)")
    print("   • logs/              (application logs)")
    print("\n📝 Drafts remain in local: s3-data/_drafts/\n")
