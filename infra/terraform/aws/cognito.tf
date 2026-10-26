# ---------- Cognito ----------
#
# Sign-up and sign-in use the Cognito Hosted UI. After sign-in, the browser
# exchanges the authorization code for tokens and sends the ID token to
# API Gateway, whose Cognito authorizer checks it.

locals {
  # The page Cognito sends the browser back to after sign-in and sign-out.
  # Must match the redirect_uri the website sends exactly.
  auth_redirect_urls = [
    "https://${terraform_data.website_bucket.output}.s3.${local.region}.amazonaws.com/index.html",
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

# ---------- Machine-to-machine: tagging Lambda -> Cloud Run ----------
#
# The tagging Lambda runs on S3 events, with no user and so no user token.
# It signs in as this "machine" client instead (OAuth client credentials:
# client ID + secret, no username) and gets an access token from Cognito,
# which Cloud Run checks like any Cognito token. The token's scope and expiry
# are set by Cognito and covered by its signature, so the Lambda cannot change them.

# Defines the scope aussie-ecoguard/infer. Only clients allowed this scope
# (the tagging client below, not the website's) can get a token that carries it,
# and Cloud Run's /infer requires it. That is what keeps out self-registered
# users, whose tokens are signed by the same pool.
resource "aws_cognito_resource_server" "api" {
  user_pool_id = aws_cognito_user_pool.users.id
  identifier   = "aussie-ecoguard"
  name         = "aussie-ecoguard"

  scope {
    scope_name        = "infer"
    scope_description = "Call the inference service (Cloud Run /infer)"
  }
}

resource "aws_cognito_user_pool_client" "tagging" {
  name         = "aussie-ecoguard-tagging"
  user_pool_id = aws_cognito_user_pool.users.id

  # The secret is the machine's password. Only the tagging Lambda gets it.
  generate_secret = true

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_scopes                 = aws_cognito_resource_server.api.scope_identifiers # ["aussie-ecoguard/infer"]

  # Client credentials uses none of these; set so AWS does not add its defaults.
  explicit_auth_flows           = ["ALLOW_REFRESH_TOKEN_AUTH"]
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
# adds one automatically, the API does not, so it is created here with
# Cognito's default style.
resource "aws_cognito_managed_login_branding" "web" {
  user_pool_id                = aws_cognito_user_pool.users.id
  client_id                   = aws_cognito_user_pool_client.web.id
  use_cognito_provided_values = true
}

# Login page address: https://<domain>.auth.<region>.amazoncognito.com
resource "aws_cognito_user_pool_domain" "users" {
  # Domain prefixes are global, so the account ID keeps this one unique.
  domain       = "aussie-ecoguard-${local.account_id}"
  user_pool_id = aws_cognito_user_pool.users.id

  # 2 = managed login (the newer pages), 1 = classic Hosted UI.
  managed_login_version = 2
  depends_on            = [aws_cognito_managed_login_branding.web]
}
