# ---------------------------------------------------------------------------------------
# EC2 runtime role: what the running app on the box may do. No access keys anywhere.
# ---------------------------------------------------------------------------------------
resource "aws_iam_role" "ec2" {
  name        = "listing-app-ec2-role"
  description = "Allows EC2 instances to call AWS services on your behalf."
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name = "listing-app-ec2-role"
  role = aws_iam_role.ec2.name
}

resource "aws_iam_policy" "runtime" {
  name = "listing-app-runtime"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrPull"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = "*"
      },
      {
        # ListBucket is required: without it a missing key returns 403, not 404, and the app 500s.
        Sid      = "AppS3List"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.data.arn
      },
      {
        Sid      = "AppS3"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.data.arn}/*"
      },
      {
        # SecureString decryption uses the AWS-managed aws/ssm key, so no kms statement is needed.
        Sid      = "AppConfig"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:ap-south-1:048589483919:parameter/marketplace-listing/*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_runtime" {
  role       = aws_iam_role.ec2.name
  policy_arn = aws_iam_policy.runtime.arn
}

# Makes the box SSM-managed: CI deploys via Run Command, phase 3/4 use Session Manager.
resource "aws_iam_role_policy_attachment" "ec2_ssm_core" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# ---------------------------------------------------------------------------------------
# GitHub Actions: OIDC federation, no stored keys. ⚠️ A wrong trust policy breaks CI —
# every change here must reach a reviewed plan before apply.
# ---------------------------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["22ff89586561fc2d52f77491e9f1eff1b80be33e"]
}

resource "aws_iam_role" "github" {
  name = "github-actions-ecr-push"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          # Only pushes to main may assume this role. (The duplicate entry is as found in
          # IAM; kept so adoption is a no-op. Safe to dedupe in a later, reviewed change.)
          "token.actions.githubusercontent.com:sub" = [
            "repo:gopalthakur71/marketplace-bulklisting-semi-automation:ref:refs/heads/main",
            "repo:gopalthakur71/marketplace-bulklisting-semi-automation:ref:refs/heads/main",
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_ecr_push" {
  name = "ecr-push"
  role = aws_iam_role.github.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid      = "EcrPushPull"
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = aws_ecr_repository.app.arn
      },
    ]
  })
}

# Deploy job: find the instance tagged Name=listing-app and run `systemctl restart` on it.
resource "aws_iam_role_policy" "github_ssm_deploy" {
  name = "ssm-deploy"
  role = aws_iam_role.github.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DescribeInstances"
        Effect   = "Allow"
        Action   = "ec2:DescribeInstances"
        Resource = "*"
      },
      {
        Sid       = "SendToTaggedInstance"
        Effect    = "Allow"
        Action    = "ssm:SendCommand"
        Resource  = "arn:aws:ec2:ap-south-1:048589483919:instance/*"
        Condition = { StringEquals = { "ssm:resourceTag/Name" = "listing-app" } }
      },
      {
        Sid      = "SendUsingShellDoc"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:aws:ssm:ap-south-1::document/AWS-RunShellScript"
      },
      {
        Sid      = "ReadCommandResult"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommands"]
        Resource = "*"
      },
    ]
  })
}
