# ijorethnicpartners holds the product images Myntra fetches (myntra/…) AND the app's state
# (state/ledger, HSN KB, SKU registry). Terraform manages only the bucket's settings — never
# its objects. prevent_destroy is permanent: no plan may ever delete this bucket.
resource "aws_s3_bucket" "data" {
  bucket = "ijorethnicpartners"

  lifecycle {
    prevent_destroy = true
  }
}

# Public read on myntra/* only — Myntra downloads the .jpg image URLs written into the sheet
# (AGENTS.md invariant 4). state/ stays private. No public ListBucket, so nothing can be browsed.
resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = jsonencode({
    Version = "2012-10-17"
    Id      = "BucketPolicyForOffloading"
    Statement = [{
      Sid       = "PublicReadMyntraImages"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.data.arn}/myntra/*"
    }]
  })

  lifecycle {
    prevent_destroy = true # deleting it breaks every live Myntra image URL
  }
}

# All four blocks off: required for the public-read policy above to take effect.
resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = false
  ignore_public_acls      = false
  block_public_policy     = false
  restrict_public_buckets = false
}
