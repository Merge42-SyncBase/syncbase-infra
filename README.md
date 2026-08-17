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

## Quick Start

`syncbase-infra`만 clone하면 됩니다. `was`/`mcp`/`embedding`/`frontend` 소스는 필요 없습니다 — 전부 GHCR에 이미 public image로 올라가 있습니다. 필요한 건 자신의 OpenSQL(PostgreSQL 호환) 엔드포인트뿐입니다.

```sh
git clone https://github.com/Merge42-SyncBase/syncbase-infra.git
cd syncbase-infra
cp environments/prod/.env.example environments/prod/.env
```

`environments/prod/.env`를 열어 값을 채웁니다:
- `SYNCBASE_DB_HOST`/`PORT`/`NAME`/`SSLMODE`: 자신의 OpenSQL 접속 정보
- `SYNCBASE_POSTGRES_OWNER_PASSWORD`: OpenSQL에 이미 있는 owner(superuser에 준하는) 계정의 비밀번호
- `SYNCBASE_*_DB_PASSWORD`: `roles` 컨테이너가 자동으로 만들 3개 role(`syncbase_web`/`worker`/`mcp`)의 비밀번호(직접 정함)
- `SYNCBASE_ADMIN_PASSWORD_BCRYPT`, `SYNCBASE_MCP_TOKEN_SHA256` 등 나머지 값

그 다음:

```sh
docker compose \
  --env-file environments/prod/.env \
  -f compose.yml \
  -f environments/prod/compose.yml \
  up -d
```