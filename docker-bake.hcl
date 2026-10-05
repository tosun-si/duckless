# Runner image. Always invoke with the explicit file: docker buildx bake -f docker-bake.hcl
#
#   local:   docker buildx bake -f docker-bake.hcl --set '*.output=type=docker'
#   CI:      IMAGE=ghcr.io/tosun-si/duckless-runner \
#            CACHE_IMAGE=ghcr.io/tosun-si/duckless-runner-cache \
#            TAGS=edge,sha-abc1234 docker buildx bake -f docker-bake.hcl --push

variable "IMAGE" {
  default = "duckless-runner"
}

# Separate (private) package holding the layer cache, so the public image only carries
# release tags. Empty = no registry cache (local builds, fork PRs).
variable "CACHE_IMAGE" {
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
  tags       = [for tag in split(",", TAGS) : "${IMAGE}:${tag}"]
  labels = {
    "org.opencontainers.image.source"      = "https://github.com/tosun-si/duckless"
    "org.opencontainers.image.description" = "DuckLess runner: DuckDB tuned for the VM, GCS via ADC"
    "org.opencontainers.image.licenses"    = "Apache-2.0"
  }
  # ignore-error: a missing cache (first build) falls back to a cold build.
  cache-from = CACHE_IMAGE == "" ? [] : ["type=registry,ref=${CACHE_IMAGE}:buildcache,ignore-error=true"]
  cache-to   = CACHE_IMAGE == "" ? [] : ["type=registry,ref=${CACHE_IMAGE}:buildcache,mode=max"]
}
