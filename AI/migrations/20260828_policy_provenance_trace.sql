-- Additive, privacy-minimized policy provenance trace. No prompt or answer text.
ALTER TABLE chatbot_logs
  ADD COLUMN policy_version VARCHAR(32) CHARACTER SET ascii NULL,
  ADD COLUMN applied_rule_ids TEXT NULL,
  ADD COLUMN source_ids TEXT NULL;

CREATE INDEX idx_chatbot_logs_policy_version
  ON chatbot_logs (policy_version);
