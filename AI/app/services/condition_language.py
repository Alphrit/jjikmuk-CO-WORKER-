"""Clause-local coordination shared by food condition readers."""

import re


WORD_COORDINATOR = r"(?:그리고|및|하고|이랑|랑|과|와)"
COORDINATOR = rf"(?:,|、|{WORD_COORDINATOR})"
# Unknown nouns require a separating space after a word conjunction. Known
# attached food names are split separately, avoiding ambiguous regex branches.
NOUN_LIST_SEPARATOR = rf"\s*(?:[,、]\s*|{WORD_COORDINATOR}\s+)"


def coordinated_mentions(text, terms):
    """Match attached noun lists without matching inside unrelated food names."""
    start = re.compile(rf"(?<![가-힣A-Za-z0-9])(?:{terms})")
    noun = re.compile(rf"(?:{terms})")
    link = re.compile(rf"\s*{COORDINATOR}\s*({terms})")
    mentions = []
    position = 0
    while first := start.search(text, position):
        mentions.append(first)
        position = first.end()
        while next_item := link.match(text, position):
            mention = noun.match(text, next_item.start(1))
            mentions.append(mention)
            position = mention.end()
    return mentions


def shared_predicate(text, mentions, index):
    """Follow noun-only links, never cross a separate predicate or subject."""
    last = index
    while last + 1 < len(mentions):
        between = text[mentions[last].end():mentions[last + 1].start()]
        if not re.fullmatch(rf"\s*{COORDINATOR}\s*", between):
            break
        last += 1
    end = mentions[last + 1].start() if last + 1 < len(mentions) else len(text)
    return text[mentions[last].end():end]


def listed_food_names(text, aliases):
    # Space-delimited conjunctions allow unknown nouns such as 사과/바나나
    # without splitting an internal syllable (e.g. the 과 in 사과).
    parts = re.split(r"\s*[,、]\s*|\s+(?:및|그리고)\s+|(?:하고|이랑|랑|과|와)\s+", text)
    names = []
    known = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
    for part in parts:
        # Attached lists are unambiguous for known labels; unsupported attached
        # words remain unresolved as a whole rather than being guessed safe.
        rest = part.strip()
        while match := re.match(rf"({known})(?:하고|이랑|랑|과|와)(?=\S)", rest, re.I):
            names.append(match[1].lower())
            rest = rest[match.end():]
        if rest:
            names.append(rest.lower())
    return names
