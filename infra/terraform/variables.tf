# Base URL of the Cloud Run inference service, e.g. https://<service>-<hash>.a.run.app
# Set in terraform.tfvars.
variable "inference_url" {
  type        = string
  description = "Cloud Run inference service base URL"

  validation {
    condition     = can(regex("^https://[^/]+$", var.inference_url))
    error_message = "inference_url must be an https:// base URL with no path or trailing slash."
  }
}

# Your own GCP project (with billing enabled). Set in terraform.tfvars.
variable "gcp_project_id" {
  type        = string
  description = "GCP project for Cloud Run and the model bucket"
}

# us-east4 (N. Virginia) sits next to AWS us-east-1, so the Lambda call and
# the S3 download stay in one metro.
variable "gcp_region" {
  type    = string
  default = "us-east4"
}

# Folder in the model bucket that the service loads at startup. Versions are
# never edited in place: upload a new folder and change this to switch models.
variable "model_version" {
  type    = string
  default = "v2"
}
