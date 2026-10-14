terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0" # same as the Week 11 lab
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  # Learner Lab region. The bucket commands in storage.tf assume us-east-1.
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = "aussie-ecoguard"
      ManagedBy = "Terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # Learner Lab cannot create IAM roles, so every function uses LabRole.
  lab_role = "arn:aws:iam::${local.account_id}:role/LabRole"
}
