# Five-repository verification

Round-1의 canonical sibling layout은 다음과 같다.

```text
<workspace>/
├── SyncBase-FE/
├── syncbase-embedding/
├── syncbase-was/
├── syncbase-infra/
└── syncbase-mcp/
```

release source baseline은 clean worktree와 다섯 full SHA를 요구한다.

```sh
python3 syncbase-infra/quality/verify_repositories.py \
  --workspace-root . \
  --run-id "$run_id" \
  --output syncbase-infra/evidence/round1/lane-c/$run_id/01-repository-checks/result.json
```

dirty checkout의 개발 도구 점검에만 `--allow-dirty`를 사용할 수 있다. 그 결과를 release
source baseline으로 제출하지 않는다.

```sh
bash syncbase-infra/quality/check-boundaries.sh
bash syncbase-infra/quality/check-environments.sh
bash syncbase-infra/quality/run-lane-c-tooling-tests.sh
```

`run-p0.sh`도 같은 layout을 사용하며 기본값으로 clean repository gate를 먼저 실행한다.
개발 중에만 `SYNCBASE_ALLOW_DIRTY_REPOSITORIES=true`로 우회할 수 있다. 최종 evidence에서는
이 우회를 사용하지 않는다.
