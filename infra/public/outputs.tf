output "runner_image" {
  description = "Public runner image, without tag."
  value       = "${var.registry_location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.runner.repository_id}/runner"
}

output "github_variables" {
  description = "GitHub Actions repository variables (identifiers, not secrets)."
  value = {
    GCP_WIF_PROVIDER = google_iam_workload_identity_pool_provider.github.name
    GCP_CI_SA        = google_service_account.ci.email
  }
}
