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

# Cognito user pools whose tokens /infer accepts: one per teammate's AWS
# deployment (`terraform -chdir=infra/terraform/aws output -raw cognito_user_pool_id`).
# A deployment whose pool is missing gets 401, so its uploads become FAILED.
# ["*"] accepts any Cognito pool. Only for a short changeover: anyone can create
# a pool with an aussie-ecoguard/infer scope and sign valid tokens.
variable "trusted_user_pools" {
  type        = list(string)
  description = "Cognito user pool IDs (e.g. us-east-1_AbC123) allowed to call /infer, or [\"*\"]"

  validation {
    condition     = alltrue([for p in var.trusted_user_pools : p == "*" || can(regex("^[a-z0-9-]+_[0-9A-Za-z]+$", p))])
    error_message = "Each entry must be a user pool ID such as us-east-1_AbC123, or \"*\"."
  }
}
