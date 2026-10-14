# ---------- GCP: Cloud Run inference service + model bucket ----------

data "google_project" "current" {}

locals {
  gcp_project_number = data.google_project.current.number
  # Cloud Run runs as the Compute Engine default service account unless told
  # otherwise, and Cloud Build builds as it too.
  run_service_account = "${local.gcp_project_number}-compute@developer.gserviceaccount.com"

  # TODO(step 2b): Terraform builds the image. Until then, the image that
  # deploy_gcp.sh last deployed.
  inference_image = "us-east4-docker.pkg.dev/${var.gcp_project_id}/cloud-run-source-deploy/aussie-ecoguard-inference@sha256:84eb24b52c123c10181690e483aac8c2e77580e3a8e5f493189baeb470259622"
}

# APIs a new project needs. disable_on_destroy = false: destroy leaves them on,
# since other things in the project may use them.
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}

# Cloud Build builds as the default service account, which needs to read the
# uploaded source and push the image.
resource "google_project_iam_member" "run_builder" {
  project = var.gcp_project_id
  role    = "roles/run.builder"
  member  = "serviceAccount:${local.run_service_account}"
}

# ---------- Model bucket ----------

# One folder per model version (config.json + weights, ~1.1 GB), uploaded by
# infra/upload_model.sh. The service downloads its version at startup.
resource "google_storage_bucket" "models" {
  name                        = "aussie-ecoguard-models-${local.gcp_project_number}"
  location                    = upper(var.gcp_region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  # Like the media bucket: refuse to delete a bucket that still holds models.
  force_destroy = false

  depends_on = [google_project_service.apis]
}

# Read access on this bucket only, not the whole project.
resource "google_storage_bucket_iam_member" "models_reader" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${local.run_service_account}"
}

# ---------- Cloud Run service ----------

resource "google_cloud_run_v2_service" "inference" {
  name     = "aussie-ecoguard-inference"
  location = var.gcp_region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account       = local.run_service_account
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    # A cold start (~1 min: download + load the models) plus the longest video
    # accepted (3 minutes = 18 frames, a few seconds each). The tagging Lambda
    # waits the same time (INFER_TIMEOUT in tagging.tf).
    timeout = "300s"
    # One inference at a time per instance; a second request would only
    # compete for the same CPUs.
    max_instance_request_concurrency = 1

    # Scales to zero when idle; at most 2 instances caps the cost (and matches
    # the tagging queue's maximum_concurrency 2).
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = local.inference_image

      # The models need about 3 GB at peak, plus ~1.1 GB while the downloaded
      # files sit in /tmp (memory on Cloud Run), so 8Gi. 4 CPUs: inference
      # takes half as long as with 2, and as memory is billed per second too,
      # an image costs slightly less.
      resources {
        limits = {
          cpu    = "4"
          memory = "8Gi"
        }
        cpu_idle          = true # CPU only while handling a request
        startup_cpu_boost = true # extra CPU while starting and loading the models
      }

      # The service downloads this version folder at startup (main.py), so
      # every revision keeps the model it started with.
      env {
        name  = "MODEL_GCS"
        value = "gs://${google_storage_bucket.models.name}/${var.model_version}"
      }
      # Make torch use the 4 CPUs it has rather than the host's core count.
      env {
        name  = "OMP_NUM_THREADS"
        value = "4"
      }

      ports {
        container_port = 8080
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_storage_bucket_iam_member.models_reader,
  ]
}

# Public: Cloud Run's built-in IAM check cannot validate Cognito tokens, so
# (per the architecture) requests will be checked in code instead.
# TODO(auth): verify Google ID tokens (AWS) and Cognito JWTs (browser) in main.py.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.inference.name
  location = google_cloud_run_v2_service.inference.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
