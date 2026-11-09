"""Regenerate the intent-classification snapshot used by tests/test_intent_snapshot.py.

The snapshot records what rule_based_classify_intent returns today for every
question in evaluation/*.json plus the 2026-11-09 web-test questions, with and
without a selected product. It is a record of current behaviour, not ground
truth: run this only after reviewing every changed line the test reports.

    python evaluation/update_intent_snapshot.py
"""
import json
import sys
from pathlib import Path

AI_DIR = Path(__file__).resolve().parents[1]
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

from app.core.models import Product  # noqa: E402
from app.intent.rules import rule_based_classify_intent  # noqa: E402

SNAPSHOT_PATH = AI_DIR / "tests" / "fixtures" / "intent_snapshot.json"
WEB_TEST_QUESTIONS = [
    "안녕하세요", "오늘 날씨 어때?", "트랜스지방이 뭐야?", "새우깡 먹어도 돼?", "유당불내증인데 새우깡 먹어도 돼?",
    "땅콩 알레르기인데 새우깡 먹어도 돼?", "나트륨 얼마야?", "김치찌개 만드는 법 알려줘", "비건 레시피 추천해줘",
    "저염 과자 추천해줘", "무설탕과 무가당의 차이를 알려줘", "이 상품의 원재료를 알려줘", "이 상품의 영양정보를 알려줘",
    "칼로리는?", "이 제품에 특정 성분이 들어 있어?", "대두 들어있어?", "땅콩 들어있어?", "알레르기 성분 알려줘",
    "쌀새우깡이랑 비교해줘", "비슷한데 덜 짠 거 추천해줘", "그건 알겠고, 영양표에서 당류 0g은 정말 하나도 없다는 뜻인가요?",
    "두유 먹어도 돼?", "셀리악인데 먹어도 돼?", "새우 알레르기인데 이거 먹어도 돼?",
]


def _sample_product():
    return Product(product_name="새우깡", barcode="8801043035989", rawmtrl_nm="곡류가공품, 미강유, 팜유",
                   allergy=["대두", "새우", "우유"], nutrition={"sodium": 200.0, "kcal": 160.0})


def collect_questions():
    questions = set(WEB_TEST_QUESTIONS)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("message", "question", "user") and isinstance(value, str):
                    questions.add(value.strip())
                else:
                    walk(value)
        elif isinstance(node, list):
            # live journeys: [question, [expected source ids], ...]
            if node and isinstance(node[0], str) and any(isinstance(item, list) for item in node[1:]):
                questions.add(node[0].strip())
            for item in node:
                walk(item)

    for path in sorted((AI_DIR / "evaluation").glob("*.json")):
        walk(json.loads(path.read_text(encoding="utf-8")))
    return sorted(question for question in questions if 1 <= len(question) <= 300)


def classify_all(questions):
    product = _sample_product()
    return {question: {"no_product": rule_based_classify_intent(question, None, None, []),
                       "with_product": rule_based_classify_intent(question, product, None, [])}
            for question in questions}


if __name__ == "__main__":
    snapshot = classify_all(collect_questions())
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(snapshot)} questions to {SNAPSHOT_PATH.relative_to(AI_DIR)}")
