# Read by the scripts with `terraform -chdir=infra/terraform/aws output -raw <name>`.

output "media_bucket_name" {
  value = terraform_data.media_bucket.output
}

output "media_table_name" {
  value = aws_dynamodb_table.media.name
}

output "website_bucket_name" {
  value = terraform_data.website_bucket.output
}

# HTTPS: use this one, browser SHA-256 (crypto.subtle) only works over HTTPS.
output "website_https_url" {
  value = "https://${terraform_data.website_bucket.output}.s3.amazonaws.com/index.html"
}

# HTTP-only static website endpoint, as in the Week 9 lab.
output "website_endpoint_url" {
  value = "http://${aws_s3_bucket_website_configuration.website.website_endpoint}"
}

output "api_id" {
  value = aws_api_gateway_rest_api.api.id
}

# Base URL of the REST API; the website appends /upload-url etc. to it.
output "api_url" {
  value = aws_api_gateway_stage.api.invoke_url
}

output "upload_url_endpoint" {
  value = "${aws_api_gateway_stage.api.invoke_url}/upload-url"
}

output "upload_url_function_name" {
  value = aws_lambda_function.upload_url.function_name
}

output "tagging_function_name" {
  value = aws_lambda_function.tagging.function_name
}

output "tagging_queue_url" {
  value = aws_sqs_queue.tagging.id
}

output "tagging_dead_letter_queue_url" {
  value = aws_sqs_queue.tagging_dlq.id
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.users.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

# Base URL of the Hosted UI; the website appends /oauth2/authorize etc. to it.
output "cognito_domain_url" {
  value = "https://${aws_cognito_user_pool_domain.users.domain}.auth.${local.region}.amazoncognito.com"
}

# The shared Cloud Run inference service this deployment calls (terraform.tfvars).
output "inference_url" {
  value = var.inference_url
}

# The machine client the tagging Lambda signs in as to call Cloud Run (cognito.tf).
# For manual tests of /infer; `terraform output -raw` prints the secret.
output "cognito_machine_client_id" {
  value = aws_cognito_user_pool_client.tagging.id
}

output "cognito_machine_client_secret" {
  value     = aws_cognito_user_pool_client.tagging.client_secret
  sensitive = true
}

# The smoke test's Cognito user (cognito.tf).
output "smoke_test_username" {
  value = aws_cognito_user.smoke_test.username
}

output "smoke_test_password" {
  value = local.smoke_test_password
}

# Open in a browser to try sign-up/sign-in before the website handles the code.
output "cognito_login_url" {
  value = "https://${aws_cognito_user_pool_domain.users.domain}.auth.${local.region}.amazoncognito.com/oauth2/authorize?client_id=${aws_cognito_user_pool_client.web.id}&response_type=code&scope=openid+email+profile&redirect_uri=${urlencode(local.auth_redirect_urls[0])}"
}

# SNS topic that subscribers are added to and the notify Lambda publishes to (notifications.tf).
output "notifications_topic_arn" {
  value = aws_sns_topic.notifications.arn
}

output "subscribe_function_name" {
  value = aws_lambda_function.subscribe.function_name
}

output "notify_function_name" {
  value = aws_lambda_function.notify.function_name
}
