# ---------- Thumbnails: S3 -> EventBridge -> thumbnail Lambda -> thumbnails/ ----------

# The same rule as tagging (tagging.tf, every new object under uploads/) gets
# a second target, so each upload also invokes the thumbnail function directly.
resource "aws_cloudwatch_event_target" "thumbnail" {
  rule      = aws_cloudwatch_event_rule.upload_created.name
  target_id = "thumbnail-function"
  arn       = aws_lambda_function.thumbnail.arn
}

# Resource-based policy: lets that rule (and only that rule) invoke the function.
resource "aws_lambda_permission" "thumbnail" {
  function_name = aws_lambda_function.thumbnail.function_name
  action        = "lambda:InvokeFunction"
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.upload_created.arn
}

# Pillow has compiled parts, so it is installed for Lambda's platform
# (Linux x86_64, Python 3.12) rather than for this machine, and goes into a
# layer. `apply` runs pip only when pillow_version changes, or with
# `terraform apply -replace=terraform_data.pillow_layer`. Needs pip3 on PATH.
locals {
  pillow_version   = "11.3.0"
  pillow_layer_dir = "${path.module}/build/pillow-layer"
}

resource "terraform_data" "pillow_layer" {
  input = local.pillow_version

  # Layer files must be under python/ in the zip. No shell operators, so the
  # command works in cmd.exe on Windows and sh on macOS/Linux.
  provisioner "local-exec" {
    command = "pip3 install pillow==${self.input} --platform manylinux_2_28_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: --no-compile --upgrade --target \"${local.pillow_layer_dir}/python\""
  }
}

data "archive_file" "pillow_layer" {
  type        = "zip"
  source_dir  = local.pillow_layer_dir
  output_path = "${path.module}/build/pillow-layer.zip"
  # Read after pip has run (on the first apply, or when the version changes).
  depends_on = [terraform_data.pillow_layer]
}

resource "aws_lambda_layer_version" "pillow" {
  layer_name               = "aussie-ecoguard-pillow"
  filename                 = data.archive_file.pillow_layer.output_path
  source_code_hash         = data.archive_file.pillow_layer.output_base64sha256
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
}

# Zips lambdas/thumbnail; a code change changes the hash, which updates the function.
data "archive_file" "thumbnail" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/thumbnail"
  output_path = "${path.module}/build/thumbnail.zip"
}

# Saves a small JPEG of each new image to thumbnails/<sha256>.jpg (lambdas/thumbnail).
resource "aws_lambda_function" "thumbnail" {
  function_name    = "aussie-ecoguard-thumbnail"
  runtime          = "python3.12"
  architectures    = ["x86_64"] # must match the Pillow layer
  handler          = "app.handler"
  filename         = data.archive_file.thumbnail.output_path
  source_code_hash = data.archive_file.thumbnail.output_base64sha256
  layers           = [aws_lambda_layer_version.pillow.arn]
  role             = local.lab_role
  # A 12-megapixel photo is about 36 MB once decoded; more memory also means
  # more CPU, so resizing is faster.
  memory_size = 512
  timeout     = 30

  environment {
    variables = {
      THUMB_SIZE    = "512" # px, longest side. Cards are up to ~350 px wide, so this stays sharp on HiDPI screens
      THUMB_QUALITY = "70"
      MAX_BYTES     = "52428800" # 50 MB; bigger images keep using the original in the library
    }
  }
}
