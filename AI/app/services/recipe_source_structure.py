"""Conservative syntax extraction, NOT recipe approval or portion conversion.

Keep every original token. Unparsed names, vague amounts and ranges must remain
visible for review instead of disappearing from a future allergy check.
"""

from __future__ import annotations

import re
import unicodedata
from fractions import Fraction


_GROUPS = {"재료", "주재료", "부재료", "양념", "양념장", "소스", "드레싱", "고명", "곁들임"}
_NUMBER = r"(?:\d{1,6}/\d{1,6}|\d{1,6}(?:\.\d{1,4})?)"
_UNITS = r"(?:작은술|큰술|티스푼|테이블스푼|줄기|봉지|토막|뿌리|마리|kg|mg|ml|mL|g|L|l|개|장|쪽|컵|모|알|대|줌)"
_MEASURE = re.compile(rf"^(?P<value>{_NUMBER})\s*(?P<unit>{_UNITS})(?P<note>\s*\([^\n]*\))?$")
_VAGUE = {"약간", "조금", "적당량", "소량", "취향껏"}
_SERVINGS = re.compile(r"^\s*\[\s*([1-9]\d?)\s*인분\s*\]\s*")


def _tokens(text: str) -> tuple[list[str], bool]:
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack, current, result = [], [], []
    balanced = True
    for character in text:
        if character in pairs:
            stack.append(pairs[character])
        elif character in pairs.values():
            if not stack or stack.pop() != character:
                balanced = False
        if character in ",;\n" and not stack:
            if token := "".join(current).strip():
                result.append(token)
            current = []
        else:
            current.append(character)
    if token := "".join(current).strip():
        result.append(token)
    return result, balanced and not stack


def structure_ingredients(text: str, recipe_name: str = "") -> dict:
    if not isinstance(text, str) or not text.strip() or len(text) > 20_000:
        raise ValueError("재료 원문은 1~20,000자여야 합니다.")
    original = text
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    serving_match = _SERVINGS.match(text)
    servings = int(serving_match.group(1)) if serving_match else None
    if serving_match:
        text = text[serving_match.end():]
    tokens, balanced = _tokens(text)
    items, unresolved, headings, issues = [], [], [], []
    group = None
    compact_name = "".join(unicodedata.normalize("NFC", recipe_name).split())
    for original_token in tokens:
        token = original_token.lstrip("●•·- ").strip()
        if len(token) > 512:
            unresolved.append(original_token)
            continue
        # Group headers must be explicit or from a short known heading list.
        if ":" in token or "：" in token:
            heading, remainder = re.split(r"[:：]", token, maxsplit=1)
            if not re.fullmatch(r"[가-힣A-Za-z /·-]{1,80}", heading.strip()):
                unresolved.append(original_token)
                continue
            group = heading.strip()
            headings.append({"name": group, "original": original_token})
            token = remainder.strip()
            if not token:
                continue
        elif token in _GROUPS or (compact_name and "".join(token.split()) == compact_name):
            group = token
            headings.append({"name": group, "original": original_token})
            continue
        matched = None
        # Consider only positions that could begin an amount; parentheses in a
        # food name (e.g. 황태(채)) are preserved, not mistaken for units.
        for boundary in range(1, len(token)):
            if not (token[boundary].isdigit() or token[boundary:].strip() in _VAGUE):
                continue
            name, amount = token[:boundary].strip(), token[boundary:].strip()
            if not re.fullmatch(r"[가-힣A-Za-z][가-힣A-Za-z0-9 ()·/-]{0,119}", name):
                continue
            if "/" in name or name.endswith(("-", " 약", " 각")) or re.search(r"\d$", name):
                continue
            measure = _MEASURE.fullmatch(amount)
            if not measure and amount not in _VAGUE:
                # A failed primary amount must not be reinterpreted as part of
                # the ingredient name ("소금 2g 또는" + "3g").
                break
            if measure:
                try:
                    quantity = Fraction(measure.group("value"))
                except (ValueError, ZeroDivisionError):
                    continue
                if quantity <= 0:
                    continue
            matched = {
                "name": name, "amount_text": amount, "group": group,
                "original": original_token,
                "quantity_text": measure.group("value") if measure else None,
                "unit": measure.group("unit") if measure else None,
                "measure_note": (measure.group("note") or "").strip() if measure else None,
                "amount_status": "explicit" if measure else "vague",
            }
            if not measure:
                issues.append("vague_amount")
            break
        if matched:
            items.append(matched)
        else:
            unresolved.append(original_token)
    if not balanced:
        issues.append("unbalanced_brackets")
    if unresolved:
        issues.append("unparsed_ingredient_text")
    if not items:
        issues.append("no_parsed_ingredients")
    if len(items) > 30:
        issues.append("too_many_ingredients_for_response")
    if servings is None:
        issues.append("servings_unconfirmed")
    # A syntactic parse still does not establish that sauce ingredients, recipe
    # steps, nutrition or allergy constraints are suitable.
    return {
        "format": "food-recipe-structure-v1", "original": original,
        "servings_explicit": servings, "headings": headings,
        "ingredients": items, "unparsed": unresolved,
        "parse_status": "needs_review" if issues else "parsed",
        "issues": list(dict.fromkeys(issues)),
        "content_review": "pending", "recommendation_eligible": False,
    }
