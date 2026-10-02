# Runner image. Always invoke with the explicit file: docker buildx bake -f docker-bake.hcl
#
#   local:   docker buildx bake -f docker-bake.hcl --set '*.output=type=docker'
#   CI:      REGISTRY=europe-docker.pkg.dev/duckless-public/duckless \
#            CACHE_REGISTRY=europe-docker.pkg.dev/duckless-public/ci-cache \
#            TAGS=edge,sha-abc1234 docker buildx bake -f docker-bake.hcl --push

variable "REGISTRY" {
  default = "duckless"
}

# Private repo holding the layer cache; empty = no registry cache (local builds, fork PRs).
variable "CACHE_REGISTRY" {
  default = ""
}

# Comma-separated image tags.
variable "TAGS" {
  default = "dev"
}

group "default" {
  targets = ["runner"]
}

target "runner" {
  context    = "runtime"
  dockerfile = "Dockerfile"
  platforms  = ["linux/amd64"]
  tags       = [for tag in split(",", TAGS) : "${REGISTRY}/runner:${tag}"]
  labels = {
    "org.opencontainers.image.source"      = "https://github.com/tosun-si/duckless"
    "org.opencontainers.image.description" = "DuckLess runner: DuckDB tuned for the VM, GCS via ADC"
    "org.opencontainers.image.licenses"    = "Apache-2.0"
  }
  # ignore-error: a missing cache (first build) falls back to a cold build.
  cache-from = CACHE_REGISTRY == "" ? [] : ["type=registry,ref=${CACHE_REGISTRY}/runner:buildcache,ignore-error=true"]
  cache-to   = CACHE_REGISTRY == "" ? [] : ["type=registry,ref=${CACHE_REGISTRY}/runner:buildcache,mode=max"]
}
