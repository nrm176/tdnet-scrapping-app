DO $$
BEGIN
    IF current_setting('server_version_num')::integer < 180000
       OR current_setting('server_version_num')::integer >= 190000 THEN
        RAISE EXCEPTION 'PostgreSQL 18 is required';
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS raw_tdnet AUTHORIZATION research_owner;
ALTER SCHEMA raw_tdnet OWNER TO research_owner;
REVOKE ALL ON SCHEMA raw_tdnet FROM PUBLIC;

CREATE TABLE IF NOT EXISTS raw_tdnet.list_snapshot (
    snapshot_hash text PRIMARY KEY CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    target_date date NOT NULL,
    raw_payload jsonb NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    CHECK (first_observed_at <= last_observed_at)
);

CREATE TABLE IF NOT EXISTS raw_tdnet.document (
    source_id text PRIMARY KEY,
    source_date date NOT NULL,
    title text NOT NULL,
    company_code text,
    published_at timestamptz,
    payload jsonb NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    snapshot_hash text NOT NULL REFERENCES raw_tdnet.list_snapshot(snapshot_hash),
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis),
    CHECK (source_id LIKE 'https://www.release.tdnet.info/inbs/%.pdf'),
    CHECK (first_observed_at <= last_observed_at)
);

ALTER TABLE raw_tdnet.list_snapshot OWNER TO research_owner;
ALTER TABLE raw_tdnet.document OWNER TO research_owner;
REVOKE ALL ON TABLE raw_tdnet.list_snapshot, raw_tdnet.document FROM PUBLIC;
GRANT USAGE ON SCHEMA raw_tdnet TO tdnet_ingest, research_reader;
GRANT SELECT, INSERT, UPDATE ON TABLE raw_tdnet.list_snapshot, raw_tdnet.document TO tdnet_ingest;
GRANT SELECT ON TABLE raw_tdnet.list_snapshot, raw_tdnet.document TO research_reader;
