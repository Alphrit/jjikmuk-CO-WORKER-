import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import List, Optional, Tuple

from app.infrastructure.llm import generate_text, is_llm_enabled
from app.core.models import ChatHistoryItem, ChatResponse, Product, Profile
from app.core.product_provenance import label_provenance_supports
from app.data.dictionaries import ALLERGY_MAP, INGREDIENT_KEYWORDS
from app.prompts.builder import (
    build_general_fallback_prompt,
)
from app.infrastructure.rag import get_rag_context_entries, official_citation_from_entry
from app.services.rag_grounding import (
    build_grounding_review_prompt,
    has_opposite_reviewed_statement,
    requested_topics,
    reviewed_generated_entries,
    select_reviewed_entries,
)
from app.intent.explanation_topics import resolve_explanation_question


_UNSUPPORTED_CLAIM_PATTERNS = [
    r"100\s*%.*(안전|괜찮)",
    r"무조건\s*(안전|괜찮|먹어도)",
    r"마음껏\s*먹어도",
]

_UNSUPPORTED_PRODUCT_FRESHNESS_PATTERNS = [
    r"최신(?:의|한|\s)*(?:라벨|표시|정보|데이터|성분|영양)",
    r"현재\s*판매(?:되는|\s*중인)?\s*(?:라벨|제품|정보)",
    r"가장\s*최근(?:의|한|\s)*(?:라벨|표시|정보|데이터)",
]

_UNTRACEABLE_LABEL_ABSENCE_PATTERNS = [
    r"(원재료|성분|알레르기|알러지).{0,18}(없|보이지\s*않|확인되지\s*않|포함되지\s*않)",
    r"(포함|들어\s*있).{0,14}(않|없)",
    r"충돌.{0,12}(없|하지\s*않)",
    r"(무첨가|불검출)",
    r"(원재료|성분|알레르기|알러지|우유|계란|달걀|땅콩|견과|밀|대두)"
    r".{0,12}(프리|free|제로|제외)",
]

_MEDICAL_EFFECT_CLAIM_PATTERNS = [
    r"(치료|완치|예방)(하|해|되|됩|할|한다|합니다|효과|에|\s|$)",
    r"(낫게\s*해|낫는다|낫습니다)",
    r"(개선|효능).{0,12}(있|좋|높|도움|해\s*주|됩|된다|시켜|보)",
    r"(면역력|혈압|혈당|콜레스테롤|체중|체지방|지방간).{0,20}"
    r"(개선|강화|높여|높이|증진|낮춰|낮추|떨어뜨려|내려|조절|감량|감소)",
    r"(감량|감소|낮춰|내려).{0,10}(효과|도움|줍|됩)",
]

# Unsupported health-effect claims are validated by semantic families, not only
# by a list of complete phrases. This catches novel combinations such as
# "신장 기능 + 회복" while keeping denial scoped to the same clause.
_MEDICAL_TARGET_TERMS = (
    "암",
    "당뇨",
    "고혈압",
    "저혈압",
    "혈압",
    "혈당",
    "인슐린",
    "신장",
    "콩팥",
    "간 기능",
    "지방간",
    "심장",
    "심혈관",
    "콜레스테롤",
    "면역력",
    "체중",
    "체지방",
    "비만",
    "질환",
    "질병",
    "증상",
)

_MEDICAL_EFFECT_TERMS = (
    "치료",
    "완치",
    "치유",
    "특효",
    "예방",
    "회복",
    "정상화",
    "개선",
    "완화",
    "억제",
    "해독",
    "강화",
    "증진",
    "높여",
    "높이",
    "감량",
    "감소",
    "낮춰",
    "낮추",
    "떨어뜨",
    "내려",
    "조절",
    "낫",
    "효능",
    "효과",
    "효과적",
    "탁월",
    "도움",
    "좋",
)

_STRONG_MEDICAL_EFFECT_TERMS = tuple(
    term
    for term in _MEDICAL_EFFECT_TERMS
    if term not in {"좋", "도움", "효과", "효과적"}
)

_MEDICAL_TARGET_PATTERN = "|".join(
    re.escape(term)
    for term in sorted(_MEDICAL_TARGET_TERMS, key=len, reverse=True)
)
_WEAK_MEDICAL_EFFECT_CLAIM_PATTERNS = [
    # Weak words such as "좋다" are medical claims only when they are close
    # to the condition target. This avoids treating guidance such as
    # "혈당 관리에서는 당류를 같이 보는 편이 좋아요" as a product efficacy claim.
    rf"(?:{_MEDICAL_TARGET_PATTERN}).{{0,8}}(좋|도움|효과적|효과가\s*있)",
    rf"(좋|도움|효과적).{{0,8}}(?:{_MEDICAL_TARGET_PATTERN})",
]

_TRANSIENT_SYMPTOM_WARNING_PATTERNS = [
    # Safety guidance may warn that apparent improvement can be temporary.
    # This is the opposite of a product efficacy claim and must not be
    # rejected merely because it contains both "증상" and "좋아지다".
    r"증상(?:이|은|는|도)?\s*.{0,6}(?:좋아지|좋아져|호전).{0,24}"
    r"(?:다시\s*)?(?:악화|나빠지|재발)(?:될|할)?\s*수\s*있",
    r"증상(?:이|은|는|도)?\s*.{0,6}(?:좋아지|좋아져|호전).{0,24}"
    r"(?:응급\s*)?(?:평가|진료|대응).{0,12}(?:미루지\s*마|받)",
]

_STANDALONE_MEDICAL_EFFECT_PATTERNS = [
    r"(치료|완치|치유|예방|특효)(약|제|하|해|되|됩|할|한다|합니다|효과|에|\s|$)",
    r"(개선|회복|정상화|완화|강화|증진|감량|감소|조절).{0,12}"
    r"(효과|효능|도움|시키|해\s*주|됩|된다|합니다|해요|해줌)",
]

_MEDICAL_EFFECT_DENIAL_PATTERNS = [
    r"(?:진단|치료).{0,48}(?:지원|제공|취급|다루).{0,8}(?:하지\s*않|불가|못)",
    r"(?:진단|치료).{0,24}(?:안내|기능|서비스)?(?:은|는|을|를|이|가)?\s*"
    r"(?:지원하지|제공하지|안내하지|하지)\s*않",
    r"(치료|완치|예방|낫|개선|효능|효과|감량|감소|높여|높이|증진|낮춰|낮추|떨어뜨려|내려|조절|효과적|좋).{0,8}"
    r"(한다고|된다고|준다고|다고|라고|인지|여부(?:는|를|가)?|가능성(?:은|을|이)?|\s*효과(?:는|를|가|이)?).{0,12}"
    r"(단정|판단|확인|말하|안내).{0,12}(수\s*없|어렵)",
    r"(치료|완치|예방|낫|개선|효능|효과|감량|감소|높여|높이|증진|낮춰|낮추|떨어뜨려|내려|조절|효과적|좋).{0,16}"
    r"(아니|없|어렵|확인되지\s*않)",
    r"(치료|완치|예방|개선)(하|되|시키).{0,6}(않|못)",
]

_REASSURING_CONCLUSION_PATTERNS = [
    r"안전(합니다|해요|하다|한\s*(편|제품)|해\s*보|하다고)",
    r"(먹어도|섭취해도|드셔도).{0,10}(괜찮|문제\s*없|됩니다|돼요|가능)",
    r"(먹을|섭취할)\s*수(?:는)?\s*있",
    r"섭취(?:가|는|도)?\s*가능",
    r"안심하고",
    r"(문제|위험)(이|은|는|도)?\s*(없|낮)",
    r"(걱정|주의).{0,10}(필요.{0,3}없|하지\s*않|안\s*해도)",
    r"피할\s*필요(?:가)?\s*없",
    r"충돌하지\s*않",
    r"(포함|들어\s*있).{0,12}(않|없)",
    r"(알레르기|알러지)(?:\s*유발)?\s*성분.{0,10}(없|보이지\s*않|확인되지\s*않)",
]

_SEMANTIC_REASSURANCE_PATTERNS = [
    # Mental reassurance is itself a positive safety conclusion unless the
    # same clause explicitly rejects that conclusion.
    r"(안심|마음\s*놓)",
    # Ingestion permission combined with a positive/no-harm predicate.
    r"(먹|섭취|드시|복용).{0,20}"
    r"(무방|괜찮|가능|안전|문제.{0,5}없|지장.{0,5}없|부담.{0,5}없)",
    r"(무방|괜찮|가능|안전).{0,12}(먹|섭취|드시|복용)",
    # Absence of concern, caution, harm, or a reason to avoid the product.
    r"(걱정|우려|염려|주의).{0,12}"
    r"(필요.{0,3}없|하지\s*않아도|안\s*해도|안\s*하|없)",
    r"(문제|위험|지장|부담|부작용)(이|은|는|도|가)?\s*"
    r"(전혀\s*)?(없|낮|적)",
    r"(피하|제한하).{0,10}(필요.{0,3}없|지\s*않아도|안\s*해도)",
    r"(해롭|유해).{0,6}(하지\s*않|지\s*않|않|아니)",
]

_EPISTEMIC_REJECTION_PATTERNS = [
    r"(단정|판단|확인|보장|말하|말할|안내).{0,14}"
    r"(수\s*(?:는\s*)?없|어렵|못)",
    r"(근거|정보|증거).{0,12}(없|부족|확인되지\s*않)",
    r"(입증|검증|확인).{0,8}(되지\s*않|안\s*됐|어렵)",
    r"(뜻|의미)(이|은|는|가)?\s*(아니|아닙|아닌|않)",
]

_DIRECT_SAFETY_REJECTION_PATTERNS = [
    r"(안전|괜찮|무방|가능|위험.{0,4}낮).{0,8}"
    r"(하지\s*않|지\s*않|아니|수\s*없)",
    r"(안심|마음\s*놓).{0,10}(하면\s*안|해서는\s*안|수\s*없|지\s*않)",
    r"(먹|섭취|드시|복용).{0,14}(하면\s*안|해서는\s*안|안\s*되|되지\s*않)",
    r"(없|낮|적).{0,4}지\s*않",
    r"(걱정|우려|염려|주의).{0,12}(않으면\s*안|안\s*하면\s*안)",
]

_SAFETY_DENIAL_PATTERNS = [
    r"(안전|괜찮|문제\s*없|위험.{0,4}낮|먹어도|섭취해도|드셔도|섭취.{0,4}가능).{0,12}"
    r"(다고|라고|인지|여부(?:는|를|가)?).{0,8}"
    r"(단정|판단|확인|말하).{0,12}(수\s*없|어렵)",
    r"(안전|괜찮).{0,5}(하지\s*않|아니)",
    r"(먹어도|섭취해도|드셔도).{0,10}(안\s*되|되지\s*않)",
    r"(포함|들어\s*있|알레르기|알러지).{0,16}"
    r"(다고|라고|인지|여부(?:는|를|가)?).{0,8}"
    r"(단정|판단|확인|말하).{0,12}(수\s*없|어렵)",
]

_CAUTIONARY_SAFETY_CONTEXT_TERMS = [
    "피하",
    "주의하",
    "조심하",
    "확인하",
    "교차오염",
    "추가 확인",
    "섭취량",
    "조절",
]

_ALLERGEN_ABSENCE_PATTERNS = [
    r"(알레르기|알러지)(?:\s*유발)?\s*성분.{0,10}(없|보이지\s*않|확인되지\s*않)",
    r"(포함|들어\s*있).{0,12}(않|없)",
    r"(알레르기|알러지).{0,16}충돌하지\s*않",
]

_FOOD_DOMAIN_TERMS = [
    "식품",
    "제품",
    "음식",
    "간식",
    "성분",
    "원재료",
    "알레르기",
    "알러지",
    "영양",
    "영양성분",
    "열량",
    "칼로리",
    "당류",
    "탄수화물",
    "단백질",
    "지방",
    "나트륨",
    "채식",
    "글루텐프리",
    "저염",
    "저당",
    "저칼로리",
    "저지방",
    "고단백",
    "먹어",
    "섭취",
    "주의",
]


def _env_flag(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _is_supported_food_domain_question(message: str, profile: Optional[Profile], product: Optional[Product]) -> bool:
    if product is not None:
        return True

    if profile is not None and (
        profile.resolved_allergies()
        or profile.resolved_special_diets()
    ):
        return True

    normalized = message.lower()
    return any(term in normalized for term in _FOOD_DOMAIN_TERMS) or bool(requested_topics(message))


def _requires_rag_context_for_general_llm() -> bool:
    legacy_value = os.getenv("REQUIRE_RAG_CONTEXT_FOR_LLM")
    if legacy_value is not None:
        return _env_flag("REQUIRE_RAG_CONTEXT_FOR_LLM", "true")
    return _env_flag("REQUIRE_RAG_CONTEXT_FOR_GENERAL_LLM", "true")


def _sources_from_entries(entries: List[dict[str, str]]) -> List[str]:
    # These identifiers are intentionally explicit about their authority:
    # source-controlled Markdown is useful traceability, but it is not an
    # independently verifiable clinical citation or an official-source URL.
    sources: List[str] = []
    prefix = "internal-policy:"
    for entry in entries:
        source = entry.get("source")
        if isinstance(source, str) and source.startswith("official-source:"):
            citation = official_citation_from_entry(entry)
            if citation and citation not in sources:
                sources.append(citation)
            if len(sources) >= 5:
                break
            continue
        if not isinstance(source, str) or not source.startswith(prefix):
            continue
        relative_value = source.removeprefix(prefix)
        relative_path = PurePosixPath(relative_value)
        if (
            not relative_value
            or len(source) > 600
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative_path.as_posix() != relative_value
            or relative_path.suffix != ".md"
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in source
            )
        ):
            continue
        if source not in sources:
            sources.append(source)
        if len(sources) >= 5:
            break
    return sources


def _evidence_text(
    rule_response: ChatResponse,
    product: Optional[Product],
    rag_context: List[str],
) -> str:
    product_parts: List[str] = []
    if product is not None:
        product_parts.extend([
            product.product_name or "",
            product.rawmtrl_nm or "",
            " ".join(product.ingredients),
            " ".join(product.resolved_allergies()),
            " ".join(f"{key} {value}" for key, value in product.nutrition.items()),
        ])

    return "\n".join([
        rule_response.answer,
        " ".join(rule_response.reasons),
        "\n".join(product_parts),
        "\n".join(rag_context),
    ]).lower()


def _has_unsupported_numbers(answer: str, evidence: str) -> bool:
    numbers = re.findall(r"\d+(?:\.\d+)?", answer)
    evidence_numbers = {
        _canonical_number(number)
        for number in re.findall(r"\d+(?:\.\d+)?", evidence)
    }
    return any(_canonical_number(number) not in evidence_numbers for number in numbers)


def _canonical_number(value: str) -> str:
    try:
        return str(Decimal(value).normalize())
    except InvalidOperation:
        return value


_NUTRIENT_CLAIM_ALIASES = {
    "탄수화물": "carbohydrate",
    "포화지방": "saturated_fat",
    "콜레스테롤": "cholesterol",
    "나트륨": "sodium",
    "단백질": "protein",
    "당류": "sugars",
    "칼로리": "kcal",
    "열량": "kcal",
    "지방": "fat",
}

_NUTRIENT_UNITS = {
    "kcal": "kcal",
    "sodium": "mg",
    "cholesterol": "mg",
    "carbohydrate": "g",
    "sugars": "g",
    "protein": "g",
    "fat": "g",
    "saturated_fat": "g",
}

_NUTRIENT_LABEL_PATTERN = "|".join(
    re.escape(label)
    for label in sorted(_NUTRIENT_CLAIM_ALIASES, key=len, reverse=True)
)
_NUMBER_PATTERN = r"(\d+(?:\.\d+)?)"
_UNIT_PATTERN = r"(kcal|mg|g|%)?"
_LABEL_FIRST_NUTRIENT_PATTERN = re.compile(
    rf"({_NUTRIENT_LABEL_PATTERN})\s*(?:함량|수치)?\s*(?:은|는|이|가|:)?\s*"
    rf"{_NUMBER_PATTERN}\s*{_UNIT_PATTERN}",
    re.IGNORECASE,
)
_NUMBER_FIRST_NUTRIENT_PATTERN = re.compile(
    rf"{_NUMBER_PATTERN}\s*{_UNIT_PATTERN}\s*(?:의\s*)?({_NUTRIENT_LABEL_PATTERN})",
    re.IGNORECASE,
)


def _claim_matches_product(
    nutrient_key: str,
    claimed_value: str,
    claimed_unit: str,
    product: Product,
) -> bool:
    if claimed_unit == "%":
        expected = product.nutrition_percent.get(nutrient_key)
        if expected is None:
            expected = product.macro_percent.get(nutrient_key)
    else:
        expected = product.nutrition.get(nutrient_key)
        expected_unit = _NUTRIENT_UNITS.get(nutrient_key)
        if claimed_unit and expected_unit and claimed_unit.lower() != expected_unit:
            return False

    if expected is None:
        return False
    try:
        return Decimal(claimed_value) == Decimal(str(expected))
    except InvalidOperation:
        return False


def _has_mismatched_nutrition_claims(answer: str, product: Optional[Product]) -> bool:
    if product is None:
        return False

    claims: List[Tuple[str, str, str]] = []
    for match in _LABEL_FIRST_NUTRIENT_PATTERN.finditer(answer):
        label, value, unit = match.groups()
        claims.append((_NUTRIENT_CLAIM_ALIASES[label], value, (unit or "").lower()))
    for match in _NUMBER_FIRST_NUTRIENT_PATTERN.finditer(answer):
        value, unit, label = match.groups()
        claims.append((_NUTRIENT_CLAIM_ALIASES[label], value, (unit or "").lower()))

    return any(
        not _claim_matches_product(nutrient_key, value, unit, product)
        for nutrient_key, value, unit in claims
    )


def _has_unsupported_nutrition_interpretation(
    answer: str,
    product: Optional[Product],
) -> bool:
    if product is None or not product.nutrition:
        return False

    interpretation_patterns = [
        r"(높|낮|많|적)은?\s*편",
        r"부담이?\s*(크|작|낮)",
        r"(주의|안전)\s*구간",
        r"(적합|부적합|괜찮)",
        r"(저당|저염|저칼로리|고단백)(?:\s*수준|\s*제품)?",
        r"(영양|수치|함량).{0,10}(양호|가벼|부담이?\s*적)",
        r"가벼운?\s*제품",
    ]
    uncertainty_patterns = [
        r"(판단|단정|해석|확인).{0,16}(어렵|어려|수\s*없|않|보류|미확인)",
        r"(어렵|어려|수\s*없|미확인).{0,16}(판단|단정|해석|확인)",
    ]
    for clause in _split_claim_clauses(answer.lower()):
        if any(re.search(pattern, clause) for pattern in uncertainty_patterns):
            continue
        direct_interpretation = any(re.search(pattern, clause) for pattern in (
            r"(저당|저염|저칼로리|고단백)(?:\s*수준|\s*제품)?",
            r"(영양|수치|함량).{0,10}(양호|가벼|부담이?\s*적)",
            r"가벼운?\s*제품",
        ))
        if (
            direct_interpretation
            or any(label in clause for label in _NUTRIENT_CLAIM_ALIASES)
            and any(re.search(pattern, clause) for pattern in interpretation_patterns)
        ):
            return True
    return False


def _groundable_terms() -> List[str]:
    terms = set(INGREDIENT_KEYWORDS)
    for allergen, aliases in ALLERGY_MAP.items():
        terms.add(allergen)
        terms.update(aliases)
    return sorted(
        (term.strip().lower() for term in terms if term and len(term.strip()) >= 2),
        key=len,
        reverse=True,
    )


def _has_unsupported_ingredient_terms(answer: str, evidence: str) -> bool:
    answer_lower = answer.lower()
    return any(term in answer_lower and term not in evidence for term in _groundable_terms())


def _split_claim_clauses(text: str, *, split_contrasts: bool = False) -> List[str]:
    separator = r"[.!?\n;,/]+"
    if split_contrasts:
        separator += (
            r"|하지만|그러나|다만|반면|지만|으나|그리고|또한"
            r"|하고\s+|하며\s+|있으며\s+|없으며\s+"
        )
    return [
        clause.strip()
        for clause in re.split(separator, text)
        if clause.strip()
    ]


def _epistemic_rejection_matches(clause: str) -> List[re.Match[str]]:
    return [
        match
        for pattern in _EPISTEMIC_REJECTION_PATTERNS
        for match in re.finditer(pattern, clause)
    ]


def _pattern_rejects_claim(
    clause: str,
    patterns: List[str],
    claim_position: int,
) -> bool:
    return any(
        match.start() <= claim_position < match.end()
        for pattern in patterns
        for match in re.finditer(pattern, clause)
    )


def _last_medical_effect_position(clause: str) -> int:
    return max(
        (clause.rfind(term) for term in _MEDICAL_EFFECT_TERMS if term in clause),
        default=-1,
    )


def _has_direct_medical_effect_denial(clause: str) -> bool:
    claim_position = _last_medical_effect_position(clause)
    if claim_position < 0:
        return False

    effect_pattern = "|".join(
        re.escape(term)
        for term in sorted(_MEDICAL_EFFECT_TERMS, key=len, reverse=True)
    )
    direct_pattern = (
        rf"(?:{effect_pattern}).{{0,8}}"
        r"(하지\s*않|되지\s*않|시키지\s*않|지\s*않|아니|없|못)"
    )
    return any(
        match.start() <= claim_position < match.end()
        for match in re.finditer(direct_pattern, clause)
    )


def _is_explicit_medical_denial(clause: str) -> bool:
    claim_position = _last_medical_effect_position(clause)
    if claim_position < 0:
        return False

    return (
        _pattern_rejects_claim(
            clause,
            _MEDICAL_EFFECT_DENIAL_PATTERNS,
            claim_position,
        )
        or _has_direct_medical_effect_denial(clause)
        or any(
            match.start() >= claim_position
            for match in _epistemic_rejection_matches(clause)
        )
    )


def _has_medical_effect_language(clause: str) -> bool:
    has_target = any(term in clause for term in _MEDICAL_TARGET_TERMS)
    has_strong_effect = any(
        term in clause
        for term in _STRONG_MEDICAL_EFFECT_TERMS
    )
    has_weak_claim = any(
        re.search(pattern, clause)
        for pattern in _WEAK_MEDICAL_EFFECT_CLAIM_PATTERNS
    )
    if has_weak_claim and any(
        re.search(pattern, clause)
        for pattern in _TRANSIENT_SYMPTOM_WARNING_PATTERNS
    ):
        has_weak_claim = False
    has_standalone_claim = any(
        re.search(pattern, clause)
        for pattern in _STANDALONE_MEDICAL_EFFECT_PATTERNS
    )
    return (
        (has_target and has_strong_effect)
        or has_weak_claim
        or has_standalone_claim
    )


def _has_disease_treatment_claim(answer: str) -> bool:
    normalized_answer = answer.lower()
    for clause in _split_claim_clauses(normalized_answer, split_contrasts=True):
        has_pattern_claim = any(
            re.search(pattern, clause)
            for pattern in _MEDICAL_EFFECT_CLAIM_PATTERNS
        )
        has_claim = has_pattern_claim or _has_medical_effect_language(clause)
        if has_claim and not _is_explicit_medical_denial(clause):
            return True
    return False


def _is_explicit_safety_denial(clause: str, claim_position: int) -> bool:
    return (
        _pattern_rejects_claim(clause, _SAFETY_DENIAL_PATTERNS, claim_position)
        or _pattern_rejects_claim(
            clause,
            _DIRECT_SAFETY_REJECTION_PATTERNS,
            claim_position,
        )
        or any(
            match.start() >= claim_position
            for match in _epistemic_rejection_matches(clause)
        )
    )


def _contains_reassuring_conclusion(
    text: str,
    *,
    allow_cautioned_reassurance: bool = False,
) -> bool:
    normalized = text.lower()
    for clause in _split_claim_clauses(normalized, split_contrasts=True):
        clause_has_caution = any(
            term in clause
            for term in _CAUTIONARY_SAFETY_CONTEXT_TERMS
        )
        reassurance_patterns = [
            *_REASSURING_CONCLUSION_PATTERNS,
            *_SEMANTIC_REASSURANCE_PATTERNS,
        ]
        for pattern in reassurance_patterns:
            for match in re.finditer(pattern, clause):
                if _is_explicit_safety_denial(clause, match.start()):
                    continue
                if allow_cautioned_reassurance and clause_has_caution:
                    continue
                return True
    return False


def _has_cross_contact_context(answer: object, reasons: object) -> bool:
    if isinstance(reasons, list):
        reason_text = " ".join(str(reason) for reason in reasons)
    else:
        reason_text = str(reasons or "")
    context = f"{answer or ''} {reason_text}".lower()
    return any(marker in context for marker in [
        "교차오염",
        "교차 오염",
        "혼입 가능",
        "같은 제조시설",
        "같은 제조 시설",
        "같은 제조라인",
        "같은 제조 라인",
    ])


def _risk_result_has_mismatch(
    text: str,
    risk_level: object,
    status: object,
    *,
    cross_contact_context: bool = False,
) -> bool:
    risk = str(risk_level or "")
    assessment_status = str(status or "complete")
    requires_caution = (
        risk in {"high", "medium", "unknown"}
        or assessment_status in {"incomplete", "unsupported"}
    )
    if not requires_caution:
        return False
    return _contains_reassuring_conclusion(
        text,
        allow_cautioned_reassurance=(
            risk == "medium"
            and assessment_status == "complete"
            and not cross_contact_context
        ),
    )


def _has_risk_conclusion_mismatch(
    answer: str,
    rule_response: ChatResponse,
) -> bool:
    per_profile_results = rule_response.per_profile_results
    if not per_profile_results:
        return _risk_result_has_mismatch(
            answer,
            rule_response.risk_level,
            rule_response.assessment_status,
            cross_contact_context=_has_cross_contact_context(
                rule_response.answer,
                rule_response.reasons,
            ),
        )

    profile_names = [
        str(result.get("profile_name") or "").strip()
        for result in per_profile_results
        if result.get("profile_name")
    ]
    clauses = _split_claim_clauses(answer)
    for result in per_profile_results:
        profile_name = str(result.get("profile_name") or "").strip()
        if not profile_name:
            continue
        if any(
            profile_name in clause
            and _risk_result_has_mismatch(
                clause,
                result.get("risk_level"),
                result.get("assessment_status"),
                cross_contact_context=_has_cross_contact_context(
                    result.get("answer"),
                    result.get("reasons"),
                ),
            )
            for clause in clauses
        ):
            return True

    global_clauses = [
        clause
        for clause in clauses
        if not any(profile_name in clause for profile_name in profile_names)
    ]
    return any(
        _risk_result_has_mismatch(
            clause,
            rule_response.risk_level,
            rule_response.assessment_status,
            cross_contact_context=_has_cross_contact_context(
                rule_response.answer,
                rule_response.reasons,
            ),
        )
        for clause in global_clauses
    )


def _is_explicit_absence_uncertainty(clause: str) -> bool:
    patterns = [
        r"(포함|들어\s*있|없).{0,20}(단정|판단|확인).{0,12}(수\s*없|어렵)",
        r"(포함|들어\s*있).{0,16}(여부).{0,12}(미확인|어렵)",
        r"(없|미확인).{0,20}(근거로).{0,12}(사용하지\s*않|판정하지\s*않)",
        r"(부재|없음).{0,16}(단정|판정).{0,12}(수\s*없|어렵)",
    ]
    return any(re.search(pattern, clause) for pattern in patterns)


def _has_untraceable_label_claim(
    answer: str,
    product: Optional[Product],
) -> bool:
    if product is None or label_provenance_supports(
        product,
        ("ingredients", "allergens"),
    ):
        return False
    for clause in _split_claim_clauses(answer.lower()):
        if _is_explicit_absence_uncertainty(clause):
            continue
        if any(
            re.search(pattern, clause)
            for pattern in _UNTRACEABLE_LABEL_ABSENCE_PATTERNS
        ):
            return True
    return False


def _has_allergen_presence_contradiction(
    answer: str,
    rule_response: ChatResponse,
    product: Optional[Product],
) -> bool:
    rule_text = " ".join([rule_response.answer, *rule_response.reasons]).lower()
    has_positive_allergen_evidence = bool(
        product
        and (
            product.resolved_allergies()
            or any(marker in rule_text for marker in [
                "프로필 알러지 반응",
                "프로필 알레르기 반응",
                "관련 성분",
                "교차오염",
            ])
        )
    )
    if not has_positive_allergen_evidence:
        return False

    for clause in _split_claim_clauses(answer.lower()):
        if _is_explicit_absence_uncertainty(clause):
            continue
        if any(re.search(pattern, clause) for pattern in _ALLERGEN_ABSENCE_PATTERNS):
            return True
    return False


def _grounding_failure_types(
    answer: str,
    rule_response: ChatResponse,
    product: Optional[Product],
    rag_context: List[str],
) -> List[str]:
    failures: List[str] = []
    normalized_answer = answer.lower()
    if _has_disease_treatment_claim(normalized_answer):
        failures.append("disease_treatment_claim")
    if _has_risk_conclusion_mismatch(normalized_answer, rule_response):
        failures.append("risk_conclusion_mismatch")
    if _has_allergen_presence_contradiction(
        normalized_answer,
        rule_response,
        product,
    ):
        failures.append("allergen_presence_contradiction")

    if any(re.search(pattern, normalized_answer) for pattern in _UNSUPPORTED_CLAIM_PATTERNS):
        failures.append("unsupported_safety_or_effect_claim")

    evidence = _evidence_text(
        rule_response=rule_response,
        product=product,
        rag_context=rag_context,
    )
    if _has_unsupported_numbers(normalized_answer, evidence):
        failures.append("unsupported_number")
    elif _has_mismatched_nutrition_claims(normalized_answer, product):
        failures.append("unsupported_number")

    if _has_unsupported_nutrition_interpretation(normalized_answer, product):
        failures.append("unsupported_nutrition_interpretation")

    if product is not None and any(
        re.search(pattern, normalized_answer)
        for pattern in _UNSUPPORTED_PRODUCT_FRESHNESS_PATTERNS
    ):
        failures.append("unsupported_product_freshness_claim")

    if _has_untraceable_label_claim(normalized_answer, product):
        failures.append("untraceable_label_absence_claim")

    if _has_unsupported_ingredient_terms(normalized_answer, evidence):
        failures.append("unsupported_ingredient_or_allergen")

    return list(dict.fromkeys(failures))


def _violates_grounding(
    answer: str,
    rule_response: ChatResponse,
    product: Optional[Product],
    rag_context: List[str],
) -> bool:
    return bool(_grounding_failure_types(answer, rule_response, product, rag_context))


def _fallback_with_grounding_reason(
    response: ChatResponse,
    fallback_reason: str = "llm_grounding_validation_failed",
    validation_failure_types: Optional[List[str]] = None,
) -> ChatResponse:
    failure_types = validation_failure_types or ["grounding_validation_failed"]
    reasons = list(dict.fromkeys([
        *response.reasons,
        "LLM 생성 답변이 제공된 근거 범위를 벗어나 규칙 기반 답변으로 대체됨",
    ]))
    return response.model_copy(update={
        "answer_source": "rules",
        "sources": [],
        "reasons": reasons,
        "fallback_reason": fallback_reason,
        "validation_failure_types": failure_types,
        "critical_safety_violation": any(
            failure_type in {
                "disease_treatment_claim",
                "risk_conclusion_mismatch",
                "allergen_presence_contradiction",
                "untraceable_label_absence_claim",
                "unsupported_product_freshness_claim",
                "nutrition_interpretation_without_basis",
            }
            for failure_type in failure_types
        ),
        "blocked_llm_output_stored": False,
    })


def validate_final_response_grounding(
    final_response: ChatResponse,
    rule_response: ChatResponse,
    product: Optional[Product],
    rag_context: Optional[List[str]] = None,
) -> ChatResponse:
    failure_types = _grounding_failure_types(
        answer=final_response.answer,
        rule_response=rule_response,
        product=product,
        rag_context=rag_context or [],
    )
    if not failure_types:
        return final_response

    return _fallback_with_grounding_reason(
        rule_response,
        validation_failure_types=failure_types,
    )


def _split_answer_and_questions(text: str) -> Tuple[str, List[str]]:
    marker = "원하시면 아래와 같은 내용도 도와드릴게요:"

    if marker not in text:
        return text.strip(), []

    answer_part, questions_part = text.split(marker, 1)

    questions = []
    for line in questions_part.splitlines():
        cleaned = line.strip()
        cleaned = re.sub(r"^[\-\*\•]\s*", "", cleaned)
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)

        if cleaned:
            questions.append(cleaned)

    return answer_part.strip(), [_to_user_utterance(question) for question in questions[:3]]


def _to_user_utterance(question: str) -> str:
    text = question.strip()
    replacements = [
        ("더 자세히 설명해드릴까요?", "더 자세히 설명해줘"),
        ("설명해드릴까요?", "설명해줘"),
        ("정리해드릴까요?", "정리해줘"),
        ("알려드릴까요?", "알려줘"),
        ("확인해볼까요?", "확인해줘"),
        ("살펴볼까요?", "살펴봐줘"),
        ("비교해볼까요?", "비교해줘"),
        ("물어보실까요?", "물어봐줘"),
        ("볼까요?", "알려줘"),
        ("볼까요", "알려줘"),
        ("드릴까요?", "줘"),
        ("드릴까요", "줘"),
        ("쉬우실까요?", "쉬워지게 설명해줘"),
        ("편하실까요?", "편하게 설명해줘"),
        ("도움이 될까요?", "도움 되게 설명해줘"),
    ]
    for source, target in replacements:
        text = text.replace(source, target)

    polite_suffixes = [
        ("해주세요", "해줘"),
        ("알려주세요", "알려줘"),
        ("설명해주세요", "설명해줘"),
        ("정리해주세요", "정리해줘"),
        ("확인해주세요", "확인해줘"),
    ]
    for source, target in polite_suffixes:
        if text.endswith(source):
            text = text[: -len(source)] + target

    return text.rstrip("?").strip()


def enhance_rule_response(
    rule_response: ChatResponse,
    message: str,
    profile: Optional[Profile],
    product: Optional[Product],
    profiles: Optional[List[Profile]] = None,
    chat_history: Optional[List[ChatHistoryItem]] = None,
) -> ChatResponse:
    if not is_llm_enabled():
        return rule_response

    if rule_response.task_type != "product_chat":
        return rule_response

    # Product answers combine mutable label facts with personal health/diet
    # constraints. A regex validator cannot prove that a free rewrite preserved
    # every absence, threshold, and hold conclusion (novel phrases continually
    # evade enumerated patterns). Keep the deterministic rule template for the
    # entire product-chat boundary; LLM/RAG generation remains available only
    # for non-product general explanations.
    return rule_response.model_copy(update={
        "fallback_reason": "product_rule_template_preserved",
        "validation_failure_types": ["free_product_rewrite_disabled"],
    })


def try_general_llm_fallback(
    base_response: ChatResponse,
    message: str,
    profile: Optional[Profile],
    product: Optional[Product],
    chat_history: Optional[List[ChatHistoryItem]] = None,
) -> ChatResponse:
    if base_response.fallback_reason == "rag_question_outside_evidence_scope":
        return base_response
    if not is_llm_enabled():
        return base_response

    if product is not None:
        return base_response

    original_question = message
    message = resolve_explanation_question(message, chat_history)
    if not _is_supported_food_domain_question(message, profile, product):
        return base_response

    if base_response.intent not in {
        "general_allergy_guide",
        "general_diet_guide",
        "general_nutrition_guide",
    }:
        return base_response

    mode = os.getenv("RAG_GENERAL_RESPONSE_MODE", "extractive").strip().lower()
    if mode not in {"extractive", "generated"}:
        return base_response.model_copy(update={"fallback_reason": "rag_response_mode_invalid"})

    insufficient_scope = {
        "answer": "현재 확인한 자료만으로는 질문에 필요한 내용을 충분히 설명하기 어려워요. 확인되지 않은 수치나 적합성을 추측해서 안내하지 않아요.",
        "fallback_reason": "rag_question_outside_evidence_scope",
        "validation_failure_types": ["rag_evidence_insufficient"],
    }
    question_topics = requested_topics(message)
    if not question_topics:
        # Unsupported questions need no embedding of the user's context.
        return base_response.model_copy(update=insufficient_scope)

    rag_entries = get_rag_context_entries(
        message=message, profile=profile, product=product, official_only=True,
        source_topics=question_topics,
        top_k=min(5, max(3, len(question_topics or ()))),
    )
    if any(
        entry.get("source", "").startswith("official-source:")
        and official_citation_from_entry(entry) is None
        for entry in rag_entries
    ):
        return base_response.model_copy(update={"fallback_reason": "rag_source_approval_changed"})
    # Internal Markdown describes application policy, not independent food
    # evidence. Keep it out of factual grounding, including mixed retrievals.
    # Use the verified body, not provider-supplied `text` or citation titles:
    # a historical title's numbers must not become approved nutrition claims.
    rag_entries = [
        entry for entry in rag_entries
        if entry.get("source", "").startswith("official-source:")
    ]
    rag_context = [
        " ".join(entry["content"].split())
        for entry in rag_entries
    ]

    if _requires_rag_context_for_general_llm() and not rag_context:
        return base_response.model_copy(update={
            "fallback_reason": "rag_context_missing_for_general_llm",
            "validation_failure_types": ["rag_context_missing"],
        })

    reviewed_response = None
    if rag_entries:
        selected = select_reviewed_entries(message, rag_entries)
        if not selected:
            return base_response.model_copy(update=insufficient_scope)
        # Exact reviewed bodies are already written for end users. Recheck all
        # revisions before assembling text/citations; no generated facts enter.
        sources = _sources_from_entries(selected)
        if len(sources) != len(selected):
            return base_response.model_copy(update={"fallback_reason": "rag_source_approval_changed"})
        context = [entry["content"] for entry in selected]
        reviewed_response = base_response.model_copy(update={
            "answer": "\n\n".join(context), "answer_source": "rag",
            "task_type": "general_chat", "sources": sources,
            "grounding_context": context, "fallback_reason": "rag_reviewed_excerpt_answer",
        })
        if mode == "extractive":
            return reviewed_response
        from app.services.rag_grounding import verified_mass_conversion
        conversion = verified_mass_conversion(original_question, selected)
        if conversion:
            return reviewed_response.model_copy(update={
                "answer": conversion,
                # This evidence is server-calculated from the verified unit
                # relation, not a generated draft or a fabricated source body.
                "grounding_context": [*context, conversion],
                "fallback_reason": "rag_verified_unit_calculation",
            })
        rag_entries, rag_context = selected, context
    elif mode == "extractive":
        return base_response.model_copy(update=insufficient_scope)

    def rejected_draft(reason: str, failures: list[str]) -> ChatResponse:
        if reviewed_response is not None:
            return reviewed_response.model_copy(update={
                "fallback_reason": reason,
                "validation_failure_types": failures,
                "blocked_llm_output_stored": False,
            })
        return _fallback_with_grounding_reason(base_response, reason, failures)

    prompt = build_general_fallback_prompt(
        message=message,
        profile=profile,
        chat_history=chat_history,
        rag_context=rag_context,
    )

    from app.infrastructure.llm_trace import llm_purpose
    with llm_purpose("general_answer"):
        llm_answer = generate_text(prompt)

    if not llm_answer:
        return rejected_draft("rag_generated_answer_rejected", ["llm_empty_response"])

    if any(
        entry.get("source", "").startswith("official-source:")
        and official_citation_from_entry(entry) is None
        for entry in rag_entries
    ):
        return _fallback_with_grounding_reason(
            base_response, validation_failure_types=["rag_source_approval_changed"],
        )

    answer, recommended_questions = _split_answer_and_questions(llm_answer)

    failure_types = _grounding_failure_types(
        answer=answer,
        rule_response=base_response,
        product=product,
        rag_context=rag_context,
    )
    if failure_types:
        return rejected_draft("rag_generated_answer_rejected", failure_types)

    if rag_entries:
        if has_opposite_reviewed_statement(answer, rag_entries):
            return rejected_draft("rag_generated_answer_rejected", ["rag_evidence_contradiction"])
        if not answer.strip() or len(answer) > 2000:
            return rejected_draft("rag_generated_answer_rejected", ["rag_generated_answer_invalid"])
        with llm_purpose("grounding_review"):
            review = generate_text(build_grounding_review_prompt(message, answer, rag_entries))
        verified = reviewed_generated_entries(review, answer, message, rag_entries)
        if not verified:
            # No model output becomes user-visible unless all sentences have
            # supported claims bound to complete quotes from current sources.
            if any(official_citation_from_entry(entry) is None for entry in rag_entries):
                return _fallback_with_grounding_reason(
                    base_response, validation_failure_types=["rag_source_approval_changed"],
                )
            return rejected_draft("rag_generated_answer_rejected", ["rag_claim_review_failed"])
        rag_entries = verified
        rag_context = [entry["content"] for entry in verified]

    return base_response.model_copy(update={
        "answer": answer,
        "recommended_questions": recommended_questions,
        "answer_source": "rag" if rag_context else "llm",
        "task_type": "general_chat",
        "sources": _sources_from_entries(rag_entries) if rag_context else [],
        "grounding_context": rag_context,
        "fallback_reason": "rag_generated_answer_verified" if rag_context else None,
    })
