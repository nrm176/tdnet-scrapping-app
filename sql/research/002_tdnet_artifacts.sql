DO $$
BEGIN
    IF current_setting('server_version_num')::integer < 180000
       OR current_setting('server_version_num')::integer >= 190000 THEN
        RAISE EXCEPTION 'PostgreSQL 18 is required';
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS raw_tdnet.document_artifact (
    source_id text NOT NULL REFERENCES raw_tdnet.document(source_id),
    artifact_kind text NOT NULL CHECK (artifact_kind IN ('pdf', 'xbrl_zip')),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    content bytea NOT NULL,
    byte_length bigint NOT NULL CHECK (
        byte_length = octet_length(content)
        AND byte_length > 0
        AND byte_length <= 20971520
    ),
    source_url text NOT NULL,
    snapshot_hash text NOT NULL REFERENCES raw_tdnet.list_snapshot(snapshot_hash),
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis),
    PRIMARY KEY (source_id, artifact_kind, content_hash),
    CHECK (first_observed_at <= last_observed_at)
);

ALTER TABLE raw_tdnet.document_artifact OWNER TO research_owner;
REVOKE ALL ON TABLE raw_tdnet.document_artifact FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE raw_tdnet.document_artifact TO tdnet_ingest;
GRANT SELECT ON TABLE raw_tdnet.document_artifact TO research_reader;
