"""Offline fixtures only: no Downloads, private CSV, database or external AI."""

import csv
import io
import json
import unicodedata
from dataclasses import replace
from types import SimpleNamespace

import pypdf
import pytest

from app import knowledge_import as cli
from app.infrastructure import knowledge_sources as sources
from app.services.knowledge_preview import screen_recipe_text, search_prepared_sources, review_recipe_candidate


HEADERS = [
    "RCP_SEQ", "RCP_NM", "RCP_PARTS_DTLS", *sources.MANUAL_FIELDS,
    *sources.NUTRITION_FIELDS, "RCP_NA_TIP", "RCP_WAY2", "RCP_PAT2",
]


def row(**changes):
    return {
        **dict.fromkeys(HEADERS, ""), "RCP_SEQ": "28", "RCP_NM": "새우 두부 찜",
        "RCP_PARTS_DTLS": "재료\n두부 75g(3/4모), 새우 20g, 생크림 13g(1큰술)",
        "MANUAL01": "1. 두부와 새우를 익힌다.", "MANUAL02": "2. 생크림을 넣는다.",
        "INFO_NA": "99", "RCP_NA_TIP": "질병을 치료한다는 검증되지 않은 문구",
        **changes,
    }


def csv_bytes(*rows):
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=HEADERS)
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue().encode("utf-8-sig")


def blank_pdf(*, encrypted=False):
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt("fixture-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def prepared_fixture(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for spec in sources.SOURCES:
        data = csv_bytes(row()) if spec.kind == "csv" else blank_pdf()
        (raw / unicodedata.normalize("NFD", spec.filename)).write_bytes(data)
    return raw, sources.prepare_sources(raw)


def test_csv_preserves_multiline_ingredients_amounts_and_original_step_numbers():
    original = row(MANUAL02="", MANUAL03="3. 생크림을 넣는다.")
    recipes, rejected = sources.extract_recipes(csv_bytes(original), "foodsafety-cookrcp01")
    assert rejected == []
    recipe = recipes[0]
    assert recipe["ingredients_text"] == original["RCP_PARTS_DTLS"]
    assert [step["number"] for step in recipe["steps"]] == [1, 3]
    assert recipe["nutrition_raw"]["INFO_NA"] == "99"
    assert recipe["nutrition_basis"] == "unverified"
    assert recipe["servings"] is None
    assert recipe["review_status"] == "pending"
    assert "치료" not in json.dumps(recipe, ensure_ascii=False)


@pytest.mark.parametrize(("changes", "issue"), [
    ({"RCP_NM": ""}, "missing_name"),
    ({"RCP_PARTS_DTLS": ""}, "missing_ingredients"),
    ({"MANUAL01": "", "MANUAL02": ""}, "missing_steps"),
    ({"RCP_SEQ": ""}, "invalid_id"),
    ({"RCP_SEQ": "../28"}, "invalid_id"),
    ({"RCP_SEQ": "２８"}, "invalid_id"),
])
def test_invalid_recipe_is_quarantined_not_silently_filled(changes, issue):
    recipes, rejected = sources.extract_recipes(csv_bytes(row(**changes)), "fixture")
    assert recipes == []
    assert issue in rejected[0]["issues"]


def test_both_duplicate_ids_are_quarantined():
    recipes, rejected = sources.extract_recipes(csv_bytes(row(), row(RCP_NM="다른 음식")), "fixture")
    assert not recipes
    assert len(rejected) == 2
    assert all("duplicate_id" in r["issues"] for r in rejected)


@pytest.mark.parametrize("data", [
    b"RCP_SEQ,RCP_NM\n1,example\n", b"\xff\xfe", b"\x00",
    (",".join(HEADERS) + ",RCP_SEQ\n").encode(),
    (",".join(HEADERS) + "\n28,short row\n").encode(),
    (",".join(HEADERS) + "\n" + "," * len(HEADERS) + "extra\n").encode(),
])
def test_bad_csv_is_not_partially_imported(data):
    with pytest.raises(ValueError):
        sources.extract_recipes(data, "fixture")


def test_empty_csv_fails_instead_of_replacing_catalog_with_no_recipes():
    with pytest.raises(ValueError, match="레코드가 없습니다"):
        sources.extract_recipes(csv_bytes(), "fixture")


def test_csv_limits(monkeypatch):
    monkeypatch.setattr(sources, "MAX_CSV_ROWS", 1)
    with pytest.raises(ValueError, match="행 수"):
        sources.extract_recipes(csv_bytes(row(), row(RCP_SEQ="29")), "fixture")
    monkeypatch.setattr(sources, "MAX_FIELD_CHARS", 2)
    with pytest.raises(ValueError, match="필드 길이"):
        sources.extract_recipes(csv_bytes(row()), "fixture")


def test_real_pdf_reader_retains_blank_pages_as_review_required():
    pages = sources.extract_pdf(blank_pdf(), "fixture")
    assert [p["page"] for p in pages] == [1, 2]
    assert all(p["extraction_status"] == "no_text" for p in pages)
    assert all(p["review_status"] == "pending" for p in pages)


def test_pdf_encrypted_malformed_and_page_limit(monkeypatch):
    with pytest.raises(ValueError, match="암호화"):
        sources.extract_pdf(blank_pdf(encrypted=True), "fixture")
    with pytest.raises(ValueError, match="PDF 구조"):
        sources.extract_pdf(b"not a PDF", "fixture")
    monkeypatch.setattr(sources, "MAX_PDF_PAGES", 1)
    with pytest.raises(ValueError, match="페이지 수"):
        sources.extract_pdf(blank_pdf(), "fixture")


def test_one_pdf_page_error_is_visible_and_does_not_shift_page_numbers(monkeypatch):
    def broken():
        raise RuntimeError("private parser detail")

    pages = [
        SimpleNamespace(get_contents=lambda: None, extract_text=lambda: "알레르기 표시"),
        SimpleNamespace(get_contents=lambda: None, extract_text=broken),
        SimpleNamespace(get_contents=lambda: None, extract_text=lambda: "다음 조항"),
    ]
    monkeypatch.setattr(pypdf, "PdfReader", lambda _stream: SimpleNamespace(is_encrypted=False, pages=pages))
    result = sources.extract_pdf(b"fixture", "fixture")
    assert result[0]["text"] == "알레르기 표시"
    assert result[1]["extraction_status"] == "error"
    assert result[1]["error_type"] == "RuntimeError"
    assert result[2]["page"] == 3
    assert "private parser detail" not in str(result)


def test_import_preserves_original_hashes_and_unicode_filenames(tmp_path):
    raw, bundle = prepared_fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in raw.iterdir()}
    assert len(bundle["sources"]) == 3
    assert all(len(source["sha256"]) == 64 for source in bundle["sources"])
    assert all(source["rights_review"] == "pending" for source in bundle["sources"])
    assert bundle["runtime_enabled"] is False
    assert bundle["status"] == "review_required"
    summary = sources.preparation_summary(bundle)
    assert summary["recipe_candidates"] == 1
    assert summary["pdf_sources"][0]["pages_requiring_visual_review"] == [1, 2]
    assert before == {p.name: p.read_bytes() for p in raw.iterdir()}


def test_source_missing_symlink_and_oversize_fail_closed(tmp_path, monkeypatch):
    spec = replace(sources.SOURCES[0], filename="source.pdf")
    with pytest.raises(ValueError, match="파일이 없거나"):
        sources.read_source(tmp_path, spec)
    outside = tmp_path / "other.pdf"
    outside.write_bytes(b"private")
    target = tmp_path / spec.filename
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="심볼릭"):
        sources.read_source(tmp_path, spec)
    target.unlink()
    target.write_bytes(b"12345")
    monkeypatch.setattr(sources, "MAX_SOURCE_BYTES", 4)
    with pytest.raises(ValueError, match="20 MiB"):
        sources.read_source(tmp_path, spec)


def test_atomic_output_roundtrip_and_refuses_unrelated_file(tmp_path):
    _, bundle = prepared_fixture(tmp_path)
    output = tmp_path / "generated" / "bundle.json"
    cli.write_bundle(bundle, output)
    assert cli.load_bundle(output) == bundle
    cli.write_bundle(bundle, output)
    assert not list(output.parent.glob(".prepare-*"))
    unrelated = tmp_path / "keep.json"
    unrelated.write_text('{"user": "data"}')
    with pytest.raises(ValueError):
        cli.write_bundle(bundle, unrelated)
    assert unrelated.read_text() == '{"user": "data"}'
    with pytest.raises(ValueError):
        cli.write_bundle(bundle, tmp_path / "original.csv")


def test_failed_import_does_not_replace_previous_output(tmp_path, monkeypatch):
    raw, bundle = prepared_fixture(tmp_path)
    output = tmp_path / "bundle.json"
    cli.write_bundle(bundle, output)
    before = output.read_bytes()
    monkeypatch.setattr(cli, "DEFAULT_BUNDLE", output)
    (raw / "COOKRCP01.csv").write_bytes(b"invalid CSV")
    with pytest.raises(SystemExit) as error:
        cli.main(["prepare", "--raw-dir", str(raw)])
    assert error.value.code == 1
    assert output.read_bytes() == before


def test_recipe_preview_reuses_allergen_checks_but_never_approves(tmp_path):
    _, bundle = prepared_fixture(tmp_path)
    result = search_prepared_sources(
        bundle, "두부", kind="recipe", allergies=["우유", "새우", "바나나"],
    )[0]
    matches = result["precheck"]["allergen_matches"]
    assert {m["allergen"] for m in matches} == {"우유", "새우"}
    assert result["precheck"]["unsupported_allergies"] == ["바나나"]
    assert result["precheck"]["personalized_recommendation"] is False
    assert result["source_url"].startswith("https://www.foodsafetykorea.go.kr/")
    assert "nutrition_raw" not in result
    assert result["servings"] is None
    without_match = screen_recipe_text(bundle["recipes"][0], ["땅콩"])
    assert without_match["allergen_matches"] == []
    assert without_match["status"] == "review_required"
    assert without_match["personalized_recommendation"] is False


def test_recipe_step_allergen_is_checked_even_when_missing_from_list():
    recipes, _ = sources.extract_recipes(csv_bytes(row(
        RCP_PARTS_DTLS="두부 100g", MANUAL01="새우와 섞는다.", MANUAL02="",
    )), "foodsafety-cookrcp01")
    result = screen_recipe_text(recipes[0], ["새우"])
    assert result["allergen_matches"][0]["allergen"] == "새우"


def test_pdf_search_keeps_page_and_official_reference_without_network(tmp_path):
    _, bundle = prepared_fixture(tmp_path)
    bundle["pdf_pages"][1].update(text="알레르기 표시 원문", extraction_status="text_extracted")
    result = search_prepared_sources(bundle, "알레르기", kind="pdf")[0]
    assert result["page"] == 2
    assert result["source_url"] == sources.SOURCES[0].url
    assert result["text"] == "알레르기 표시 원문"
    assert result["review_status"] == "pending"
    assert search_prepared_sources(bundle, "일치하지않는검색어", kind="pdf") == []


@pytest.mark.parametrize(("query", "kind", "limit"), [
    ("", "pdf", 3), ("a" * 201, "pdf", 3), ("a", "sql", 3),
    ("a", "pdf", 0), ("a", "pdf", 21), ("a", "pdf", True),
])
def test_preview_bounds(query, kind, limit):
    with pytest.raises(ValueError):
        search_prepared_sources({}, query, kind=kind, limit=limit)


def test_prepare_and_status_cli_do_not_need_external_settings(tmp_path, monkeypatch, capsys):
    raw, _ = prepared_fixture(tmp_path)
    output = tmp_path / "prepared.json"
    monkeypatch.setattr(cli, "DEFAULT_BUNDLE", output)
    assert cli.main(["prepare", "--raw-dir", str(raw)]) == 0
    assert json.loads(capsys.readouterr().out)["runtime_enabled"] is False
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["recipe_candidates"] == 1
    assert cli.main(["search", "--kind", "recipe", "--query", "두부", "--allergy", "우유"]) == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["precheck"]["allergen_matches"]


def test_raw_documents_never_enter_internal_policy_rag(tmp_path):
    from app.infrastructure import rag

    (tmp_path / "policy.md").write_text("검토된 내부 규칙", encoding="utf-8")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "unreviewed.md").write_text("등록되지 않은 외부 원문", encoding="utf-8")
    assert [p.name for p in rag._list_knowledge_documents(tmp_path)] == ["policy.md"]
    assert [entry["path"] for entry in rag._build_document_manifest(tmp_path)] == ["policy.md"]


def review_bundle(*records):
    content = csv_bytes(*records)
    recipes, rejected = sources.extract_recipes(content, "foodsafety-cookrcp01")
    return {
        "recipes": recipes, "rejected_recipes": rejected,
        "sources": [{"source_id": "foodsafety-cookrcp01", "sha256": "a" * 64}],
    }


def test_review_preserves_raw_quantities_and_unconfirmed_servings():
    bundle = review_bundle(row())
    result = review_recipe_candidate(bundle, "28", ["우유", "새우"])
    assert result["ingredient_structure"]["servings_explicit"] is None
    assert "servings_unconfirmed" in result["ingredient_structure"]["issues"]
    assert result["ingredients_text"] == row()["RCP_PARTS_DTLS"]
    assert "3/4모" in result["ingredient_structure"]["ingredients"][0]["amount_text"]
    assert result["nutrition_basis"] == "unverified"
    assert not result["recommendation_eligible"]
    assert not result["source_release"]["runtime_allowed"]
    assert {m["allergen"] for m in result["precheck"]["allergen_matches"]} == {"우유", "새우"}


def test_review_looks_for_step_only_additions_and_allergens():
    result = review_recipe_candidate(review_bundle(row(
        RCP_PARTS_DTLS="[1인분] 두부 100g", MANUAL01="새우를 넣고 섞는다.", MANUAL02="",
    )), "28", ["새우"])
    assert result["steps"][0]["possible_unlisted_mentions"] == ["새우"]
    assert result["precheck"]["allergen_matches"][0]["allergen"] == "새우"
    assert result["ingredient_structure"]["servings_explicit"] == 1
    assert not result["recommendation_eligible"]


def test_review_does_not_approve_clean_syntax_or_fill_missing_nutrition():
    result = review_recipe_candidate(review_bundle(row(
        RCP_PARTS_DTLS="[1인분] 두부 100g", MANUAL01="두부를 찐다.", MANUAL02="",
    )), "28", ["땅콩"])
    assert result["ingredient_structure"]["issues"] == []
    assert result["precheck"]["allergen_matches"] == []
    assert result["steps"][0]["listed_names_found"] == ["두부"]
    assert result["steps"][0]["possible_unlisted_mentions"] == []
    assert result["review_status"] == "pending"
    assert not result["recommendation_eligible"]
    assert "nutrition_raw" not in result


def test_review_recomputes_instead_of_trusting_cached_structure():
    bundle = review_bundle(row(RCP_PARTS_DTLS="두부 100g, 참깨 약간, 알 수 없는 재료"))
    bundle["recipes"][0]["ingredient_structure"] = {"recommendation_eligible": True}
    result = review_recipe_candidate(bundle, "28")
    assert not result["ingredient_structure"]["recommendation_eligible"]
    assert "vague_amount" in result["ingredient_structure"]["issues"]
    assert result["ingredient_structure"]["unparsed"] == ["알 수 없는 재료"]


@pytest.mark.parametrize("step", ["안전한 레시피입니다.", "혈당을 낮춥니다.", "질병을 치료합니다."])
def test_recipe_review_flags_claims_without_rewriting_original(step):
    result = review_recipe_candidate(review_bundle(row(MANUAL01=step)), "28")
    assert result["steps"][0]["claim_review_required"]
    assert result["steps"][0]["text"] == step


@pytest.mark.parametrize("missing", ["ingredients", "steps"])
def test_rejected_recipe_stays_available_for_review_not_recommendation(missing):
    changes = {"RCP_PARTS_DTLS": ""} if missing == "ingredients" else {"MANUAL01": "", "MANUAL02": ""}
    result = review_recipe_candidate(review_bundle(row(**changes)), "28")
    assert f"missing_{missing}" in result["import_issues"]
    assert result["recommendation_eligible"] is False


def test_recipe_review_keeps_nonconsecutive_step_numbers_visible():
    result = review_recipe_candidate(review_bundle(row(MANUAL02="", MANUAL03="3. 두부를 담는다.")), "28")
    assert result["step_number_gap"]
    assert [step["number"] for step in result["steps"]] == [1, 3]


@pytest.mark.parametrize("identifier", ["", "-1", "２８", "../28", 28, True, "999999"])
def test_recipe_review_rejects_invalid_or_unknown_ids(identifier):
    with pytest.raises(ValueError):
        review_recipe_candidate(review_bundle(row()), identifier)


def test_recipe_review_does_not_guess_which_duplicate_id_to_use():
    with pytest.raises(ValueError, match="중복"):
        review_recipe_candidate(review_bundle(row(), row()), "28")


def test_recipe_review_cli_is_offline_and_leaves_preparation_untouched(tmp_path, monkeypatch, capsys):
    _, bundle = prepared_fixture(tmp_path)
    output = tmp_path / "bundle.json"
    cli.write_bundle(bundle, output)
    before = output.read_bytes()
    monkeypatch.setattr(cli, "DEFAULT_BUNDLE", output)
    assert cli.main(["recipe-review", "--id", "28", "--allergy", "우유"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "offline_recipe_review"
    assert not result["runtime_enabled"]
    assert not result["result"]["recommendation_eligible"]
    assert output.read_bytes() == before
