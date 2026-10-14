# Read by deploy.sh and the scripts with `terraform -chdir=infra/terraform output -raw <name>`.

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
