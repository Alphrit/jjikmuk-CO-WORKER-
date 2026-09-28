import re
from typing import List, Optional, Tuple

from app.core.models import ChatResponse, Product, Profile
from app.core.nutrition_basis import has_explicit_nutrition_basis
from app.data.dictionaries import (
    ALLERGEN_CANONICAL_ALIASES,
    ALLERGEN_CHILDREN,
    ALLERGY_MAP,
    INGREDIENT_KEYWORDS,
    SPECIAL_DIET_KEYWORDS,
    SPECIAL_DIET_RESTRICTIONS,
    canonical_special_diet_name,
)
from app.intent.common import TARGET_REFERENCE_KEYWORDS


def get_profile_prefix(profile: Optional[Profile]) -> str:
    if profile and profile.profile_name:
        return f"'{profile.profile_name}' 기준으로 보면"
    return "지금 보이는 정보로는"


def topic_particle(text: str) -> str:
    if not text:
        return "은"

    last_char = text[-1]
    if not ("가" <= last_char <= "힣"):
        return "은"

    has_final_consonant = (ord(last_char) - ord("가")) % 28 != 0
    return "은" if has_final_consonant else "는"


def object_particle(text: str) -> str:
    if not text:
        return "을"

    last_char = text[-1]
    if not ("가" <= last_char <= "힣"):
        return "을"

    has_final_consonant = (ord(last_char) - ord("가")) % 28 != 0
    return "을" if has_final_consonant else "를"


def format_measurement(value: object, unit: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{value}{unit}" if value is not None else f"미확인{unit}"

    if number.is_integer():
        text = str(int(number))
    else:
        text = f"{number:.1f}".rstrip("0").rstrip(".")
    return f"{text}{unit}"


def normalize_items(product: Product) -> List[str]:
    items = []
    items.extend(product.ingredient_text_sources())
    items.extend(product.resolved_allergies())
    return [item.lower() for item in items if item]


_CROSS_CONTAMINATION_MARKERS = [
    "같은 제조시설",
    "같은 제조 시설",
    "같은 제조라인",
    "같은 제조 라인",
    "동일 제조시설",
    "동일 제조 시설",
    "동일 제조라인",
    "동일 제조 라인",
    "제품과 함께 제조",
    "제품과 같은 시설",
    "공용 설비",
    "혼입 가능",
    "혼입될 수",
    "혼입될 가능성",
    "교차오염",
    "교차 오염",
    "may contain",
    "shared facility",
    "traces of",
]

_DIRECT_INCLUSION_MARKERS = [
    "함유",
    "포함",
    "들어",
    "첨가",
]
_MIXED_EVIDENCE_CONNECTOR_PATTERN = re.compile(
    r"""
    (?:
        \s+(?:및|그리고|또한|반면|다만|그러나)\s+
        |하고\s+있(?:고|으며|지만|으나)\s*
        |되어\s+있(?:고|으며|지만|으나)\s*
        |(?:하면서|하지만|되고|되며|되면서|되지만|되나|하고|하며|이고|이며|이지만|있고|있으며|있지만)\s*
        |(?<![a-z])(?:and|but|while)(?![a-z])
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_AMBIGUOUS_SHORT_ALIASES = {"콩", "밀", "게", "잣"}
_ALLERGY_SUFFIX_PATTERN = re.compile(r"(?:알레르기|알러지|allergy)$", re.IGNORECASE)


def _normalize_allergen_label(value: str) -> str:
    normalized = "".join(value.strip().lower().split())
    return _ALLERGY_SUFFIX_PATTERN.sub("", normalized).strip()


def canonical_allergen_name(allergen: str) -> Optional[str]:
    normalized = _normalize_allergen_label(allergen)
    if not normalized:
        return None

    # Exact canonical names always win. A leaf such as ``호두`` or ``피칸``
    # must not become the broader ``견과류`` merely because the parent's
    # directional expansion contains that leaf.
    for canonical in ALLERGY_MAP:
        if normalized == _normalize_allergen_label(canonical):
            return canonical

    parent_names = frozenset(ALLERGEN_CHILDREN)
    ordered_canonicals = [
        canonical
        for canonical in ALLERGEN_CANONICAL_ALIASES
        if canonical not in parent_names
    ] + [
        canonical
        for canonical in ALLERGEN_CANONICAL_ALIASES
        if canonical in parent_names
    ]
    for canonical in ordered_canonicals:
        aliases = ALLERGEN_CANONICAL_ALIASES[canonical]
        if normalized in {
            _normalize_allergen_label(candidate)
            for candidate in aliases
        }:
            return canonical
    return None


def unsupported_profile_allergies(profile: Optional[Profile]) -> List[str]:
    if profile is None:
        return []
    return [
        allergy
        for allergy in profile.resolved_allergies()
        if canonical_allergen_name(allergy) is None
    ]


def aliases_for_allergen(allergen: str) -> List[str]:
    canonical_name = canonical_allergen_name(allergen)
    if canonical_name is not None:
        aliases = [canonical_name, *ALLERGY_MAP[canonical_name]]
        # A generic parent label (for example, "견과류 함유") is safety-
        # relevant to a specific child allergy too, without importing sibling
        # aliases and over-broadening that child's profile.
        for parent, children in ALLERGEN_CHILDREN.items():
            if canonical_name in children:
                aliases.extend(ALLERGEN_CANONICAL_ALIASES[parent])
        return list(dict.fromkeys(aliases))
    return [allergen]


def _alias_in_text(alias: str, text: str) -> bool:
    alias_lower = alias.strip().lower()
    text_lower = text.lower()
    if not alias_lower:
        return False
    if alias_lower.isascii():
        return bool(re.search(
            rf"(?<![a-z0-9]){re.escape(alias_lower)}(?![a-z0-9])",
            text_lower,
        ))
    if alias_lower not in _AMBIGUOUS_SHORT_ALIASES:
        return alias_lower in text_lower

    tokens = [
        token
        for token in re.split(r"[^0-9a-z가-힣]+", text_lower)
        if token
    ]
    return alias_lower in tokens


def _contains_cross_contamination_marker(text: str) -> bool:
    return any(marker in text.lower() for marker in _CROSS_CONTAMINATION_MARKERS)


def _contains_direct_inclusion_marker(text: str) -> bool:
    normalized = text.lower()
    if any(marker in normalized for marker in _DIRECT_INCLUSION_MARKERS):
        return True
    return bool(re.search(r"(?<![a-z])(?:contains?|includes?)(?![a-z])", normalized))


def _split_mixed_allergen_evidence(piece: str) -> List[Tuple[str, bool]]:
    """Separate explicit inclusion from cross-contact text in one unpunctuated clause."""
    piece = piece.strip()
    if not piece:
        return []
    if not _contains_cross_contamination_marker(piece):
        return [(piece, False)]

    for connector in _MIXED_EVIDENCE_CONNECTOR_PATTERN.finditer(piece):
        left = piece[:connector.start()].strip()
        right = piece[connector.end():].strip()
        if not left or not right:
            continue

        left_is_cross = _contains_cross_contamination_marker(left)
        right_is_cross = _contains_cross_contamination_marker(right)
        if (
            _contains_direct_inclusion_marker(left)
            and not left_is_cross
            and right_is_cross
        ):
            return [
                (left, False),
                *_split_mixed_allergen_evidence(right),
            ]
        if (
            left_is_cross
            and _contains_direct_inclusion_marker(right)
            and not right_is_cross
        ):
            return [
                *_split_mixed_allergen_evidence(left),
                (right, False),
            ]

    return [(piece, True)]


def _allergen_evidence_segments(product: Product) -> List[Tuple[str, bool]]:
    raw_items = [
        *product.ingredient_text_sources(),
        *product.resolved_allergies(),
    ]
    if product.allergy_warning:
        raw_items.append(product.allergy_warning)

    segments: List[Tuple[str, bool]] = []
    for item in raw_items:
        for sentence in re.split(r"[.;\n]+", item.lower()):
            pieces = [piece.strip() for piece in re.split(r"[,/]+", sentence) if piece.strip()]
            if not pieces:
                continue
            marker_indexes = [
                index
                for index, piece in enumerate(pieces)
                if _contains_cross_contamination_marker(piece)
            ]
            if not marker_indexes:
                segments.extend((piece, False) for piece in pieces)
                continue

            first_marker_index = min(marker_indexes)
            for index, piece in enumerate(pieces):
                if _contains_cross_contamination_marker(piece):
                    segments.extend(_split_mixed_allergen_evidence(piece))
                elif index < first_marker_index:
                    segments.append((piece, False))
                elif _contains_direct_inclusion_marker(piece):
                    # A direct-inclusion clause can follow a cross-contact clause,
                    # especially in reverse-order comma-separated warnings.
                    segments.append((piece, False))
                else:
                    # Preserve a marker's scope for comma-split continuations such
                    # as "같은 제조시설에서 우유, 대두 사용".
                    segments.append((piece, True))
    return list(dict.fromkeys(segments))


def _find_allergen_aliases(
    product: Product,
    allergen: str,
    *,
    cross_contamination: bool,
) -> List[str]:
    matched = []
    for alias in aliases_for_allergen(allergen):
        for item, is_cross_contamination in _allergen_evidence_segments(product):
            if is_cross_contamination != cross_contamination:
                continue
            if _alias_in_text(alias, item):
                matched.append(alias)
                break
    return list(dict.fromkeys(matched))


def find_matching_aliases(product: Product, allergen: str) -> List[str]:
    return _find_allergen_aliases(
        product,
        allergen,
        cross_contamination=False,
    )


def find_cross_contamination_aliases(product: Product, allergen: str) -> List[str]:
    return _find_allergen_aliases(
        product,
        allergen,
        cross_contamination=True,
    )


def allergy_terms_from_product_name(product: Product) -> List[str]:
    if not product.product_name:
        return []

    name = product.product_name.lower()
    return [
        ingredient
        for ingredient in INGREDIENT_KEYWORDS
        if _alias_in_text(ingredient, name)
    ]


def product_contains_allergen(product: Product, allergen: str) -> bool:
    return len(find_matching_aliases(product, allergen)) > 0


def find_triggered_allergies(profile: Profile, product: Product) -> List[Tuple[str, List[str]]]:
    triggered = []
    for allergy in profile.resolved_allergies():
        matched_aliases = find_matching_aliases(product, allergy)
        if matched_aliases:
            triggered.append((allergy, matched_aliases))
    return triggered


def find_cross_contamination_allergies(
    profile: Profile,
    product: Product,
) -> List[Tuple[str, List[str]]]:
    triggered = []
    for allergy in profile.resolved_allergies():
        matched_aliases = find_cross_contamination_aliases(product, allergy)
        if matched_aliases:
            triggered.append((allergy, matched_aliases))
    return triggered


def has_meaningful_product_data(product: Optional[Product]) -> bool:
    if product is None:
        return False

    return any([
        bool(product.product_name),
        bool(product.ingredient_text_sources()),
        bool(product.resolved_allergies()),
        bool(product.nutrition),
    ])


def has_allergy_label_data(product: Product) -> bool:
    return bool(product.resolved_allergies())


def has_ingredient_data(product: Product) -> bool:
    return bool(product.ingredient_text_sources())


def assess_product_data_confidence(product: Product) -> Tuple[float, List[str]]:
    has_name = bool(product.product_name)
    has_ingredients = has_ingredient_data(product)
    has_allergy_label = has_allergy_label_data(product)
    has_nutrition = bool(product.nutrition)

    score = 0.0
    reasons = []
    if has_name:
        score += 0.1
        reasons.append("제품명 확인")
    if has_ingredients:
        score += 0.45
        reasons.append("원재료명 확인")
    if has_allergy_label:
        score += 0.25
        reasons.append("알레르기 표시 확인")
    if has_nutrition:
        if has_explicit_nutrition_basis(product):
            score += 0.2
            reasons.append("영양성분과 수치 기준 확인")
        else:
            # A raw number without its denominator is useful display data, but
            # it is not complete evidence for high/low interpretation.
            score += 0.1
            reasons.append("영양성분 수치는 있으나 기준량 미확인")

    if not has_ingredients and not has_allergy_label:
        reasons.append("성분/알레르기 근거 부족")
    elif has_ingredients and not has_allergy_label:
        reasons.append("알레르기 표시는 없어 원재료명 기준으로 판단")

    return round(min(score, 1.0), 2), reasons


def format_structured_answer(conclusion: str, reasons: List[str], limitations: List[str], next_steps: List[str]) -> str:
    parts = [conclusion]
    if reasons:
        parts.append(" ".join(reasons))
    if limitations:
        parts.append(" ".join(limitations))
    if next_steps:
        if len(next_steps) == 1:
            parts.append(next_steps[0])
        else:
            parts.append("다음처럼 다시 물어봐 주세요.\n" + "\n".join(f"- {step}" for step in next_steps))
    return "\n".join(parts)


def compose_answer(*parts: Optional[str]) -> str:
    normalized = [part.strip() for part in parts if part and part.strip()]
    return "\n".join(normalized)


def format_can_i_eat_answer(conclusion: str, reasons: List[str], limitations: List[str], next_steps: List[str]) -> str:
    reason_line = " ".join(reasons[:4]) if reasons else ""
    limitation_line = " ".join(limitations[:2]) if limitations else ""
    guidance_line = " ".join(next_steps[:1]) if next_steps else ""
    return compose_answer(conclusion, reason_line, limitation_line, guidance_line)


def format_binary_check_answer(primary: str, evidence: List[str], caveats: List[str], next_steps: List[str]) -> str:
    evidence_line = " ".join(evidence[:2]) if evidence else ""
    caveat_line = " ".join(caveats[:1]) if caveats else ""
    guidance_line = " ".join(next_steps[:1]) if next_steps else ""
    return compose_answer(primary, evidence_line, caveat_line, guidance_line)


def format_nutrition_answer(primary: str, value_line: str, caveats: List[str], next_steps: List[str]) -> str:
    # Nutrition threshold replies may contain several triggered nutrients plus
    # the mandatory internal-heuristic disclaimer. Do not silently truncate it.
    caveat_line = " ".join(caveats[:4]) if caveats else ""
    guidance_line = " ".join(next_steps[:1]) if next_steps else ""
    return compose_answer(primary, value_line, caveat_line, guidance_line)


def format_health_answer(primary: str, reasons: List[str], caveats: List[str], next_steps: List[str]) -> str:
    reason_line = " ".join(reasons[:2]) if reasons else ""
    caveat_line = " ".join(caveats[:2]) if caveats else ""
    guidance_line = " ".join(next_steps[:1]) if next_steps else ""
    return compose_answer(primary, reason_line, caveat_line, guidance_line)


def format_summary_answer(primary: str, details: List[str], closing: List[str]) -> str:
    detail_line = " ".join([detail for detail in details if detail]) if details else ""
    closing_line = " ".join(closing[:1]) if closing else ""
    return compose_answer(primary, detail_line, closing_line)


def format_general_answer(primary: str, details: List[str], caveats: List[str], next_steps: List[str]) -> str:
    detail_line = " ".join(details[:2]) if details else ""
    caveat_line = " ".join(caveats[:1]) if caveats else ""
    guidance_line = " ".join(next_steps[:1]) if next_steps else ""
    return compose_answer(primary, detail_line, caveat_line, guidance_line)


def unsupported_profile_diets(profile: Optional[Profile]) -> List[str]:
    if profile is None:
        return []
    return [
        diet
        for diet in profile.resolved_special_diets()
        if canonical_special_diet_name(diet) is None
    ]


def resolve_profile_diets(profile: Optional[Profile], message: str) -> List[str]:
    resolved = []

    if profile:
        resolved.extend(
            canonical_special_diet_name(diet) or diet
            for diet in profile.resolved_special_diets()
        )

    from app.services.diet_goal_context import read_diet_goal_update
    resolved.extend(read_diet_goal_update(message).apply())

    return list(dict.fromkeys(resolved))


def scan_disliked_ingredients(profile: Optional[Profile], product: Product) -> List[str]:
    if not profile:
        return []

    items = normalize_items(product)
    matched = []
    for ingredient in profile.disliked_ingredients:
        terms = [
            term.strip()
            for term in re.split(r"\s*(?:,|/|및|그리고)\s*", ingredient)
            if term.strip()
        ]
        for term in terms:
            ingredient_lower = term.lower()
            if any(ingredient_lower in item for item in items):
                matched.append(term)
    return list(dict.fromkeys(matched))


def evaluate_special_diets_with_policy(
    profile: Optional[Profile],
    product: Product,
    message: str,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Evaluate diets from ingredient conflicts and explicit label claims only.

    Nutrition numbers are still available to the UI, but this function does not
    turn private service cutoffs into low/high or suitable/unsuitable claims.
    """

    diets = resolve_profile_diets(profile, message)
    items = normalize_items(product)
    allergens = [item.lower() for item in product.resolved_allergies()]
    evidence = "".join(filter(None, [
        product.product_name,
        product.clean_product_name,
        product.rawmtrl_nm,
        product.allergy_warning,
        " ".join(product.resolved_allergies()),
    ])).lower().replace(" ", "")

    hard_conflicts: List[str] = []
    cautions: List[str] = []
    limitations: List[str] = []

    # A user can exclude an ingredient without being vegetarian or allergic.
    # This common evaluator is used for source products, alternatives and all
    # rule/LLM recipe ingredients, so the exclusion cannot vanish downstream.
    exclusions = profile._conversation_exclusions if profile else ()
    excluded_ingredient_matches = {
        field: (find_matching_aliases(product, allergen) if field in exclusions or "채식" in diets else [])
        for field, allergen in (("dairy", "우유"), ("egg", "계란"))
    }
    for field, label in (
        ("dairy", "유제품"), ("egg", "달걀"),
    ):
        if field in exclusions and excluded_ingredient_matches[field]:
            hard_conflicts.append(f"이번 대화에서 제외한 재료({label})가 확인됐어요.")

    unsupported_diets = unsupported_profile_diets(profile)
    if unsupported_diets:
        limitations.append(
            f"현재 지원하지 않는 식이 기준({', '.join(unsupported_diets)})이 있어요."
        )

    claim_aliases = {
        "저염식": ("저염", "저나트륨"),
        "저당식": ("저당", "저당류"),
        "저칼로리": ("저칼로리", "저열량"),
        "저지방": ("저지방",),
        "고단백": ("고단백",),
    }
    for diet in diets:
        restrictions = SPECIAL_DIET_RESTRICTIONS.get(diet)
        if restrictions:
            blocked_ingredients = restrictions.get("blocked_ingredients", ())
            blocked_allergens = restrictions.get("blocked_allergens", ())
            matched_ingredients = [
                term
                for term in blocked_ingredients
                if any(term.lower() in item for item in items)
            ]
            matched_allergens = [
                term
                for term in blocked_allergens
                if any(term.lower() in item for item in allergens)
            ]
            matches = list(dict.fromkeys([
                *matched_ingredients,
                *matched_allergens,
            ]))
            if matches:
                hard_conflicts.append(
                    f"{diet} 기준으로 제한되는 성분({', '.join(matches[:3])})이 확인됐어요."
                )
                continue

        if diet == "글루텐프리" and not (
            "글루텐프리" in evidence or "gluten-free" in evidence
        ):
            limitations.append(
                "밀 성분이 보이지 않는 것만으로 글루텐프리라고 확인할 수 없어요. "
                "제품의 글루텐프리 표시·인증을 확인해 주세요."
            )
        elif diet == "채식":
            for field, label, allowed in (
                ("dairy", "유제품", profile.vegetarian_dairy_allowed if profile else None),
                ("egg", "달걀", profile.vegetarian_egg_allowed if profile else None),
            ):
                if not excluded_ingredient_matches[field]:
                    continue
                if allowed is False:
                    hard_conflicts.append(f"적용 중인 채식 조건에서 제외한 재료({label})가 확인됐어요.")
                elif allowed is None:
                    cautions.append(f"채식 유형에 따라 {label} 허용 여부가 달라 해당 유형을 확인해야 해요.")
        elif diet in claim_aliases and not any(
            alias in evidence for alias in claim_aliases[diet]
        ):
            limitations.append(
                f"{diet} 수치는 표시할 수 있지만, 제품에 해당 특성이 명시되지 않아 "
                "적합하다고 판정하지 않았어요."
            )

    return (
        list(dict.fromkeys(hard_conflicts)),
        list(dict.fromkeys(cautions)),
        list(dict.fromkeys(limitations)),
        [],
    )



def evaluate_special_diets(
    profile: Optional[Profile],
    product: Product,
    message: str,
) -> Tuple[List[str], List[str], List[str]]:
    """Compatibility wrapper for consumers that do not emit a ChatResponse."""

    conflicts, cautions, limitations, _ = evaluate_special_diets_with_policy(
        profile,
        product,
        message,
    )
    return conflicts, cautions, limitations


def message_mentions_other_target(message: str, profile: Optional[Profile]) -> bool:
    text = message.strip()

    if not any(keyword in text for keyword in TARGET_REFERENCE_KEYWORDS):
        return False

    if profile and profile.profile_name and profile.profile_name in text:
        return False

    return True


def infer_task_type(intent: str) -> str:
    if intent.startswith("general_"):
        return "general_chat"
    return "product_chat"


def build_response(
    intent: str,
    answer: str,
    risk_level: str,
    reasons: List[str],
    profile: Optional[Profile],
    task_type: Optional[str] = None,
    answer_source: str = "rules",
    pipeline_stage: str = "rules",
    recommended_questions: Optional[List[str]] = None,
    confidence: Optional[float] = None,
    confidence_reasons: Optional[List[str]] = None,
    assessment_status: Optional[str] = None,
) -> ChatResponse:
    return ChatResponse(
        task_type=task_type or infer_task_type(intent),
        answer_source=answer_source,
        pipeline_stage=pipeline_stage,
        intent=intent,
        answer=answer,
        risk_level=risk_level,
        assessment_status=assessment_status or ("incomplete" if risk_level == "unknown" else "complete"),
        confidence=confidence,
        confidence_reasons=confidence_reasons or [],
        reasons=reasons,
        profile_name=profile.profile_name if profile else None,
        recommended_questions=recommended_questions or [],
    )
