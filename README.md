# SyncBase infrastructure

이 저장소는 애플리케이션 소스가 아니라 실행 조립만 소유한다.

- `compose.yml`: 환경 공통 PostgreSQL/pgvector, migrate, web, worker, MCP 계약
- `environments/local/`: 소스 build와 loopback 포트를 추가하는 local override
- `environments/prod/`: GHCR image와 AWS EC2 운영 기본값을 추가하는 prod override
- `docker/go.Dockerfile`: superproject의 Go workspace를 빌드하는 공통 이미지
- `docker-bake.hcl`: web, worker, migrate, MCP 이미지 빌드 행렬
- `deploy/`: 보호된 환경 파일 생성, EC2 release 전송, readiness/rollback
- `postgres/`: 최소 권한 역할 및 GRANT 검증
- `acceptance/`: P0, 브라우저, DB 장애·복구, OpenSQL 장애 시연
- `qualification/`: 실제 OpenSQL Gate 증거 수집

모든 명령은 superproject 루트에서 실행한다.

## Local

```sh
cp infra/environments/local/.env.example infra/environments/local/.env
vector-embedding/ops/model/fetch-e5-small.sh infra/build/models/multilingual-e5-small
vector-embedding/ops/model/fetch-onnxruntime.sh infra/build/runtime linux-amd64
docker compose \
  --env-file infra/environments/local/.env \
  -f infra/compose.yml \
  -f infra/environments/local/compose.yml \
  up --build
```

기존 `syncbase` Compose project 이름과 volume 이름을 유지하므로 환경 분리 전의 local
PostgreSQL 및 원문 volume을 그대로 사용한다.

## Production

Production override는 로컬 build를 포함하지 않으며 네 개의 immutable image가 반드시
필요하다. GitHub Actions는 `sha-<commit>` 태그로 GHCR에 push한 후 EC2의
`$HOME/syncbase/releases/<tag>`로 release bundle을 전송한다. readiness 실패 시
`current`가 가리키던 직전 release image로 자동 복구한다.

설정과 GitHub Secrets는 `environments/prod/README.md`를 따른다.

## 검증

```sh
infra/quality/check-environments.sh
docker buildx bake -f infra/docker-bake.hcl --print
```

`docker-bake.hcl`은 Terraform이 아니라 Docker Buildx가 읽는 HCL이다.
