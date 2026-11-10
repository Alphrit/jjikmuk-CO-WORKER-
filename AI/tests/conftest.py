import os


# Unit tests should be deterministic and must not call live LLM/RAG providers just
# because a developer has real API keys in AI/.env. Individual tests can still set
# temporary keys explicitly when they are checking environment helper behavior.
os.environ["OPENAI_API_KEY"] = ""
os.environ["FOOD_CHATBOT_ENABLE_EXTERNAL_AI"] = "false"
os.environ["FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET"] = ""
os.environ["FOOD_CHATBOT_EXTERNAL_AI_CONSENT_MAX_AGE_SECONDS"] = "60"
# Consent tests check the strict boundary; the assumed-consent default has its own tests.
os.environ["FOOD_CHATBOT_EXTERNAL_AI_ASSUME_CONSENT"] = "false"
# Rule-text assertions stay exact; answer_polish has its own tests.
os.environ["FOOD_CHATBOT_POLISH_PRODUCT_ANSWERS"] = "false"
os.environ["FOOD_CHATBOT_ENV"] = "test"
os.environ["FOOD_CHATBOT_API_TOKEN"] = ""
os.environ["FOOD_CHATBOT_ENABLE_RATE_LIMIT"] = "false"
os.environ["FOOD_CHATBOT_BEHIND_TRUSTED_GATEWAY"] = "false"
os.environ["FOOD_CHATBOT_TRUST_PROXY_HEADERS"] = "false"
os.environ["FOOD_CHATBOT_STORE_RAW_MESSAGES"] = "false"
os.environ["FOOD_CHATBOT_ENABLE_CONVERSATIONS"] = "false"
os.environ["FOOD_CHATBOT_USER_IDENTITY_SECRET"] = ""
os.environ["FOOD_CHATBOT_PRODUCT_LABEL_MAX_AGE_DAYS"] = "180"
os.environ["FOOD_CHATBOT_PUBLIC_IMAGE_HOSTS"] = ""
os.environ["FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES"] = ",".join([
    "manufacturer_label:synthetic-label-test",
    "manufacturer_label:synthetic-nutrition-test-fixture",
    "manufacturer_label:synthetic-safety-test-fixture",
    "manufacturer_label:manufacturer-a",
    "manufacturer_label:source-a",
    "manufacturer_label:freshness-test",
])
