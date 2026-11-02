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

# The chat function only needs to send text to the one bot alias, so it gets
# that single action instead of AmazonLexFullAccess.
resource "aws_iam_role_policy" "lex_chat" {
  name = "lex-recognize-text"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lex:RecognizeText"]
      Resource = local.lex_bot_alias_arn
    }]
  })
}
