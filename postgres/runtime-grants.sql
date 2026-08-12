REVOKE ALL ON SCHEMA syncbase FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA syncbase FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA syncbase FROM PUBLIC;
REVOKE ALL ON SCHEMA syncbase FROM syncbase_web, syncbase_worker, syncbase_mcp;
REVOKE ALL ON ALL TABLES IN SCHEMA syncbase FROM syncbase_web, syncbase_worker, syncbase_mcp;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA syncbase FROM syncbase_web, syncbase_worker, syncbase_mcp;

GRANT USAGE ON SCHEMA syncbase TO syncbase_web, syncbase_worker, syncbase_mcp;

GRANT SELECT ON TABLE
    syncbase.processing_profile,
    syncbase.document,
    syncbase.document_version,
    syncbase.processing_run,
    syncbase.queue_control,
    syncbase.upload_request,
    syncbase.browser_session
TO syncbase_web;
GRANT INSERT, UPDATE ON TABLE
    syncbase.document,
    syncbase.document_version,
    syncbase.processing_run,
    syncbase.queue_control,
    syncbase.upload_request
TO syncbase_web;
GRANT INSERT, DELETE ON TABLE syncbase.browser_session TO syncbase_web;
GRANT INSERT ON TABLE syncbase.change_log TO syncbase_web;
GRANT USAGE ON SEQUENCE syncbase.change_log_sequence_id_seq TO syncbase_web;

GRANT SELECT ON TABLE
    syncbase.processing_profile,
    syncbase.document,
    syncbase.document_version,
    syncbase.processing_run,
    syncbase.queue_control,
    syncbase.processing_checkpoint,
    syncbase.processing_step_attempt,
    syncbase.search_chunk
TO syncbase_worker;
GRANT UPDATE ON TABLE
    syncbase.queue_control,
    syncbase.document,
    syncbase.document_version,
    syncbase.processing_run
TO syncbase_worker;
GRANT INSERT, UPDATE ON TABLE
    syncbase.processing_checkpoint,
    syncbase.processing_step_attempt
TO syncbase_worker;
GRANT INSERT, DELETE ON TABLE syncbase.search_chunk TO syncbase_worker;
GRANT INSERT ON TABLE syncbase.change_log TO syncbase_worker;
GRANT USAGE ON SEQUENCE syncbase.change_log_sequence_id_seq TO syncbase_worker;

GRANT SELECT ON TABLE
    syncbase.processing_profile,
    syncbase.document,
    syncbase.document_version,
    syncbase.search_chunk
TO syncbase_mcp;

ALTER DEFAULT PRIVILEGES FOR ROLE syncbase IN SCHEMA syncbase REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE syncbase IN SCHEMA syncbase REVOKE ALL ON SEQUENCES FROM PUBLIC;

DO $$
BEGIN
    IF NOT has_table_privilege('syncbase_web', 'syncbase.document', 'SELECT')
       OR NOT has_table_privilege('syncbase_web', 'syncbase.document', 'INSERT')
       OR NOT has_table_privilege('syncbase_web', 'syncbase.document', 'UPDATE')
       OR has_table_privilege('syncbase_web', 'syncbase.document', 'DELETE') THEN
        RAISE EXCEPTION 'syncbase_web privileges do not match the runtime contract';
    END IF;
    IF NOT has_table_privilege('syncbase_web', 'syncbase.browser_session', 'SELECT')
       OR NOT has_table_privilege('syncbase_web', 'syncbase.browser_session', 'INSERT')
       OR NOT has_table_privilege('syncbase_web', 'syncbase.browser_session', 'DELETE')
       OR has_table_privilege('syncbase_web', 'syncbase.browser_session', 'UPDATE') THEN
        RAISE EXCEPTION 'syncbase_web browser session privileges do not match the runtime contract';
    END IF;
    IF NOT has_table_privilege('syncbase_worker', 'syncbase.search_chunk', 'SELECT')
       OR NOT has_table_privilege('syncbase_worker', 'syncbase.search_chunk', 'INSERT')
       OR NOT has_table_privilege('syncbase_worker', 'syncbase.search_chunk', 'DELETE')
       OR has_schema_privilege('syncbase_worker', 'syncbase', 'CREATE') THEN
        RAISE EXCEPTION 'syncbase_worker privileges do not match the runtime contract';
    END IF;
    IF NOT has_table_privilege('syncbase_mcp', 'syncbase.search_chunk', 'SELECT')
       OR has_table_privilege('syncbase_mcp', 'syncbase.search_chunk', 'INSERT')
       OR has_table_privilege('syncbase_mcp', 'syncbase.search_chunk', 'UPDATE')
       OR has_table_privilege('syncbase_mcp', 'syncbase.search_chunk', 'DELETE')
       OR has_schema_privilege('syncbase_mcp', 'syncbase', 'CREATE') THEN
        RAISE EXCEPTION 'syncbase_mcp privileges do not match the runtime contract';
    END IF;
END
$$;
