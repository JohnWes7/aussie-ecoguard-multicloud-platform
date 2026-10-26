terraform {
  required_version = ">= 1.10.0" # use_lockfile below needs 1.10+

  # Shared state, so the whole team applies to the same stack. The bucket is
  # created once with the aws cli (README § Deploy), not by this config, and
  # has versioning on so an old state can be restored. use_lockfile stops two
  # applies from running at once. A backend block cannot use variables, so the
  # name is written out.
  backend "s3" {
    bucket       = "aussie-ecoguard-tfstate-908203539695"
    key          = "aws/terraform.tfstate"
    region       = "ap-southeast-2"
    encrypt      = true
    use_lockfile = true
  }

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
