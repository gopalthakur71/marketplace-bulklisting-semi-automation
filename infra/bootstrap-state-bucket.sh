#!/usr/bin/env bash
# One-time, by hand: create the S3 bucket that holds Terraform state (phase 1 of
# docs/superpowers/specs/2026-11-13-terraform-ansible-iac-design.md).
# Chicken-and-egg: Terraform can't create the bucket its own state lives in.
# Safe to re-run: every step after create-bucket is idempotent.
set -euo pipefail

BUCKET="ijor-tfstate-048589483919"
REGION="ap-south-1"
PROFILE="gt"

aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION" --profile "$PROFILE"

# Versioning: every state write keeps the previous version — the undo button for a bad apply.
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled --profile "$PROFILE"

# Default encryption (state holds the Cognito client secret until phase 5).
aws s3api put-bucket-encryption --bucket "$BUCKET" --profile "$PROFILE" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'

aws s3api put-public-access-block --bucket "$BUCKET" --profile "$PROFILE" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "State bucket $BUCKET ready. Next: cd infra/terraform && terraform init"
