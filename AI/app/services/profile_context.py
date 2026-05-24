from typing import List, Optional

from app.core.models import ChatResponse, Profile


RISK_PRIORITY = {
    "unknown": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


def dedupe_profiles(profiles: List[Profile]) -> List[Profile]:
    seen = set()
    result = []
    for profile in profiles:
        key = (
            profile.profile_id,
            profile.user_id,
            profile.profile_name,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(profile)
    return result


def active_profiles_from_inputs(
    primary_profile: Optional[Profile],
    profiles: Optional[List[Profile]] = None,
) -> List[Profile]:
    collected: List[Profile] = []
    if primary_profile and primary_profile.is_active and (
        not profiles or primary_profile.target_type != "group"
    ):
        collected.append(primary_profile)

    for profile in profiles or []:
        if profile.is_active:
            collected.append(profile)

    return dedupe_profiles(collected)


def merge_profiles(profiles: List[Profile]) -> Optional[Profile]:
    if not profiles:
        return None

    if len(profiles) == 1:
        return profiles[0]

    first = profiles[0]
    names = [profile.profile_name for profile in profiles if profile.profile_name]

    merged_profile = Profile(
        profile_id=first.profile_id,
        profile_name=", ".join(names),
        user_id=first.user_id,
        nickname=", ".join(names),
        allergies=[],
        user_allergies=[],
        conditions=[],
        special_diets=[],
        special_diet=[],
        disliked_ingredients=[],
        target_type="group",
        is_active=True,
    )

    for profile in profiles:
        merged_profile.allergies.extend(profile.allergies)
        merged_profile.user_allergies.extend(profile.user_allergies)
        merged_profile.conditions.extend(profile.conditions)
        merged_profile.special_diets.extend(profile.special_diets)
        merged_profile.special_diet.extend(profile.special_diet)
        merged_profile.disliked_ingredients.extend(profile.disliked_ingredients)

    merged_profile.allergies = list(dict.fromkeys(merged_profile.allergies))
    merged_profile.user_allergies = list(dict.fromkeys(merged_profile.user_allergies))
    merged_profile.conditions = list(dict.fromkeys(merged_profile.conditions))
    merged_profile.special_diets = list(dict.fromkeys(merged_profile.special_diets))
    merged_profile.special_diet = list(dict.fromkeys(merged_profile.special_diet))
    merged_profile.disliked_ingredients = list(dict.fromkeys(merged_profile.disliked_ingredients))
    return merged_profile


def active_profile_names(profiles: List[Profile]) -> List[str]:
    return [profile.profile_name for profile in profiles if profile.profile_name]


def highest_risk_level(responses: List[ChatResponse]) -> str:
    if not responses:
        return "unknown"

    return max(
        responses,
        key=lambda response: RISK_PRIORITY.get(response.risk_level, -1),
    ).risk_level


def summarize_profile_response(profile: Profile, response: ChatResponse) -> str:
    return f"[{profile.profile_name}]\n{response.answer}"


def summarize_profile_outcome(profile: Profile, response: ChatResponse) -> str:
    if response.risk_level == "high":
        status = "피하는 편이 좋아요"
    elif response.risk_level == "medium":
        status = "주의가 필요해요"
    elif response.risk_level == "low":
        status = "현재 기준으로는 큰 문제는 없어 보여요"
    else:
        status = "정보가 더 필요해요"
    return f"{profile.profile_name}: {status}"


def build_multi_profile_answer(profiles: List[Profile], responses: List[ChatResponse]) -> str:
    headline = "활성화된 프로필 기준 요약: " + " / ".join(
        summarize_profile_outcome(profile, response)
        for profile, response in zip(profiles, responses)
    )
    details = "\n\n".join(
        summarize_profile_response(profile, response)
        for profile, response in zip(profiles, responses)
    )
    return f"{headline}\n\n{details}"
