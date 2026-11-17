# Cognito login — BEING REMOVED (IaC phase 5). The app no longer has a login; the SSM port
# forward (AWS IAM) is the only gate. Deleting takes two applies:
#   A (this file as it stands): turn off the pool's AWS-side deletion_protection and drop
#     prevent_destroy. For Cognito the plan must show only that one in-place pool update
#     (the 5 /marketplace-listing/cognito_* SSM params are destroyed in the same apply).
#   B: delete this whole file + its import blocks in imports.tf → apply destroys pool, client
#     and domain (the test user goes with the pool).
resource "aws_cognito_user_pool" "app" {
  name                     = "User pool - e6qzup"
  deletion_protection      = "INACTIVE" # phase 5 step A: unlock for deletion
  mfa_configuration        = "OFF"
  user_pool_tier           = "ESSENTIALS"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
    recovery_mechanism {
      name     = "verified_phone_number"
      priority = 2
    }
  }
  admin_create_user_config {
    allow_admin_create_user_only = false
  }
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }
  password_policy {
    minimum_length                   = 8
    password_history_size            = 0
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 7
  }
  sign_in_policy {
    allowed_first_auth_factors = ["PASSWORD"]
  }
  username_configuration {
    case_sensitive = false
  }
  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }
}

resource "aws_cognito_user_pool_client" "app" {
  name                                 = "marketplace-listing-pool"
  user_pool_id                         = aws_cognito_user_pool.app.id
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["email", "openid", "phone"]
  callback_urls                        = ["http://localhost:8000/auth/callback"] # SSH tunnel
  logout_urls                          = ["http://localhost:8000/"]
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_AUTH", "ALLOW_USER_SRP_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  auth_session_validity                = 3
  access_token_validity                = 60
  id_token_validity                    = 480 # 8 h = TOKEN_MAX_AGE in auth_routes.py
  refresh_token_validity               = 5
  read_attributes                      = []
  write_attributes                     = []

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_domain" "app" {
  domain                = "ijor-marketplace"
  user_pool_id          = aws_cognito_user_pool.app.id
  managed_login_version = 2
}
