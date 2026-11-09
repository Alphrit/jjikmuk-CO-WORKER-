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
    # Find the dismissal first and check the short topic prefix separately: a
    # '(?:[가-힣]+\s*){0,3}' prefix backtracks cubically on long unspaced text
    # and, holding the GIL, stalls every other request.
    match = re.search(
        r"(?:얘기|이야기|설명|질문)(?:는|은|을|를)?\s*(?:됐고|말고|여기까지\s*하고|그만하고)[,，\s]*", message,
    )
    if not match:
        return message
    prefix = message[:match.start()].split()
    rest = re.match(r"(.+)$", message[match.end():])
    if len(prefix) > 3 or not all(re.fullmatch(r"[가-힣A-Za-z]+", word) for word in prefix) or not rest:
        return message
    return rest[1]


_LABEL_FIELD = r"원재료|원료|성분|알레르기|알러지|영양|나트륨|당류|당분|칼로리|열량|단백질|지방|탄수화물|콜레스테롤|먹어도|마셔도|괜찮"
_SHORT_NUTRIENT_FIELD = r"(?:그럼\s*)?(?:나트륨|당류|당분|칼로리|열량|단백질|포화지방|트랜스지방|식이섬유|지방|탄수화물|콜레스테롤)(?:은|는|도)?\s*[?？]*"


def productless_reference_question(message: str) -> bool:
    """'이 상품의 원재료 알려줘', '칼로리는?': a label fact of a product the turn did not name."""
    text = " ".join(message.strip().split())
    if re.search(r"비교|추천|대체|레시피", text):
        return False
    # A bare '그건' is also discourse ('그건 알겠고, 당류 0g은…'); only a
    # deictic with 상품/제품 refers to a product.
    deictic = re.search(r"(?:^|\s)(?:이|그|저|해당|현재|방금)\s*(?:상품|제품)", text)
    return bool((deictic and re.search(_LABEL_FIELD, text)) or re.fullmatch(_SHORT_NUTRIENT_FIELD, text))


_DAILY_NUTRIENT = r"커피|카페인|나트륨|소금|당류|설탕|포화지방|트랜스지방|지방|단백질|탄수화물|콜레스테롤|식이섬유|열량|칼로리"


def general_label_term_question(message: str) -> bool:
    """'나트륨 하루 권장량', '카페인은 하루에 얼마까지', '유통기한이랑 소비기한 차이': general, not a product."""
    text = " ".join(message.strip().split())
    if re.search(r"(?:^|\s)(?:이|그|저|해당)\s*(?:상품|제품|과자|음료|우유|거)|바코드", text):
        return False
    if re.search(r"추천|비교|골라|고를|먹어도|마셔도|먹을\s*수|대비|기준으로|(?:으로|로)\s*보면|몇\s*(?:개|봉지|캔|병|팩)", text):
        # Recommendations, comparisons and eatability keep their own routes.
        return False
    # Only when the nutrient leads: '허니버터칩 나트륨이 하루 권장량의…' names a product.
    if re.match(rf"(?:{_DAILY_NUTRIENT})\s*(?:은|는|이|가|의)?\s*(?:하루|1일|일일)", text):
        return True
    if re.match(rf"(?:하루|1일|일일)\s*(?:에\s*)?(?:{_DAILY_NUTRIENT})\s*(?:은|는|이|가|의)?\s*(?:섭취|권장|기준)", text):
        return True
    if re.match(rf"(?:{_DAILY_NUTRIENT})\s*(?:은|는|이|가|의)?\s*(?:권장\s*(?:섭취)?\s*량|섭취\s*기준|섭취량\s*기준)", text):
        return True
    # The terms themselves, asked first; a product's own date or whether an
    # expired food is still edible stays out of scope.
    return bool(re.match(r"(?:유통\s*기한|소비\s*기한|품질\s*유지\s*기한)", text)
                and re.search(r"차이|뜻|의미|뭐|무슨|달라|다르|다른|구분|언제부터|시행", text)
                and not re.search(r"언제까지|지난|지나|넘은|넘었|상한|상했|버려", text))


def _halved_amount(text: str) -> bool:
    """'양을 절반으로': an amount word, then 절반/반으로 later on the same line.

    Written as two searches; '(?:양|분량).*(?:절반|반으로)' rescans the rest of
    the line from every 양 and is quadratic on repeated text.
    """
    for line in text.splitlines():
        amount = re.search(r"양|분량", line)
        if amount and re.search(r"절반|반으로", line[amount.end():]):
            return True
    return False


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


def vague_food_recommendation(message: str) -> bool:
    """'다이어트 간식 추천', '아무거나 추천해줘': asks for food, names no product or recipe."""
    return bool(re.search(
        r"(?:간식|과자|음료|음식|먹을\s*(?:거|것)|마실\s*(?:거|것)|아무거나|뭐\s*먹을지)\s*(?:좀\s*)?추천(?:해\s*줘|해줘|해\s*주세요|해|좀)?[요?？.!\s]*$",
        message.strip(),
    )) and not re.search(r"레시피|요리|만드는", message)


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
    if (re.search(r"(?:(?<!\d)\d+(?:\.\d+)?|한|두|세|네|반)\s*(?:인분|명|사람|배)(?:\s*분량)?", text)
            or _halved_amount(text)) and re.search(r"늘려|줄여|바꿔|만들|해줘|맞춰|분량|양", text):
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
