DO $$
BEGIN
    IF current_setting('server_version_num')::integer < 180000
       OR current_setting('server_version_num')::integer >= 190000 THEN
        RAISE EXCEPTION 'PostgreSQL 18 is required';
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS raw_tdnet.summary_extraction (
    source_id text NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    parser_version text NOT NULL,
    artifact_kind text NOT NULL DEFAULT 'xbrl_zip' CHECK (artifact_kind = 'xbrl_zip'),
    snapshot_hash text NOT NULL REFERENCES raw_tdnet.list_snapshot(snapshot_hash),
    source_url text NOT NULL,
    member_path text NOT NULL,
    extraction_hash text NOT NULL CHECK (extraction_hash ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    extracted_at timestamptz NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis),
    PRIMARY KEY (source_id, content_hash, parser_version),
    FOREIGN KEY (source_id, artifact_kind, content_hash)
        REFERENCES raw_tdnet.document_artifact(source_id, artifact_kind, content_hash)
);

CREATE TABLE IF NOT EXISTS raw_tdnet.summary_fact (
    source_id text NOT NULL,
    content_hash text NOT NULL,
    parser_version text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    concept_qname text NOT NULL,
    context_id text NOT NULL,
    unit_id text NOT NULL,
    numeric_value numeric,
    is_nil boolean NOT NULL,
    payload jsonb NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis),
    PRIMARY KEY (source_id, content_hash, parser_version, ordinal),
    FOREIGN KEY (source_id, content_hash, parser_version)
        REFERENCES raw_tdnet.summary_extraction(source_id, content_hash, parser_version),
    CHECK ((is_nil AND numeric_value IS NULL) OR (NOT is_nil AND numeric_value IS NOT NULL))
);

ALTER TABLE raw_tdnet.summary_extraction OWNER TO research_owner;
ALTER TABLE raw_tdnet.summary_fact OWNER TO research_owner;
REVOKE ALL ON TABLE raw_tdnet.summary_extraction, raw_tdnet.summary_fact FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE raw_tdnet.summary_extraction, raw_tdnet.summary_fact TO tdnet_ingest;
GRANT SELECT ON TABLE raw_tdnet.summary_extraction, raw_tdnet.summary_fact TO research_reader;
