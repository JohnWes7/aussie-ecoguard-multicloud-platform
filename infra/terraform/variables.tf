# Set these in terraform.tfvars (git-ignored); terraform.tfvars.example lists them.

# Your own GCP project (with billing enabled).
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

# A teammate's model bucket to copy model_version from, e.g.
# "gs://aussie-ecoguard-models-123456789012". Leave empty if you upload the
# models yourself with infra/upload_model.sh.
variable "model_source" {
  type    = string
  default = ""

  validation {
    condition     = var.model_source == "" || can(regex("^gs://[^/]+$", var.model_source))
    error_message = "model_source must be empty or gs://<bucket> with no folder or trailing slash."
  }
}

# Google accounts (emails) allowed to read your model bucket, so they can use
# it as their model_source.
variable "model_readers" {
  type    = list(string)
  default = []
}
