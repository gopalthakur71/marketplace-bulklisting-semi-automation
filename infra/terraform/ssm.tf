# Runtime config the app reads at startup (src/web settings) via the EC2 instance role.
locals {
  ssm_plain_params = {
    cognito_client_id    = { value = "29oo5dtqh8j30k2481lmffqb0e", description = "Cognito app client ID from Task 8" }
    cognito_pool_id      = { value = "ap-south-1_NdxNQ1pIz", description = "Cognito user pool ID from Task 8" }
    cognito_domain       = { value = "ijor-marketplace", description = "Cognito domain prefix from Task 8" }
    cognito_redirect_uri = { value = "http://localhost:8000/auth/callback", description = "OAuth2 callback URL for the deployed app" }
    s3_bucket            = { value = "ijorethnicpartners", description = "S3 bucket for product images and exports" }
    s3_prefix            = { value = "myntra/", description = "S3 object key prefix for uploads" }
    s3_region            = { value = "ap-south-1", description = "AWS region for S3" }
  }
}

resource "aws_ssm_parameter" "plain" {
  for_each = local.ssm_plain_params

  name        = "/marketplace-listing/${each.key}"
  type        = "String"
  value       = each.value.value
  description = each.value.description
}

# The secret's value never lives in code. Terraform adopts the parameter but ignores its
# value, so the real secret (set in the console) is never overwritten by this placeholder.
resource "aws_ssm_parameter" "cognito_client_secret" {
  name  = "/marketplace-listing/cognito_client_secret"
  type  = "SecureString"
  value = "managed-outside-terraform"

  lifecycle {
    ignore_changes = [value]
  }
}

# Gemini key for the Fix flow's plain-English error explanations. Same pattern as above:
# adopted, value never in code.
resource "aws_ssm_parameter" "gemini_api_key" {
  name        = "/marketplace-listing/gemini_api_key"
  description = "Gemini API key for Fix-Error explanations"
  type        = "SecureString"
  value       = "managed-outside-terraform"

  lifecycle {
    ignore_changes = [value]
  }
}
