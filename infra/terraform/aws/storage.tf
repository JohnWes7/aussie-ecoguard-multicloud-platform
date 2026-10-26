# ---------- S3 buckets ----------
#
# The buckets themselves are created with the aws cli, not aws_s3_bucket:
# Learner Lab's service control policy denies s3:GetBucketObjectLockConfiguration,
# which aws_s3_bucket calls every time it reads a bucket, so plan/apply fail.
# Everything else about the buckets (CORS, events, website, policy) uses the
# normal resources below, which do not need that permission.
# The commands use no shell operators because local-exec runs cmd.exe on
# Windows and sh on macOS/Linux. Outside us-east-1, create-bucket needs a
# LocationConstraint, and fails with BucketAlreadyOwnedByYou if the bucket
# already exists; terraform_data only runs it when the bucket is first created.

# Private bucket for user media: originals in uploads/, thumbnails in thumbnails/.
resource "terraform_data" "media_bucket" {
  # Bucket names are global, so the account ID keeps this one unique.
  input = "aussie-ecoguard-media-${local.account_id}"

  provisioner "local-exec" {
    command = "aws s3api create-bucket --bucket ${self.input} --region ${local.region} --create-bucket-configuration LocationConstraint=${local.region}"
  }

  # No --force: like CloudFormation, refuse to delete a bucket that still holds
  # user media. Empty it first (aws s3 rm s3://<bucket> --recursive).
  provisioner "local-exec" {
    when    = destroy
    command = "aws s3 rb s3://${self.output}"
  }
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = terraform_data.media_bucket.output
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Publish every object event to the default EventBridge bus,
# where rules route uploads/ events to the processing Lambdas.
resource "aws_s3_bucket_notification" "media" {
  bucket      = terraform_data.media_bucket.output
  eventbridge = true
}

# The browser PUTs files directly with a presigned URL,
# sending the SHA-256 checksum header that S3 verifies.
resource "aws_s3_bucket_cors_configuration" "media" {
  bucket = terraform_data.media_bucket.output

  cors_rule {
    allowed_methods = ["PUT", "GET"]
    # Only pages served from the website bucket (or a local dev server) may call it.
    allowed_origins = [
      "https://${terraform_data.website_bucket.output}.s3.amazonaws.com",
      "http://${aws_s3_bucket_website_configuration.website.website_endpoint}",
      "http://localhost:8000",
    ]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

# Public bucket that hosts the web UI (HTML/JS only, no user data),
# set up the same way as the Week 9 lab.
resource "terraform_data" "website_bucket" {
  input = "aussie-ecoguard-web-${local.account_id}"

  provisioner "local-exec" {
    command = "aws s3api create-bucket --bucket ${self.input} --region ${local.region} --create-bucket-configuration LocationConstraint=${local.region}"
  }

  # --force: the site is generated from web/, so its files can be deleted.
  provisioner "local-exec" {
    when    = destroy
    command = "aws s3 rb s3://${self.output} --force"
  }
}

# Allow a public bucket policy; ACLs stay blocked since the policy is all we need.
resource "aws_s3_bucket_public_access_block" "website" {
  bucket                  = terraform_data.website_bucket.output
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "website" {
  bucket = terraform_data.website_bucket.output

  index_document {
    suffix = "index.html"
  }
  error_document {
    key = "error.html"
  }
}

resource "aws_s3_bucket_policy" "website" {
  bucket = terraform_data.website_bucket.output
  # S3 rejects a public policy until the public access block allows it.
  depends_on = [aws_s3_bucket_public_access_block.website]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicReadGetObject"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "arn:aws:s3:::${terraform_data.website_bucket.output}/*"
    }]
  })
}

# ---------- DynamoDB ----------

# Single table for file metadata and tags.
#   META item:          {file_id, sk: "META", status, file_url, thumb_url, file_type, ...}
#   one item per tag:   {file_id, sk: "TAG#fox", tag: "fox", tag_count: 2}
resource "aws_dynamodb_table" "media" {
  name         = "aussie-ecoguard-media"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "file_id"
  range_key    = "sk"

  # Every change goes on a stream; the notify Lambda reads new TAG# items
  # from it (notifications.tf). NEW_IMAGE: only the item after the change.
  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"

  attribute {
    name = "file_id" # SHA-256 of the file
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "tag"
    type = "S"
  }
  attribute {
    name = "tag_count" # "count" is a DynamoDB reserved word
    type = "N"
  }

  # Sparse index: only TAG# items have a "tag" attribute, so META items
  # stay out of it. Answers "tag = fox AND tag_count >= 2".
  global_secondary_index {
    name            = "tag-count-index"
    projection_type = "KEYS_ONLY"

    key_schema {
      attribute_name = "tag"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "tag_count"
      key_type       = "RANGE"
    }
  }

  # Abandoned PENDING items (URL issued, never uploaded) carry expires_at
  # and are deleted automatically, usually within a few days of expiring.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
