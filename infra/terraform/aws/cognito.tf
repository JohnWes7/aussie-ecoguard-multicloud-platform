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

  # Only the Hosted UI signs users in; the website itself only refreshes tokens.
  explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]

  # Sign-in errors do not reveal whether an email has an account.
  prevent_user_existence_errors = "ENABLED"
}

# Hosted UI address: https://<domain>.auth.us-east-1.amazoncognito.com
resource "aws_cognito_user_pool_domain" "users" {
  # Domain prefixes are global, so the account ID keeps this one unique.
  domain       = "aussie-ecoguard-${local.account_id}"
  user_pool_id = aws_cognito_user_pool.users.id

  # 1 = classic Hosted UI. Version 2 (managed login) also needs an
  # aws_cognito_managed_login_branding resource, or the page shows an error.
  managed_login_version = 1
}
