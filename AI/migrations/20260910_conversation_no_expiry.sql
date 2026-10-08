-- Product decision: saved chat rooms remain until user/account deletion.
-- Clear legacy sliding-retention timestamps without deleting any transcript.
UPDATE chatbot_conversations
SET expires_at = NULL
WHERE expires_at IS NOT NULL;
