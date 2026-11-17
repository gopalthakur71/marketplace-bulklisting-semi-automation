# Runtime config the app reads at startup (src/web settings) via the EC2 instance role.
locals {
  ssm_plain_params = {
    s3_bucket = { value = "ijorethnicpartners", description = "S3 bucket for product images and exports" }
    s3_prefix = { value = "myntra/", description = "S3 object key prefix for uploads" }
    s3_region = { value = "ap-south-1", description = "AWS region for S3" }
  }
}

resource "aws_ssm_parameter" "plain" {
  for_each = local.ssm_plain_params

  name        = "/marketplace-listing/${each.key}"
  type        = "String"
  value       = each.value.value
  description = each.value.description
}

# Gemini key for the Fix flow's plain-English error explanations. The value never lives in
# code: Terraform adopts the parameter but ignores its value, so the real key (set in the
# console) is never overwritten by this placeholder.
resource "aws_ssm_parameter" "gemini_api_key" {
  name        = "/marketplace-listing/gemini_api_key"
  description = "Gemini API key for Fix-Error explanations"
  type        = "SecureString"
  value       = "managed-outside-terraform"

  lifecycle {
    ignore_changes = [value]
  }
}
