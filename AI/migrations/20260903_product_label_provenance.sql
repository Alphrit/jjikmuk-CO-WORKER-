-- Product-label lineage columns used by the AI safety boundary.
--
-- IMPORTANT: every column is nullable on purpose. Existing product rows do not
-- acquire an observation time, source, revision, or completeness claim merely
-- because this migration ran. Backfill only from retained source evidence.
-- DATETIME values in these columns are UTC by contract; the repository adapter
-- attaches UTC tzinfo to the timezone-naive values returned by the MySQL driver.

ALTER TABLE products
  ADD COLUMN label_source_type VARCHAR(32) NULL,
  ADD COLUMN label_source_id VARCHAR(128) NULL,
  ADD COLUMN label_source_record_id VARCHAR(256) NULL,
  ADD COLUMN label_observed_at DATETIME(6) NULL,
  ADD COLUMN upstream_updated_at DATETIME(6) NULL,
  ADD COLUMN upstream_revision VARCHAR(256) NULL,
  ADD COLUMN content_revision_sha256 CHAR(64) NULL,
  ADD COLUMN label_covered_fact_groups JSON NULL,
  ADD COLUMN label_lineage_status VARCHAR(16) NULL;

CREATE INDEX idx_products_label_source_record
  ON products (label_source_id, label_source_record_id);

CREATE INDEX idx_products_label_content_revision
  ON products (content_revision_sha256);
