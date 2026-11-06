"""Find checkable claims in the agent's final message. Rules only, no model.

A claim is a sentence saying that tests passed, that lint or a build succeeded, or that work was
committed, pushed or merged. Only those are looked for: they are the ones a transcript can back or
contradict. Questions, instructions, plans and negated sentences are not claims.

The patterns come from how agents actually phrase these in the owner's transcripts (zh-TW and English),
measured on 1,636 end-of-turn messages, not from a style guide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KINDS = ("tests", "lint", "build", "commit", "push", "merge")


@dataclass(frozen=True)
class Claim:
    kind: str  # one of KINDS
    quote: str  # the sentence it was found in, trimmed


_FENCE = re.compile(r"```.*?(?:```|$)", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]*)`")
# A sentence ends at a full stop, a semicolon or a line break. A table row stays one sentence
# ("| 程式 | 已 commit、已 push |"); a clause after "；" is its own ("測試全過；全套還沒跑").
_SENTENCE_END = re.compile(r"[。！!；;\n]|(?<=[^\s.][a-z0-9)\]])\.\s+")
# A question mark ends a sentence too, and stays in it so the sentence reads as a question; one inside a
# query string ("新版（?v= 已換）") is not a question mark.
_QUESTION_END = re.compile(r"([？?])(?=\s|$|[」』）)\]])")
_MARKUP = re.compile(r"\*\*|__|^[-*>]\s+|^\d+\.\s+|^#+\s+", re.MULTILINE)

# A sentence that asks, instructs, plans or hedges is not a claim about what happened.
_QUESTION = re.compile(
    r"[？?](?!\w)|嗎|要我|要不要|是不是|還不知道|不確定|不清楚"
    r"|\bshould i\b|\bdo you want\b|\bwant me\b|\bsay\s+[\"“']", re.I)
_CONDITIONAL = re.compile(
    r"必須|需要|應該|才會|就會|將會|才能|之後再|然後再|之前先|等你|請你|如果|若是|就好|就可以|就能|我就|(?:了|完|後)就"
    r"|要(?:先|再|重新|用|全綠|通過|全過|都過|綠)|會(?:跑|做|先|再|觸發|一起)|再\s*(?:commit|push|merge|推|提交|合併|跑)"
    r"|\b(?:if|once|when|unless|until|will|would|should|must|need(?:s)? to|to be|going to)\b", re.I)
# "After the tests pass, push it" is a plan; "all tests passed after the fix" is what happened.
_PLAN_AHEAD = re.compile(r"\b(?:before|after)\b", re.I)
# English negation shortly before the claim ("Nothing is committed or pushed yet", "has not been pushed") or
# right after it ("pushed? not yet"); a "no" later in the sentence ("all tests pass, no regressions") is not one.
_NEGATED_EN_BEFORE = re.compile(r"(?:\b(?:not|nothing|never|no|without|cannot)\b|n't\b)[^,.;]{0,40}$", re.I)
_NEGATED_EN_AFTER = re.compile(r"^[^,.;]{0,12}\b(?:yet|not)\b", re.I)
# Chinese negation right before the claim, a quote or bracket allowed in between: 未 push, 沒有「已 commit.
_NEGATED_ZH_BEFORE = re.compile(r"(?:沒有|沒|未|不|非|無|尚未|還沒|並未|無法|別|不是)\s*[「“\"(（『]?\s*$")
# Inside a claim's own clause nothing may be negated or failing: "測試沒通過" and "tests did not pass" are
# not "tests pass".
_SPAN = r"(?:(?!沒|未|不|非|無|失敗|fail|\bnot\b|n't\b|\bnever\b|\bno\b)[^，。；])*?"
_DONE = r"(?:已經|已|都已|都|也都|也|剛剛|剛|全部|全都)\s*"
_SHA = r"`?[0-9a-f]{6,40}`?"
# "merged" is about git only when the sentence says so: blog posts and data rows get 合併 too.
_GIT_CONTEXT = re.compile(
    r"分支|branch|\bmain\b|\bmaster\b|\bpr\b|#\d+|pull request|\bgit\b|\bgh\b|\b[0-9a-f]{7,40}\b", re.I)

_PATTERNS = {
    "tests": re.compile(
        rf"(?:測試|全套|tests?\b|testing|spec|phpunit|pytest|vitest|jest|unit|e2e|smoke){_SPAN}"
        r"(?:全部通過|都通過|全通過|全過|都過|通過|全綠|綠|過了|pass(?:ed|es|ing)?\b|0\s*個?\s*失敗|0\s+fail(?:ed|ures?)\b)"
        r"|\b\d[\d,]*\s*passed\b|\b\d+\s*/\s*\d+\s*(?:通過|都過|全過|綠|pass(?:ed)?\b)"
        r"|\b(?:all|every)\s+(?:\d+\s+)?tests?\s+(?:pass|passed|passing|are green)\b|\bthey all pass\b"
        r"|\btest suite (?:passes|passed|is green)\b"
        r"|\b\d[\d,]*\s*(?:個|條|項|題|支|檔)?\s*(?:全部|都)?(?:通過|全過|都過|綠)(?![\w色])",
        re.I),
    "lint": re.compile(
        r"(?:\blint(?:er|ing)?\b|\bruff\b|eslint|flake8|mypy|pylint|clippy|\btsc\b|typecheck|型別檢查|類型檢查|phpstan"
        rf"|larastan|\bpint\b|psalm|stylelint|golangci|rubocop|pyright){_SPAN}"
        r"(?:通過|過了|都過|沒問題|沒有問題|乾淨|clean|pass(?:es|ed|ing)?\b|\bok\b|0\s*個?\s*錯(?:誤)?"
        r"|0\s+errors?\b|no errors?\b|0\s+issues?\b)",
        re.I),
    "build": re.compile(
        r"(?:已經|已|重新|重|剛|也)\s*build\b(?!\s*好?的)"
        r"|\bbuild\s*(?:完成|完了|完\b|好了|成功|通過|過了|過\b|OK\b|succe\w+|pass\w*\b|is green|works)"
        r"|\bbuild\b[^，。；]{0,24}?(?:都過|都通過|全過|成功|通過)"
        r"|\bbuilt\s+(?:ok|fine|successfully|cleanly)\b|編譯(?:成功|通過|過了)",
        re.I),
    "commit": re.compile(
        rf"{_DONE}commit(?:ted)?(?!\s*的)|\bcommit\s*(?:了|完成|完\b|好了|成\s*{_SHA}|在本機|進|到\s*(?:整合|分支|branch|main|master|feat))"
        rf"|{_DONE}提交(?=[^，。；]{{0,12}}commit)|提交了\s*\d+\s*個?\s*commit"
        r"|\b(?:is|are|was|were|has been|have been|got|now|already)\s+committed\b|\bi(?:'ve| have)?\s+committed\b"
        r"|\bcommitted\s*(?:,|and\b|as\b|to\b|on\b|in\b|the\b|everything\b|all\b)",
        re.I),
    "push": re.compile(
        rf"{_DONE}push(?:ed)?(?!\s*的)|\bpush\s*(?:了|完成|完\b|好了|成功|上去了|到了)"
        rf"|{_DONE}推(?:上去|上|到)?(?![上去]*\s*的)|推上去了|推上了|推到了|推了(?!\s*的)"
        r"|\b(?:is|are|was|were|has been|have been|got|now|already)\s+pushed\b|\bi(?:'ve| have)?\s+pushed\b"
        r"|\bpushed\s+(?:to|the|it|them|everything|both)\b",
        re.I),
    "merge": re.compile(
        rf"{_DONE}merge[d]?(?!\s*的)|\bmerge\s*(?:了|完成|完\b|好了|進|到)|{_DONE}合併(?!\s*的)|合併(?:了|完成|進)"
        r"|\b(?:is|are|was|were|has been|have been|got|now|already)\s+merged\b|\bi(?:'ve| have)?\s+merged\b"
        r"|\bmerged\s+(?:into|to|as|it|them)\b|\bpr\s*#?\d+\s+(?:is\s+|was\s+)?merged\b",
        re.I),
}
_NOT_GIT_PUSH = re.compile(r"indexnow|notification|通知|推播", re.I)
# "1 failed, 2 passed" reports a failure, whatever else it says.
_FAILING = re.compile(r"\b[1-9]\d*\s*(?:failed|failures?|errors?)\b|[1-9]\d*\s*(?:個|條|項)?\s*失敗", re.I)


def find_claims(message: str) -> list[Claim]:
    """Every checkable claim in the message, one per kind per sentence, in the order they appear."""
    claims: list[Claim] = []
    for sentence in sentences(message):
        if _QUESTION.search(sentence) or _CONDITIONAL.search(sentence):
            continue
        for kind in KINDS:
            m = _PATTERNS[kind].search(sentence)
            if m is None:
                continue
            if _NEGATED_ZH_BEFORE.search(sentence[max(0, m.start() - 8):m.start()]):
                continue
            if _PLAN_AHEAD.search(sentence[:m.start()]):
                continue
            if re.search(r"[A-Za-z]", m.group(0)) and (
                    _NEGATED_EN_BEFORE.search(sentence[:m.start()]) or _NEGATED_EN_AFTER.search(sentence[m.end():])):
                continue
            if kind == "tests" and _FAILING.search(sentence):
                continue
            if kind == "merge" and not _GIT_CONTEXT.search(sentence):
                continue
            if kind == "push" and _NOT_GIT_PUSH.search(sentence):
                continue
            claims.append(Claim(kind, sentence))
    return claims


def sentences(message: str) -> list[str]:
    """The message as plain sentences: code blocks dropped, inline code kept as its text, markup stripped."""
    text = _QUESTION_END.sub(r"\1\n", _INLINE_CODE.sub(r"\1", _FENCE.sub(" ", message or "")))
    out = []
    for raw in _SENTENCE_END.split(text):
        sentence = re.sub(r"\s+", " ", _MARKUP.sub(" ", raw.replace("|", " "))).strip(" -–—:：、，,")
        sentence = re.sub(r"\s+([:：,，、])", r"\1", sentence)  # "**Tests**: …" reads as "Tests: …"
        if sentence:
            out.append(sentence)
    return out
