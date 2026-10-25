# ---------- Notifications: subscribe Lambda -> SNS topic <- notify Lambda <- DynamoDB Stream ----------

# One topic for every subscriber. Each email subscription has a filter policy
# {"species": [...]} (set by lambdas/subscribe), and the notify Lambda publishes
# one message per file with a `species` message attribute listing its new tags,
# so SNS only emails users who chose at least one of them.
# The display name is the sender name on the confirmation and notification
# emails (instead of "AWS Notifications").
resource "aws_sns_topic" "notifications" {
  name         = "aussie-ecoguard-notifications"
  display_name = "Aussie EcoGuard"
}

data "archive_file" "notify" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/notify"
  output_path = "${path.module}/build/notify.zip"
}

# Publishes one message per file with new tags to the topic (lambdas/notify).
resource "aws_lambda_function" "notify" {
  function_name    = "aussie-ecoguard-notify"
  runtime          = "python3.12"
  handler          = "app.handler"
  filename         = data.archive_file.notify.output_path
  source_code_hash = data.archive_file.notify.output_base64sha256
  role             = local.lab_role
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.media.name
      TOPIC_ARN  = aws_sns_topic.notifications.arn
      # The links in the emails open the file page of the website.
      SITE_URL = "https://${terraform_data.website_bucket.output}.s3.amazonaws.com/index.html"
    }
  }
}

# Lambda reads the table's stream and invokes notify with up to 100 records.
# The filter passes only new TAG# items (a file gained a tag, from tagging or
# a manual edit); META changes and tag count updates never invoke it.
resource "aws_lambda_event_source_mapping" "notify" {
  event_source_arn  = aws_dynamodb_table.media.stream_arn
  function_name     = aws_lambda_function.notify.arn
  starting_position = "LATEST"
  batch_size        = 100
  # Wait up to 5 s for more records, so all tags of one file make one email.
  maximum_batching_window_in_seconds = 5
  # notify reports the first file it could not publish; Lambda retries from
  # there, at most 3 times, and then skips those records (logged in CloudWatch).
  function_response_types = ["ReportBatchItemFailures"]
  maximum_retry_attempts  = 3
  # Like the tagging queue, Learner Lab can disable this; apply turns it back on.
  enabled = true

  filter_criteria {
    filter {
      pattern = jsonencode({
        eventName = ["INSERT"]
        dynamodb  = { NewImage = { sk = { S = [{ prefix = "TAG#" }] } } }
      })
    }
  }
}
