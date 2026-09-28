from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.models import ChatRequest, Product, ProductLabelProvenance, Profile
from app.core.numeric import parse_non_negative_number
from app.services.profile_context import dedupe_profiles


def _clean_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _split_to_list(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = str(value).strip()
    if not text:
        return []

    normalized = text.replace("/", ",").replace("|", ",").replace(";", ",")
    items = [item.strip() for item in normalized.split(",")]
    return [item for item in items if item]


def _pick_first(payload: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _to_float(
    value: Any,
    *,
    expected_unit: Optional[str] = None,
) -> Optional[float]:
    return parse_non_negative_number(value, expected_unit=expected_unit)


def _to_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    text = str(value).strip().lower()
    if text in {"true", "t", "yes", "y", "1", "on"}:
        return True
    if text in {"false", "f", "no", "n", "0", "off"}:
        return False
    return None


def _normalize_nutrition(payload: Dict[str, Any]) -> Dict[str, float]:
    nutrition_aliases = {
        "kcal": ["kcal", "energy", "energy_kcal", "energyKcal", "에너지", "열량"],
        "protein": ["protein", "protein_g", "proteinG", "단백질"],
        "fat": ["fat", "fat_g", "fatG", "지방"],
        "carbohydrate": ["carbohydrate", "carbs_g", "carbsG", "탄수화물"],
        "sugars": ["sugars", "sugar", "sugar_g", "sugarG", "당류", "당"],
        "sodium": ["sodium", "sodium_mg", "sodiumMg", "나트륨"],
        "saturated_fat": ["saturated_fat", "saturatedFat", "포화지방"],
        "cholesterol": ["cholesterol", "cholesterol_mg", "cholesterolMg", "콜레스테롤"],
    }
    nutrition_units = {
        "kcal": "kcal",
        "protein": "g",
        "fat": "g",
        "carbohydrate": "g",
        "sugars": "g",
        "sodium": "mg",
        "saturated_fat": "g",
        "cholesterol": "mg",
    }

    normalized: Dict[str, float] = {}
    nutrition_payload = payload.get("nutrition")
    if isinstance(nutrition_payload, dict):
        source_dicts = [nutrition_payload, payload]
    else:
        source_dicts = [payload]

    for field_name, aliases in nutrition_aliases.items():
        parsed = None
        for source in source_dicts:
            value = _pick_first(source, aliases)
            parsed = _to_float(
                value,
                expected_unit=nutrition_units[field_name],
            )
            if parsed is not None:
                break
        if parsed is not None:
            normalized[field_name] = parsed

    return normalized


def _normalize_explicit_nutrition_basis(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only basis metadata explicitly supplied by the data source.

    ``SERVING_SIZE`` describes a nutrition denominator, not generic product
    weight.  A size alone also does not prove that every nutrient is reported
    per serving, so this adapter deliberately leaves ``basis_type`` unknown
    unless the source declares it.
    """

    nested = _pick_first(payload, ["nutrition_basis", "nutritionBasis"])
    basis = dict(nested) if isinstance(nested, dict) else {}
    flat_fields = {
        "basis_type": _pick_first(
            payload,
            [
                "nutrition_basis_type",
                "nutritionBasisType",
                "measurement_basis",
                "measurementBasis",
                "NUTRITION_BASIS_TYPE",
            ],
        ),
        "serving_size": _pick_first(
            payload,
            ["serving_size", "servingSize", "SERVING_SIZE"],
        ),
        "serving_size_value": _pick_first(
            payload,
            ["serving_size_value", "servingSizeValue"],
        ),
        "serving_size_unit": _pick_first(
            payload,
            ["serving_size_unit", "servingSizeUnit"],
        ),
        "package_size": _pick_first(
            payload,
            ["package_size", "packageSize"],
        ),
        "package_size_value": _pick_first(
            payload,
            ["package_size_value", "packageSizeValue"],
        ),
        "package_size_unit": _pick_first(
            payload,
            ["package_size_unit", "packageSizeUnit"],
        ),
        "servings_per_package": _pick_first(
            payload,
            [
                "servings_per_package",
                "servingsPerPackage",
                "servings_per_container",
                "servingsPerContainer",
            ],
        ),
        "basis_source": _pick_first(
            payload,
            ["nutrition_basis_source", "nutritionBasisSource"],
        ),
    }
    for key, value in flat_fields.items():
        if value is not None and key not in basis:
            basis[key] = value
    return basis


def _normalize_label_provenance(
    payload: Dict[str, Any],
    *,
    assume_database_utc: bool = False,
) -> Dict[str, Any]:
    """Preserve only explicitly supplied source observations and revisions.

    The legacy free-form ``source`` column is intentionally not promoted into
    this contract, and no request/lookup timestamp is generated here.
    """

    nested = _pick_first(
        payload,
        [
            "label_provenance",
            "product_label_provenance",
            "productLabelProvenance",
        ],
    )
    provenance = dict(nested) if isinstance(nested, dict) else {}
    flat_fields = {
        "source_type": _pick_first(
            payload,
            ["label_source_type", "labelSourceType"],
        ),
        "source_id": _pick_first(
            payload,
            ["label_source_id", "labelSourceId"],
        ),
        "source_record_id": _pick_first(
            payload,
            ["label_source_record_id", "labelSourceRecordId"],
        ),
        "label_observed_at": _pick_first(
            payload,
            ["label_observed_at", "labelObservedAt"],
        ),
        "upstream_updated_at": _pick_first(
            payload,
            ["upstream_updated_at", "upstreamUpdatedAt"],
        ),
        "upstream_revision": _pick_first(
            payload,
            ["upstream_revision", "upstreamRevision", "source_revision"],
        ),
        "ingested_content_revision_sha256": _pick_first(
            payload,
            [
                "ingested_content_revision_sha256",
                "ingestedContentRevisionSha256",
                "label_ingested_content_revision_sha256",
            ],
        ),
        "covered_fact_groups": _pick_first(
            payload,
            ["label_covered_fact_groups", "coveredFactGroups"],
        ),
        "lineage_status": _pick_first(
            payload,
            ["label_lineage_status", "lineageStatus"],
        ),
    }
    for key, value in flat_fields.items():
        if value is not None and key not in provenance:
            provenance[key] = value
    if assume_database_utc:
        for field_name in ("label_observed_at", "upstream_updated_at"):
            value = provenance.get(field_name)
            # MySQL DATETIME values are returned without tzinfo. The migration
            # contract stores these particular columns as UTC; attach that
            # declared storage timezone without generating or changing an
            # observation instant. Raw API strings remain strict and must carry
            # their own offset.
            if isinstance(value, datetime) and value.tzinfo is None:
                provenance[field_name] = value.replace(tzinfo=timezone.utc)
        try:
            # Corrupt legacy/catalog metadata must not turn a product lookup
            # into a 500. Preserve the product facts but remove all authority
            # from malformed provenance; Product will expose it unavailable.
            ProductLabelProvenance.model_validate(provenance)
        except (TypeError, ValueError):
            return {}
    return provenance


def normalize_product_payload(
    raw_product: Optional[Dict[str, Any]],
    *,
    repository_record: bool = False,
) -> Optional[Product]:
    if not raw_product:
        return None

    payload = dict(raw_product)
    envelopes = [payload]
    if isinstance(payload.get("data"), dict):
        payload = dict(payload["data"])
        envelopes.append(payload)
    if isinstance(payload.get("product"), dict):
        payload = dict(payload["product"])
        envelopes.append(payload)

    # Some upstream APIs put provenance beside a ``data``/``product`` wrapper.
    # Copy only that typed metadata across the envelope boundary; never let an
    # outer client payload override inner product facts.
    if not _normalize_label_provenance(
        payload,
        assume_database_utc=repository_record,
    ):
        for envelope in reversed(envelopes[:-1]):
            outer_provenance = _normalize_label_provenance(
                envelope,
                assume_database_utc=repository_record,
            )
            if outer_provenance:
                payload["label_provenance"] = outer_provenance
                break

    ingredients = _split_to_list(_pick_first(payload, ["ingredients", "원재료리스트"]))
    rawmtrl_nm = _clean_string(_pick_first(payload, ["rawmtrl_nm", "raw_materials", "rawMaterials", "rawMaterialName", "RAWMTRL_NM", "원재료명"]))
    allergies = _split_to_list(_pick_first(payload, ["allergy", "allergy_info", "allergy_warning", "allergyWarning", "알레르기", "allergy_list"]))
    product_found = _to_bool(_pick_first(payload, ["product_found", "found"]))

    return Product(
        product_id=_pick_first(payload, ["product_id", "id"]),
        product_name=_clean_string(_pick_first(payload, ["product_name", "productName", "식품명", "PRDLST_NM", "food_name"])),
        food_code=_clean_string(_pick_first(payload, ["food_code", "foodCode", "식품코드", "FOOD_CD"])),
        report_number=_clean_string(
            _pick_first(payload, ["report_number", "report_no", "reportNo", "품목제조보고번호", "PRDLST_REPORT_NO"])
        ),
        manufacturer_name=_clean_string(
            _pick_first(payload, ["manufacturer_name", "manufacturer", "manufacturerName", "제조사명", "BSSH_NM"])
        ),
        company_name=_clean_string(_pick_first(payload, ["company_name", "companyName", "업체명", "company"])),
        weight=_clean_string(_pick_first(payload, ["weight", "total_weight", "totalWeight", "식품중량"])),
        image_url=_clean_string(_pick_first(payload, ["image_url", "imageUrl", "img_url", "이미지URL"])),
        clean_product_name=_clean_string(_pick_first(payload, ["clean_product_name", "cleanProductName"])),
        total_weight=_clean_string(_pick_first(payload, ["total_weight", "totalWeight", "식품중량"])),
        category_large=_clean_string(_pick_first(payload, ["category_large", "categoryLarge", "식품대분류"])),
        category_medium=_clean_string(_pick_first(payload, ["category_medium", "categoryMedium", "식품중분류"])),
        category_small=_clean_string(_pick_first(payload, ["category_small", "categorySmall", "식품소분류"])),
        category_detail=_clean_string(_pick_first(payload, ["category_detail", "categoryDetail", "식품세분류"])),
        prdlst_dcnm=_clean_string(_pick_first(payload, ["prdlst_dcnm", "PRDLST_DCNM", "유형명"])),
        barcode=_clean_string(_pick_first(payload, ["barcode", "바코드", "BAR_CD"])),
        rawmtrl_nm=rawmtrl_nm,
        source=_clean_string(_pick_first(payload, ["source", "데이터출처"])),
        nutrient_text=_clean_string(_pick_first(payload, ["nutrient_text", "nutrientText", "영양성분"])),
        allergy_warning=_clean_string(_pick_first(payload, ["allergy_warning", "allergyWarning", "알레르기경고"])),
        allergy=allergies,
        ingredients=ingredients,
        allergy_info=allergies,
        nutrition=_normalize_nutrition(payload),
        nutrition_basis=_normalize_explicit_nutrition_basis(payload),
        label_provenance=_normalize_label_provenance(
            payload,
            assume_database_utc=repository_record,
        ),
        nutrition_percent={
            key: value
            for key, value in {
                "carbohydrate": _to_float(_pick_first(payload, ["carbs_percent", "carbsPercent"]), expected_unit="%"),
                "protein": _to_float(_pick_first(payload, ["protein_percent", "proteinPercent"]), expected_unit="%"),
                "fat": _to_float(_pick_first(payload, ["fat_percent", "fatPercent"]), expected_unit="%"),
                "sodium": _to_float(_pick_first(payload, ["sodium_g", "sodiumG"]), expected_unit="%"),
                "cholesterol": _to_float(_pick_first(payload, ["cholesterol_g", "cholesterolG"]), expected_unit="%"),
            }.items()
            if value is not None
        },
        macro_percent={
            key: value
            for key, value in {
                "carbohydrate": _to_float(_pick_first(payload, ["carbs_percent", "carbsPercent"]), expected_unit="%"),
                "protein": _to_float(_pick_first(payload, ["protein_percent", "proteinPercent"]), expected_unit="%"),
                "fat": _to_float(_pick_first(payload, ["fat_percent", "fatPercent"]), expected_unit="%"),
            }.items()
            if value is not None
        },
        match_confidence=_to_float(_pick_first(payload, ["match_confidence", "similarity_score"])),
        product_found=product_found if product_found is not None else True,
    )


def normalize_profile_payload(raw_profile: Optional[Dict[str, Any]]) -> Optional[Profile]:
    if not raw_profile:
        return None
    return Profile.model_validate(raw_profile)


def normalize_profiles_payload(raw_profiles: Optional[List[Dict[str, Any]]]) -> List[Profile]:
    if not raw_profiles:
        return []

    profiles = []
    for raw_profile in raw_profiles:
        profile = normalize_profile_payload(raw_profile)
        if profile:
            profiles.append(profile)
    return profiles


def normalize_known_profiles(req: ChatRequest) -> List[Profile]:
    profiles: List[Profile] = []
    if req.profile:
        profiles.append(req.profile)
    raw_primary_profile = normalize_profile_payload(req.raw_profile)
    if raw_primary_profile:
        profiles.append(raw_primary_profile)
    profiles.extend(req.profiles)
    profiles.extend(normalize_profiles_payload(req.raw_profiles))

    return profiles


def normalize_active_profiles(req: ChatRequest) -> List[Profile]:
    return dedupe_profiles(normalize_known_profiles(req))


def normalize_chat_request(
    req: ChatRequest,
) -> Tuple[Optional[Profile], Optional[Product], Optional[str], Optional[str]]:
    active_profiles = normalize_active_profiles(req)
    profile = active_profiles[0] if active_profiles else None
    product = req.product or normalize_product_payload(req.raw_product)
    barcode = req.barcode or (product.barcode if product else None)
    report_number = req.report_number or (product.report_number if product else None)
    return profile, product, barcode, report_number
