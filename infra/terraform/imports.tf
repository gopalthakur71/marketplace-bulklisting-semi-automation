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
import {
  to = aws_ssm_parameter.cognito_client_secret
  id = "/marketplace-listing/cognito_client_secret"
}
