terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.18" # database_roles on google_sql_user (DuckLake); checked in CI
    }
  }
}
