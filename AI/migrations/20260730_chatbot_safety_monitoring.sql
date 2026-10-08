-- MySQL chatbot audit-log baseline and safety monitoring columns.
-- This migration is the only supported creation/upgrade path for chatbot_logs.
-- The AI runtime validates this schema and never creates or alters it.
-- Apply once through the deployment migration owner. Before applying to a database
-- that already has chatbot_logs, confirm which columns/indexes already exist because
-- plain ADD COLUMN/CREATE INDEX statements can fail when re-run.

CREATE TABLE IF NOT EXISTS chatbot_logs (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NULL,
  barcode VARCHAR(255) NULL,
  report_number VARCHAR(255) NULL,
  product_id BIGINT NULL,
  action_type VARCHAR(255) NOT NULL DEFAULT 'chat',
  message TEXT NULL,
  intent VARCHAR(255) NULL,
  answer_source VARCHAR(64) NULL,
  pipeline_stage VARCHAR(64) NULL,
  risk_level VARCHAR(64) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  INDEX idx_chatbot_logs_user_created (user_id, created_at),
  INDEX idx_chatbot_logs_barcode_created (barcode, created_at)
);

ALTER TABLE chatbot_logs
  ADD COLUMN confidence DOUBLE NULL,
  ADD COLUMN confidence_reasons TEXT NULL,
  ADD COLUMN fallback_reason VARCHAR(255) NULL,
  ADD COLUMN validation_failure_types TEXT NULL,
  ADD COLUMN critical_safety_violation BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN blocked_llm_output_stored BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX idx_chatbot_logs_fallback_reason
  ON chatbot_logs (fallback_reason);

CREATE INDEX idx_chatbot_logs_critical_safety
  ON chatbot_logs (critical_safety_violation);
