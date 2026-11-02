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
    command = "pip3 install pillow==${self.input} --no-user --platform manylinux_2_28_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: --no-compile --upgrade --target ${local.pillow_layer_dir}/python"
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

# ffmpeg for video thumbnails, as its own layer. The imageio-ffmpeg wheel for
# Linux x86_64 contains a static ffmpeg build (about 80 MB, 30 MB zipped), so
# it is installed the same way as Pillow, with no download step of its own.
locals {
  imageio_ffmpeg_version = "0.6.0" # ships ffmpeg 7.0.2
  ffmpeg_layer_dir       = "${path.module}/build/ffmpeg-layer"
}

resource "terraform_data" "ffmpeg_layer" {
  input = local.imageio_ffmpeg_version

  provisioner "local-exec" {
    command = "pip3 install imageio-ffmpeg==${self.input} --no-user --no-deps --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: --no-compile --upgrade --target ${local.ffmpeg_layer_dir}/python"
  }
}

data "archive_file" "ffmpeg_layer" {
  type        = "zip"
  source_dir  = local.ffmpeg_layer_dir
  output_path = "${path.module}/build/ffmpeg-layer.zip"
  depends_on  = [terraform_data.ffmpeg_layer]
}

resource "aws_lambda_layer_version" "ffmpeg" {
  layer_name               = "aussie-ecoguard-ffmpeg"
  filename                 = data.archive_file.ffmpeg_layer.output_path
  source_code_hash         = data.archive_file.ffmpeg_layer.output_base64sha256
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
}

# Zips lambdas/thumbnail; a code change changes the hash, which updates the function.
data "archive_file" "thumbnail" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/thumbnail"
  output_path = "${path.module}/build/thumbnail.zip"
}

# Saves a small JPEG of each new image, or of one frame of each new video, to
# thumbnails/<sha256>.jpg (lambdas/thumbnail).
resource "aws_lambda_function" "thumbnail" {
  function_name    = "aussie-ecoguard-thumbnail"
  runtime          = "python3.12"
  architectures    = ["x86_64"] # must match the Pillow and ffmpeg layers
  handler          = "app.handler"
  filename         = data.archive_file.thumbnail.output_path
  source_code_hash = data.archive_file.thumbnail.output_base64sha256
  layers           = [aws_lambda_layer_version.pillow.arn, aws_lambda_layer_version.ffmpeg.arn]
  role             = local.lab_role
  # A 12-megapixel photo is about 36 MB once decoded; more memory also means
  # more CPU, so resizing and decoding a video frame are faster.
  memory_size = 1024
  # A 500 MB video is copied from S3 to /tmp before ffmpeg reads one frame.
  timeout = 90
  ephemeral_storage {
    size = 1024 # MB of /tmp: the largest video (500 MB) plus room to spare
  }

  environment {
    variables = {
      THUMB_SIZE    = "512" # px, longest side. Cards are up to ~350 px wide, so this stays sharp on HiDPI screens
      THUMB_QUALITY = "70"
      MAX_BYTES     = "52428800" # 50 MB; bigger images keep using the original in the library
      # Videos: the website's upload limit (inference/main.py MAX_VIDEO_BYTES)
      MAX_VIDEO_BYTES     = "524288000"
      VIDEO_FRAME_SECONDS = "1" # the very first frame is often black or a fade in
    }
  }
}
