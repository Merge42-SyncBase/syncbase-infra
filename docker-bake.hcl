variable "REGISTRY" {
  default = "syncbase"
}

variable "TAG" {
  default = "dev"
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
  tags = ["${REGISTRY}/web:${TAG}"]
}

target "worker" {
  inherits = ["go-binary"]
  args = {
    TARGET_PACKAGE = "./was/cmd/worker"
  }
  tags = ["${REGISTRY}/worker:${TAG}"]
}

target "migrate" {
  inherits = ["go-binary"]
  args = {
    TARGET_PACKAGE = "./was/cmd/migrate"
  }
  tags = ["${REGISTRY}/migrate:${TAG}"]
}

target "mcp" {
  inherits = ["go-binary"]
  args = {
    TARGET_PACKAGE = "./mcp/cmd/mcp"
  }
  tags = ["${REGISTRY}/mcp:${TAG}"]
}
