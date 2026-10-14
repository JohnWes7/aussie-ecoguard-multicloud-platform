# Set these in terraform.tfvars (git-ignored); terraform.tfvars.example lists them.

# The GCP project (with billing enabled) that hosts the shared inference service.
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
