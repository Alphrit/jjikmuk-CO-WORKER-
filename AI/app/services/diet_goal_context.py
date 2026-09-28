"""Bounded dietary-goal updates, shared by lookup and response generation.

These are conversation goals, never edits to an account restriction. Product
names and goal edits are separated without treating a failed lookup as generic.
"""

import re
from dataclasses import dataclass

from app.data.dictionaries import SPECIAL_DIET_KEYWORDS


_ALIASES = {term.lower(): name for name, terms in SPECIAL_DIET_KEYWORDS.items()
            for term in (name, *terms)}
_TERMS = "|".join(re.escape(term) for term in sorted(_ALIASES, key=len, reverse=True))
_MENTION = re.compile(_TERMS, re.I)
_PARTICLES = r"\s*(?:은|는|을|를|도|으로|로)?\s*(?:(?:조건|목표)(?:은|는|을|를|만)?\s*)?"
_REMOVE = r"(?:말고|대신|없이|필요\s*없(?:어|고|어요)?|빼(?:고|줘(?:요)?|주세요)|(?:제외|해제|취소|초기화)(?:하고|해줘(?:요)?|해주세요|할게(?:요)?)?)(?![가-힣])"
_RESET = re.compile(r"(?:식이\s*)?(?:조건|목표)(?:은|는|을|를)?\s*(?:(?:모두|전부)\s*)?(?:초기화|해제|지워)(?!하지|하진|할지|해도|할까|\s*안)|조건\s*없이")
_NOUN_LINK = re.compile(r"\s*(?:,|그리고|및|과|와|하고|이랑|랑)?\s*")


def _mention_groups(message):
    groups = []
    for mention in _MENTION.finditer(message):
        if groups and _NOUN_LINK.fullmatch(message[groups[-1][-1].end():mention.start()]):
            groups[-1].append(mention)
        else:
            groups.append([mention])
    for index, group in enumerate(groups):
        end = groups[index + 1][0].start() if index + 1 < len(groups) else len(message)
        predicate = re.split(r"[.!?;\n,]", message[group[-1].end():end], maxsplit=1)[0]
        yield group, predicate


@dataclass(frozen=True)
class DietGoalUpdate:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    replace: bool = False

    @property
    def touched(self):
        return bool(self.added or self.removed or self.replace)

    def apply(self, previous=()):
        base = () if self.replace else previous
        return list(dict.fromkeys([*(name for name in base if name not in self.removed), *self.added]))

    def restate(self, message):
        """Carry only parsed goals across a task-text rewrite, not raw clauses."""
        parts = ["식이 목표는 초기화해줘"] if self.replace else []
        parts += [f"{name} 목표는 빼줘" for name in self.removed]
        parts += [f"{name} 목표로 해줘" for name in self.added]
        instructions = ". ".join(parts)
        if not parts or message.rstrip(" .").endswith(instructions):
            return message
        return ". ".join([message.rstrip(" ."), instructions])


def read_diet_goal_update(message):
    added, removed = [], []
    resets = [match for match in _RESET.finditer(message) if not re.search(
        rf"(?:{_TERMS})(?:은|는|을|를|도)?\s*$", message[:match.start()], re.I,
    )]
    reset_end = resets[-1].end() if resets else 0
    replace = bool(resets)
    for group, predicate in _mention_groups(message):
        if group[0].start() < reset_end:
            continue
        withdrawal = re.match(_PARTICLES + _REMOVE, predicate)
        if not withdrawal and re.match(r"\s*만(?:으로)?(?=\s|$)", predicate):
            replace = True
            added.clear()
        for mention in group:
            name = _ALIASES[mention[0].lower()]
            keep, discard = (removed, added) if withdrawal else (added, removed)
            if name in discard:
                discard.remove(name)
            if name not in keep:
                keep.append(name)
    return DietGoalUpdate(tuple(added), tuple(removed), replace)


def product_text_without_goal_edits(message):
    """Strip explicit goal operations, never a bare adjective in a name."""
    spans = []
    for group, predicate in _mention_groups(message):
        withdrawal = re.match(_PARTICLES + _REMOVE, predicate)
        if withdrawal:
            spans.append((group[0].start(), group[-1].end() + withdrawal.end()))
    for start, end in reversed(spans):
        message = message[:start] + " " + message[end:]
    return re.sub(rf"({_TERMS})만(?=\s|으로)", r"\1", message, flags=re.I)


def is_goal_only_reply(message):
    """Recognize a bounded edit, not a catalog name containing a diet word."""
    if not (_MENTION.search(message) or re.search(r"식이|조건|목표", message)):
        return False
    text = _MENTION.sub("목표", message)
    word = re.compile(
        r"(?:목표|조건)(?:은|는|을|를|도|만|으로|로|만으로)?|식이|"
        r"그리고|및|과|와|하고|이랑|랑|대신|말고|필요|없이|모두|전부|다시|"
        r"(?:빼|추가해|제외해|해제해|해|말아)(?:줘(?:요)?|주세요|줄래(?:요)?)|"
        r"빼고|빼지|없(?:어|고|어요)|마(?:요|세요)?|"
        r"(?:취소|초기화|해제|제외)(?:하고|해줘|해주세요|할게(?:요)?|하지)"
    )
    words = re.findall(r"[^\s.,!?]+", text)
    return bool(words) and all(word.fullmatch(token) for token in words)
