# DuckLake catalog: Cloud SQL Postgres, private IP only, IAM authentication.
# The network's private services access (range + peering with Google services) is set up by
# `duckless init`, outside Terraform: it is shared with any other Cloud SQL instance of the
# network, and the peering cannot be deleted right after the instance.
# Jobs reach it through the Cloud SQL Auth Proxy started by the runner (--auto-iam-authn):
# no password anywhere, the proxy refreshes the IAM token of long jobs.

locals {
  ducklake_services = var.ducklake ? toset(["sqladmin.googleapis.com", "servicenetworking.googleapis.com"]) : toset([])
  ducklake_runner_roles = var.ducklake ? toset([
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
  ]) : toset([])
  # Postgres name of an IAM service account user: its email without ".gserviceaccount.com".
  catalog_user  = trimsuffix(google_service_account.runner.email, ".gserviceaccount.com")
  catalog_db    = "ducklake"
  ducklake_data = "gcss://${google_storage_bucket.work.name}/lake/"
  network_id    = startswith(var.network, "projects/") ? var.network : "projects/${var.project_id}/global/networks/${var.network}"
}

resource "google_project_service" "ducklake" {
  for_each = local.ducklake_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_sql_database_instance" "catalog" {
  count = var.ducklake ? 1 : 0

  project             = var.project_id
  name                = "${var.name}-catalog"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = !var.force_destroy

  settings {
    edition           = "ENTERPRISE"
    tier              = var.catalog_tier
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 10
    user_labels       = local.labels

    ip_configuration {
      ipv4_enabled    = false
      private_network = local.network_id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    # Deleting a Cloud SQL instance deletes its automated backups: keep a final one.
    final_backup_config {
      enabled        = true
      retention_days = var.catalog_final_backup_days
    }

    # The catalog is the lake: without it the Parquet files on GCS are unreadable as tables.
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "02:00"
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 7
      }
    }
  }

  depends_on = [google_project_service.ducklake]
}

resource "google_sql_database" "catalog" {
  count = var.ducklake ? 1 : 0

  project  = var.project_id
  instance = google_sql_database_instance.catalog[0].name
  name     = local.catalog_db
}

# cloudsqlsuperuser on an instance that only holds this catalog: the runner owns the lake's
# data on GCS already, and grants on one database cannot be set from Terraform (the instance
# has no public IP, so no SQL session from Infrastructure Manager).
resource "google_sql_user" "runner" {
  count = var.ducklake ? 1 : 0

  project        = var.project_id
  instance       = google_sql_database_instance.catalog[0].name
  name           = local.catalog_user
  type           = "CLOUD_IAM_SERVICE_ACCOUNT"
  database_roles = ["cloudsqlsuperuser"]
  # The user owns the lake's catalog tables: Postgres refuses to drop it while they exist, and
  # Terraform deletes it alongside the database. It goes with the instance anyway.
  deletion_policy = "ABANDON"
}

resource "google_project_iam_member" "runner_catalog" {
  for_each = local.ducklake_runner_roles

  project = var.project_id
  role    = each.value
  member  = google_service_account.runner.member
}
