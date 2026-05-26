from typing import Any, Dict, List, Optional, Tuple

from app.core.models import ChatRequest, Product, Profile


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


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    filtered = "".join(char for char in text if char.isdigit() or char in ".-")
    if filtered in ("", "-", ".", "-."):
        return None

    try:
        return float(filtered)
    except ValueError:
        return None


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

    normalized: Dict[str, float] = {}
    nutrition_payload = payload.get("nutrition")
    if isinstance(nutrition_payload, dict):
        source_dicts = [nutrition_payload, payload]
    else:
        source_dicts = [payload]

    for field_name, aliases in nutrition_aliases.items():
        value = None
        for source in source_dicts:
            value = _pick_first(source, aliases)
            if value is not None:
                break
        parsed = _to_float(value)
        if parsed is not None:
            normalized[field_name] = parsed

    return normalized


def normalize_product_payload(raw_product: Optional[Dict[str, Any]]) -> Optional[Product]:
    if not raw_product:
        return None

    payload = dict(raw_product)
    if isinstance(payload.get("data"), dict):
        payload = dict(payload["data"])
    if isinstance(payload.get("product"), dict):
        payload = dict(payload["product"])

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
        weight=_clean_string(_pick_first(payload, ["weight", "식품중량", "SERVING_SIZE"])),
        image_url=_clean_string(_pick_first(payload, ["image_url", "imageUrl", "img_url", "이미지URL"])),
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
        match_confidence=_to_float(_pick_first(payload, ["match_confidence", "similarity_score"])),
        product_found=product_found if product_found is not None else True,
    )


def normalize_profile_payload(raw_profile: Optional[Dict[str, Any]]) -> Optional[Profile]:
    if not raw_profile:
        return None

    payload = dict(raw_profile)
    if isinstance(payload.get("data"), dict):
        payload = dict(payload["data"])

    profile_id = _pick_first(payload, ["profile_id", "id", "user_id", "userId"])
    nickname = _clean_string(_pick_first(payload, ["nickname", "profile_name", "name"]))

    return Profile(
        profile_id=int(profile_id) if profile_id is not None else 0,
        profile_name=nickname or "기본 프로필",
        user_id=_pick_first(payload, ["user_id", "userId", "id"]),
        email=_clean_string(_pick_first(payload, ["email"])),
        nickname=nickname,
        allergies=_split_to_list(_pick_first(payload, ["allergies"])),
        user_allergies=_split_to_list(_pick_first(payload, ["user_allergies", "userAllergies", "allergies", "알레르기리스트"])),
        conditions=_split_to_list(_pick_first(payload, ["conditions", "diseases", "질환정보"])),
        special_diets=_split_to_list(_pick_first(payload, ["special_diets", "specialDiet"])),
        special_diet=_split_to_list(_pick_first(payload, ["special_diet", "specialDiet", "식이습관"])),
        disliked_ingredients=_split_to_list(
            _pick_first(payload, ["disliked_ingredients", "dislikedIngredients", "비선호성분"])
        ),
        target_type=_clean_string(_pick_first(payload, ["target_type", "targetType"])) or "self",
        is_active=bool(_pick_first(payload, ["is_active", "isActive", "active"])) if any(key in payload for key in ["is_active", "isActive", "active"]) else True,
    )


def normalize_profiles_payload(raw_profiles: Optional[List[Dict[str, Any]]]) -> List[Profile]:
    if not raw_profiles:
        return []

    profiles = []
    for raw_profile in raw_profiles:
        profile = normalize_profile_payload(raw_profile)
        if profile:
            profiles.append(profile)
    return profiles


def normalize_active_profiles(req: ChatRequest) -> List[Profile]:
    profiles = list(req.profiles or normalize_profiles_payload(req.raw_profiles))
    primary_profile = req.profile or normalize_profile_payload(req.raw_profile)

    if primary_profile:
        profiles.insert(0, primary_profile)

    active_profiles = []
    seen = set()
    for profile in profiles:
        key = (profile.profile_id, profile.user_id, profile.profile_name)
        if key in seen or not profile.is_active:
            continue
        seen.add(key)
        active_profiles.append(profile)

    return active_profiles


def normalize_chat_request(
    req: ChatRequest,
) -> Tuple[Optional[Profile], Optional[Product], Optional[str], Optional[str]]:
    active_profiles = normalize_active_profiles(req)
    profile = active_profiles[0] if active_profiles else (req.profile or normalize_profile_payload(req.raw_profile))
    product = req.product or normalize_product_payload(req.raw_product)
    barcode = req.barcode or (product.barcode if product else None)
    report_number = req.report_number or (product.report_number if product else None)
    return profile, product, barcode, report_number
