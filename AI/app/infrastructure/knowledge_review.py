"""Source release decisions are separate from successful text extraction.

These are conservative application release decisions, not legal opinions. Never
infer permission from an official hostname or applicability from a filename.
Approvals bind the exact local source/capture bytes AND the reviewed excerpt.
For web references, the capture is selected text, not a full HTML snapshot.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.infrastructure.knowledge_sources import SOURCES as IMPORT_SOURCES, WEB_SOURCES, read_source


SOURCES = IMPORT_SOURCES + WEB_SOURCES
REFERENCE_DIR = Path(__file__).resolve().parents[1] / "reference_sources"


@dataclass(frozen=True)
class SourceReview:
    sha256: str
    rights: str
    applicability: str
    reason: str
    reviewed_at: str = "2026-10-01"

    @property
    def released(self) -> bool:
        return self.rights == "cleared" and self.applicability == "reviewed"


SOURCE_REVIEWS = {
    "mfds-label-2026-37": SourceReview(
        "5535476a8d123bc5a7f9ffcf492ec8a8f1a88f574e523092e1a7b8673d8b30e5",
        "review_required", "clause_review_required",
        "PDF 124쪽 부칙: 원칙적 시행일 2028-01-01, 일부 조항 별도 시행 및 경과조치. "
        "전문 전체를 현재 기준으로 사용하지 않음. 조항별 적용 시점·이용 조건 확인 필요.",
    ),
    "kdca-food-allergy-management": SourceReview(
        "5f1dd8e4fac4d4fd96c50bfb2a8fcb95e0681837fe1788bc4d193e62d07f2953",
        "permission_required", "scope_review_required",
        "원문 페이지에 공공누리 출처표시·상업적 이용 금지·변경 금지 조건이 명시됨. "
        "서비스용 생성 답변에 자동 사용하지 않음. 진단·치료·약물 설명은 일반 식품 RAG에서 제외.",
    ),
    "foodsafety-cookrcp01": SourceReview(
        "aa9eb1507d65dd03bd696a977d94442ddc0ba0e0d95558f70edef19f9225b2b9",
        "cleared", "recipe_review_required",
        "공공데이터포털 data/15060073/openapi.do에서 이용허락범위 제한 없음 확인. "
        "근거는 reference_sources/foodsafety-cookrcp01.rights.json. 외부 이미지·별도 책에는 적용하지 않음. "
        "전체 CSV 일괄 추천은 보류하며 개별 검토본은 별도 승인. INFO_WGT는 공식 명세상 중량(1인분)이나 "
        "개별 값 누락과 영양값 단위·기준은 별도 확인. 당류 값을 탄수화물로 대체하지 않음.",
    ),
    "kca-allergy-purchase-2018": SourceReview(
        "cf2ddd0f7e00992ad485dee33bcbb313a74cf7d54ea0b4320bdcbd6b8239ebca",
        "cleared", "reviewed",
        "게시물의 공공누리 제1유형 확인. 구매 전 알레르기 성분 확인 문장만 사용. "
        "2018년 당시 법령·업체 현황·의료 설명 제외. 해시는 선택적 웹 전사 기록이며 전체 HTML 해시가 아님.",
    ),
    "korea-diet-guidelines-2021": SourceReview(
        "506f985c54a4e78df82b769811569e74fadcab6c0c6c1811538602d588812942",
        "cleared", "reviewed",
        "기사 텍스트 공공누리 제1유형 확인. 나트륨·당류·포화지방산 줄이기 방향만 사용. "
        "개인별 수치·제품 판정·현재 법령 기준으로 사용하지 않음. 해시는 선택적 웹 전사 기록임.",
    ),
    "korea-sodium-practice-2011": SourceReview(
        "ee1109cc59dc3ad0a5c58f188b9926d35b059381a9ebefcdd04c6a6f5a866f64",
        "cleared", "reviewed",
        "기사 텍스트 공공누리 제1유형 확인. 영양표시 확인·양념 줄이기·외식 주문 요령만 사용. "
        "2011년 기사의 제목/본문 수치·질병 설명은 제외하며 최신 기준으로 주장하지 않음. "
        "해시는 선택적 웹 전사 기록임.",
    ),
}

# A content review must add an exact canonical digest here. Marking an
# arbitrary file "approved" is not sufficient to publish it as official evidence.
RELEASED_EXCERPT_HASHES: dict[str, str] = {
    "allergy-purchase-check": "cce6dec7bce1addc53a3e4dc6c6fc9e37015bc72516e4e4ab744606c675fe12f",
    "diet-reduction-direction": "41a3e5fc1ff6aeb8f07ffb7b37479cde796dbabb6e2819c0cd9af76d7029984c",
    "sodium-practical-choices": "737977ad202ce48b2906532c2af4140b912b36a2ab4c9ddb37e39d77057e41cd",
}

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{1,119}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ReviewedExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["food-rag-excerpt-v1"]
    document_id: Identifier
    source_id: Identifier
    source_sha256: Digest
    page: int | None = Field(default=None, ge=1, le=500)
    section: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=3000)
    content_review: Literal["approved"]
    usage_scope: Literal["general_food_explanation"]
    reviewed_at: date
    review_due: date


def excerpt_digest(excerpt: ReviewedExcerpt) -> str:
    canonical = json.dumps(excerpt.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_release_status(source_id: str, sha256: str) -> dict:
    review = SOURCE_REVIEWS.get(source_id)
    if review is None or review.sha256 != sha256:
        return {
            "source_id": source_id, "runtime_allowed": False,
            "rights": "unreviewed", "applicability": "unreviewed",
            "reason": "원본 해시가 검토 기록과 다르거나 등록되지 않은 출처입니다.",
        }
    return {
        "source_id": source_id, "runtime_allowed": review.released,
        "rights": review.rights, "applicability": review.applicability,
        "reason": review.reason, "reviewed_at": review.reviewed_at,
    }


def parse_released_excerpt(text: str, *, today: date | None = None) -> ReviewedExcerpt:
    excerpt = ReviewedExcerpt.model_validate_json(text)
    current = today or date.today()
    if not excerpt.reviewed_at <= current <= excerpt.review_due:
        raise ValueError("Official source review is expired or future-dated.")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in excerpt.text + excerpt.section):
        raise ValueError("Official source contains invalid control characters.")
    spec = next((source for source in SOURCES if source.source_id == excerpt.source_id), None)
    if spec is None or spec.kind not in {"pdf", "web"}:
        raise ValueError("Official source is not a registered PDF/web reference.")
    if (spec.kind == "pdf" and excerpt.page is None) or (spec.kind == "web" and excerpt.page is not None):
        raise ValueError("PDF sources require a page; web references must not invent PDF pages.")
    status = source_release_status(excerpt.source_id, excerpt.source_sha256)
    if not status["runtime_allowed"]:
        raise ValueError("Official source rights/applicability review is not cleared.")
    if spec.kind == "web":
        # Ship the bounded review evidence alongside the excerpt. A checkout
        # missing/altering that evidence must not claim the source is released.
        capture = read_source(REFERENCE_DIR, spec)
        if len(capture) > 16_384 or hashlib.sha256(capture).hexdigest() != excerpt.source_sha256:
            raise ValueError("Web reference capture does not match the reviewed revision.")
    if RELEASED_EXCERPT_HASHES.get(excerpt.document_id) != excerpt_digest(excerpt):
        raise ValueError("Official source excerpt has not been approved at this revision.")
    return excerpt


def excerpt_reference(excerpt: ReviewedExcerpt) -> dict[str, str]:
    spec = next(source for source in SOURCES if source.source_id == excerpt.source_id)
    location = f"PDF {excerpt.page}쪽 · {excerpt.section}" if spec.kind == "pdf" else f"웹 본문 · {excerpt.section}"
    return {
        "source": f"official-source:{excerpt.source_id}:{excerpt.document_id}",
        # Remains a bounded public sources[] string; no private diagnostics or
        # source_details field is reintroduced into the HTTP response.
        "citation": f"{spec.publisher} | {spec.title} | {location} | {spec.url}",
    }
