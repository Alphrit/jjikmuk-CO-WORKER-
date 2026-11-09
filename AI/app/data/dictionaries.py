from types import MappingProxyType
from typing import Mapping


_ALLERGEN_DIRECT_ALIAS_DATA = {
    "우유": [
        "우유", "원유", "분유", "탈지분유", "전지분유", "유청",
        "카제인", "유단백", "농축우유단백", "버터", "치즈", "생크림",
        "유제품", "연유", "유크림", "버터밀크", "milk", "dairy", "whey", "casein",
    ],
    "계란": [
        "계란", "달걀", "난류", "난백", "난황", "전란액", "전란",
        "계란흰자", "달걀흰자", "알류", "메추리알", "마요네즈", "egg",
    ],
    "땅콩": ["땅콩", "땅콩버터", "볶음땅콩", "땅콩분태", "피넛", "피넛버터", "peanut"],
    "대두": [
        "대두", "분리대두단백", "대두단백", "콩단백", "콩가루",
        "콩기름", "콩유", "두유", "두부", "간장", "검은콩", "검정콩", "서리태", "흑태",
        "soy", "soybean", "콩",
    ],
    "밀": ["밀", "통밀", "소맥분", "소맥", "밀가루", "밀전분", "밀단백", "밀글루텐", "밀함유", "wheat"],
    "메밀": ["메밀", "메밀가루", "buckwheat"],
    "새우": ["새우", "새우분말", "새우엑기스", "shrimp", "prawn"],
    "게": ["게", "게살", "꽃게", "대게", "홍게", "게맛살", "게농축액", "게엑기스", "게추출물", "크랩", "crab"],
    "견과류": ["견과류", "tree nut", "tree nuts"],
    "아몬드": ["아몬드", "almond"],
    "캐슈넛": ["캐슈넛", "캐슈", "캐시우넛", "cashew"],
    "피스타치오": ["피스타치오", "pistachio"],
    "헤이즐넛": ["헤이즐넛", "헤즐넛", "hazelnut"],
    "피칸": ["피칸", "pecan"],
    "마카다미아": ["마카다미아", "마카다미아넛", "마카다미아너트", "macadamia"],
    "브라질너트": ["브라질너트", "브라질넛", "brazil nut", "brazil nuts"],
    "고등어": ["고등어", "mackerel"],
    "돼지고기": ["돼지고기", "돼지", "돈육", "돈지", "돼지지방", "돼지껍데기", "라드", "pork", "lard"],
    "복숭아": ["복숭아", "피치", "peach"],
    "토마토": ["토마토", "케첩", "케찹", "tomato"],
    "아황산류": ["아황산류", "아황산", "이산화황", "메타중아황산", "sulfite", "sulfites"],
    "호두": ["호두", "월넛", "walnut"],
    "닭고기": ["닭고기", "닭", "계육", "치킨", "chicken"],
    "쇠고기": ["쇠고기", "소고기", "우육", "우지", "사골", "beef"],
    "오징어": ["오징어", "squid"],
    "조개류": ["조개류", "조개", "조갯살", "clam", "clams", "shellfish"],
    "굴": ["굴", "석화", "oyster", "oysters"],
    "전복": ["전복", "abalone"],
    "홍합": ["홍합", "mussel", "mussels"],
    "가리비": ["가리비", "scallop", "scallops"],
    "바지락": ["바지락"],
    "꼬막": ["꼬막", "cockle", "cockles"],
    "잣": ["잣", "국산잣", "잣가루", "잣분태", "잣기름", "pine nut", "pine nuts", "pine_nut"],
    "갑각류": ["갑각류", "crustacean", "crustaceans"],
    "랍스터": ["랍스터", "로브스터", "바닷가재", "lobster"],
    "가재": ["가재", "민물가재", "crayfish", "crawfish"],
}

# Broad profile concepts expand through explicit canonical children. Keeping
# this relationship separate prevents a specific profile term such as
# ``피칸`` from being canonicalized as every tree nut.
ALLERGEN_CHILDREN: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "견과류": (
        "아몬드", "호두", "캐슈넛", "피스타치오", "헤이즐넛",
        "피칸", "마카다미아", "브라질너트", "잣",
    ),
    "갑각류": ("새우", "게", "랍스터", "가재"),
    "조개류": ("굴", "전복", "홍합", "가리비", "바지락", "꼬막"),
})

# Capture direct synonyms before replacing the legacy dictionary with a
# parent-expanded, deeply immutable compatibility view.
ALLERGEN_CANONICAL_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    canonical: tuple(aliases)
    for canonical, aliases in _ALLERGEN_DIRECT_ALIAS_DATA.items()
})
del _ALLERGEN_DIRECT_ALIAS_DATA


def _expanded_allergen_aliases(canonical: str) -> tuple[str, ...]:
    aliases = list(ALLERGEN_CANONICAL_ALIASES[canonical])
    for child in ALLERGEN_CHILDREN.get(canonical, ()):
        aliases.extend(ALLERGEN_CANONICAL_ALIASES[child])
    return tuple(dict.fromkeys(aliases))


ALLERGY_MAP: Mapping[str, tuple[str, ...]] = MappingProxyType({
    canonical: _expanded_allergen_aliases(canonical)
    for canonical in ALLERGEN_CANONICAL_ALIASES
})

INGREDIENT_KEYWORDS = [
    "우유", "유제품", "계란", "달걀", "난류", "땅콩", "대두", "콩", "메밀", "밀",
    "카페인", "견과류", "새우", "게", "고등어", "돼지고기", "복숭아",
    "토마토", "아황산류", "호두", "닭고기", "쇠고기", "오징어", "조개류",
    "잣", "갑각류", "아몬드", "캐슈넛", "캐슈", "피스타치오", "헤이즐넛",
    "피칸", "마카다미아", "브라질너트", "랍스터", "가재", "굴", "전복", "홍합",
    "가리비", "바지락", "꼬막", "소맥분", "설탕", "감자", "원유", "소금"
]

# Broad health advice is outside the service. These words are retained only
# as supported food-label/diet context cues.
HEALTH_KEYWORDS = ["식이", "식단", "영양"]

EATABILITY_KEYWORDS = [
    "먹어도 돼",
    "먹어도 됨",
    "먹어도 되나",
    "먹어도 되나요",
    "먹어도 될까",
    "먹어도 될까요",
    "먹을 수 있어",
    "먹을수 있어",
    "섭취해도 돼",
    "섭취해도 되",
    "섭취해도 될까",
    "섭취해도 될까요",
    "괜찮아",
    "괜찮을까",
    "섭취 가능",
    "섭취가능",
    "안전해",
    "위험해",
    "위험한가",
    "위험할까",
    "안심하고 먹",
    "먹여도 돼",
    "먹어도 문제 없어",
    "먹어도 문제 없을까",
    "먹어도 문제 없나요",
    "먹어도 문제 없나",
    "문제 없어",
    "문제 없을까",
    "문제 없나요",
    "문제 없나",
    "먹어도 괜찮아",
    "먹어도 괜찮을까",
    "먹어도 괜찮나요",
    "먹어도 괜찮은지",
    "먹어도 되는지",
    "드셔도 괜찮아",
]

INCLUSION_KEYWORDS = [
    "들어 있어",
    "들어있어",
    "들어 있지",
    "들어있지",
    "함유",
    "포함",
    "있어?",
    "있는지",
    "없는지",
    "들어갔어",
    "있나",
    "들어가",
    "있어",
]

NUTRITION_KEYWORDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "당류": ("당류", "당", "설탕"),
    "탄수화물": ("탄수화물",),
    "단백질": ("단백질",),
    "지방": ("지방",),
    "포화지방": ("포화지방",),
    "나트륨": ("나트륨", "소금"),
    "콜레스테롤": ("콜레스테롤", "cholesterol"),
    "칼로리": ("칼로리", "열량", "kcal"),
})

NUTRITION_EVAL_KEYWORDS = [
    "높아", "많아", "적어", "어때", "높은 편", "많은 편", "괜찮아", "괜찮을까"
]

SUMMARY_KEYWORDS = [
    "설명해줘", "요약해줘", "어떤 제품이야", "쉽게 알려줘", "이게 뭐야"
]

PROFILE_RECHECK_KEYWORDS = [
    "엄마 기준", "자녀 기준", "아이 기준", "부모님 기준", "반려동물 기준", "다시 봐줘"
]

GENERAL_ALLERGY_GUIDE_KEYWORDS = [
    "알레르기", "알러지", "알레르기면", "주의 성분", "피해야",
    "교차접촉", "교차 접촉", "교차오염", "교차 오염", "같은 제조시설",
    "같은 제조 시설", "동일 제조시설", "동일한 제조시설", "같은 제조라인",
]

GENERAL_DIET_GUIDE_KEYWORDS = [
    "채식", "저염", "저당", "글루텐프리", "글루텐 프리",
    "저칼로리", "저열량", "저지방", "고단백", "식단", "식이"
]

GENERAL_NUTRITION_GUIDE_KEYWORDS = [
    "영양", "나트륨", "당류", "탄수화물", "단백질", "칼로리", "열량", "지방", "포화지방",
    "무당", "무설탕", "무가당", "설탕 무첨가", "설탕무첨가", "제로슈거", "감미료",
]

ALTERNATIVE_FOOD_KEYWORDS = [
    "대체 식품", "대체식품", "대신 먹", "대신먹", "대체할", "대체 음식", "대체음식",
    "비슷한 제품", "비슷한 거", "대체 제품", "대체품", "대안", "추천", "대신 뭐", "대체 뭐", "뭐 먹지", "다른 거"
]

FOOD_COMPARISON_KEYWORDS = [
    "비교", "더 낮", "더 높", "더 많", "더 적", "뭐가 더", "뭐가 덜", "어느 게", "어떤 게",
    "a와 b", "a랑 b", "둘 중", "중에 뭐", "뭐가 나아", "뭐가 나을", "뭐가 낫", "어느 쪽"
]

ALTERNATIVE_CATEGORY_MAP: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "우유": ("두유", "식물성음료", "음료", "기타음료"),
    "유제품": ("두유", "식물성음료", "음료", "기타음료"),
    "유가공품": ("두유", "식물성음료", "음료", "기타음료"),
    "발효유": ("두유", "식물성음료", "음료", "기타음료"),
    "밀가루면": ("쌀면", "곤약면", "면류"),
    "면류": ("쌀면", "곤약면", "면류"),
    "과자": ("과자", "스낵", "저당간식"),
    "스낵": ("과자", "스낵", "저당간식"),
    "에너지바": ("에너지바", "과자", "고단백간식"),
    "빵": ("글루텐프리빵", "비건빵", "빵"),
    "디저트": ("비건디저트", "저당디저트", "디저트"),
    "음료": ("무가당음료", "제로음료", "차류", "음료"),
})


def normalize_category_name(category: str | None) -> str:
    if not category:
        return ""
    return "".join(char for char in category.strip().lower() if char.isalnum())


def alternative_categories_for(category: str | None) -> list[str]:
    normalized = normalize_category_name(category)
    if not normalized:
        return []

    categories = [category.strip()]
    for source, alternatives in ALTERNATIVE_CATEGORY_MAP.items():
        source_normalized = normalize_category_name(source)
        if source_normalized and (source_normalized in normalized or normalized in source_normalized):
            categories.extend(alternatives)

    return list(dict.fromkeys(item for item in categories if item))


SPECIAL_DIET_KEYWORDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "채식": ("채식", "채식주의", "베지테리언", "vegetarian"),
    "저염식": ("저염", "저염식", "나트륨 조절", "나트륨 적은", "나트륨 낮은"),
    "저당식": ("저당", "저당식", "당 조절", "혈당 관리"),
    "글루텐프리": ("글루텐프리", "글루텐 프리", "gluten free", "gluten-free"),
    "저칼로리": ("저칼로리", "저열량", "낮은 칼로리"),
    "저지방": ("저지방", "낮은 지방"),
    "고단백": ("고단백", "단백질 위주", "단백질 많은"),
})


def canonical_special_diet_name(value: str) -> str | None:
    """Resolve a structured diet value through the registered alias table."""

    normalized = "".join(value.strip().lower().split())
    for canonical, aliases in SPECIAL_DIET_KEYWORDS.items():
        if normalized in {
            "".join(candidate.strip().lower().split())
            for candidate in (canonical, *aliases)
        }:
            return canonical
    return None


SPECIAL_DIET_RESTRICTIONS: Mapping[
    str,
    Mapping[str, tuple[str, ...]],
] = MappingProxyType({
    "채식": MappingProxyType({
        "blocked_ingredients": (
            "소고기", "쇠고기", "돼지고기", "닭고기", "오리고기",
            "햄", "베이컨", "젤라틴", "생선", "참치", "멸치",
            "새우", "게", "오징어", "조개",
        ),
        "blocked_allergens": ("새우", "게", "오징어", "조개류"),
    }),
    "글루텐프리": MappingProxyType({
        "blocked_ingredients": (
            "밀", "밀가루", "소맥", "소맥분", "보리", "호밀",
        ),
        "blocked_allergens": ("밀",),
    }),
})
