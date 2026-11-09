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

from app.infrastructure.knowledge_sources import (
    ADDITIONAL_PDF_SOURCES, EXPLANATION_WEB_SOURCES, SOURCES as IMPORT_SOURCES, WEB_SOURCES, read_source,
)


SOURCES = IMPORT_SOURCES + ADDITIONAL_PDF_SOURCES + WEB_SOURCES + EXPLANATION_WEB_SOURCES
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
    'fda-carbohydrate-basics': SourceReview(
        'e1cc84a9fd13a510ce3d43b5e00e5cb36933f7f796ba405166f2e2f788adfe05', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'fda-fat-basics': SourceReview(
        '43c17ea669b8cf78631af202155c961dc773b1b91e713a875b7957346097f522', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'fda-protein-basics': SourceReview(
        '6556832b5c42ad5ae532cd6423e40f85905b70e42687417dd841299e6b143526', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'fda-sodium-basics': SourceReview(
        'de19a12581597bed1a005345f2286c1db66dbeeb4274465f0219c907c8f6e327', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'fda-label-basics': SourceReview(
        'e07f90e2f11d6ccfb67bdc8c4ee4667c8bb3b7611844fbe73c8ef7cb79720e28', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'fda-added-sugars-basics': SourceReview(
        '5bc2eef9b0bc2daa6b386a935ce1792439fb4c15f02e32702dd778dcb7db7a09', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'nhs-vegan-basics': SourceReview(
        '472e904f77947d8bfc4bae41d5fe91829889da38915cc7d29f8a7fe11eec0083', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'nhs-vegetarian-basics': SourceReview(
        '474ef22648093fcc7fa89f72c097b0ba4cbb8a2a490807651a4463eebf47dba9', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'uk-vegan-allergen-basics': SourceReview(
        'cc2c64b58547a060fcaae58ef0ad707fefb2b14589846eb5fe8de44c81ba8f06', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'fda-gluten-basics': SourceReview(
        '0ddc554788803c50c170896d7b772ec3f3dffbacf43551b7e06e6ada045fcc28', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'fda-lactose-label-basics': SourceReview(
        '7e7509cff0abbd01df1cf1eeaab24e3a9bfa5e273c56c501d9f3907635192f64', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-10-15",
    ),
    'fda-cholesterol-basics': SourceReview(
        'da16ff7a68721e7891a93138f5a0c241c6b49c0e99996b6166192f5f74bee367', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-11-09",
    ),
    'mfds-date-marking-2023': SourceReview(
        'a0a337e6f16b892d071c07b7d0059620d4bfd143235918615482bc7935c4ef4a', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-11-09",
    ),
    'korea-haccp-2022': SourceReview(
        '5f199407827cacb981891cc925a6554f341173f1be33eb0f7e021b78220822f4', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-11-09",
    ),
    'mfds-beverage-caffeine-2021': SourceReview(
        'f2ed231e752189b6c79288b7fa2a7bed97e0e71d384a39fc86a1336315613448', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-11-09",
    ),
    'foodsafety-processed-food-label-2014': SourceReview(
        'f62992b91e917b6ee5336fb444017a303bdd670c1c03c63f1faf67df23d48ae9', "cleared", "reviewed",
        "선택한 사실만 새 문장으로 설명. 원문·그림·표·전체 번역의 이용 허락을 주장하지 않음. "
        "해외 의무·수치·의료·개인 적합성 제외. 검토 근거는 같은 source_id의 reference.json.",
        reviewed_at="2026-11-09",
    ),
    "mfds-allergy-label-reading-2022": SourceReview(
        "1fb3e315408e3493a0421e79e956544db25edbff586df516740598d546b08532",
        "cleared", "reviewed",
        "사용자가 제공한 13쪽 PDF의 공식 카드 8~10쪽을 시각 확인하고 선택된 사실만 새 문장으로 설명. "
        "앞 1~4쪽의 전사문은 이미지와 구분하며 공식 원본 PDF로 간주하지 않음. "
        "이미지·원문 전체의 복제 허락이나 공공누리 허락을 확인했다는 뜻이 아님. "
        "2022년 표시 의무·목록·의료·대체식품을 제외. "
        "검토 근거: reference_sources/allergy-uploaded-pdfs.review.json.",
        reviewed_at="2026-10-15",
    ),
    "nfsi-allergen-labelling-2023": SourceReview(
        "3552e8940df047d5037ebb27f269eb5db0aa4792a1aa40b19ce9a6bffc100600",
        "cleared", "reviewed",
        "사용자 제공 PDF 57~59쪽(인쇄 55~57쪽)의 교차접촉 개념·관리 사례를 새 문장으로 사실 요약. "
        "붙임 6은 영국 안내를 소개한 부분이며 국가별 표시 의무·대상 목록·허용량을 국내 기준으로 쓰지 않음. "
        "식품안전정보원 원본·번역·표·그림 전체의 이용 허락을 주장하지 않음. "
        "의료·개인 적합 판정 제외. 검토 근거: reference_sources/allergy-uploaded-pdfs.review.json.",
        reviewed_at="2026-10-15",
    ),
    "mfds-nutrition-label-guide-2025-12": SourceReview(
        "d10b954559cd3d61f8bffddf0ed2972bd639c0c59238f43eb96364210f27d8e1",
        "cleared", "reviewed",
        "사용자가 제공한 PDF의 선택된 사실·수치만 새 문장으로 요약. "
        "본문·표·그림의 복제/배포 허락이나 공공누리 표시를 확인했다는 뜻이 아님. "
        "PDF 5·16·23~25쪽을 추출 및 시각 대조. 수치 설명은 2025.12 안내서 기준으로 한정. "
        "법률 자문·개인 권장량·제품 적합 판정과 그 외 원문은 승인하지 않음. "
        "검토 근거: reference_sources/mfds-uploaded-pdfs.review.json.",
        reviewed_at="2026-10-15",
    ),
    "mfds-consumer-newsletter-2026-36": SourceReview(
        "a45693b326fa6da76875c43cf619cabe4229126d5c30b4c5cb3c039edcf652ed",
        "cleared", "reviewed",
        "사용자가 제공한 이미지형 PDF 1쪽을 직접 읽고 당류·감미료·열량의 사실만 재서술. "
        "자동 텍스트 추출/OCR 성공이나 원본 이미지·디자인의 이용 허락을 주장하지 않음. "
        "감미료 안전성·질병 효과·개인 적합성·원문 전체는 승인하지 않음. "
        "검토 근거: reference_sources/mfds-uploaded-pdfs.review.json.",
        reviewed_at="2026-10-15",
    ),
    "korea-nutrition-label-reading-2013": SourceReview(
        "11316351dd33b31f0a663d0dbe7f06e76b3b7bf403154abf9d6dc3acc8c54fd6",
        "cleared", "reviewed",
        "본문의 텍스트 출처표시 이용 조건 확인. 작성자 우인혜를 출처에 포함. "
        "표시 기준량·기준치 비율·강조 문구·무가당 읽기의 일반 원칙만 사용. "
        "2013년 법정 수치·기준·감미료 건강효과·이미지는 제외. 선택적 전사 기록의 해시임.",
        reviewed_at="2026-10-15",
    ),
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
    "allergy-cross-contact-controls": "934d73aa45fab1900b8571d26e11bb697995f433a9933cafa723be425532a79e",
    "allergy-cross-contact-definition": "f124c2a1d4931e51c37b36d10233e2e9c8d44bd433eb5c633b4fb26fa0fca79d",
    "allergy-facility-warning": "b5cd5d3a46dbc4bd2cfab9f7df9ebd462db4937cb213a3243d62b72cca270ae1",
    "allergy-label-reading": "b0f0f6c40458dcf648e774128c208e5fa190c66bb283ca7223bb7206ed228533",
    "allergy-ordering-check": "eeb89f6dba9b670a282e309aa267b1988957d28cb389ed673057fed42e014fb6",
    "allergy-purchase-check": "4a5f6fe31daef8e19ed17a58b503c34a11d66c4436e3a6eb6c022efbdd12c228",
    "caffeine-label-basics": "4e4a81576717de9209d33d58d5f2b89b2701e77d6f44ae8099800b7327ce26ff",
    "calories-basics": "96fdd9d8daf4ca60fd8857df58f653ff52f63bbfdee1c88793a5614fe336c95f",
    "carbohydrate-and-sugars": "28d152d4c072b1b66de474f1cbd38d04de18407f1d8eb331de21894bf9662090",
    "cholesterol-basics": "ddeacbdb314884ae64fb154075d3f638a80848dc20ffe7eb5fd0cb85f27bbae8",
    "date-marking-basics": "9606d91e54ab8780fa584c4f0a6aacfc6941283dc07f194d489c859fd6e667e2",
    "diet-reduction-direction": "5d5043070dc400881c8300b83762dcf1537ba0ea252de2ac8e1f17ec857faa84",
    "dietary-fiber-basics": "71beefd91c1a0e003b013f7d094e1d9539658985ed7011f3483a2f4b6ff1162b",
    "fat-types-basics": "4c50d4e98743295e444b4d27c820641e6ae613c1418257c3fdf0ee590f33c1f1",
    "gluten-basics": "e7e6492e7b77c1d421d908a26ccbc532bd6a74dae25093c501a2841489f26883",
    "haccp-mark-basics": "40da9f236c466bb52e7eaf62a03ddc6b4d30a149eedb502d37766e9dbde3b7ab",
    "ingredient-order-basics": "64e64974f18da5361ee26139ba4538981fc44762b13420d9d5190a8da09126c0",
    "lactose-free-and-milk": "f2950e552247562ccac6c6ea661d456acee79b555455a807b3dba0b9fd89ccc8",
    "nutrition-front-claims": "a54ab4868a539771d3d3e6e9334324df99c2dcfb2256223f0f041b0a15661992",
    "nutrition-high-fiber": "36844b37a91085597d1acf32bed056211a408f4111d80f9631f72cca5fd4a464",
    "nutrition-high-protein": "0312aa0abac1b3af40491a2a48d961b59811f17edcd674b736bb4e08b2f9da4c",
    "nutrition-label-basis": "c41156454138f47aacd51dc8211f316d8c62771b622b0128306a06f6e0c7df33",
    "nutrition-label-percentage": "24a2e0690af5db9b15d23c25a19575d243384a53307479b3a474389ca149f30a",
    "nutrition-low-energy": "e7f04e7dde264a48927593cebdbd6aad31c94b8f4a0a831bd5f21fe59befa7a2",
    "nutrition-low-fat": "e50a7a508bef5b5f1db45e5196acc6e3b7d5011176549767ada76139674ec756",
    "nutrition-low-saturated-fat": "1b3aa4e66e83f9a73819438f096d33f84aa7d4f8ee3080e0556f89fb12e95edb",
    "nutrition-low-sodium": "a6946ff3112d2dd6e41dde4485da117a90e7b568123a51fb97bd791e37ecd8ae",
    "nutrition-low-sugar": "5331796ebd808e4a1ffc1c77a3b68f69489c008826855eeff698bafa9f439183",
    "nutrition-mass-units": "26695c654002d1fc9f13cc8c8d15a5e2c6aa7f6369a7f44eb82e1d0c3794d15f",
    "nutrition-percentage-calculation": "bacef27b1c973d31263a3f6a5192b58f54a7e287abc4b33a8dfc822791830493",
    "nutrition-zero-energy": "4c776a72e52c0dcd15c9459ad455de4805f8d29cda6823ab80702a75e3a3dc3d",
    "nutrition-zero-rounding": "809f88d875a9b07ce2a1b30374990bb0ab7449710c6f7b37998b0ca30759133f",
    "protein-basics": "6addad19253a533c95d5751775392505399db1989a971534a903d45e3847e170",
    "serving-and-recommendation": "325122ce12516663304a41287083970b5544b3ea9b287685c595f633394b048a",
    "sodium-and-salt": "2003a60a0111ebeeb5fdac5c0cacd43d68a5409f4b6cf1d60cdec253f7d4e576",
    "sodium-and-taste": "22d2d1b473cb64d3b5253f199071ba6f32d280b6905823cd07908c5397ed0604",
    "sodium-practical-choices": "684224d00763483dfe5125759c2ee3f3afde6a465dd76393ecc9a7b312e4f035",
    "sugar-alcohol-basics": "480cfcb791b0271f65d590a22cf41dee04e93e07b445af84fa7b324101400a1e",
    "sugar-no-added-reading": "d66c623c3af07e5dc381e6b2624f38cafb16744fa3afb3ef55980a9fb7eea7de",
    "sugar-sweeteners-calories": "7c94e497550e442533be8a421dcbec74dfde2ecfc078525cfd71adb5075fa875",
    "sugar-zero-vs-added": "b85d31711100c93139b852525a3ed05f890b554655b548dc408d619d4c08ed28",
    "sugars-definition": "361ad13939c8ab2a6ca8dbb7071b46d32be88b7b8ac101ec7bc29abd2797b73f",
    "total-and-added-sugars": "504edbc446555279e1634c3e8638c75a583f75f0616904e856653b7b3fad40c7",
    "vegan-and-allergens": "adee052ac4a0d1701f521a62d26ebf9eb605101a54d6a56b1cbe98c7207dcbdc",
    "vegan-definition": "e833e706eba92681977933231bc13a7b17e786286d9a1b36ea43ed53d2590cba",
    "vegetarian-definition": "8a3e901b8aaca089b8378e96f94ef4db67ac1cc219caa979c350d25a0695998d"
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
    # Bound to the approved excerpt digest, never trusted from vector metadata.
    topics: list[Literal[
        "allergy_purchase", "diet_direction", "sodium_practice",
        "nutrition_basis", "nutrition_percentage", "nutrition_claims", "sugar_claims",
        "sugar_zero", "sugar_energy", "low_sugar", "low_fat", "low_energy",
        "high_protein", "zero_energy",
        "allergy_label", "allergy_facility_warning", "allergy_cross_contact",
        "allergy_contact_controls", "allergy_ordering",
        "carbohydrate_sugars", "fiber_basics", "sugar_alcohol", "fat_types", "protein_basics",
        "sodium_salt", "sodium_taste", "calories_basics", "serving_portion", "sugars_added",
        "vegan_definition", "vegetarian_definition", "vegan_allergy", "gluten_basics", "lactose_free",
        "nutrition_zero", "nutrition_units", "low_sodium", "low_saturated_fat", "high_fiber", "sugar_definition",
        "percentage_calculation", "cholesterol_basics", "date_marking", "haccp_mark",
        "caffeine_label", "ingredient_order", "no_additive_claim",
    ]] = Field(default_factory=list, max_length=64)


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
