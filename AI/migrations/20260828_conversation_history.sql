-- MySQL saved-conversation schema for GPT-style chat rooms.
--
-- Apply through the deployment migration owner before enabling conversation
-- persistence. The AI runtime performs readiness checks and never CREATEs or
-- ALTERs these MySQL tables. This file is intentionally separate from the
-- privacy-minimized chatbot_logs audit table: saved messages are user-facing data
-- governed by retention and explicit hard deletion.

CREATE TABLE IF NOT EXISTS chatbot_conversations (
  conversation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  user_id BIGINT NOT NULL,
  title VARCHAR(32) NOT NULL,
  title_manually_set BOOLEAN NOT NULL DEFAULT FALSE,
  state_json JSON NOT NULL,
  revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  expires_at DATETIME(6) NULL,
  PRIMARY KEY (conversation_id),
  INDEX idx_chatbot_conversations_user_updated
    (user_id, updated_at, conversation_id),
  INDEX idx_chatbot_conversations_expires
    (expires_at, conversation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chatbot_messages (
  message_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  conversation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  message_sequence BIGINT UNSIGNED NOT NULL,
  turn_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
  role VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  content MEDIUMTEXT NOT NULL,
  metadata_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  PRIMARY KEY (message_id),
  UNIQUE KEY uq_chatbot_messages_conversation_sequence
    (conversation_id, message_sequence),
  UNIQUE KEY uq_chatbot_messages_conversation_turn_role
    (conversation_id, turn_id, role),
  INDEX idx_chatbot_messages_conversation_created
    (conversation_id, created_at, message_id),
  CONSTRAINT fk_chatbot_messages_conversation
    FOREIGN KEY (conversation_id)
    REFERENCES chatbot_conversations (conversation_id)
    ON DELETE CASCADE,
  CONSTRAINT chk_chatbot_messages_role
    CHECK (role IN ('user', 'assistant'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
