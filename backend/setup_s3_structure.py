#!/usr/bin/env python3
"""
Setup S3 bucket structure for Vertical Adaptation Agent
Creates: industry/ and context-pack/ folders with proper hierarchy
"""

import boto3
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET", "vertical-adaption-agent")
CONTEXT_PACK_PREFIX = os.getenv("CONTEXT_PACK_PREFIX", "context-pack/")
INDUSTRY_DOCS_PREFIX = os.getenv("INDUSTRY_DOCS_PREFIX", "industry-docs/")

# S3 client
s3_client = boto3.client("s3", region_name=AWS_REGION)

# Folder structure
INDUSTRIES = ["banking", "healthcare", "retail", "telecommunications"]
AGENTS_BY_INDUSTRY = {
    "banking": ["support", "fraud-detection"],
    "healthcare": ["assist", "appointment-scheduling"],
    "retail": ["customer-service", "recommendation"],
    "telecommunications": ["support", "billing"],
}

AGENT_SUBFOLDERS = [
    "persona",
    "prompts",
    "guardrails",
    "handoff",
    "uploaded-data",
]

def create_folder(bucket, prefix):
    """Create a folder by uploading an empty .keep file"""
    try:
        s3_client.put_object(Bucket=bucket, Key=prefix + ".keep", Body=b"")
        print(f"✓ Created: {prefix}")
    except Exception as e:
        print(f"✗ Error creating {prefix}: {e}")
        return False
    return True

def setup_s3_structure():
    """Setup complete S3 structure"""
    print(f"\n🚀 Setting up S3 structure in bucket: {S3_BUCKET}\n")
    
    # Check bucket exists
    try:
        s3_client.head_bucket(Bucket=S3_BUCKET)
        print(f"✓ Bucket exists: {S3_BUCKET}\n")
    except Exception as e:
        print(f"✗ Bucket does not exist or access denied: {e}")
        print("Creating bucket...")
        try:
            s3_client.create_bucket(Bucket=S3_BUCKET)
            print(f"✓ Bucket created: {S3_BUCKET}\n")
        except Exception as create_err:
            print(f"✗ Failed to create bucket: {create_err}")
            sys.exit(1)
    
    # 1. Create industry folder structure
    print("📁 Setting up INDUSTRY folder structure:\n")
    
    for industry in INDUSTRIES:
        industry_prefix = f"{INDUSTRY_DOCS_PREFIX}{industry}/"
        create_folder(S3_BUCKET, industry_prefix)
        
        # Create agent subfolders for each industry
        agents = AGENTS_BY_INDUSTRY.get(industry, [])
        for agent in agents:
            agent_prefix = f"{industry_prefix}{agent}/"
            create_folder(S3_BUCKET, agent_prefix)
            
            # Create subfolders inside each agent
            for subfolder in AGENT_SUBFOLDERS:
                subfolder_prefix = f"{agent_prefix}{subfolder}/"
                create_folder(S3_BUCKET, subfolder_prefix)
    
    # 2. Create context-pack folder structure (mirroring industry structure)
    print("\n📁 Setting up CONTEXT-PACK folder structure:\n")
    
    for industry in INDUSTRIES:
        context_industry_prefix = f"{CONTEXT_PACK_PREFIX}{industry}/"
        create_folder(S3_BUCKET, context_industry_prefix)
        
        # Create agent contexts
        agents = AGENTS_BY_INDUSTRY.get(industry, [])
        for agent in agents:
            agent_context_prefix = f"{context_industry_prefix}{agent}/"
            create_folder(S3_BUCKET, agent_context_prefix)
            
            # Create context-pack specific subfolders
            context_subfolders = [
                "persona",
                "prompts",
                "guardrails",
                "handoff",
                "ontology",  # shared per industry
                "uploaded-data",
            ]
            for subfolder in context_subfolders:
                subfolder_prefix = f"{agent_context_prefix}{subfolder}/"
                create_folder(S3_BUCKET, subfolder_prefix)
    
    # 3. Create other top-level folders
    print("\n📁 Setting up additional folders:\n")
    
    additional_folders = [
        "generated-packs/",
        "sessions/",
        "logs/bedrock-calls/",
        "logs/agent-execution/",
        "logs/errors/",
    ]
    
    for folder in additional_folders:
        create_folder(S3_BUCKET, folder)
    
    print("\n✅ S3 structure setup complete!\n")
    print(f"📊 Summary:")
    print(f"  • Industries: {', '.join(INDUSTRIES)}")
    print(f"  • Total agents: {sum(len(v) for v in AGENTS_BY_INDUSTRY.values())}")
    print(f"  • Bucket: {S3_BUCKET}")
    print(f"  • Region: {AWS_REGION}\n")

if __name__ == "__main__":
    setup_s3_structure()
