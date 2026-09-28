from typing import List, Optional

from app.core.models import ChatResponse, Profile


RISK_PRIORITY = {
    "low": 0,
    "unknown": 1,
    "medium": 2,
    "high": 3,
}


def _stable_profile_id(profile: Profile) -> Optional[int]:
    if (
        isinstance(profile.profile_id, int)
        and not isinstance(profile.profile_id, bool)
        and profile.profile_id > 0
    ):
        return profile.profile_id
    return None


def dedupe_profiles(profiles: List[Profile]) -> List[Profile]:
    inactive_profile_ids = {
        stable_profile_id
        for profile in profiles
        if not profile.is_active
        and (stable_profile_id := _stable_profile_id(profile)) is not None
    }
    seen = set()
    result = []
    for profile in profiles:
        stable_profile_id = _stable_profile_id(profile)
        if not profile.is_active:
            continue
        if stable_profile_id is not None and stable_profile_id in inactive_profile_ids:
            continue
        key = (
            (
                "profile_id",
                stable_profile_id,
                profile.model_dump_json(exclude_none=False),
            )
            if stable_profile_id is not None
            else ("anonymous_profile", profile.model_dump_json(exclude_none=False))
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
    if primary_profile and (
        not profiles
        or primary_profile.target_type != "group"
        or not primary_profile.is_active
    ):
        collected.append(primary_profile)

    for profile in profiles or []:
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
        merged_profile.special_diets.extend(profile.special_diets)
        merged_profile.special_diet.extend(profile.special_diet)
        merged_profile.disliked_ingredients.extend(profile.disliked_ingredients)

    merged_profile.allergies = list(dict.fromkeys(merged_profile.allergies))
    merged_profile.user_allergies = list(dict.fromkeys(merged_profile.user_allergies))
    merged_profile.special_diets = list(dict.fromkeys(merged_profile.special_diets))
    merged_profile.special_diet = list(dict.fromkeys(merged_profile.special_diet))
    merged_profile.disliked_ingredients = list(dict.fromkeys(merged_profile.disliked_ingredients))
    merged_profile._conversation_exclusions = tuple(dict.fromkeys(
        field for profile in profiles for field in profile._conversation_exclusions
    ))
    # Positive facts from every selected profile must still be retained for
    # conservative conflict detection. Completeness is different: one complete
    # profile cannot prove that another person's omitted allergy/diet
    # section was checked. Profile construction above sees the aggregate lists
    # and would otherwise turn every flag into True, so restore their actual
    # all-members provenance explicitly after merging.
    return merged_profile.model_copy(update={
        "allergy_context_provided": all(
            profile.allergy_context_provided for profile in profiles
        ),
        # Accepted for input compatibility only; medical conditions are outside
        # this chatbot's decision scope and are never merged.
        "condition_context_provided": True,
        "diet_context_provided": all(
            profile.diet_context_provided for profile in profiles
        ),
    })


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
    if response.overall_decision == "avoid":
        status = "피하는 편이 좋아요"
    elif response.overall_decision == "caution":
        status = "주의가 필요해요"
    elif response.overall_decision == "no_known_conflict":
        status = "현재 확인된 표시에서는 등록 알레르기 충돌을 찾지 못했어요"
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
