# Product Search Post DB Change Checklist

Use this checklist after the DB owner applies `products.search_keywords` and ngram FULLTEXT indexing.

## 1. Runtime/DB Shape

Run:

```bash
PYTHONPATH=AI python - <<'PY'
from app.api.main import configure_repositories, get_product_repository

configure_repositories()
repo = get_product_repository()
print(type(repo).__name__)
print(repo.health_check())
PY
```

Check:

- Repository is `MySQLProductRepository`
- `products` row count is close to the expected production count
- `search_keywords` is detected in product columns
- FULLTEXT index exists
- No DB connection fallback to memory

## 2. Search Baseline Comparison

Run:

```bash
python AI/evaluation/run_product_search_eval.py --cases AI/evaluation/product_search_baseline_cases.json --limit 5
python AI/evaluation/run_product_search_eval.py --limit 5
```

Compare the new results with:

- `AI/evaluation/results/archive/pre-cleanup-20260928/product_search_eval_20260820_151624.json`
- `AI/evaluation/results/archive/pre-cleanup-20260928/product_search_eval_20260820_151233.json`

These historical files are local-only archives; see `artifact_retention.md` before sharing them.

You can also generate a machine-readable before/after comparison report:

```bash
python AI/evaluation/compare_product_search_results.py \
  AI/evaluation/results/archive/pre-cleanup-20260928/product_search_eval_20260820_151624.json \
  AI/evaluation/results/<NEW_BASELINE_RESULT>.json
```

Watch especially:

- `두유바`
- `두유 바`
- `동원 참치 라이트`
- `나100우유`
- `닥터유 단백질바`

## 3. Chat Product Matching

Call `/chat` or use FastAPI TestClient with:

| Message | Expected Behavior |
| --- | --- |
| `단백질칩 찾아줘` | Product candidates are returned, no general nutrition guide |
| `두유바 찾아줘` | Product candidates are returned, preferably bar/snack-like products when present |
| `매운 새우깡 먹어도 돼?` | Product is either confidently matched or candidate confirmation is shown |
| `아인슈타인 우유 알레르기 있는데 먹어도 돼?` | Candidate confirmation, not forced answer |
| `우유 먹어도 돼?` | Broad category guidance, not dozens of product candidates |
| `오늘 날씨 어때?` | `out_of_domain`, no product search |

## 4. Safety/Recommendation Regression

Run:

```bash
pytest AI/tests/test_chatbot.py -q
python AI/evaluation/run_recommendation_quality_eval.py
env FOOD_CHATBOT_DB_PROVIDER=memory python AI/evaluation/run_eval.py --limit 100 --disable-llm
```

Check:

- Allergy-conflicting recommendations are excluded
- Candidate duplicates do not appear
- `missing_product` guidance remains situation-specific
- RAG/LLM answer source still matches actual processing

## 5. Success Criteria

The DB search change is safe to accept when:

- Existing product search eval has no failed cases
- Baseline weak cases improve or at least do not regress
- Duplicate candidate failures stay `0`
- `/chat` does not route food/product search requests to generic guide responses
- Out-of-domain questions do not trigger product search
- No new recommendation safety regression appears
