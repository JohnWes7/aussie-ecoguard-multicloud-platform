# Base URL of the shared Cloud Run inference service, e.g.
# https://<service>-<hash>.a.run.app. One person deploys it (infra/terraform/gcp,
# output inference_url); everyone's AWS points at it. Set in terraform.tfvars.
variable "inference_url" {
  type        = string
  description = "Cloud Run inference service base URL"

  validation {
    condition     = can(regex("^https://[^/]+$", var.inference_url))
    error_message = "inference_url must be an https:// base URL with no path or trailing slash."
  }
}

# The Lex V2 bot (AussieEcoGuardBot), set up in the console. Bot ID from the
# Lex console's bot list; the alias is the built-in test alias, which serves
# the latest build of the Draft version.
variable "lex_bot_id" {
  type        = string
  description = "Lex V2 bot ID"
  default     = "2VC6CJK2LJ"
}

variable "lex_bot_alias_id" {
  type        = string
  description = "Lex V2 bot alias ID"
  default     = "TSTALIASID"
}
