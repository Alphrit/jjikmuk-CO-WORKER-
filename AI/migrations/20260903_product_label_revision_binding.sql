-- Additive follow-up for deployments that may already have applied
-- 20260903_product_label_provenance.sql.
--
-- The original content_revision_sha256 column is intentionally NOT copied.
-- It did not distinguish an ingestion-time binding from a hash calculated at
-- read time. Populate this new column only in the ingestion transaction that
-- writes the exact facts canonicalized by app.core.product_provenance. Existing
-- rows remain NULL and therefore fail closed.

ALTER TABLE products
  ADD COLUMN ingested_content_revision_sha256 CHAR(64) NULL,
  ADD CONSTRAINT chk_products_label_source_type
    CHECK (
      label_source_type IS NULL OR label_source_type IN (
        'manufacturer_label', 'regulatory_database', 'retailer_catalog',
        'internal_catalog', 'user_supplied_label', 'other'
      )
    ),
  ADD CONSTRAINT chk_products_label_source_id_nonblank
    CHECK (
      label_source_id IS NULL
      OR label_source_id REGEXP '^[A-Za-z0-9][A-Za-z0-9._-]*$'
    ),
  ADD CONSTRAINT chk_products_label_source_record_id_nonblank
    CHECK (
      label_source_record_id IS NULL
      OR label_source_record_id REGEXP '^[A-Za-z0-9][A-Za-z0-9._-]*$'
    ),
  ADD CONSTRAINT chk_products_label_upstream_revision_public_id
    CHECK (
      upstream_revision IS NULL
      OR upstream_revision REGEXP '^[A-Za-z0-9][A-Za-z0-9._-]*$'
    ),
  ADD CONSTRAINT chk_products_label_ingested_revision
    CHECK (
      ingested_content_revision_sha256 IS NULL
      OR ingested_content_revision_sha256 REGEXP '^[0-9a-f]{64}$'
    ),
  ADD CONSTRAINT chk_products_label_lineage_status
    CHECK (
      label_lineage_status IS NULL
      OR label_lineage_status IN ('complete', 'partial', 'unavailable')
    ),
  ADD CONSTRAINT chk_products_label_temporal_order
    CHECK (
      upstream_updated_at IS NULL
      OR (
        label_observed_at IS NOT NULL
        AND upstream_updated_at <= label_observed_at
      )
    ),
  ADD CONSTRAINT chk_products_label_groups_json
    CHECK (
      label_covered_fact_groups IS NULL
      OR JSON_TYPE(label_covered_fact_groups) = 'ARRAY'
    );

CREATE INDEX idx_products_label_ingested_content_revision
  ON products (ingested_content_revision_sha256);
