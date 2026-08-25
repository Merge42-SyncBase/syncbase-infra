# Round-1 security operation status

The sanitized record in `20260825-rotation-status.json` documents the security operations that were
explicitly authorized for this rescue task. It contains results and residual blockers, never secret
values or live infrastructure identifiers.

The database owner, web, worker, and MCP credentials were rotated in one transaction; the new values
were accepted, the old values were rejected, and existing runtime-role sessions were terminated at
rotation time. The administrator password and MCP token were also rotated. Current local checks show
that the protected administrator plaintext matches the configured bcrypt hash and that the MCP token
payload matches both configured digests. A loopback MCP smoke accepted the rotated token and rejected
missing and invalid tokens with HTTP 401.

The obsolete untracked `bootstrap-shared-env.sh` file, which contained superseded credential
material, was deleted. Because it was untracked, it is not recoverable from Git. The replacement
protected files are untracked, Git-ignored, and mode 0600.

The development-worktree release-secret gate passed across all five local repositories: zero
findings in all local Git refs and zero findings in the tracked plus untracked-nonignored source
candidate. A separate fully redacted disk diagnostic found only the expected protected runtime
material and no unexpected path. This must be rerun against the clean frozen release; it does not
claim coverage of remote refs that have not been fetched locally.

The `GH_MODULES_TOKEN` repository-secret bindings were removed from `syncbase-embedding`,
`syncbase-was`, and `syncbase-mcp`. This contains immediate repository use, but GitHub's available API
did not identify or prove revocation of the underlying personal access token. Its owner must revoke it
through GitHub settings. Until then, full revocation is **not proved**. The affected private
cross-repository workflows are intentionally expected to fail until a new least-privilege credential
is installed or publication removes the dependency.

GitHub Pages for `SyncBase_randing` is no longer configured and its published URL returns HTTP 404.
The repository itself remains public, as required by the authorization boundary.

This is a time-bounded engineering audit snapshot, not a guarantee that the system is secure. A
qualified security professional should review the final frozen release, credentials, organization
settings, and deployment before production use.
