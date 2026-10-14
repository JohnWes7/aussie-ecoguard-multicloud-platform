terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0" # same as the Week 11 lab
    }
  }
}

# Uses Application Default Credentials: run `gcloud auth application-default login` once.
provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region

  default_labels = {
    project    = "aussie-ecoguard"
    managed-by = "terraform"
  }
}
