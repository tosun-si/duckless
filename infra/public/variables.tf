variable "project_id" {
  type    = string
  default = "duckless-public"
}

variable "registry_location" {
  description = "Artifact Registry location; multi-region so the image is close to most job regions."
  type        = string
  default     = "europe"
}

variable "github_repository" {
  description = "owner/repo allowed to publish (WIF attribute condition). Update when the repo moves."
  type        = string
  default     = "tosun-si/duckless"
}
