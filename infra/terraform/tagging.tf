# ---------- Tagging: S3 -> EventBridge -> SQS -> TaggingFunction -> Cloud Run ----------

# Sends every new object under uploads/ to the tagging queue.
resource "aws_cloudwatch_event_rule" "upload_created" {
  name = "aussie-ecoguard-upload-created"

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [terraform_data.media_bucket.output] }
      object = { key = [{ prefix = "uploads/" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "tagging_queue" {
  rule      = aws_cloudwatch_event_rule.upload_created.name
  target_id = "tagging-queue"
  arn       = aws_sqs_queue.tagging.arn
}

# Buffers uploads so at most maximum_concurrency files are tagged at once,
# however many arrive together. Cloud Run only has a few instances.
resource "aws_sqs_queue" "tagging" {
  name = "aussie-ecoguard-tagging"
  # AWS recommends at least 6x the function timeout (315 s), leaving room
  # for Lambda to retry if it is throttled. A failed attempt also becomes
  # visible again after this long, so it is the delay between retries.
  visibility_timeout_seconds = 1890

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.tagging_dlq.arn
    # AWS recommends at least 5. The function gives up and writes FAILED
    # on attempt MAX_ATTEMPTS (3), so only messages whose function keeps
    # crashing or timing out reach the dead-letter queue.
    maxReceiveCount = 5
  })
}

# Messages that failed every delivery. Their files stay PROCESSING; use
# "Start DLQ redrive" in the SQS console to send them back for another try.
resource "aws_sqs_queue" "tagging_dlq" {
  name                      = "aussie-ecoguard-tagging-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
}

# Resource-based policy: lets the EventBridge rule (and only that rule)
# send messages to the queue. LabRole is not involved here.
resource "aws_sqs_queue_policy" "tagging" {
  queue_url = aws_sqs_queue.tagging.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.tagging.arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.upload_created.arn }
      }
    }]
  })
}

# Zips lambdas/tagging; a code change changes the hash, which updates the function.
data "archive_file" "tagging" {
  type        = "zip"
  source_dir  = "${path.module}/../../lambdas/tagging"
  output_path = "${path.module}/build/tagging.zip"
}

# Runs inference on a new upload and saves the tags (lambdas/tagging).
resource "aws_lambda_function" "tagging" {
  function_name    = "aussie-ecoguard-tagging"
  runtime          = "python3.12"
  handler          = "app.handler"
  filename         = data.archive_file.tagging.output_path
  source_code_hash = data.archive_file.tagging.output_base64sha256
  role             = local.lab_role
  memory_size      = 128
  # One Cloud Run call (INFER_TIMEOUT) plus time for DynamoDB and S3.
  timeout = 315

  environment {
    variables = {
      TABLE_NAME    = aws_dynamodb_table.media.name
      BUCKET_NAME   = terraform_data.media_bucket.output
      INFERENCE_URL = var.inference_url
      MAX_ATTEMPTS  = "3"   # must stay below the queue's maxReceiveCount
      INFER_TIMEOUT = "300" # = Cloud Run --timeout; a cold start (~1 min) plus a 3-minute video (18 frames)
    }
  }
}

# Lambda polls the queue and invokes the function with one message at a time.
# If LabRole loses its SQS permissions (seen after a Learner Lab session ends),
# Lambda disables this mapping by itself; `terraform apply` sees
# enabled = false and turns it back on.
resource "aws_lambda_event_source_mapping" "tagging" {
  event_source_arn = aws_sqs_queue.tagging.arn
  function_name    = aws_lambda_function.tagging.arn
  batch_size       = 1
  enabled          = true

  scaling_config {
    maximum_concurrency = 2 # the minimum allowed; matches Cloud Run max-instances
  }
}
