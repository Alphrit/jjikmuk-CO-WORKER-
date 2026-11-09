-- The backend sends policyVersion "unified-hard-constraints-purpose-v5.2" (37 chars);
-- the request contract allows 256. VARCHAR(32) rejected every backend-analysis audit row
-- with ER_DATA_TOO_LONG. Widening is in place and keeps the existing index.
ALTER TABLE chatbot_logs
  MODIFY COLUMN policy_version VARCHAR(256) CHARACTER SET ascii NULL;
