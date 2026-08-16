# SyncBase infrastructure

이 저장소는 애플리케이션 소스가 아니라 실행 조립만 소유한다.

- `compose.yml`: 환경 공통 migrate, React `web`, Go `api`, worker, MCP 계약 (PostgreSQL은 local에만 있음)
- `environments/local/`: 컨테이너 PostgreSQL/pgvector, 소스 build, loopback 포트를 추가하는 local override
- `environments/prod/`: GHCR image와 외부 OpenSQL 접속 정보를 추가하는 prod override (PostgreSQL 컨테이너 없음)
- `deploy/`: 보호된 환경 파일 생성, EC2 release 전송, readiness/rollback
- `postgres/`: 최소 권한 역할 및 GRANT 검증
- `acceptance/`: P0, 브라우저, DB 장애·복구, OpenSQL 장애 시연
- `qualification/`: 실제 OpenSQL Gate 증거 수집

`was`, `mcp`, `SyncBase-FE`(→ `frontend`), `syncbase-embedding`(→ `vector-embedding`)은 각자
자기 Dockerfile과 CI를 소유하며 GHCR에 독립적으로 image를 push한다. 이 저장소는 그 image를
참조하기만 하면 되고(prod), local override만 소스에서 직접 build한다. 모든 명령은 이 네
저장소가 sibling으로 나란히 checkout된 상위 폴더에서 실행한다.

## Quick Start (일반 사용자 — 소스 checkout 불필요)

`syncbase-infra`만 clone하면 됩니다. `was`/`mcp`/`embedding`/`frontend` 소스는 필요 없습니다 —
전부 GHCR에 이미 public image로 올라가 있습니다. 필요한 건 자신의 OpenSQL(PostgreSQL 호환)
엔드포인트뿐입니다.

```sh
git clone https://github.com/Merge42-SyncBase/syncbase-infra.git
cd syncbase-infra
cp environments/prod/.env.example environments/prod/.env
```

`environments/prod/.env`를 열어 값을 채웁니다:
- `SYNCBASE_DB_HOST`/`PORT`/`NAME`/`SSLMODE`: 자신의 OpenSQL 접속 정보
- `SYNCBASE_*_DB_PASSWORD`, `SYNCBASE_POSTGRES_OWNER_PASSWORD`: 아래 role 생성 시 쓸 비밀번호(직접 정함)
- `SYNCBASE_ADMIN_PASSWORD_BCRYPT`, `SYNCBASE_MCP_TOKEN_SHA256` 등 나머지 값

역할/권한은 compose가 자동으로 만들어주지 않습니다(외부 DB에 자동으로 role을 만드는 건
위험해서 의도적으로 뺐습니다). 최초 1회만 수동으로 실행합니다:

```sh
PGHOST=<your-opensql-host> PGPORT=5432 PGUSER=<superuser> PGPASSWORD=<superuser-pw> \
  psql -d syncbase -f postgres/runtime-roles.sql
PGHOST=<your-opensql-host> PGPORT=5432 PGUSER=<superuser> PGPASSWORD=<superuser-pw> \
  psql -d syncbase -f postgres/runtime-grants.sql
```

그 다음:

```sh
docker compose \
  --env-file environments/prod/.env \
  -f compose.yml \
  -f environments/prod/compose.yml \
  pull
docker compose \
  --env-file environments/prod/.env \
  -f compose.yml \
  -f environments/prod/compose.yml \
  up -d
```

`http://127.0.0.1:8080`에서 운영 콘솔에 접속합니다. `worker`/`mcp`는 E5 임베딩 모델과 ONNX
Runtime 파일이 필요합니다 — 아래 "Local" 섹션의 모델 다운로드 스크립트를 참고하세요
(이 경우 `syncbase-embedding`만 별도로 clone하면 됩니다, 다른 소스는 여전히 필요 없습니다).

## Local (SyncBase 개발자용 — 소스에서 직접 빌드)

```sh
cp infra/environments/local/.env.example infra/environments/local/.env
# SYNCBASE_GITHUB_TOKEN_FILE에 지정한 파일에 Contents:read 권한 GitHub 토큰을 저장한다
# (syncbase-embedding이 private module이라 was/mcp를 소스 build할 때 필요하다).
gh auth token > ~/.syncbase-github-token
vector-embedding/ops/model/fetch-e5-small.sh infra/build/models/multilingual-e5-small
# Docker daemon architecture에 맞춰 linux-amd64 또는 linux-arm64를 선택한다.
vector-embedding/ops/model/fetch-onnxruntime.sh infra/build/runtime linux-arm64
docker compose \
  --env-file infra/environments/local/.env \
  -f infra/compose.yml \
  -f infra/environments/local/compose.yml \
  up --build
```

기존 `syncbase` Compose project 이름과 volume 이름을 유지하므로 환경 분리 전의 local
PostgreSQL 및 원문 volume을 그대로 사용한다.

## Production

Production override는 로컬 build를 포함하지 않으며 다섯 개의 immutable image가 반드시
필요하다. GitHub Actions는 `sha-<commit>` 태그로 GHCR에 push한 후 EC2의
`$HOME/syncbase/releases/<tag>`로 release bundle을 전송한다. readiness 실패 시
`current`가 가리키던 직전 release image로 자동 복구한다.

설정과 GitHub Secrets는 `environments/prod/README.md`를 따른다.

## 검증

```sh
infra/quality/check-environments.sh
```

`web`은 정적 SPA 및 same-origin `/api/` proxy만 제공한다. PostgreSQL, MCP token,
원문 저장소는 `api`/worker/MCP에만 전달된다.
