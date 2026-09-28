"""Reviewed fallback recipes; these are goals, not nutrition certifications."""

from typing import Sequence

from app.core.models import Product
from app.services.response_helpers import object_particle


_DIET_RECIPE_TITLES = {
    "채식": "채식",
    "저염식": "저염 목표",
    "저당식": "저당 목표",
    "글루텐프리": "글루텐프리 목표",
    "저칼로리": "저칼로리 목표",
    "저지방": "저지방 목표",
    "고단백": "고단백 목표",
}


def _diet_goal_steps(diets: Sequence[str]) -> list[str]:
    steps = []
    if "저염식" in diets:
        steps.append("소금·시판 소스는 추가하지 않고 재료 목록의 분량대로 조리해요.")
    if "저당식" in diets:
        steps.append("설탕·시럽·달콤한 드레싱은 추가하지 않아요.")
    if "저지방" in diets:
        steps.append("튀기지 않고 삶거나 굽는 방식으로 조리해요.")
    if "저칼로리" in diets:
        steps.append("채소 비중을 높이고 별도의 소스와 토핑은 생략해요.")
    if "고단백" in diets:
        steps.append("단백질 원료는 표시된 분량대로 사용하되, 실제 단백질 수치는 사용 제품의 표시를 확인해요.")
    if "글루텐프리" in diets:
        steps.append("포장된 재료는 글루텐프리 표시와 교차접촉 주의문구를 다시 확인해요.")
    return steps


def _general_recipe_variants(diets: Sequence[str]) -> list[dict]:
    title_prefix = next(
        (_DIET_RECIPE_TITLES[diet] for diet in diets if diet in _DIET_RECIPE_TITLES),
        "담백한",
    )
    goal_steps = _diet_goal_steps(diets)
    variants = [
        {
            "title": f"{title_prefix} 병아리콩 채소 볼",
            "ingredients": [
                {"name": "삶은 병아리콩", "amount": "1/2컵"},
                {"name": "현미밥", "amount": "1/2공기"},
                {"name": "오이", "amount": "1/3개"},
                {"name": "양배추", "amount": "한 줌"},
                {"name": "레몬즙", "amount": "1작은술"},
            ],
            "steps": [
                "병아리콩은 물에 헹구고 채소를 먹기 좋은 크기로 썰어요.",
                "현미밥 위에 병아리콩과 채소를 올리고 레몬즙을 뿌려요.",
                *goal_steps,
            ],
        },
        {
            "title": f"{title_prefix} 렌틸콩 토마토 수프",
            "ingredients": [
                {"name": "삶은 렌틸콩", "amount": "1/2컵"},
                {"name": "토마토", "amount": "1개"},
                {"name": "양파", "amount": "1/4개"},
                {"name": "당근", "amount": "1/4개"},
                {"name": "물", "amount": "1컵"},
            ],
            "steps": [
                "토마토·양파·당근을 작게 썰어요.",
                "물에 채소와 렌틸콩을 넣고 재료가 부드러워질 때까지 끓여요.",
                *goal_steps,
            ],
        },
        {
            "title": f"{title_prefix} 두부 채소 쌈",
            "ingredients": [
                {"name": "두부", "amount": "150g"},
                {"name": "상추", "amount": "6장"},
                {"name": "오이", "amount": "1/3개"},
                {"name": "토마토", "amount": "1/2개"},
                {"name": "레몬즙", "amount": "1작은술"},
            ],
            "steps": [
                "두부는 물기를 빼고 마른 팬에 노릇하게 구워요.",
                "상추에 두부와 채소를 올리고 레몬즙을 곁들여요.",
                *goal_steps,
            ],
        },
        {
            "title": f"{title_prefix} 감자 구운 채소 접시",
            "ingredients": [
                {"name": "감자", "amount": "1개"},
                {"name": "애호박", "amount": "1/3개"},
                {"name": "양배추", "amount": "한 줌"},
                {"name": "파슬리", "amount": "약간"},
                {"name": "레몬즙", "amount": "1작은술"},
            ],
            "steps": [
                "감자와 채소를 먹기 좋은 크기로 썰어 익혀요.",
                "접시에 담고 파슬리와 레몬즙으로 마무리해요.",
                *goal_steps,
            ],
        },
    ]
    if "고단백" in diets:
        variants[0]["ingredients"][0]["amount"] = "1컵"
        variants[0]["ingredients"][1]["amount"] = "1/3공기"
        variants[1]["ingredients"][0]["amount"] = "3/4컵"
        variants[2]["ingredients"][0]["amount"] = "200g"
        variants[3]["ingredients"].append({"name": "삶은 렌틸콩", "amount": "3/4컵"})
        variants[3]["steps"].insert(1, "익힌 채소에 삶은 렌틸콩을 곁들여요.")
        variants[3]["title"] = f"{title_prefix} 렌틸콩 구운 채소 접시"
    if "저칼로리" in diets:
        for variant in variants:
            for ingredient in variant["ingredients"]:
                if ingredient["name"] in {"현미밥", "감자"}:
                    ingredient["amount"] = "1/3공기" if ingredient["name"] == "현미밥" else "1/2개"
        variants[0]["ingredients"][3]["amount"] = "두 줌"
    # Pick a suitable composition first, instead of relabelling one universal bowl.
    first = 2 if "채식" in diets or "저당식" in diets else 1 if "저지방" in diets else 3 if "글루텐프리" in diets else 0
    return variants[first:] + variants[:first]


def _product_recipe_variants(product: Product) -> list[dict]:
    name = product.product_name or "선택한 제품"
    object_suffix = object_particle(name)
    declared_category = next((value.lower() for value in (
        product.category_detail, product.category_small, product.prdlst_dcnm,
    ) if value), "")
    # An explicit taxonomy takes precedence over words in a marketing name.
    category = declared_category or name.lower()
    if any(term in category for term in ("빙과", "아이스크림", "냉동디저트")):
        return []
    snack_type = any(term in category for term in (
        "과자", "스낵", "에너지바", "단백질바", "두유바", "칩", "빵", "크래커",
    ))
    if not snack_type and any(term in category for term in ("우유", "두유", "음료", "드링크", "주스")):
        return [
            {
                "title": f"{name} 오트밀 볼",
                "ingredients": [
                    {"name": name, "amount": "150ml"},
                    {"name": "오트밀", "amount": "1/2컵"},
                    {"name": "바나나", "amount": "1/2개"},
                ],
                "steps": [f"오트밀에 {name}{object_suffix} 부어 10분간 불려요.", "바나나를 썰어 올려요."],
            },
            {
                "title": f"{name} 바나나 스무디",
                "ingredients": [
                    {"name": name, "amount": "150ml"},
                    {"name": "바나나", "amount": "1개"},
                    {"name": "얼음", "amount": "1/2컵"},
                ],
                "steps": [f"{name}와 바나나, 얼음을 믹서에 넣고 갈아요."],
            },
            {
                "title": f"{name} 따뜻한 귀리죽",
                "ingredients": [
                    {"name": name, "amount": "150ml"},
                    {"name": "오트밀", "amount": "1/2컵"},
                    {"name": "사과", "amount": "1/4개"},
                ],
                "steps": [f"오트밀과 {name}{object_suffix} 약불에 끓여요.", "사과를 작게 썰어 곁들여요."],
            },
            {
                "title": f"{name} 과일 컵",
                "ingredients": [
                    {"name": name, "amount": "120ml"},
                    {"name": "사과", "amount": "1/2개"},
                    {"name": "바나나", "amount": "1/2개"},
                ],
                "steps": ["과일을 먹기 좋은 크기로 썰어요.", f"컵에 과일을 담고 {name}{object_suffix} 부어요."],
            },
        ]
    if any(term in category for term in ("면류", "국수", "라면", "파스타", "면 ")):
        return [
            {
                "title": f"{name} 채소 볶음면",
                "ingredients": [{"name": name, "amount": "1인분"}, {"name": "애호박", "amount": "1/3개"}, {"name": "당근", "amount": "1/4개"}],
                "steps": [f"{name}{object_suffix} 포장지 조리법대로 익혀요.", "채소를 볶아 익힌 제품과 섞어요."],
            },
            {
                "title": f"{name} 토마토 면",
                "ingredients": [{"name": name, "amount": "1인분"}, {"name": "토마토", "amount": "1개"}, {"name": "양파", "amount": "1/4개"}],
                "steps": [f"{name}{object_suffix} 포장지 조리법대로 익혀요.", "토마토와 양파를 끓여 익힌 제품에 곁들여요."],
            },
        ]
    if any(term in category for term in ("소스", "드레싱", "양념")):
        return [
            {
                "title": f"{name} 채소 샐러드",
                "ingredients": [{"name": name, "amount": "1큰술"}, {"name": "양상추", "amount": "두 줌"}, {"name": "오이", "amount": "1/2개"}],
                "steps": ["채소를 씻어 썰어요.", f"{name}{object_suffix} 채소에 조금씩 곁들여요."],
            },
            {
                "title": f"{name} 구운 채소 접시",
                "ingredients": [{"name": name, "amount": "1큰술"}, {"name": "감자", "amount": "1개"}, {"name": "애호박", "amount": "1/3개"}],
                "steps": ["감자와 애호박을 익혀요.", f"{name}{object_suffix} 곁들여요."],
            },
        ]
    if not snack_type:
        return []
    return [
        {
            "title": f"{name} 바삭 샐러드",
            "ingredients": [
                {"name": name, "amount": "30g"},
                {"name": "양상추", "amount": "두 줌"},
                {"name": "오이", "amount": "1/3개"},
                {"name": "토마토", "amount": "1/2개"},
                {"name": "올리브유", "amount": "1작은술"},
            ],
            "steps": [
                "양상추·오이·토마토를 씻어 먹기 좋은 크기로 준비해요.",
                f"{name}{object_suffix} 잘게 부숴 채소 위에 올려요.",
                "올리브유를 가볍게 둘러 섞어요.",
            ],
        },
        {
            "title": f"{name} 채소 덮밥",
            "ingredients": [
                {"name": name, "amount": "25g"},
                {"name": "밥", "amount": "1공기"},
                {"name": "애호박", "amount": "1/4개"},
                {"name": "당근", "amount": "1/4개"},
                {"name": "올리브유", "amount": "1작은술"},
            ],
            "steps": [
                "애호박과 당근을 잘게 썰어 올리브유에 익혀요.",
                f"밥 위에 익힌 채소와 잘게 부순 {name}{object_suffix} 올려요.",
                "골고루 섞어 먹어요.",
            ],
        },
        {
            "title": f"{name} 오이 채소 컵",
            "ingredients": [
                {"name": name, "amount": "25g"},
                {"name": "오이", "amount": "1/2개"},
                {"name": "양배추", "amount": "한 줌"},
                {"name": "레몬즙", "amount": "1작은술"},
            ],
            "steps": [
                "오이와 양배추를 작게 썰어 컵에 담아요.",
                f"{name}{object_suffix} 먹기 좋게 잘라 올리고 레몬즙을 곁들여요.",
            ],
        },
        {
            "title": f"{name} 구운 채소 토핑",
            "ingredients": [
                {"name": name, "amount": "25g"},
                {"name": "감자", "amount": "1개"},
                {"name": "애호박", "amount": "1/3개"},
                {"name": "파슬리", "amount": "약간"},
            ],
            "steps": [
                "감자와 애호박을 먹기 좋게 썰어 익혀요.",
                f"익힌 채소 위에 잘게 부순 {name}과 파슬리를 올려요.",
            ],
        },
    ]
