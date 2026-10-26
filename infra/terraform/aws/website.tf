# ---------- Website: upload web/ to the website bucket ----------

locals {
  web_dir = "${path.module}/../../../web"

  # Every file in web/ except config.js, which is generated below. A local
  # web/config.js (for a local dev server) is never uploaded.
  web_files = setsubtract(fileset(local.web_dir, "**"), ["config.js"])

  # S3 serves the Content-Type stored with each object.
  content_types = {
    html = "text/html"
    js   = "text/javascript"
    css  = "text/css"
    png  = "image/png"
    jpg  = "image/jpeg"
    svg  = "image/svg+xml"
    ico  = "image/x-icon"
  }
}

# One object per file. A file deleted from web/ is deleted from the bucket.
# etag makes Terraform upload a file again only when its content changes.
# no-cache makes browsers check for a new version on every load instead of
# keeping an old app.js.
resource "aws_s3_object" "web" {
  for_each = local.web_files

  bucket        = terraform_data.website_bucket.output
  key           = each.value
  source        = "${local.web_dir}/${each.value}"
  etag          = filemd5("${local.web_dir}/${each.value}")
  content_type  = lookup(local.content_types, reverse(split(".", each.value))[0], "application/octet-stream")
  cache_control = "no-cache"
}

# The page reads window.CONFIG (auth.js, upload.js, library.js). Its values
# differ per account, so it is written here instead of being kept in git.
resource "aws_s3_object" "web_config" {
  bucket = terraform_data.website_bucket.output
  key    = "config.js"
  content = join("\n", [
    "window.CONFIG = {",
    "  apiUrl: \"${aws_api_gateway_stage.api.invoke_url}\",",
    "  cognitoDomain: \"https://${aws_cognito_user_pool_domain.users.domain}.auth.${local.region}.amazoncognito.com\",",
    "  clientId: \"${aws_cognito_user_pool_client.web.id}\",",
    "  inferenceUrl: \"${var.inference_url}\",", # Cloud Run, for POST /detect (web/detect.js)
    "};",
    "",
  ])
  content_type  = "text/javascript"
  cache_control = "no-cache"
}
