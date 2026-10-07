output "work_bucket" {
  description = "DUCKLESS_BUCKET"
  value       = google_storage_bucket.work.name
}

output "runner_service_account" {
  description = "DUCKLESS_SA"
  value       = google_service_account.runner.email
}

output "runner_image" {
  description = "DUCKLESS_IMAGE: the runner image, pulled through the remote repository."
  value       = local.runner_image
}

output "ducklake_instance" {
  description = "DUCKLESS_DUCKLAKE_INSTANCE: connection name of the catalog (empty without DuckLake)."
  value       = var.ducklake ? google_sql_database_instance.catalog[0].connection_name : ""
}

output "ducklake_data_path" {
  description = "DUCKLESS_DUCKLAKE_DATA_PATH: where DuckLake writes the tables' Parquet files."
  value       = var.ducklake ? local.ducklake_data : ""
}

output "network" {
  description = "VPC network of the jobs (and of the catalog's private IP)."
  value       = var.network
}

output "envrc" {
  description = "Lines to paste in .envrc."
  value       = <<-EOT
    export DUCKLESS_PROJECT=${var.project_id}
    export DUCKLESS_REGION=${var.region}
    export DUCKLESS_BUCKET=${google_storage_bucket.work.name}
    export DUCKLESS_SA=${google_service_account.runner.email}
    export DUCKLESS_IMAGE=${local.runner_image}
    export DUCKLESS_NETWORK=${var.network}
  %{~if var.ducklake}
    export DUCKLESS_DUCKLAKE_INSTANCE=${google_sql_database_instance.catalog[0].connection_name}
    export DUCKLESS_DUCKLAKE_DATA_PATH=${local.ducklake_data}
  %{~endif}
  EOT
}
