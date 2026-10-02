output "work_bucket" {
  description = "DUCKLESS_BUCKET"
  value       = google_storage_bucket.work.name
}

output "runner_service_account" {
  description = "DUCKLESS_SA"
  value       = google_service_account.runner.email
}

output "envrc" {
  description = "Lines to paste in .envrc."
  value       = <<-EOT
    export DUCKLESS_PROJECT=${var.project_id}
    export DUCKLESS_REGION=${var.region}
    export DUCKLESS_BUCKET=${google_storage_bucket.work.name}
    export DUCKLESS_SA=${google_service_account.runner.email}
  EOT
}
