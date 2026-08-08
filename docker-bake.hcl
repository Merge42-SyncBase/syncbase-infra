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
  targets = ["web", "worker", "migrate", "mcp"]
}

target "go-binary" {
  context    = "cwd://"
  dockerfile = "infra/docker/go.Dockerfile"
}

target "web" {
  inherits = ["go-binary"]
  args = {
    TARGET_PACKAGE = "./was/cmd/web"
  }
  tags = ["${REGISTRY}/${IMAGE_PREFIX}web:${TAG}"]
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
