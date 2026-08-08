# AWS EC2 production contract

## Topology

GitHub Actions가 네 개의 immutable image를 GHCR에 push하고, 승인된 `production`
Environment에서 SSH를 사용해 release bundle을 EC2에 전송한다.

```text
GitHub production Environment
  -> GHCR: web / worker / migrate / mcp
  -> SSH: compose, PostgreSQL role scripts, pinned E5/ONNX artifacts, protected env
  -> EC2: docker compose pull + migrate + readiness
```

EC2에서 source checkout이나 image build는 수행하지 않는다. PostgreSQL과 원문은
`syncbase-prod` Docker named volume에 유지되어 release 디렉터리 교체와 분리된다.

## EC2 prerequisites

- Linux x86_64 EC2 instance
- Docker Engine과 `docker compose` v2
- `curl`과 `tar`
- 배포 사용자가 Docker daemon을 사용할 수 있어야 함
- GitHub runner에서 EC2 SSH endpoint에 도달할 수 있어야 함
- 모델, runtime, image 및 원문을 수용할 충분한 디스크

GitHub-hosted runner의 주소 전체에 SSH를 공개하는 구성은 권장하지 않는다. 고정 egress를
가진 self-hosted runner/VPN을 사용하거나, 후속 단계에서 SSH transport를 AWS Systems
Manager로 교체한다. `PROD_EC2_KNOWN_HOSTS`에는 별도 채널로 fingerprint를 확인한 host
key만 저장한다.

## GitHub Environment and secrets

Repository에 `production` Environment를 만들고 required reviewer를 설정한다. 다음
Secrets를 Environment 범위에 등록한다.

| Secret | 의미 |
| --- | --- |
| `SUBMODULE_TOKEN` | private component submodule을 읽을 수 있는 fine-grained PAT 또는 GitHub App token |
| `PROD_EC2_HOST` | EC2 public/private DNS 또는 IPv4 |
| `PROD_EC2_USER` | Docker 권한이 있는 SSH 사용자 |
| `PROD_EC2_SSH_PRIVATE_KEY` | 해당 사용자용 private key |
| `PROD_EC2_KNOWN_HOSTS` | 검증된 EC2 SSH host key line |
| `PROD_POSTGRES_OWNER_PASSWORD` | PostgreSQL owner 비밀번호 |
| `PROD_WEB_DB_PASSWORD` | web 전용 DB role 비밀번호 |
| `PROD_WORKER_DB_PASSWORD` | worker 전용 DB role 비밀번호 |
| `PROD_MCP_DB_PASSWORD` | MCP 전용 DB role 비밀번호 |
| `PROD_ADMIN_USERNAME` | 단일 관리자 ID |
| `PROD_ADMIN_PASSWORD_BCRYPT` | bcrypt 관리자 비밀번호 hash |
| `PROD_MCP_TOKEN` | Web과 MCP 사이의 bearer token 원문 |
| `PROD_MCP_ALLOWED_HOSTS` | public hostname과 `mcp`의 comma-separated 목록 |
| `PROD_MCP_ALLOWED_ORIGINS` | 허용할 HTTPS origin 목록 |
| `PROD_PUBLIC_BASE_URL` | 원문 URL에 사용되는 public HTTPS base URL |

비밀번호와 token에는 줄바꿈 또는 single quote를 사용하지 않는다. DB 비밀번호는 서로
다르게 생성한다. bcrypt hash의 `$`는 renderer가 literal dotenv 값으로 보존한다.

## Network exposure

기본 설정은 Web `127.0.0.1:8080`, MCP `127.0.0.1:8081`이다. 다음 중 하나로 HTTPS를
종료한다.

1. EC2 host의 Caddy/Nginx가 443을 받고 loopback 포트로 proxy
2. ALB가 HTTPS를 종료하고, `SYNCBASE_*_BIND_ADDRESS=0.0.0.0`로 변경한 뒤 EC2
   security group ingress source를 ALB security group으로만 제한

8080/8081을 `0.0.0.0/0`에 직접 공개하지 않는다. MCP를 외부에 제공하지 않는다면
8081은 계속 loopback으로 유지한다.

## Deployment

`.github/workflows/deploy-prod.yml`의 `workflow_dispatch`를 실행하고 `production`
Environment 승인을 완료한다. Workflow는 다음을 자동으로 검증한다.

- component submodule checkout
- SHA-tagged GHCR image build/push
- pinned model/runtime checksum
- protected `.env`와 MCP token 생성
- strict host-key SSH 전송
- Compose config, pull, migrate 및 readiness
- 실패 시 직전 release 복구

실제 AWS 주소와 credential은 어느 파일에도 commit하지 않는다.
