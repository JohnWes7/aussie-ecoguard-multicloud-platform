# ---------- Cognito ----------
#
# Sign-up and sign-in use the Cognito Hosted UI. After sign-in, the browser
# exchanges the authorization code for tokens and sends the ID token to
# API Gateway, whose Cognito authorizer checks it.

locals {
  # The page Cognito sends the browser back to after sign-in and sign-out.
  # Must match the redirect_uri the website sends exactly.
  auth_redirect_urls = [
    "https://${terraform_data.website_bucket.output}.s3.amazonaws.com/index.html",
    "http://localhost:8000/index.html", # local dev server (README), http allowed for localhost only
  ]
}

resource "aws_cognito_user_pool" "users" {
  name = "aussie-ecoguard-users"

  # Users sign in with their email address instead of a separate username.
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # The spec asks for email, first name and last name on every account.
  # Schema attributes cannot be changed after the pool is created.
  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true
    string_attribute_constraints {
      min_length = 0
      max_length = 2048
    }
  }
  schema {
    name                = "given_name"
    attribute_data_type = "String"
    required            = true
    mutable             = true
    string_attribute_constraints {
      min_length = 0
      max_length = 2048
    }
  }
  schema {
    name                = "family_name"
    attribute_data_type = "String"
    required            = true
    mutable             = true
    string_attribute_constraints {
      min_length = 0
      max_length = 2048
    }
  }

  # Forgotten passwords are reset with a code sent to the verified email.
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

# The client the website uses. No secret: the website is static JS, so anyone
# can read its code and a secret would not stay secret.
resource "aws_cognito_user_pool_client" "web" {
  name         = "aussie-ecoguard-web"
  user_pool_id = aws_cognito_user_pool.users.id

  generate_secret = false

  # Hosted UI with the authorization code flow.
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = local.auth_redirect_urls
  logout_urls                          = local.auth_redirect_urls

  # Users sign in only through the Hosted UI; the website itself only refreshes
  # tokens. ADMIN_USER_PASSWORD_AUTH lets the smoke test get a token for its
  # test user from the command line. It needs AWS credentials (the call is
  # AdminInitiateAuth), so the public client ID alone cannot use it.
  explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_ADMIN_USER_PASSWORD_AUTH"]

  # Sign-in errors do not reveal whether an email has an account.
  prevent_user_existence_errors = "ENABLED"
}

# Test user for scripts/smoke_test.sh, which signs in with ADMIN_USER_PASSWORD_AUTH.
# The password is written here on purpose so nobody has to set anything up.
# Anyone who can read this repository can sign in to the website with it, so
# it is for a coursework deployment only. No email is sent (SUPPRESS) and the
# address is marked verified, so the account is ready to use at once.
locals {
  smoke_test_username = "smoke-test@example.com" # example.com is reserved; nobody receives its mail
  smoke_test_password = "Smoke-Test-2026"        # meets the default policy: upper, lower, number, symbol
}

resource "aws_cognito_user" "smoke_test" {
  user_pool_id   = aws_cognito_user_pool.users.id
  username       = local.smoke_test_username
  password       = local.smoke_test_password # a permanent password: no change is asked at first sign-in
  message_action = "SUPPRESS"

  attributes = {
    email          = local.smoke_test_username
    email_verified = "true"
    given_name     = "Smoke"
    family_name    = "Test"
  }
}

# Managed login pages need a branding style for the app client. The console
# adds one automatically, the API does not. aws provider 5.x has no resource
# for it, so it is created with the aws cli using Cognito's default style.
# A new client gets a new style; deleting the user pool deletes the style.
resource "terraform_data" "login_branding" {
  triggers_replace = aws_cognito_user_pool_client.web.id

  provisioner "local-exec" {
    command = "aws cognito-idp create-managed-login-branding --user-pool-id ${aws_cognito_user_pool.users.id} --client-id ${aws_cognito_user_pool_client.web.id} --use-cognito-provided-values --region us-east-1"
  }
}

# Login page address: https://<domain>.auth.us-east-1.amazoncognito.com
resource "aws_cognito_user_pool_domain" "users" {
  # Domain prefixes are global, so the account ID keeps this one unique.
  domain       = "aussie-ecoguard-${local.account_id}"
  user_pool_id = aws_cognito_user_pool.users.id

  # 2 = managed login (the newer pages), 1 = classic Hosted UI.
  managed_login_version = 2
  depends_on            = [terraform_data.login_branding]
}
