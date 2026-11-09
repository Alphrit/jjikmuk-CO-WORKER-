"""Intent classification must not change silently.

The fixture records today's rule_based_classify_intent result for ~640
evaluation and web-test questions, with and without a product. A rule added
for one sentence often moves others (the '그건 알겠고' case, 2026-11-09). When a
change is intended, review every reported line, then regenerate with
`python evaluation/update_intent_snapshot.py`.
"""

import json
import sys
from pathlib import Path

AI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI_DIR / "evaluation"))

from update_intent_snapshot import SNAPSHOT_PATH, classify_all, collect_questions  # noqa: E402


def test_every_recorded_question_keeps_its_intent():
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    actual = classify_all(sorted(expected))
    changed = [f"{question!r}: {expected[question]} -> {actual[question]}"
               for question in expected if actual[question] != expected[question]]
    assert not changed, "intent changed for:\n" + "\n".join(changed[:40])


def test_snapshot_covers_every_evaluation_question():
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    missing = [question for question in collect_questions() if question not in expected]
    assert not missing, f"{len(missing)} new questions; regenerate the snapshot: {missing[:5]}"
