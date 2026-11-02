# Product Search Baseline Notes

Historical baseline run before `products.search_keywords` and MySQL ngram parser changes.
Result files were moved to the local archive on 2026-09-28; see `artifact_retention.md`.

- Baseline cases: `AI/evaluation/product_search_baseline_cases.json`
- Baseline result: `AI/evaluation/results/archive/pre-cleanup-20260928/product_search_eval_20260820_151624.json`
- Existing search eval result: `AI/evaluation/results/archive/pre-cleanup-20260928/product_search_eval_20260820_151233.json`
- DB rows at verification time: `233086`
- `products.search_keywords` exists: `false`

## Summary

The current search logic works without `search_keywords`; both baseline and existing evaluation sets passed.

- Baseline top1/top5: `12/12`, `100%`
- Existing eval top1/top5: `15/15`, `100%`
- Duplicate candidate failures: `0`

These scores are useful as a regression baseline, but they should not be read as "search is perfect." Some cases pass because the expected condition is broad, while the actual top result may still be less ideal for a real user.

## Good Cases

| Query | Top Candidates |
| --- | --- |
| `단백질칩` | `단백질공화국 닭가슴살칩_매운카레`, `단백질공화국 닭가슴살칩_참깨`, `단백질공화국 닭가슴살칩_오리지날` |
| `매운 새우깡` | `매운새우깡`, `농심 매운새우깡(800원)90G - VINYL`, `매운새우깡대30` |
| `아인슈타인 우유` | `남양 아인슈타인 첫 우유`, `아인슈타인 딸기우유`, `아인슈타인 초코우유` |
| `동원참치` | `동원참치`, `동원참치 스프레드 매콤마요`, `동원참치 황다랑어 통뱃살` |
| `아몬드브리즈` | `아몬드브리즈 오리지널 45`, `아몬드브리즈 언스위트 35`, `아몬드브리즈 초콜릿` |

## Ambiguous Or Weak Cases To Watch

| Query | Current Top Candidates | Why It Needs Review |
| --- | --- | --- |
| `두유바` | `삼육두유바나나`, `산양삼두유`, `찐두유` | User may intend a bar/snack product, but the top result can be a drink/flavor product containing `두유바` as part of a longer token. |
| `두유 바` | `두유액`, `두유속에 렌틸`, `두유속에 오트` | Spacing changes the result and does not reliably preserve the intended `바` concept. |
| `동원 참치 라이트` | `참치마요네즈삼각김밥`, `참치마요 삼각김밥`, `참치야채오트밀죽` | The query loses the brand/variant intent and returns broad 참치 products instead of the likely canned tuna target. |
| `나100우유` | `바삭하고 고소한 우유팝콘`, `제직증명 무항생제우유`, `토이스토리_우유컵쉬폰` | Compact brand/product expression is not handled well; likely needs better keyword normalization or ngram/search_keywords support. |
| `닥터유 단백질바` | `닥터유 프로 한입쏙 단백질바`, `단백질바`, `POST 단백질바` | Acceptable, but exact brand-family ranking should be watched after DB search changes. |

## After DB Changes

After `search_keywords` and ngram FULLTEXT are applied, rerun:

```bash
python AI/evaluation/run_product_search_eval.py --cases AI/evaluation/product_search_baseline_cases.json --limit 5
python AI/evaluation/run_product_search_eval.py --limit 5
```

Compare:

- `AI/evaluation/results/archive/pre-cleanup-20260928/product_search_eval_20260820_151624.json` vs the new baseline result
- Top candidate names for the weak cases above
- `top1_accuracy`
- `top5_accuracy`
- `duplicate_candidate_failures`
- Any newly failed case IDs

Optional machine-readable comparison:

```bash
python AI/evaluation/compare_product_search_results.py \
  AI/evaluation/results/archive/pre-cleanup-20260928/product_search_eval_20260820_151624.json \
  AI/evaluation/results/<NEW_BASELINE_RESULT>.json
```
