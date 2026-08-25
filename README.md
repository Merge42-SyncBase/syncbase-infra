# SyncBase infrastructure

이 저장소는 애플리케이션 소스가 아니라 실행 조립만 소유한다.

## Round 1 source index

`syncbase-infra`는 심사위원에게 제출할 대표 저장소입니다. 아래 URL과
`v0.1.0-round1`은 합의된 release target이며, 이 표 자체는 저장소 공개 여부,
태그 존재 여부 또는 clean-room PASS를 증명하지 않습니다. 최종 값은
[`RELEASE_MANIFEST.template.json`](RELEASE_MANIFEST.template.json)을 복제한 release
manifest에 기록하고 별도로 검증해야 합니다.

| Component | Repository | Local checkout | Responsibility |
| --- | --- | --- | --- |
| Infrastructure | <https://github.com/Merge42-SyncBase/syncbase-infra> | `syncbase-infra` | Compose, environments, qualification, release evidence |
| Web application server | <https://github.com/Merge42-SyncBase/syncbase-was> | `syncbase-was` | API, worker, migrations, consistency rules |
| MCP server | <https://github.com/Merge42-SyncBase/syncbase-mcp> | `syncbase-mcp` | Authenticated `search_documents` MCP transport |
| Embedding library | <https://github.com/Merge42-SyncBase/syncbase-embedding> | `syncbase-embedding` | Local E5/tokenizer/ONNX Runtime adapter |
| Frontend | <https://github.com/Merge42-SyncBase/SyncBase-FE> | `SyncBase-FE` | React operator console and page-source viewer |

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

공개 image와 manifest digest가 release gate에서 검증된 뒤에는
`syncbase-infra`만 clone하여 실행할 수 있습니다. 검증 전 개발 환경에서는 상위
폴더에 다섯 저장소를 sibling으로 checkout하고 local build overlay를 사용하십시오.
이 문서는 검증되지 않은 GHCR 공개 상태를 주장하지 않습니다.

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

## Release security preflight

Round-1 공개 전 security gate는 다음 세 결과를 분리해 생성합니다.

1. `public-source/result.json`: 다섯 non-shallow clone의 모든 local Git ref history와
   tracked 및 untracked-nonignored 파일로 만든 source archive candidate를
   검사합니다.
   이 결과는 반드시 `PASS`여야 합니다.
2. `protected-material/result.json`: 로컬 production `.env`/secret이
   [`security/protected-material-policy.json`](security/protected-material-policy.json)의
   정확한 경로에 regular file로 존재하고, repository-local `.gitignore`에
   포함되며, Git에 tracked되지 않고, source archive candidate에서 제외되며,
   mode `0600`인지 검증합니다. 정상 상태는 `PRESENT_PROTECTED`입니다.
3. `full-disk-diagnostic/result.json`: ignored/untracked를 포함한 전체 디스크를
   100% redaction으로 추가 검사합니다. 정확한 protected path 내 finding은
   `NON_PASS_EXPECTED_PROTECTED`로 기록하되 release source 실패로 취급하지
   않지만, 그 밖의 finding은 즉시 release gate를 실패시킵니다.

결과 폴더는 self-scan을 피하도록 workspace 밖의 새 절대 경로여야
합니다.

```sh
report_parent="$(mktemp -d /tmp/syncbase-secret-scan.XXXXXX)"
security/run-release-secret-scan.sh .. "$report_parent/result"
```

실제 credential pattern은 allowlist하지 않습니다. Protected file은 scan rule을
우회하는 것이 아니라, 공개 source set에서 구조적으로 제외한 뒤 경로·mode·
ignore·archive membership을 별도로 검증합니다. Raw finding의 secret/match 값은
100% redact하며 요약에는 rule, 경로, line, 분류만 남깁니다. Credential
rotation/revocation 증거는 이 source gate와 별도로 관리해야 합니다. 최종 RC 정보는
`RELEASE_MANIFEST.template.json`의 복사본에 채우며, template 자체는 release
evidence가 아닙니다.

## License

SyncBase infrastructure의 자체 소스와 구성은
[Apache License 2.0](LICENSE) (`Apache-2.0`)으로 배포합니다. 조립 과정에서
사용하는 container, database extension, 모델 및 runtime은 각자의
라이선스를 따르며 세부 출처는
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 기록합니다.
