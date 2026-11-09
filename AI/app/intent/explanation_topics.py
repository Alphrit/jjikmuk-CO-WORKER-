"""Bounded topics for general food explanations, shared by routing and RAG.

These rules recognize supported wording; they are not a semantic entailment
model. Clinical, personal suitability and unreviewed detail still fail closed.
"""

from __future__ import annotations

import re

from app.intent.common import OUT_OF_DOMAIN_KEYWORDS


def compact_question(message: str) -> str:
    # An explicitly dismissed topic is not part of the new retrieval query.
    from app.intent.request_semantics import current_topic_message
    message = current_topic_message(message)
    text = re.sub(r"\s+", "", message).casefold()
    text = text.replace("에너지", "열량")
    for alias in ("슈가프리", "슈거프리", "sugarfree", "sugar-free", "노슈가"):
        text = text.replace(alias, "제로슈거")
    for alias in ("노애디드슈가", "no-added-sugar", "설탕을첨가하지않", "설탕을넣지않"):
        text = text.replace(alias, "무가당")
    facility = r"(?:제조|생산)(?:시설|설비|공장|라인|공정)"
    text = re.sub(facility + r"(?:이|가)?(?:같|동일)", "같은제조시설", text)
    text = re.sub(facility + r"(?:을|를|이|가)?(?:(?:같이|함께)(?:쓰|쓴|썼)|공유)", "같은제조시설", text)
    text = re.sub(r"(?:다른|여러)제품(?:도|을)?(?:같이|함께)?만드(?:는|는곳인)(?:공장|시설)", "같은제조시설", text)
    for alias in ("유당을뺀", "유당을제거", "유당을없앤"):
        text = text.replace(alias, "락토프리")
    return text


def resolve_explanation_question(message: str, chat_history=None) -> str:
    """Recover only a general topic from recent user turns, never their facts.

    This bounded rewrite is shared by routing and retrieval. Assistant text is
    not used as a topic authority, and personal/product requests stay intact.
    """
    def continue_question(current: str, previous: str | None) -> str:
        if not previous:
            return current
        text = compact_question(current)
        if len(text) > 80 or any(compact_question(cue) in text for cue in OUT_OF_DOMAIN_KEYWORDS) or any(cue in text for cue in (
            "제품", "이거", "바코드", "추천", "레시피", "찾아", "비교해",
            "먹어", "마셔", "섭취", "알레르기가있", "알러지가있",
        )):
            return current
        if any(cue in text for cue in ("허용", "검출", "혼입", "확률", "가열", "의무", "인증")) and not requested_topics(previous + " " + current):
            return previous + " " + current
        prior_topics = requested_topics(previous)
        followup = text.startswith(("그럼", "그러면", "그렇다면", "아그러면", "그건", "그게", "그뜻", "그말", "그거", "그문구", "그표시", "이표시", "거기", "여기")) or any(
            cue in text for cue in ("쉽게", "쉬운말", "간단히", "짧게", "자세히", "다시설명", "다시알려", "방금설명", "앞에서설명", "한문장", "두문장", "요약", "어려워")
        )
        energy_followup = bool(prior_topics.intersection({"sugar_zero", "sugar_claims", "sugar_energy"})) and bool(
            re.search(r"(?:칼로리|열량)(?:도|는|은|이|까지)?(?:아예|전혀)?(?:0|제로|없|[?？])", text)
        )
        allergy_detail = any(topic.startswith("allergy_") for topic in prior_topics) and any(
            cue in text for cue in ("섞", "얼마나", "혼입", "확률", "%", "원재료", "재료로넣", "재료로썼", "문구")
        )
        explicit = requested_topics(current)
        restatement = any(cue in text for cue in ("앞에서설명", "방금설명", "쉬운말", "쉽게", "줄여", "요약"))
        same_topic = not explicit or bool(explicit & prior_topics) or (
            "sugar_definition" in explicit and bool(prior_topics & {"sugars_added", "sugar_zero", "sugar_claims", "sugar_energy"})
        )
        if restatement and same_topic:
            return previous + " " + current
        if explanation_intent(current) and not energy_followup and not (allergy_detail and (
            followup or not explicit or explicit <= {"nutrition_percentage", "allergy_purchase", "allergy_label"}
        )):
            return current
        followup = followup or energy_followup or allergy_detail
        if not followup:
            return current
        if not requested_topics(previous + " " + current):
            # Retain the scope of an unsupported follow-up. Otherwise '%' in
            # an allergy probability question could become a nutrition-label
            # percentage explanation after the allergy context is discarded.
            return previous + " " + current
        # An explicitly named new concept owns the next question. Bare energy
        # follow-ups inherit a sugar/label topic rather than asking for a
        # generic definition of calories.
        if explicit and not allergy_detail and not any(term in text for term in ("칼로리", "열량", "단맛", "달아")):
            candidate = current + " 뜻을 설명해줘"
        elif (prior_topics.intersection({"sugar_zero", "sugar_claims", "sugar_energy"})
              and any(term in text for term in ("칼로리", "열량"))):
            # Carry the topic, not the previous question's entire task. Asking
            # about calories after a definition comparison does not request a
            # second definition comparison (whose evidence wasn't retrieved).
            candidate = "무당·무가당 표시만으로 열량이 없다고 볼 수 있는지 설명해줘"
        else:
            candidate = previous + " " + current + " 설명해줘"
        return candidate if explanation_intent(candidate) else current

    previous = None
    users = [item for item in (chat_history or []) if getattr(item, "role", None) == "user"]
    for item in users[-6:]:
        question = continue_question(item.content, previous)
        previous = question if explanation_intent(question) else None
    return continue_question(message, previous)


def requested_topics(message: str) -> set[str]:
    text = compact_question(message)
    # Distinguish what a label's serving means from prescribing a daily dose.
    serving_definition = bool(re.search(r"servingsize|서빙|1회제공량", text)) and bool(re.search(
        r"(?:권장|섭취량).*(?:뜻|의미|같|봐도|인가|인지)|차이", text,
    )) and not re.search(r"얼마|몇|개인|나에게|나한테|하루에", text)
    scope_text = re.sub(r"하루권장|권장섭취", "", text) if serving_definition else text
    if any(term in scope_text for term in (
        "치료", "완치", "진단", "법정", "법률", "법적", "법령", "시행일",
        "의무", "인증기준", "하루권장", "하루에얼마", "하루에몇", "권장섭취",
        "무염", "무가염", "당뇨", "혈당", "신장", "임산부", "임신", "어린이", "아기",
        "부작용", "발암", "안전성", "살빠", "살이빠", "살쪄", "살이쪄",
        "먹어도", "마셔도", "섭취해도", "안전해", "얼마나먹", "얼마나마",
        "먹을수", "마실수", "괜찮", "안심", "위험해", "문제없", "효능", "효과",
        "순탄수", "흡수율", "상한량", "허용량", "개인별", "나에게", "나한테", "질병", "건강에", "몸에좋",
    )):
        return set()
    allergy_context = any(term in text for term in (
        "알레르기", "알러지", "교차접촉", "교차오염", "같은제조시설",
        "동일제조시설", "동일한제조시설", "같은제조라인", "같은제조공정",
    )) or ("함유표시" in text and any(t in text for t in ("우유", "계란", "달걀", "땅콩", "대두", "밀")))
    if allergy_context and any(term in text for term in (
        "표시기준", "표시대상", "몇종", "목록", "허용", "검출", "혼입량",
        "몇g", "몇mg", "ppm", "확률", "%", "해외", "나라별", "국가별",
        "영국", "미국", "유럽", "캐나다", "일본", "안전", "먹으면",
        "교차반응", "증상", "검사", "응급", "약물", "가열", "끓이",
        "세균", "바이러스", "식중독", "감염",
    )):
        return set()
    if allergy_context and re.search(r"혼입|섞", text) and re.search(r"양|밀리그램|계산|추정", text):
        return set()
    # General concepts do not support arbitrary thresholds, legal standards or
    # numeric calculations for a specific product. Existing reviewed label
    # claims are the only exceptions for threshold questions.
    claim_terms = (
        "저당", "무당", "무설탕", "무가당", "고단백", "저지방", "저칼로리",
        "저열량", "제로슈거", "제로칼로리", "저염", "저나트륨", "저포화지방",
        "고식이섬유", "식이섬유풍부", "섬유질풍부",
    )
    if re.search(r"표시기준(?!량|치|단위)", text) and not any(t in text for t in claim_terms):
        return set()
    if any(t in text for t in ("몇g", "몇mg", "몇kcal", "몇칼로리", "몇그램", "몇밀리그램", "얼마까지", "허용량")):
        if not any(t in text for t in claim_terms):
            return set()
    if any(t in text for t in ("글루텐", "비건", "채식", "베지테리언", "락토프리", "유당")) and any(
        t in text for t in ("기준", "인증", "허용", "ppm", "몇", "수치")
    ):
        return set()

    topics: set[str] = set()
    vegan = any(t in text for t in ("비건", "vegan", "완전채식"))
    vegetarian = any(t in text for t in ("채식", "베지테리언", "vegetarian"))
    if vegan and (allergy_context or any(term in text for term in ("우유표시", "땅콩표시", "계란표시", "달걀표시"))
                  or ("원재료" in text and any(term in text for term in ("우유", "달걀", "계란", "땅콩")))):
        topics.add("vegan_allergy")
    elif vegan:
        topics.add("vegan_definition")
    if vegetarian and (not vegan or any(t in text for t in ("차이", "달라", "다르", "비교", "같은뜻", "같은말", "달걀", "계란", "유제품"))):
        topics.add("vegetarian_definition")
    if any(t in text for t in ("글루텐", "gluten")):
        topics.add("gluten_basics")
    if any(t in text for t in ("락토프리", "락토스프리", "유당제거", "유당이없", "유당없는", "lactosefree", "lactose-free")):
        topics.add("lactose_free")
    if "유당불내증" in text and ("우유알레르기" in text or "우유알러지" in text):
        topics.add("lactose_free")

    facility = any(term in text for term in (
        "같은제조시설", "동일제조시설", "동일한제조시설", "같은제조라인", "같은제조공정",
    )) or (allergy_context and any(term in text for term in ("같은시설", "같은공장"))) or bool(
        # '같은 시설에서 만든다는 표시는 무슨 뜻이야?' names the label phrase itself.
        re.search(r"같은(?:시설|공장)에서(?:만든|만들|제조)", text) and any(t in text for t in ("표시", "뜻", "문구", "의미")))
    contact = any(term in text for term in ("교차접촉", "교차오염")) or (
        allergy_context and any(term in text for term in ("공용설비", "공유장비", "공유설비"))
    )
    if facility:
        topics.add("allergy_facility_warning")
    if contact:
        controls = any(term in text for term in ("줄이", "줄여", "관리", "방지", "세척", "분리", "도구", "보관방법"))
        if controls:
            topics.add("allergy_contact_controls")
        if not controls or any(term in text for term in (
            "교차접촉뜻", "교차접촉의뜻", "교차접촉이뭐", "교차오염뜻", "교차오염이뭐",
        )):
            topics.add("allergy_cross_contact")
    if allergy_context and any(term in text for term in ("외식", "배달", "음식주문", "주문할", "주문전")):
        topics.add("allergy_ordering")
    if allergy_context and not topics and any(term in text for term in ("표시", "라벨", "원재료명", "함유")):
        topics.add("allergy_label")

    sugar_zero = any(term in text for term in ("무당", "무설탕", "제로슈거", "설탕제로", "zerosugar"))
    sugar_added = any(term in text for term in ("무가당", "설탕무첨가", "설탕안넣", "설탕을안넣", "noaddedsugar"))
    sugar_energy = "감미료" in text or ((sugar_zero or sugar_added) and any(
        term in text for term in ("열량", "칼로리", "단맛", "달아", "달지", "달아요")
    ))
    if "당알코올" in text or "당알콜" in text:
        topics.add("sugar_alcohol")
    elif sugar_energy:
        topics.add("sugar_energy")
    elif sugar_zero:
        topics.add("sugar_zero")
    elif sugar_added:
        topics.add("sugar_claims")
    if any(t in text for t in ("첨가당", "총당류", "addedsugar", "totalsugar")):
        topics.add("sugars_added")
    if "당류" in text and "탄수" not in text and not topics and any(t in text for t in ("뭐", "뜻", "정의", "의미", "설명")):
        topics.add("sugar_definition")
    if any(t in text for t in ("탄수화물", "탄수", "carbohydrate")):
        topics.add("carbohydrate_sugars")
    for terms, topic in (
        (("저당",), "low_sugar"),
        (("고단백", "단백질함유", "단백질풍부"), "high_protein"),
        (("저지방",), "low_fat"),
        (("저칼로리", "저열량"), "low_energy"),
        (("제로칼로리", "제로열량", "무열량"), "zero_energy"),
        (("저포화지방",), "low_saturated_fat"),
        (("고식이섬유", "식이섬유풍부", "섬유질풍부"), "high_fiber"),
    ):
        if any(term in text for term in terms):
            topics.add(topic)
    # '제로 음료는 칼로리가 0이야?': a zero claim asked about energy.
    if ("제로" in text and "zero_energy" not in topics and not topics & {"sugar_energy", "sugar_alcohol"}
            and any(t in text for t in ("칼로리", "열량")) and any(t in text for t in ("0이", "0인", "0칼", "없", "제로야", "제로인", "제로에요", "제로예요"))):
        topics.add("zero_energy")
    if any(t in text for t in ("저나트륨", "저염기준", "저염표시", "나트륨함량기준", "나트륨표시기준")):
        topics.add("low_sodium")
    if any(t in text for t in ("식이섬유", "섬유질")) and "high_fiber" not in topics:
        topics.add("fiber_basics")
    if any(t in text for t in ("지방", "fat")) and not topics.intersection({"low_fat", "low_saturated_fat"}):
        topics.add("fat_types")
    if "단백질" in text and "high_protein" not in topics and ("lactose_free" not in topics or re.search(r"단백질(?:이란|은뭐|의뜻|의역할)", text)):
        topics.add("protein_basics")
    if "나트륨" in text and "소금" in text and any(t in text for t in ("차이", "같", "달라", "다르", "관계")):
        topics.add("sodium_salt")
    if any(t in text for t in ("짠맛", "안짜", "안짠", "짜지않", "안짜도")):
        topics.add("sodium_taste")
    if any(t in text for t in ("서빙", "servingsize", "1회제공량은권장", "1회제공량이권장", "한번에먹으라는")) or ("1회제공량" in text and "권장량" in text):
        topics.add("serving_portion")
    if (re.search(r"(?<![0-9])0(?:\.0)?(?:g|그램|mg|밀리그램|으로표시)", text) or "반올림" in text) and not topics.intersection({"zero_energy", "sugar_zero"}):
        topics.difference_update({"protein_basics", "fat_types", "sugar_definition"})
        topics.add("nutrition_zero")
    units = set(re.findall(r"(?<![A-Za-z])(?:mg|g)(?![A-Za-z])", message.lower()))
    if units == {"mg", "g"} or any(t in text for t in ("단위차이", "밀리그램", "그램과", "그램이랑")):
        topics.add("nutrition_units")
    if ("열량" in text or "칼로리" in text or "kcal" in text) and not topics.intersection({
        "sugar_energy", "sugar_alcohol", "low_energy", "zero_energy", "high_fiber", "low_saturated_fat",
    }) and any(t in text for t in ("뭐", "뜻", "의미", "에너지", "두배", "2배", "같은말")):
        topics.add("calories_basics")
    if any(term in text for term in ("기준치", "퍼센트", "백분율", "%")) and "low_saturated_fat" not in topics:
        topics.add("nutrition_percentage")
    if (any(term in text for term in ("기준치", "퍼센트", "백분율", "%", "비율"))
            and (re.search(r"계산|구해|구하|산출|어떻게나와|어떻게정해|어떻게매겨", text)
                 or (re.search(r"열량|칼로리|트랜스지방", text) and re.search(r"없|비어|빈칸|공란|안나와|안적", text)))):
        # How the % is computed, or why 열량·트랜스지방 have none.
        topics.difference_update({"nutrition_percentage", "fat_types", "calories_basics"})
        topics.add("percentage_calculation")
    if re.search(r"콜레스테롤|트랜스지방", text) and re.search(r"(?<![0-9.])0(?![0-9.])|미만", text) and re.search(r"표시|뜻|의미|없|진짜", text):
        # '콜레스테롤 0 표시 무슨 뜻이야?' asks the zero-display rule, not the concept.
        topics.difference_update({"fat_types"})
        topics.add("nutrition_zero")
    if "콜레스테롤" in text and not topics.intersection({"nutrition_zero", "nutrition_percentage", "percentage_calculation"}) and re.search(
            r"뭐|뜻|의미|이란|란|무엇|설명|어디|들어있|들어가|어떤", text):
        topics.add("cholesterol_basics")
    # These label terms name a product fact too ('카페인 함량 알려줘', '이 우유
    # 소비기한'): only questions about the term's meaning are explanations.
    explains = re.search(r"뜻|의미|뭐|무엇|무슨|이란|차이|달라|다르|구분|왜|어떻게봐|어떻게읽|기준|시행|언제부터|설명", text)
    if explains and re.search(r"유통기한|소비기한|품질유지기한", text) and "언제까지" not in text:
        topics.add("date_marking")
    if re.search(r"haccp|해썹|안전관리인증", text):
        topics.add("haccp_mark")
    if explains and ("고카페인" in text or ("카페인" in text and re.search(r"표시|문구", text))):
        topics.add("caffeine_label")
    if explains and (("원재료" in text and re.search(r"순서|순으로|차례|앞에|먼저적", text)) or (
            re.search(r"첨가물|감미료|보존료|착색료", text) and re.search(r"용도|괄호|명칭|이름옆|같이적|함께적", text))):
        # '감미료(용도) 표시' asks how additives are labelled, not about sweetness.
        topics.discard("sugar_energy")
        topics.add("ingredient_order")
    if not topics.intersection({
        "sugar_claims", "sugar_zero", "sugar_energy", "low_sugar", "high_protein",
        "low_fat", "low_energy", "zero_energy", "nutrition_units", "low_sodium", "high_fiber",
        "low_saturated_fat", "serving_portion", "nutrition_zero",
    }) and any(term in text for term in (
        "100g", "100ml", "1회제공", "한봉지", "한팩", "총내용량", "기준량", "표시단위",
        "기준단위", "영양정보기준", "얼마나먹었", "먹은양", "영양정보비교", "영양성분비교",
    )):
        topics.add("nutrition_basis")
    if not topics and (allergy_context or "원재료" in text):
        topics.add("allergy_purchase")
    if any(term in text for term in ("저염", "나트륨", "소금", "덜짜게", "싱겁게")) and not topics.intersection({"low_sodium", "sodium_salt", "sodium_taste", "nutrition_units"}):
        topics.add("sodium_practice")
    if not topics and any(term in text for term in ("강조", "앞면", "포장문구")):
        topics.add("nutrition_claims")
    if not topics and any(term in text for term in ("영양표시", "영양성분표", "식품표시")):
        topics.add("nutrition_basis")
    if not topics and any(term in text for term in ("식생활", "식단", "영양안내", "영양가이드")):
        topics.add("diet_direction")
    return topics


def unsupported_allergy_quantity_question(message: str) -> bool:
    """Keep a quantitative cross-contact request in its explanatory context.

    This recognizes an evidence limit, not a retrievable topic or an estimate.
    Personal suitability and explicitly referenced products keep their routes.
    """
    text = compact_question(message)
    if any(term in text for term in ("먹어", "마셔", "섭취", "이제품", "이거", "바코드")):
        return False
    context = any(term in text for term in (
        "같은제조시설", "같은제조라인", "교차접촉", "교차오염", "혼입", "알레르기", "알러지",
    ))
    quantitative = "확률" in text or (
        any(term in text for term in ("혼입", "섞"))
        and any(term in text for term in ("얼마나", "몇", "%", "수치", "숫자", "양"))
    )
    return context and quantitative and not requested_topics(message)


def explanation_intent(message: str) -> str | None:
    """Route explicit concepts before ingredient/product-name interpretation."""
    text = compact_question(message)
    if any(cue in text for cue in ("이제품", "이거", "이음료", "그제품", "바코드", "이과자", "이우유", "추천", "찾아줘")):
        return None
    if "제품" in text and any(cue in text for cue in ("알려", "골라", "어떤", "뭐")) and not any(
        cue in text for cue in ("기준", "뜻", "의미", "표시", "문구", "원재료", "확인", "방법", "개념")
    ):
        return None
    if any(cue in text for cue in ("성분알려", "성분보여", "원재료알려", "원재료뭐")) and not any(
        cue in text for cue in ("뜻", "의미", "방법", "차이", "구분", "정의", "관계", "표시", "개념", "기준", "확인")
    ):
        return None
    if not any(cue in text for cue in (
        "뭐", "무엇", "뜻", "의미", "차이", "달라", "다르", "다른", "같아", "같은", "같다", "알려", "설명",
        "어떻게", "어떤", "왜", "인가", "인지", "입니까", "인가요", "확인", "구분", "고르", "고를", "정의", "기준",
        "이란", "있나", "사이즈", "이야", "일까", "진짜", "없는거야", "뜻은", "봐도", "봐야", "말이지", "맞지",
    )):
        return None
    topics = requested_topics(message)
    if not topics:
        return None
    if any(topic.startswith("allergy_") for topic in topics) or topics.intersection({"vegan_allergy", "lactose_free"}):
        return "general_allergy_guide"
    if topics.intersection({"vegan_definition", "vegetarian_definition", "gluten_basics", "low_sodium", "low_saturated_fat", "high_fiber", "low_sugar", "low_fat", "low_energy", "high_protein", "diet_direction"}) or "저염" in text:
        return "general_diet_guide"
    return "general_nutrition_guide"
