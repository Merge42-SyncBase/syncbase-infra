# syntax=docker/dockerfile:1.7
ARG GO_IMAGE=golang:1.26.5-bookworm@sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241

FROM ${GO_IMAGE} AS build
ARG TARGET_PACKAGE
ARG BINARY=syncbase
WORKDIR /src

COPY go.work ./
COPY vector-embedding/go.mod vector-embedding/go.sum ./vector-embedding/
COPY was/go.mod was/go.sum ./was/
COPY mcp/go.mod mcp/go.sum ./mcp/
COPY was/qualification/pdf-gate/go/go.mod was/qualification/pdf-gate/go/go.sum ./was/qualification/pdf-gate/go/
RUN --mount=type=cache,target=/go/pkg/mod go mod download all

COPY vector-embedding ./vector-embedding
COPY was ./was
COPY mcp ./mcp
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    test -n "${TARGET_PACKAGE}" \
    && CGO_ENABLED=1 go build -trimpath -ldflags='-s -w' -o "/out/${BINARY}" "${TARGET_PACKAGE}"

FROM ${RUNTIME_IMAGE}
ARG BINARY=syncbase
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 syncbase \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin syncbase \
    && mkdir -p /app /data/originals \
    && chown -R syncbase:syncbase /app /data
COPY --from=build "/out/${BINARY}" /app/syncbase
USER 10001:10001
WORKDIR /app
ENTRYPOINT ["/app/syncbase"]
