-- Minimal response and external-AI trace for chatbot_logs.
--
-- This table extension deliberately contains no feedback, recheck, review-queue,
-- question, answer, profile, provider URL, or conversation fields.

ALTER TABLE chatbot_logs
  ADD COLUMN response_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
  ADD COLUMN external_ai_used BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN external_ai_services JSON NULL;

CREATE INDEX idx_chatbot_logs_response_id
  ON chatbot_logs (response_id);
