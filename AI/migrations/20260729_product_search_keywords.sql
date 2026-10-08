-- Korean product-name search index deployment template.
-- The DB owner should run each phase separately after staging verification.
-- AI runtime is read-only for schema and cannot create this index.

-- Phase 1: add only the nullable helper column.
-- ALGORITHM=INSTANT prevents an unexpected table-copy fallback.
ALTER TABLE products
  ADD COLUMN search_keywords TEXT NULL,
  ALGORITHM=INSTANT;

-- Phase 2: backfill in barcode-ordered batches of 1,000-5,000 rows.
-- Repeat this statement with the next :last_barcode and commit every batch.
-- Do not run one unbounded UPDATE against the whole production table.
UPDATE products
SET search_keywords = TRIM(
  CONCAT_WS(
    ' ',
    NULLIF(product_name, ''),
    NULLIF(clean_product_name, ''),
    NULLIF(manufacturer, ''),
    REGEXP_REPLACE(COALESCE(product_name, ''), '[[:space:][:punct:]]+', ''),
    REGEXP_REPLACE(COALESCE(clean_product_name, ''), '[[:space:][:punct:]]+', ''),
    REGEXP_REPLACE(COALESCE(manufacturer, ''), '[[:space:][:punct:]]+', '')
  )
)
WHERE barcode > :last_barcode
  AND (search_keywords IS NULL OR TRIM(search_keywords) = '')
ORDER BY barcode
LIMIT 3000;

-- Phase 3: run only after every product row is backfilled.
-- Schedule this separately because the first/changed FULLTEXT build can block DML.
CREATE FULLTEXT INDEX ft_products_search_keywords
  ON products (search_keywords)
  WITH PARSER ngram;

-- raw_materials and allergy_warning are intentionally excluded. Product-name
-- search should not return a product only because an ingredient contains the term.
