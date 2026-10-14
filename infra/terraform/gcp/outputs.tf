# Everyone's infra/terraform/aws sets its inference_url to this value.
output "inference_url" {
  value = google_cloud_run_v2_service.inference.uri
}

output "model_bucket" {
  value = "gs://${google_storage_bucket.models.name}"
}

output "inference_image" {
  value = terraform_data.inference_image.output
}
