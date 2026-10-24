# ---------- Lambda functions behind the API ----------

data "archive_file" "upload_url" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/upload_url"
  output_path = "${path.module}/build/upload_url.zip"
}

# Reserves a checksum in the media table and returns a presigned PUT URL.
resource "aws_lambda_function" "upload_url" {
  function_name    = "aussie-ecoguard-upload-url"
  runtime          = "python3.12"
  handler          = "app.handler"
  filename         = data.archive_file.upload_url.output_path
  source_code_hash = data.archive_file.upload_url.output_base64sha256
  role             = local.lab_role
  memory_size      = 128
  timeout          = 10

  environment {
    variables = {
      TABLE_NAME  = aws_dynamodb_table.media.name
      BUCKET_NAME = terraform_data.media_bucket.output
      URL_EXPIRES = "900"
      PENDING_TTL = "86400" # 24h before an unused reservation is cleaned up
    }
  }
}

data "archive_file" "files" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/files"
  output_path = "${path.module}/build/files.zip"
}

# Lists files with their tags, and returns one file's status for polling (lambdas/files).
resource "aws_lambda_function" "files" {
  function_name    = "aussie-ecoguard-files"
  runtime          = "python3.12"
  handler          = "app.handler"
  filename         = data.archive_file.files.output_path
  source_code_hash = data.archive_file.files.output_base64sha256
  role             = local.lab_role
  memory_size      = 128
  timeout          = 10

  environment {
    variables = {
      TABLE_NAME          = aws_dynamodb_table.media.name
      BUCKET_NAME         = terraform_data.media_bucket.output
      PREVIEW_URL_EXPIRES = "3600"
      URL_EXPIRES         = "900" # keep equal to the upload_url function's URL_EXPIRES
    }
  }
}

# ---------- REST API ----------

resource "aws_api_gateway_rest_api" "api" {
  name = "aussie-ecoguard-api"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# Checks the Cognito ID token in the Authorization header (the raw JWT, no
# "Bearer ") before any Lambda runs: signature, issuer (our user pool) and
# expiry. Missing or invalid -> 401, and the Lambda is never invoked. Every
# route except the OPTIONS preflights uses it. A valid token's claims reach the
# Lambda in event["requestContext"]["authorizer"]["claims"].
resource "aws_api_gateway_authorizer" "cognito" {
  name            = "cognito"
  rest_api_id     = aws_api_gateway_rest_api.api.id
  type            = "COGNITO_USER_POOLS"
  provider_arns   = [aws_cognito_user_pool.users.arn]
  identity_source = "method.request.header.Authorization"
}

# ---------- POST /upload-url -> upload_url function ----------

resource "aws_api_gateway_resource" "upload_url" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "upload-url"
}

resource "aws_api_gateway_method" "upload_url_post" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.upload_url.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

# Lambda proxy integration: API Gateway passes the whole HTTP request to the
# function as `event` and returns the function's {statusCode, headers, body}.
resource "aws_api_gateway_integration" "upload_url_post" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.upload_url.id
  http_method             = aws_api_gateway_method.upload_url_post.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST" # Lambda is always invoked with POST
  uri                     = aws_lambda_function.upload_url.invoke_arn
}

# CORS preflight. A JSON POST from the website (another origin) makes the
# browser send OPTIONS first; this answers it without invoking Lambda.
# A MOCK answer takes four resources in Terraform: the method, the integration,
# and the response on each side.
resource "aws_api_gateway_method" "upload_url_options" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.upload_url.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "upload_url_options" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.upload_url.id
  http_method = aws_api_gateway_method.upload_url_options.http_method
  type        = "MOCK"
  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

resource "aws_api_gateway_method_response" "upload_url_options" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.upload_url.id
  http_method = aws_api_gateway_method.upload_url_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Headers" = true
  }
}

resource "aws_api_gateway_integration_response" "upload_url_options" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.upload_url.id
  http_method = aws_api_gateway_method.upload_url_options.http_method
  status_code = aws_api_gateway_method_response.upload_url_options.status_code
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
    "method.response.header.Access-Control-Allow-Methods" = "'POST,OPTIONS'"
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
  }
  depends_on = [aws_api_gateway_integration.upload_url_options]
}

# Resource-based policy on the function: lets this API (and only this
# route) invoke it. LabRole is not involved here.
resource "aws_lambda_permission" "upload_url" {
  function_name = aws_lambda_function.upload_url.function_name
  action        = "lambda:InvokeFunction"
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/POST/upload-url"
}

# ---------- GET /files and GET /files/{sha256} -> files function ----------

resource "aws_api_gateway_resource" "files" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "files"
}

# {sha256} is a path parameter; Lambda finds it in event["pathParameters"].
resource "aws_api_gateway_resource" "file" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_resource.files.id
  path_part   = "{sha256}"
}

resource "aws_api_gateway_method" "files_get" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.files.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "files_get" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.files.id
  http_method             = aws_api_gateway_method.files_get.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.files.invoke_arn
}

resource "aws_api_gateway_method" "file_get" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.file.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "file_get" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.file.id
  http_method             = aws_api_gateway_method.file_get.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.files.invoke_arn
}

# CORS preflights for both GET routes, the same as for /upload-url. A plain
# GET needs none, but the Authorization header makes the browser send one.
resource "aws_api_gateway_method" "files_options" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.files.id
  http_method   = "OPTIONS"
  authorization = "NONE" # the browser never sends a token with a preflight
}

resource "aws_api_gateway_integration" "files_options" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.files.id
  http_method = aws_api_gateway_method.files_options.http_method
  type        = "MOCK"
  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

resource "aws_api_gateway_method_response" "files_options" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.files.id
  http_method = aws_api_gateway_method.files_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Headers" = true
  }
}

resource "aws_api_gateway_integration_response" "files_options" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.files.id
  http_method = aws_api_gateway_method.files_options.http_method
  status_code = aws_api_gateway_method_response.files_options.status_code
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
  }
  depends_on = [aws_api_gateway_integration.files_options]
}

resource "aws_api_gateway_method" "file_options" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.file.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "file_options" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.file.id
  http_method = aws_api_gateway_method.file_options.http_method
  type        = "MOCK"
  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

resource "aws_api_gateway_method_response" "file_options" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.file.id
  http_method = aws_api_gateway_method.file_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Headers" = true
  }
}

resource "aws_api_gateway_integration_response" "file_options" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.file.id
  http_method = aws_api_gateway_method.file_options.http_method
  status_code = aws_api_gateway_method_response.file_options.status_code
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
  }
  depends_on = [aws_api_gateway_integration.file_options]
}

# Lets this API invoke the function for GET /files and GET /files/<anything>.
resource "aws_lambda_permission" "files" {
  function_name = aws_lambda_function.files.function_name
  action        = "lambda:InvokeFunction"
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/files*"
}

# ---------- CORS headers on API Gateway's own errors ----------

# Errors that API Gateway answers itself, without running a Lambda (401 from
# the Cognito authorizer, 403 for an unknown route, 429 throttling, 5xx), carry
# no CORS header by default. The browser then hides the status from the page and
# reports only a CORS error, so the website could not tell "signed out" from
# "network down". These add the header to every 4xx and 5xx of that kind.
# response_templates is API Gateway's default body ({"message": "..."}); it is
# written out because AWS fills it in anyway, and leaving it out makes every
# plan show a change.
resource "aws_api_gateway_gateway_response" "default_4xx" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  response_type = "DEFAULT_4XX"
  response_parameters = {
    "gatewayresponse.header.Access-Control-Allow-Origin" = "'*'"
  }
  response_templates = {
    "application/json" = "{\"message\":$context.error.messageString}"
  }
}

resource "aws_api_gateway_gateway_response" "default_5xx" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  response_type = "DEFAULT_5XX"
  response_parameters = {
    "gatewayresponse.header.Access-Control-Allow-Origin" = "'*'"
  }
  response_templates = {
    "application/json" = "{\"message\":$context.error.messageString}"
  }
}

# ---------- Deployment and stage ----------

# A deployment is a snapshot of the API; the stage "api" serves it at
# https://<api-id>.execute-api.<region>.amazonaws.com/api/...
# A deployment never changes after it is created, so a change to `triggers`
# replaces it with a new snapshot:
#   - the IDs make the snapshot wait until every route exists, and change when
#     a route is added or removed. Add every new route's resources here.
#   - the hash of this file catches in-place edits that keep the IDs, e.g.
#     changing a method's authorization. Keep API routes in this file.
# (Hashing whole resources would also work, but shows a change after the
# first apply; see the aws_api_gateway_deployment docs.)
resource "aws_api_gateway_deployment" "api" {
  rest_api_id = aws_api_gateway_rest_api.api.id

  triggers = {
    redeployment = sha1(jsonencode([
      filesha1("${path.module}/api.tf"),
      aws_api_gateway_authorizer.cognito.id,
      aws_api_gateway_resource.upload_url.id,
      aws_api_gateway_method.upload_url_post.id,
      aws_api_gateway_integration.upload_url_post.id,
      aws_api_gateway_method.upload_url_options.id,
      aws_api_gateway_integration.upload_url_options.id,
      aws_api_gateway_method_response.upload_url_options.id,
      aws_api_gateway_integration_response.upload_url_options.id,
      aws_api_gateway_resource.files.id,
      aws_api_gateway_resource.file.id,
      aws_api_gateway_method.files_get.id,
      aws_api_gateway_integration.files_get.id,
      aws_api_gateway_method.file_get.id,
      aws_api_gateway_integration.file_get.id,
      aws_api_gateway_method.files_options.id,
      aws_api_gateway_integration.files_options.id,
      aws_api_gateway_method_response.files_options.id,
      aws_api_gateway_integration_response.files_options.id,
      aws_api_gateway_method.file_options.id,
      aws_api_gateway_integration.file_options.id,
      aws_api_gateway_method_response.file_options.id,
      aws_api_gateway_integration_response.file_options.id,
      aws_api_gateway_gateway_response.default_4xx.id,
      aws_api_gateway_gateway_response.default_5xx.id,
    ]))
  }

  # Create the new snapshot and move the stage to it before deleting the old
  # one; a deployment that a stage still uses cannot be deleted.
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "api" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  deployment_id = aws_api_gateway_deployment.api.id
  stage_name    = "api"
}
