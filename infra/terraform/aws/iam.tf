# ---------- Lambda execution role ----------
#
# Replaces Learner Lab's LabRole: one role shared by every function.
# The functions together use S3, DynamoDB (tables and the stream that
# triggers notify), SNS, the SQS queue that triggers tagging, and CloudWatch
# Logs, so the role gets the AWS managed policy for each of those.

resource "aws_iam_role" "lambda" {
  name = "aussie-ecoguard-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda" {
  for_each = toset([
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",    # CloudWatch Logs
    "arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole", # tagging queue
    "arn:aws:iam::aws:policy/AmazonS3FullAccess",
    "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess", # includes stream reads
    "arn:aws:iam::aws:policy/AmazonSNSFullAccess",
  ])

  role       = aws_iam_role.lambda.name
  policy_arn = each.value
}
