"""Shared request grammar for routing; never supplies product or profile facts.

Objects, actions and request endings are parsed separately so each workflow
does not need a separate list of complete conversational sentences. Original
user text remains the evidence used for conditions and response generation.
"""

import re


def command_text(message: str) -> str:
    text = " ".join(message.strip().split()).rstrip(".!?？")
    # Normalize politeness only at the end of a request, never within names,
    # condition assertions or a negated/hypothetical clause.
    text = re.sub(
        r"\s*(?:주(?:세요|십시오|실래요|시겠어요)|줘요|줄래(?:요)?|(?:줄|주실)\s*수\s*(?:있어(?:요)?|있나요|있을까(?:요)?))$",
        "줘", text,
    )
    text = re.sub(r"\s+줘$", "줘", text)
    # A request for an action noun ('비교 부탁해') and a conjugated action
    # ('다시 설명해 주세요') share the same terminal form.
    text = re.sub(r"\s*부탁(?:해(?:요)?|드립니다|드려요)$", "해줘", text)
    text = re.sub(r"해해줘$", "해줘", text)
    return text


def current_topic_message(message: str) -> str:
    """Drop only an explicitly dismissed topic or a leading greeting, not arbitrary earlier text."""
    from app.services.conversation_controls import strip_leading_small_talk
    message = strip_leading_small_talk(message)
    if re.match(r"\s*(?:(?:확인\s*)?질문)(?:은|는|만)?\s*그만", message):
        return message
    match = re.match(
        r"^\s*(?:[가-힣A-Za-z]+\s*){0,3}(?:얘기|이야기|설명|질문)(?:는|은|을|를)?\s*"
        r"(?:됐고|말고|여기까지\s*하고|그만하고)[,，\s]*(.+)$", message,
    )
    return match[1] if match else message


def personal_assessment_request(message: str) -> bool:
    return bool(re.search(
        r"먹어도|먹을\s*수|드셔도|드실\s*수|줄\s*수|마셔도|섭취|괜찮|기준|조건으로|대상|위한|"
        r"에게\s*추천|한테\s*추천|확인해|봐줘|봐\s*줘", message,
    ))


def package_amount(message: str):
    """Explicit package count, never a serving recommendation or a label fact."""
    matches = list(re.finditer(r"(?<![\w.−-])(한|두|세|네|반|\d+(?:\.\d+)?)\s*(팩|봉지|병|통|상자)(?=\s|[의은는을를도만]|$)", message))
    if len(matches) != 1:
        return None
    match = matches[0]
    words = {"한": 1, "두": 2, "세": 3, "네": 4, "반": .5}
    count = words[match[1]] if match[1] in words else float(match[1])
    return (count, match[2]) if 0 < count <= 1000 else None


def missing_nutrient_check(message: str) -> bool:
    return bool(re.search(r"(?:수치|정보).*(?:비어|없|누락)|(?:단백질|당류|나트륨|지방|열량).*(?:0|영)\s*(?:g|mg)?\s*(?:이|라|인|이라는|이란).*(?:뜻|거|건)", message))


def food_recommendation_request(message: str) -> bool:
    return bool(re.search(r"(?:다른|대체|대안|비슷한)\s*(?:제품|상품|식품|음식|간식|과자).*?(?:추천|알려|골라|찾아)", message)
                or re.search(r"(?:현재\s*)?선택된\s*(?:사람들?|프로필).*기준.*추천", message))


def nutrient_exclusion_question(message: str) -> bool:
    """A whole-nutrient avoidance decision is outside label-definition evidence."""
    return bool(re.search(
        r"(?:지방|탄수화물|단백질|당류)(?:을|를|은|는)?\s*(?:다|모두|전부)\s*"
        r"(?:없애|빼|제외|끊|먹지)", message,
    ))


def concept_allergy_mention(message: str, start: int, end: int) -> bool:
    """An explanation about an allergy is not a statement of ownership.

    Decide per mention: '나는 새우 알레르기가 있어. 비건과 우유 알레르기는
    무슨 관계야?' must keep the first assertion and ignore only the second.
    """
    prefix = re.split(r"[.!?;\n]", message[:start])[-1]
    tail = re.split(r"[.!?;\n]", message[end:], maxsplit=1)[0]
    # A direct condition assertion stays personal even with a concept request
    # later in the same sentence ('있는데 비건 뜻도 알려줘').
    quoted_proposition = re.match(
        r"\s*(?:은|는|이|가|도)?\s*(?:있|없)[가-힣]{0,6}(?:다는|라는)\s*(?:말|뜻|표현|문구|것)", tail,
    )
    if quoted_proposition and re.search(r"설명|뜻|의미|모순|관계|차이", tail):
        return True
    direct_assertion = re.match(
        r"\s*(?:은|는|이|가|도)?\s*(?:(?:좀|정말|전혀)\s*)?"
        r"(?:있(?:어|고|는데|습니다)|없(?:어|고|는데|습니다)|모르|의심)", tail,
    )
    if direct_assertion and not quoted_proposition:
        return False
    concept = re.search(r"뜻|의미|개념|정의|차이|관계|모순|다른|같은|다르|달라|구분", tail)
    if not concept:
        return False
    # The noun/comparison or quoted proposition is the question's object.
    return bool(re.match(r"\s*(?:와|과|랑|이랑|하고|은|는|이|가|도|에도|에|란|라는|의|걱정|차이|뜻|의미|개념)", tail)
                or re.search(r"비건|채식|락토프리|유당|표시|문구", prefix))


def recipe_followup_action(message: str) -> str | None:
    """Classify a reference to an existing recipe; no recipe context is inferred."""
    text = command_text(message)
    if re.search(r"(?:새로운|새)\s*(?:레시피|요리)|다른\s*(?:레시피|요리)", text):
        return None
    if re.search(r"(?:제품|상품)\s*(?:없이|없는).*?(?:레시피|요리)", text):
        return None
    if re.search(r"(?:\d+(?:\.\d+)?|한|두|세|네|반)\s*(?:인분|명|사람|배)(?:\s*분량)?|(?:양|분량).*(?:절반|반으로)", text) and re.search(
        r"늘려|줄여|바꿔|만들|해줘|맞춰|분량|양", text,
    ):
        return "servings"
    if re.search(r"빼(?:고|서|줘)|넣지\s*(?:말|마|않)|제외|대신|없이|재료(?:를)?\s*바꿔", text) and re.search(
        r"해줘|만들|말아줘|말아|마|빼줘|넣지|바꿔", text,
    ):
        return "edit"
    # Require only reference/modifier words around the requested recipe field.
    # '새우깡 재료 알려줘' is a new product question, not a recipe recap.
    match = re.fullmatch(
        r"(?:(?:방금|이전|아까|위에서\s*알려준|필요한|준비할|그|이|해당|(?:레시피|요리)(?:의|에서)?)\s*)*"
        r"(재료(?:들)?(?:\s*목록)?|조리\s*순서|조리법|만드는\s*(?:법|순서|과정))"
        r"(?:만|를|을|은|는|도)?\s*(?:(?:다시|짧게|간단히|간단하게|한\s*번\s*더)\s*)*(?:보여|정리해|알려|설명해|적어|해)줘", text,
    )
    if match:
        return "ingredients" if match[1].startswith("재료") else "steps"
    return None
