from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _ensure_list(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = str(value).strip()
    if not text:
        return []

    normalized = text.replace("/", ",").replace("|", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _pick_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            continue
        return value
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


class Profile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    profile_id: int = 0
    profile_name: str = "기본 프로필"
    user_id: Optional[int] = None
    email: Optional[str] = None
    nickname: Optional[str] = None
    allergies: List[str] = Field(default_factory=list)
    user_allergies: List[str] = Field(default_factory=list)
    conditions: List[str] = Field(default_factory=list)
    special_diets: List[str] = Field(default_factory=list)
    special_diet: List[str] = Field(default_factory=list)
    disliked_ingredients: List[str] = Field(default_factory=list)
    target_type: str = "self"
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_profile_input(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        if isinstance(payload.get("data"), dict):
            payload = dict(payload["data"])

        profile_name = _pick_non_empty(
            payload.get("profile_name"),
            payload.get("nickname"),
            payload.get("name"),
            "기본 프로필",
        )
        profile_id = _pick_non_empty(
            payload.get("profile_id"),
            payload.get("id"),
            payload.get("user_id"),
            payload.get("userId"),
            0,
        )
        user_id = _pick_non_empty(
            payload.get("user_id"),
            payload.get("userId"),
            payload.get("id"),
        )
        diseases = _pick_non_empty(payload.get("conditions"), payload.get("diseases"))
        special_diets = _pick_non_empty(
            payload.get("special_diets"),
            payload.get("specialDiet"),
            payload.get("special_diet"),
        )
        disliked_ingredients = _pick_non_empty(
            payload.get("disliked_ingredients"),
            payload.get("dislikedIngredients"),
        )
        allergies = payload.get("allergies")
        user_allergies = _pick_non_empty(
            payload.get("user_allergies"),
            payload.get("userAllergies"),
            allergies,
        )
        target_type = _pick_non_empty(payload.get("target_type"), payload.get("targetType"), "self")
        is_active = _pick_non_empty(payload.get("is_active"), payload.get("isActive"), payload.get("active"), True)

        payload["profile_id"] = int(profile_id) if profile_id not in (None, "") else 0
        payload["profile_name"] = str(profile_name).strip() or "기본 프로필"
        payload["user_id"] = int(user_id) if user_id not in (None, "") else None
        payload["nickname"] = payload.get("nickname") or payload["profile_name"]
        payload["conditions"] = _ensure_list(diseases)
        payload["special_diets"] = _ensure_list(special_diets)
        payload["special_diet"] = _ensure_list(special_diets)
        payload["disliked_ingredients"] = _ensure_list(disliked_ingredients)
        payload["allergies"] = _ensure_list(allergies)
        payload["user_allergies"] = _ensure_list(user_allergies)
        payload["target_type"] = str(target_type).strip() if target_type is not None else "self"
        payload["is_active"] = bool(is_active)
        return payload

    def resolved_allergies(self) -> List[str]:
        return list(dict.fromkeys([
            *self.allergies,
            *self.user_allergies,
        ]))

    def resolved_special_diets(self) -> List[str]:
        return list(dict.fromkeys([
            *self.special_diets,
            *self.special_diet,
        ]))


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    product_id: Optional[int] = None
    product_name: Optional[str] = None
    food_code: Optional[str] = None
    report_number: Optional[str] = None
    manufacturer_name: Optional[str] = None
    company_name: Optional[str] = None
    source: Optional[str] = None
    nutrient_text: Optional[str] = None
    allergy_warning: Optional[str] = None
    weight: Optional[str] = None
    image_url: Optional[str] = None
    category_large: Optional[str] = None
    category_medium: Optional[str] = None
    category_small: Optional[str] = None
    category_detail: Optional[str] = None
    prdlst_dcnm: Optional[str] = None
    barcode: Optional[str] = None
    rawmtrl_nm: Optional[str] = None
    allergy: List[str] = Field(default_factory=list)
    ingredients: List[str] = Field(default_factory=list)
    allergy_info: List[str] = Field(default_factory=list)
    nutrition: Dict[str, float] = Field(default_factory=dict)
    match_confidence: Optional[float] = None
    product_found: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_product_input(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        if isinstance(payload.get("data"), dict):
            payload = dict(payload["data"])
        if isinstance(payload.get("product"), dict):
            payload = dict(payload["product"])

        allergy_value = payload.get("allergy", payload.get("allergy_info"))
        payload["product_name"] = _pick_non_empty(
            payload.get("product_name"),
            payload.get("productName"),
            payload.get("식품명"),
            payload.get("PRDLST_NM"),
        )
        payload["report_number"] = _pick_non_empty(
            payload.get("report_number"),
            payload.get("report_no"),
            payload.get("reportNo"),
            payload.get("품목제조보고번호"),
            payload.get("PRDLST_REPORT_NO"),
        )
        payload["food_code"] = _pick_non_empty(
            payload.get("food_code"),
            payload.get("foodCode"),
            payload.get("식품코드"),
        )
        payload["rawmtrl_nm"] = _pick_non_empty(
            payload.get("rawmtrl_nm"),
            payload.get("raw_materials"),
            payload.get("rawMaterials"),
            payload.get("rawMaterialName"),
            payload.get("RAWMTRL_NM"),
            payload.get("원재료명"),
        )
        payload["manufacturer_name"] = _pick_non_empty(
            payload.get("manufacturer_name"),
            payload.get("manufacturer"),
            payload.get("manufacturerName"),
            payload.get("제조사명"),
            payload.get("BSSH_NM"),
        )
        payload["source"] = _pick_non_empty(payload.get("source"), payload.get("데이터출처"))
        payload["nutrient_text"] = _pick_non_empty(payload.get("nutrient_text"), payload.get("nutrientText"), payload.get("영양성분"))
        payload["allergy_warning"] = _pick_non_empty(payload.get("allergy_warning"), payload.get("allergyWarning"), payload.get("알레르기경고"))
        payload["allergy"] = _ensure_list(allergy_value)
        payload["allergy_info"] = _ensure_list(_pick_non_empty(payload.get("allergy_info"), payload.get("allergy_warning"), payload.get("allergyWarning"), allergy_value))
        nutrition = dict(payload.get("nutrition") or {})
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
        for field_name, aliases in nutrition_aliases.items():
            if field_name in nutrition and nutrition[field_name] not in (None, ""):
                continue
            raw_nutrition_value = _pick_non_empty(*(payload.get(alias) for alias in aliases))
            parsed = _to_float(raw_nutrition_value)
            if parsed is not None:
                nutrition[field_name] = parsed
        payload["nutrition"] = nutrition
        if "found" in payload and "product_found" not in payload:
            product_found = _to_bool(payload.get("found"))
            if product_found is not None:
                payload["product_found"] = product_found
        return payload

    def resolved_allergies(self) -> List[str]:
        return list(dict.fromkeys([
            *self.allergy_info,
            *self.allergy,
        ]))

    def ingredient_text_sources(self) -> List[str]:
        items = list(self.ingredients)
        if self.rawmtrl_nm:
            items.append(self.rawmtrl_nm)
        return [item for item in items if item]


class ChatHistoryItem(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    profile: Optional[Profile] = None
    profiles: List[Profile] = Field(default_factory=list)
    product: Optional[Product] = None
    raw_profile: Optional[Dict[str, Any]] = None
    raw_profiles: List[Dict[str, Any]] = Field(default_factory=list)
    raw_product: Optional[Dict[str, Any]] = None
    barcode: Optional[str] = None
    report_number: Optional[str] = None
    conversation_state: Dict[str, Any] = Field(default_factory=dict)
    chat_history: List[ChatHistoryItem] = Field(default_factory=list)
    message: str


class ChatResponse(BaseModel):
    task_type: str = "general_chat"
    answer_source: str = "rules"
    pipeline_stage: str = "rules"
    intent: str
    answer: str
    risk_level: str
    reasons: List[str] = Field(default_factory=list)
    profile_name: Optional[str] = None
    active_profile_names: List[str] = Field(default_factory=list)
    per_profile_results: List[Dict[str, Any]] = Field(default_factory=list)
    current_product: Optional[Dict[str, Any]] = None
    conversation_state: Dict[str, Any] = Field(default_factory=dict)
    recommended_questions: List[str] = Field(default_factory=list)
    recommended_products: List[Dict[str, Any]] = Field(default_factory=list)


class ActivityRecord(BaseModel):
    user_id: Optional[int] = None
    barcode: Optional[str] = None
    report_number: Optional[str] = None
    product_id: Optional[int] = None
    action_type: str = "chat"
    message: Optional[str] = None
    intent: Optional[str] = None
    created_at: Optional[str] = None
