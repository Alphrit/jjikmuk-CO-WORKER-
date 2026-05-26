# Food Chatbot MVP

식품 성분, 알레르기, 식단 조건을 바탕으로 사용자의 섭취 가능 여부를 안내하는 FastAPI 기반 챗봇 MVP입니다.

## Project Structure

```text
.
├── app/
│   ├── api/             # FastAPI routes and request normalization
│   ├── core/            # Shared Pydantic models
│   ├── data/            # Static dictionaries
│   ├── infrastructure/  # LLM, RAG, and repository adapters
│   ├── intent/          # Intent classification and follow-up routing
│   ├── prompts/         # LLM prompt builders
│   ├── services/        # Chat, product, profile, and response logic
│   └── main.py          # Compatibility entrypoint for uvicorn app.main:app
├── tests/               # Unit tests
├── .env.example         # Environment variable template
├── .gitignore
├── README.md
└── requirements.txt
```

Generated local files such as `.env`, `venv/`, `__pycache__/`, `.pytest_cache/`, and `app/chroma_db/` are intentionally excluded from Git.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then add the API key for the provider you want to use in `.env`.

### Product DB

If the API receives only a barcode or report number, it looks up product details through the configured repository. Set `FOOD_CHATBOT_DB_PATH` in `.env` to the real SQLite product DB used by the backend:

```env
FOOD_CHATBOT_DB_PATH=/absolute/path/to/products.sqlite3
```

When `FOOD_CHATBOT_DB_PATH` is empty, the app falls back to the in-memory seed products in `app/infrastructure/repository.py`. In that mode, barcode-only requests only work for the built-in sample data.

## Run

```bash
uvicorn app.main:app --reload
```

API health check:

```bash
curl http://127.0.0.1:8000/health
```

Chat API check:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"저염식이면 뭘 먼저 봐야 해?"}'
```

## Test

```bash
python -m pytest
```

RAG-related tests require `OPENAI_API_KEY` and the optional LangChain/Chroma dependencies installed.

## Main Backend Flow

- `app/api/main.py`: FastAPI endpoints only. It normalizes requests, resolves product context, calls the chat service, and logs activity.
- `app/services/conversation_router.py`: Product lookup, typo-tolerant candidate confirmation, previous-product reuse, and topic-shift handling.
- `app/services/product_search.py`: Product-name tokenization, typo-tolerant similarity scoring, and explicit product mention checks.
- `app/services/chat.py`: Intent classification and handler dispatch.
- `app/services/handlers.py`: Product-specific eating, ingredient, nutrition, and profile recheck handlers.
- `app/services/health_handlers.py`: Health or disease-condition checks such as diabetes and weight-control logic.
- `app/services/general_handlers.py`: General food, allergy, diet, nutrition, and unknown-question answers.
- `app/services/product_status_handlers.py`: Missing-product and scan-summary responses.
- `app/services/response_helpers.py`: Shared answer formatting, profile checks, allergy matching, and diet evaluation helpers.
- `app/services/recommended_questions.py`: Follow-up question text generation.
