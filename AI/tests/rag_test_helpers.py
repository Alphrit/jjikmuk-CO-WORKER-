"""Explicit model-review fixtures; no real provider calls."""

import json

from app.services.rag_grounding import answer_sentences


def grounding_review(answer, bodies, *, verdict="supported"):
    return json.dumps({"claims": [
        {"text": sentence, "verdict": verdict,
         "evidence": [{"source_id": f"E{index}", "quote": body}
                      for index, body in enumerate(bodies, 1)]}
        for sentence in answer_sentences(answer)
    ]}, ensure_ascii=False)
