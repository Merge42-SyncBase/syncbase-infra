\getenv web_password SYNCBASE_WEB_DB_PASSWORD
\getenv worker_password SYNCBASE_WORKER_DB_PASSWORD
\getenv mcp_password SYNCBASE_MCP_DB_PASSWORD

SELECT format('CREATE ROLE syncbase_web LOGIN PASSWORD %L', :'web_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='syncbase_web')
\gexec

SELECT format('CREATE ROLE syncbase_worker LOGIN PASSWORD %L', :'worker_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='syncbase_worker')
\gexec

SELECT format('CREATE ROLE syncbase_mcp LOGIN PASSWORD %L', :'mcp_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='syncbase_mcp')
\gexec

ALTER ROLE syncbase_web PASSWORD :'web_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE syncbase_worker PASSWORD :'worker_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE syncbase_mcp PASSWORD :'mcp_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

SELECT format('REVOKE %I FROM %I', granted.rolname, member.rolname)
FROM pg_catalog.pg_auth_members membership
JOIN pg_catalog.pg_roles granted ON granted.oid=membership.roleid
JOIN pg_catalog.pg_roles member ON member.oid=membership.member
WHERE member.rolname IN ('syncbase_web','syncbase_worker','syncbase_mcp')
\gexec

REVOKE ALL ON DATABASE syncbase FROM PUBLIC;
REVOKE ALL ON DATABASE syncbase FROM syncbase_web, syncbase_worker, syncbase_mcp;
GRANT CONNECT ON DATABASE syncbase TO syncbase_web, syncbase_worker, syncbase_mcp;
