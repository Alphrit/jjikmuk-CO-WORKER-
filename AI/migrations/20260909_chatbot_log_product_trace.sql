-- Additive, privacy-minimized product-label trace for the exact generated response.
--
-- Apply after 20260828_response_trace.sql through the deployment
-- migration owner. The AI runtime validates this column and never ALTERs or
-- CREATEs MySQL schema. Existing/non-product rows deliberately remain NULL.
-- The JSON object is bounded by app.core.models.ActivityProductLabelTrace and
-- contains no question, answer, prompt, raw label facts, source ID, source-record
-- ID, upstream revision, product snapshot, profile, or conversation ID.

ALTER TABLE chatbot_logs
  ADD COLUMN response_product_label_trace JSON NULL,
  ADD CONSTRAINT chk_chatbot_logs_response_product_label_trace_object
    CHECK (
      response_product_label_trace IS NULL
      OR JSON_TYPE(response_product_label_trace) = 'OBJECT'
    );
