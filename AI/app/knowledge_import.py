"""Run from AI/: python -m app.knowledge_import prepare (offline only)."""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path

from app.infrastructure.knowledge_sources import prepare_sources, preparation_summary


AI_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RAW_DIR = AI_DIR / "app" / "knowledge" / "raw"
DEFAULT_BUNDLE = AI_DIR / ".local" / "knowledge" / "prepared_sources.json"
MAX_BUNDLE_BYTES = 32 * 1024 * 1024


def load_bundle(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("추출 결과가 없습니다. 먼저 prepare를 실행하세요.")
    with path.open("rb") as handle:
        content = handle.read(MAX_BUNDLE_BYTES + 1)
    if len(content) > MAX_BUNDLE_BYTES:
        raise ValueError("추출 결과 파일 크기 제한을 초과했습니다.")
    bundle = json.loads(content)
    if (
        not isinstance(bundle, dict)
        or bundle.get("format") != "food-chatbot-source-preparation"
        or bundle.get("version") != 1
        or bundle.get("status") != "review_required"
        or bundle.get("runtime_enabled") is not False
        or any(not isinstance(bundle.get(key), list) for key in (
            "sources", "pdf_pages", "recipes", "rejected_recipes",
        ))
    ):
        raise ValueError("지원하지 않는 추출 결과입니다. 다시 prepare를 실행하세요.")
    return bundle


def write_bundle(bundle: dict, destination: Path) -> None:
    """Atomic replacement of our generated file, never a raw PDF/CSV."""
    if destination.suffix != ".json":
        raise ValueError("추출 결과는 JSON 파일이어야 합니다.")
    if destination.is_symlink() or any(parent.is_symlink() for parent in destination.parents):
        raise ValueError("추출 결과 경로에 심볼릭 링크가 있습니다.")
    if destination.exists():
        load_bundle(destination)  # Refuse to replace another file or broken output.
    encoded = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise ValueError("추출 결과 파일 크기 제한을 초과했습니다.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".prepare-", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="식품 원문 자동 추출·로컬 검토 (외부 호출 없음)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="PDF 페이지와 레시피 CSV를 추출합니다.")
    prepare.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    subparsers.add_parser("status", help="로컬 추출 현황을 표시합니다.")
    released = subparsers.add_parser("released", help="실제 RAG에 허용된 공식 자료만 확인합니다. 외부 호출 없음.")
    released.add_argument("--query", help="선택적 본문 키워드 필터 (임베딩/LLM 검색 아님)")
    subparsers.add_parser("released-recipes", help="검토 후 연결한 레시피 후보와 분량·출처를 확인합니다. 외부 호출 없음.")
    recipe_review = subparsers.add_parser("recipe-review", help="레시피 한 건의 재료·조리 단계 검토표를 표시합니다.")
    recipe_review.add_argument("--id", required=True, dest="recipe_id")
    recipe_review.add_argument("--allergy", action="append", default=[], help="가상 알레르기, 반복 지정 가능")
    search = subparsers.add_parser("search", help="추출한 원문을 키워드로 미리 봅니다.")
    search.add_argument("--kind", choices=("pdf", "recipe"), required=True)
    search.add_argument("--query", required=True, help="자연어 질문 대신 짧은 검색어를 사용하세요.")
    search.add_argument("--allergy", action="append", default=[], help="가상 알레르기, 반복 지정 가능")
    search.add_argument("--limit", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            bundle = prepare_sources(args.raw_dir)
            write_bundle(bundle, DEFAULT_BUNDLE)
            result = preparation_summary(bundle)
        elif args.command == "status":
            result = preparation_summary(load_bundle(DEFAULT_BUNDLE))
        elif args.command == "released":
            from app.services.knowledge_preview import preview_released_sources

            result = {
                "mode": "offline_released_source_preview", "llm_called": False,
                "index_built": False, "results": preview_released_sources(args.query),
            }
        elif args.command == "released-recipes":
            from app.services.reviewed_recipes import PORTION_NOTE, reviewed_recipe_variants

            result = {
                "mode": "offline_released_recipe_preview", "llm_called": False,
                "profile_check_required": True, "portion_note": PORTION_NOTE,
                "results": [
                    {key: value for key, value in variant.items() if key != "source_recipe_revision"}
                    for variant in reviewed_recipe_variants()
                ],
            }
        elif args.command == "recipe-review":
            from app.services.knowledge_preview import review_recipe_candidate

            result = {
                "mode": "offline_recipe_review", "runtime_enabled": False,
                "result": review_recipe_candidate(load_bundle(DEFAULT_BUNDLE), args.recipe_id, args.allergy),
            }
        else:
            if args.kind != "recipe" and args.allergy:
                raise ValueError("알레르기 사전 검사는 recipe 검색에서만 사용합니다.")
            from app.services.knowledge_preview import search_prepared_sources

            bundle = load_bundle(DEFAULT_BUNDLE)
            result = {
                "mode": "offline_keyword_preview", "runtime_enabled": False,
                "prepared_at": bundle.get("prepared_at"),
                "results": search_prepared_sources(
                    bundle, args.query, kind=args.kind,
                    allergies=args.allergy, limit=args.limit,
                ),
            }
    except (OSError, ValueError, KeyError, TypeError, StopIteration, RuntimeError, csv.Error) as exc:
        parser.exit(1, f"추출·검색 실패: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
