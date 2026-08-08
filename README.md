# SyncBase infrastructure

이 저장소는 애플리케이션 소스가 아니라 실행 조립만 소유한다.

- `compose.yml`: PostgreSQL/pgvector, 역할 부트스트랩, migrate, web, worker, MCP
- `docker/go.Dockerfile`: superproject의 Go workspace를 빌드하는 공통 이미지
- `docker-bake.hcl`: web, worker, migrate, MCP 이미지 빌드 행렬
- `postgres/`: 최소 권한 역할 및 GRANT 검증
- `acceptance/`: P0, 브라우저, DB 장애·복구, OpenSQL 장애 시연
- `qualification/`: 실제 OpenSQL Gate 증거 수집

superproject 루트에서 실행한다.

```sh
cp infra/.env.example infra/.env
vector-embedding/ops/model/fetch-e5-small.sh infra/build/models/multilingual-e5-small
vector-embedding/ops/model/fetch-onnxruntime.sh infra/build/runtime linux-amd64
docker buildx bake -f infra/docker-bake.hcl --print
docker compose --env-file infra/.env -f infra/compose.yml up --build
```

`docker-bake.hcl`은 Docker Buildx가 읽는 HCL이며 이미지별 빌드 인수를 한 곳에서 관리한다.
