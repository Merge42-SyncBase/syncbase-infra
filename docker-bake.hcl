variable "REGISTRY" {
  default = "syncbase"
}

variable "TAG" {
  default = "dev"
}

variable "IMAGE_PREFIX" {
  default = ""
}

group "default" {
  targets = ["web", "api", "worker", "migrate", "mcp"]
}

target "attested-image" {
  attest = [
    "type=provenance,mode=max",
    "type=sbom"
  ]
}

target "go-binary" {
  inherits = ["attested-image"]
  context    = "cwd://"
  dockerfile = "infra/docker/go.Dockerfile"
}

target "api" {
  inherits = ["go-binary"]
  args = {
    TARGET_PACKAGE = "./was/cmd/web"
  }
  tags = ["${REGISTRY}/${IMAGE_PREFIX}api:${TAG}"]
}

target "web" {
  inherits   = ["attested-image"]
  context    = "cwd://frontend"
  dockerfile = "Dockerfile"
  tags       = ["${REGISTRY}/${IMAGE_PREFIX}web:${TAG}"]
}

target "worker" {
  inherits = ["go-binary"]
  args = {
    TARGET_PACKAGE = "./was/cmd/worker"
  }
  tags = ["${REGISTRY}/${IMAGE_PREFIX}worker:${TAG}"]
}

target "migrate" {
  inherits = ["go-binary"]
  args = {
    TARGET_PACKAGE = "./was/cmd/migrate"
  }
  tags = ["${REGISTRY}/${IMAGE_PREFIX}migrate:${TAG}"]
}

target "mcp" {
  inherits = ["go-binary"]
  args = {
    TARGET_PACKAGE = "./mcp/cmd/mcp"
  }
  tags = ["${REGISTRY}/${IMAGE_PREFIX}mcp:${TAG}"]
}
