"""Offline preparation of the three operator-supplied food sources.

Extraction is NOT approval for RAG, recipe generation, legal interpretation, or
allergy suitability. The resulting bundle deliberately stays outside knowledge/.
No network, database, embeddings, or LLM calls are made here.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import stat
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path


MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_PAGE_CHARS = 100_000
MAX_PAGE_STREAM_BYTES = 8 * 1024 * 1024
MAX_CSV_ROWS = 20_000
MAX_FIELD_CHARS = 20_000
NUTRITION_FIELDS = (
    "INFO_WGT", "INFO_ENG", "INFO_CAR", "INFO_PRO", "INFO_FAT", "INFO_NA",
)
MANUAL_FIELDS = tuple(f"MANUAL{index:02d}" for index in range(1, 21))


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    filename: str
    kind: str
    title: str
    publisher: str
    url: str
    usage_scope: str


SOURCES = (
    SourceSpec(
        "mfds-label-2026-37",
        "☆식품등의+표시기준+고시+전문(고시+제2026-37호，+2026.5.12.).pdf",
        "pdf", "식품등의 표시기준 (고시 제2026-37호, 2026.5.12.)",
        "식품의약품안전처", "https://www.mfds.go.kr/brd/m_211/view.do?seq=14968",
        "알레르기·영양 표시 설명의 검토 자료. 조항별 시행일·부칙·표 원문 확인 필요.",
    ),
    SourceSpec(
        "kdca-food-allergy-management",
        "알레르기 없이 맛있게! 식품알레르기 관리하기.pdf",
        "pdf", "알레르기 없이 맛있게! 식품알레르기 관리하기", "질병관리청",
        "https://health.kdca.go.kr/healthinfo/biz/health/ntcnInfo/healthSourc/"
        "thtimtCntnts/thtimtCntntsView.do?thtimt_cntnts_sn=81",
        "식품알레르기·식품 선택에 관한 일반 설명의 검토 자료. 진단·치료 기능 확장 금지.",
    ),
    SourceSpec(
        "foodsafety-cookrcp01", "COOKRCP01.csv", "csv",
        "조리식품의 레시피 DB (COOKRCP01)", "식품의약품안전처 식품안전나라",
        "https://www.foodsafetykorea.go.kr/api/newDatasetDetail.do?svc_no=COOKRCP01",
        "레시피 검색 후보. 원재료·사용량·조리법 검토와 프로필별 재검사 필요.",
    ),
)

# These are reviewed, selected web-text captures, NOT raw HTML downloads. They
# are distributed separately from the user's three offline import inputs. The
# source_sha256 binds the exact .reference.json capture bytes, not a remote page.
WEB_SOURCES = (
    SourceSpec(
        "kca-allergy-purchase-2018", "kca-allergy-purchase-2018.reference.json", "web",
        "커피전문점 알레르기 유발 식품 주의 필요", "한국소비자원",
        "https://www.kca.go.kr/home/sub.do?menukey=4002&mode=view&no=1002741894",
        "구매 전 알레르기 성분 확인 원칙만 사용. 과거 법령·업체 현황·의료 설명 제외.",
    ),
    SourceSpec(
        "korea-diet-guidelines-2021", "korea-diet-guidelines-2021.reference.json", "web",
        "“덜 짜게, 덜 달게, 덜 기름지게”…한국인을 위한 식생활지침",
        "보건복지부·농림축산식품부·식품의약품안전처 / 대한민국 정책브리핑",
        "https://www.korea.kr/news/policyNewsView.do?newsId=148886239",
        "일반 식생활 방향 중 나트륨·당류·포화지방산 줄이기만 사용. 개인별 수치·법령 기준 아님.",
    ),
    SourceSpec(
        "korea-sodium-practice-2011", "korea-sodium-practice-2011.reference.json", "web",
        "“소금, 하루 5g 미만 섭취 바람직”", "식품의약품안전청 / 대한민국 정책브리핑",
        "https://www.korea.kr/briefing/policyBriefingView.do?newsId=148715048",
        "나트륨 적게 먹기 실천 요령 중 영양표시 확인·양념·외식 주문만 사용. "
        "제목과 본문의 수치, 개인별 권장량·질병 설명·저염 표시 판정 기준 제외.",
    ),
)


def normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def read_source(raw_dir: Path, spec: SourceSpec) -> bytes:
    """Exact NFC filename matching; never follow a source file symlink."""
    if raw_dir.is_symlink() or not raw_dir.is_dir():
        raise ValueError("원본 폴더가 없거나 심볼릭 링크입니다.")
    matches = [
        path for path in raw_dir.iterdir()
        if normalized(path.name) == normalized(spec.filename)
    ]
    if len(matches) != 1:
        raise ValueError(f"원본 파일이 없거나 이름이 중복됩니다: {spec.source_id}")
    path = matches[0]
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("원본 파일의 심볼릭 링크나 특수 파일은 허용하지 않습니다.")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_SOURCE_BYTES:
            raise ValueError("원본은 20 MiB 이하의 비어 있지 않은 일반 파일이어야 합니다.")
        content = handle.read(MAX_SOURCE_BYTES + 1)
    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError("원본 파일 크기 제한을 초과했습니다.")
    return content


def extract_pdf(content: bytes, source_id: str) -> list[dict]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        raise ValueError("PDF 추출에는 requirements.txt의 pypdf 설치가 필요합니다.") from exc
    try:
        reader = PdfReader(io.BytesIO(content))
    except PdfReadError as exc:
        raise ValueError("PDF 구조를 읽을 수 없습니다. 원본을 확인하세요.") from exc
    if reader.is_encrypted:
        raise ValueError("암호화된 PDF는 자동으로 추출하지 않습니다.")
    if not 0 < len(reader.pages) <= MAX_PDF_PAGES:
        raise ValueError("PDF 페이지 수 제한을 초과했습니다.")
    pages = []
    for number, page in enumerate(reader.pages, 1):
        text, error = "", None
        try:
            stream = page.get_contents()
            if stream is not None and len(stream.get_data()) > MAX_PAGE_STREAM_BYTES:
                raise ValueError("page_stream_limit")
            text = normalized(page.extract_text() or "").strip()
            if len(text) > MAX_PAGE_CHARS or "\x00" in text:
                raise ValueError("page_text_limit_or_nul")
        except Exception as exc:
            # Keep the page and failure visible; do not silently lose a clause.
            text, error = "", type(exc).__name__
        pages.append({
            "source_id": source_id,
            "page": number,  # Physical PDF page, not a printed page number.
            "text": text,
            "extraction_status": (
                "error" if error else "text_extracted" if text else "no_text"
            ),
            "error_type": error,
            "review_status": "pending",
        })
    return pages


def extract_recipes(content: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    from app.services.recipe_source_structure import structure_ingredients

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("레시피 CSV는 UTF-8 또는 UTF-8 BOM 형식이어야 합니다.") from exc
    if "\x00" in text:
        raise ValueError("CSV에 NUL 문자가 있습니다.")
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    required = {"RCP_SEQ", "RCP_NM", "RCP_PARTS_DTLS", *MANUAL_FIELDS, *NUTRITION_FIELDS}
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or not required.issubset(headers):
        raise ValueError("COOKRCP01 필수 컬럼이 없거나 컬럼명이 중복됩니다.")
    records = []
    for row_number, row in enumerate(reader, 2):
        if len(records) >= MAX_CSV_ROWS:
            raise ValueError("레시피 CSV 행 수 제한을 초과했습니다.")
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"CSV {row_number}번째 레코드의 컬럼 수가 다릅니다.")
        if any(len(value) > MAX_FIELD_CHARS for value in row.values()):
            raise ValueError("레시피 CSV 필드 길이 제한을 초과했습니다.")
        row = {key: normalized(value.strip()) for key, value in row.items()}
        records.append((row_number, row))
    if not records:
        raise ValueError("CSV에 레시피 레코드가 없습니다.")
    counts = Counter(row["RCP_SEQ"] for _, row in records)
    recipes, rejected = [], []
    for row_number, row in records:
        identifier, name = row["RCP_SEQ"], row["RCP_NM"]
        ingredients = row["RCP_PARTS_DTLS"]
        steps = [
            {"number": index, "text": row[field]}
            for index, field in enumerate(MANUAL_FIELDS, 1) if row[field]
        ]
        issues = []
        if not identifier.isascii() or not identifier.isdigit():
            issues.append("invalid_id")
        if counts[identifier] > 1:
            issues.append("duplicate_id")
        for missing, reason in ((not name, "missing_name"),
                                (not ingredients, "missing_ingredients"),
                                (not steps, "missing_steps")):
            if missing:
                issues.append(reason)
        # Keep freeform amounts, headings, order, and nutrition exactly as data.
        # Commas in parentheses are NOT an ingredient separator we can certify.
        recipe = {
            "source_id": source_id, "record_number": row_number,
            "recipe_id": identifier, "name": name,
            "ingredients_text": ingredients, "steps": steps,
            "method": row.get("RCP_WAY2", ""), "category": row.get("RCP_PAT2", ""),
            "nutrition_raw": {key: row[key] for key in NUTRITION_FIELDS},
            "nutrition_basis": "unverified", "servings": None,
            "review_status": "pending", "issues": issues,
        }
        if ingredients:
            recipe["ingredient_structure"] = structure_ingredients(ingredients, name)
        # RCP_NA_TIP can contain disease claims; do not ingest it as advice.
        (rejected if issues else recipes).append(recipe)
    return recipes, rejected


def prepare_sources(raw_dir: Path) -> dict:
    sources, pages, recipes, rejected = [], [], [], []
    for spec in SOURCES:
        content = read_source(raw_dir, spec)
        sources.append({
            **asdict(spec), "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content), "rights_review": "pending",
            "source_binding": "operator_registered_not_download_verified",
        })
        if spec.kind == "pdf":
            pages.extend(extract_pdf(content, spec.source_id))
        else:
            recipes, rejected = extract_recipes(content, spec.source_id)
    return {
        "format": "food-chatbot-source-preparation", "version": 1,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "pdf_parser": f"pypdf=={version('pypdf')}",
        "status": "review_required", "runtime_enabled": False,
        "sources": sources, "pdf_pages": pages,
        "recipes": recipes, "rejected_recipes": rejected,
    }


def preparation_summary(bundle: dict) -> dict:
    from app.infrastructure.knowledge_review import source_release_status

    pages = bundle["pdf_pages"]
    return {
        "status": bundle["status"], "runtime_enabled": bundle["runtime_enabled"],
        "prepared_at": bundle.get("prepared_at"),
        "pdf_parser": bundle.get("pdf_parser"),
        "pdf_sources": [{
            "source_id": source["source_id"],
            "pages": sum(p["source_id"] == source["source_id"] for p in pages),
            "text_pages": sum(
                p["source_id"] == source["source_id"] and p["extraction_status"] == "text_extracted"
                for p in pages
            ),
            "pages_requiring_visual_review": [
                p["page"] for p in pages if p["source_id"] == source["source_id"]
                and p["extraction_status"] != "text_extracted"
            ],
        } for source in bundle["sources"] if source["kind"] == "pdf"],
        "recipe_candidates": len(bundle["recipes"]),
        "rejected_recipes": [{
            "recipe_id": r["recipe_id"], "name": r["name"], "issues": r["issues"],
        } for r in bundle["rejected_recipes"]],
        "recipes_with_unverified_nutrition_basis": len(bundle["recipes"]),
        "recipe_structure": {
            "with_structure": sum("ingredient_structure" in r for r in bundle["recipes"]),
            "ingredient_lists_with_explicit_amounts": sum(
                bool(r.get("ingredient_structure", {}).get("ingredients"))
                and not (set(r["ingredient_structure"]["issues"]) - {"servings_unconfirmed"})
                for r in bundle["recipes"]
            ),
            "fully_parsed_syntax": sum(
                r.get("ingredient_structure", {}).get("parse_status") == "parsed"
                for r in bundle["recipes"]
            ),
            "recommendation_approved": 0,
        },
        "source_release_reviews": [
            source_release_status(source["source_id"], source["sha256"])
            for source in bundle["sources"]
        ],
        "note": (
            "추출·검색 준비 결과이며, 내용 검수·개인별 적합성·RAG 활성화 완료가 아닙니다."
        ),
    }
