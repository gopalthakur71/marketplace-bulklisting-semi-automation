# Audit trail of resources adopted from the console (phase 2). Import blocks are
# no-ops once the resource is in state; they stay here as the record of what was adopted.

# --- step 1: ECR, SSM, security group ---
import {
  to = aws_ecr_repository.app
  id = "marketplace-bulklisting"
}
import {
  to = aws_ecr_lifecycle_policy.app
  id = "marketplace-bulklisting"
}
import {
  to = aws_security_group.app
  id = "sg-06ceeb9a898378bb0"
}
import {
  for_each = local.ssm_plain_params
  to       = aws_ssm_parameter.plain[each.key]
  id       = "/marketplace-listing/${each.key}"
}

# --- step 2: S3 bucket (images + app state). Objects are never managed by Terraform. ---
import {
  to = aws_s3_bucket.data
  id = "ijorethnicpartners"
}
import {
  to = aws_s3_bucket_policy.data
  id = "ijorethnicpartners"
}
import {
  to = aws_s3_bucket_public_access_block.data
  id = "ijorethnicpartners"
}

# --- step 3: IAM (EC2 runtime role + GitHub Actions OIDC deploy role) ---
import {
  to = aws_iam_role.ec2
  id = "listing-app-ec2-role"
}
import {
  to = aws_iam_instance_profile.ec2
  id = "listing-app-ec2-role"
}
import {
  to = aws_iam_policy.runtime
  id = "arn:aws:iam::048589483919:policy/listing-app-runtime"
}
import {
  to = aws_iam_role_policy_attachment.ec2_runtime
  id = "listing-app-ec2-role/arn:aws:iam::048589483919:policy/listing-app-runtime"
}
import {
  to = aws_iam_role_policy_attachment.ec2_ssm_core
  id = "listing-app-ec2-role/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
import {
  to = aws_iam_openid_connect_provider.github
  id = "arn:aws:iam::048589483919:oidc-provider/token.actions.githubusercontent.com"
}
import {
  to = aws_iam_role.github
  id = "github-actions-ecr-push"
}
import {
  to = aws_iam_role_policy.github_ecr_push
  id = "github-actions-ecr-push:ecr-push"
}
import {
  to = aws_iam_role_policy.github_ssm_deploy
  id = "github-actions-ecr-push:ssm-deploy"
}

# --- step 4: Cognito (adopted only so phase 5 can delete it cleanly) + the current EC2 box ---
import {
  to = aws_cognito_user_pool.app
  id = "ap-south-1_NdxNQ1pIz"
}
import {
  to = aws_cognito_user_pool_client.app
  id = "ap-south-1_NdxNQ1pIz/29oo5dtqh8j30k2481lmffqb0e"
}
import {
  to = aws_cognito_user_pool_domain.app
  id = "ijor-marketplace"
}
# The console-built EC2 i-0add667d4cec224c6 was imported here too, then deleted at the
# phase-3 cutover (replaced by the Terraform/Ansible-built server in ec2.tf).

# --- phase 3: the Gemini key (missing from docs/infra-resources.md at import time) ---
import {
  to = aws_ssm_parameter.gemini_api_key
  id = "/marketplace-listing/gemini_api_key"
}
