# Base URL of the Cloud Run inference service, e.g. https://<service>-<hash>.a.run.app
# deploy.sh reads it from $INFERENCE_URL or asks gcloud for it.
variable "inference_url" {
  type        = string
  description = "Cloud Run inference service base URL"

  validation {
    condition     = can(regex("^https://[^/]+$", var.inference_url))
    error_message = "inference_url must be an https:// base URL with no path or trailing slash."
  }
}
