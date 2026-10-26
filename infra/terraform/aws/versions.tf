terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.23" # 6.23+ reads `aws login` credentials
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = local.region

  default_tags {
    tags = {
      Project   = "aussie-ecoguard"
      ManagedBy = "Terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # The personal account's organization only allows Sydney (ap-southeast-2).
  # Every hard-coded region (cli commands, Cognito URLs) uses this.
  region     = "ap-southeast-2"
  account_id = data.aws_caller_identity.current.account_id
  # Every function's execution role (iam.tf). The name is from when this was
  # Learner Lab's LabRole.
  lab_role = aws_iam_role.lambda.arn
}
