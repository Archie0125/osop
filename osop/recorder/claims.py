"""Find checkable claims in the agent's final message. Rules only, no model.

A claim is a sentence saying that tests passed, that lint or a build succeeded, or that work was
committed, pushed or merged. Only those are looked for: they are the ones a transcript can back or
contradict. Questions, instructions, plans and negated sentences are not claims.

The patterns come from how agents actually phrase these in the owner's transcripts (zh-TW and English),
measured on 1,636 end-of-turn messages (and re-measured on 3,848 on 2026-11-10), not from a style guide.
"""

from __future__ import annotations

import re
from collections.abc import Collection
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
# A question mark ends a sentence too, and stays in it so the sentence reads as a question. "測試過了？沒有"
# and "tests pass?no" have no space after it; one that opens a query string ("新版（?v= 已換）", "?page=2")
# is not a question mark.
_QUESTION_END = re.compile(r"(？|\?(?![a-z0-9_.-]+=))")
_MARKUP = re.compile(r"\*\*|__|^[-*>]\s+|^\d+\.\s+|^#+\s+", re.MULTILINE)

# A sentence that asks, instructs, plans or hedges is not a claim about what happened.
_QUESTION = re.compile(
    r"？|\?(?![a-z0-9_.-]+=)|嗎|要我|要不要|是不是|還不知道|不確定|不清楚"
    r"|\bshould i\b|\bdo you want\b|\bwant me\b|\bsay\s+[\"“']", re.I)
_CONDITIONAL = re.compile(
    r"必須|需要|應該|才會|就會|將會|才能|之後再|然後再|之前先|等你|請你|如果|若是|若無|要是|的話|一旦|只要|除非|假如"
    r"|就好|就可以|就能|我就|我會"
    r"|(?:了|完|後|綠|過|好)就|依序"
    r"|要(?:先|再|重新|用|全綠|通過|全過|都過|綠)|會(?:跑|做|先|再|觸發|一起)|再\s*(?:commit|push|merge|推|提交|合併|跑)"
    # "都要你同意", "要 rebase 再 build": the owner's go-ahead or a step still to do
    r"|要你|要\s*rebase"
    # "還在跑（目前都 PASS）" is not over; "當時都過" is about another time
    r"|還在跑|正在跑|當時"
    r"|\b(?:if|once|when|unless|until|will|would|should|must|need(?:s)? to|to be|going to)\b"
    r"|\b(?:can|could|may|might)(?:not)?\s+(?:now\s+|then\s+|also\s+)?be\b", re.I)
# "Verify that all tests pass before merging", "make sure the tests pass before you push": an instruction, not a
# claim. A clause that opens with such a verb ahead of the claim asks for something ("all tests passed, please
# check the diff" keeps its claim); so does "the tests pass before merging", unless the claim is past tense
# ("all tests passed before merging").
_INSTRUCTION = re.compile(
    r"(?:^|[,，]\s*)(?:please\s+)?(?:verify|make sure|ensure|check|confirm|remember|re-?run|run)\b", re.I)
_BEFORE_DOING = re.compile(r"\bbefore\s+(?:you\s+)?\w+ing\b", re.I)
# "After the tests pass, push it" is a plan; "all tests passed after the fix" and "after the fix, all tests
# passed" are what happened. "After the changes are committed, push" is a plan again: a present passive.
_PLAN_AHEAD = re.compile(r"\b(?:before|after)\b", re.I)
_PAST = re.compile(r"\b(?:passed|committed|pushed|merged|built|succeeded)\b", re.I)
# "is merged", "is successfully merged", "is reviewed and merged", "are tested, committed and pushed".
_PRESENT_PASSIVE = re.compile(
    r"\b(?:is|are|gets?|being)\s+(?:\w+ly\s+)?(?:\w+(?:ed|en)\b,?\s+(?:(?:and|or)\s+)?)*(?:passed|committed|pushed|merged|built)\b",
    re.I)
# English negation shortly before the claim ("Nothing is committed or pushed yet", "has not been pushed") or
# right after it ("pushed? not yet"); a "no" later in the sentence ("all tests pass, no regressions") is not one.
_NEGATED_EN_BEFORE = re.compile(r"(?:\b(?:not|nothing|never|no|without|cannot)\b|n't\b)[^,.;]{0,40}$", re.I)
_NEGATED_EN_AFTER = re.compile(r"^[^,.;]{0,12}\b(?:yet|not)\b", re.I)
# Chinese negation right before the claim, a quote or bracket allowed in between: 未 push, 沒有「已 commit.
# A modal may follow the negation: 不會 merge, 不用重新 build ("重新 build" is the claim's own start).
_NEGATED_ZH_BEFORE = re.compile(
    r"(?:沒有|沒|未|不|非|無|尚未|還沒|並未|無法|別|不是)(?:會|用|必|須|能|可以|要|准|得|該)?\s*[「“\"(（『]?\s*$")
# "測試過了之後：先備份", "通過後才 build": what follows the pass is the plan, the pass has not happened.
# "測試全過後已 push" and "重 build 之後輸出沒變" have happened.
_THEN_AFTER = re.compile(r"\s*(?:之後|以後)\s*(?:[:：]|照|再|才)|\s*後\s*(?:才|再)")
# Words in quotes are somebody else's, or the phrase itself: 別人回報「整套測試通過」, 每一句「測試過了」.
_QUOTE_PAIRS = (("「", "」"), ("『", "』"), ("“", "”"), ('"', '"'))
# Inside a claim's own clause nothing may be negated or failing: "測試沒通過" and "tests did not pass" are
# not "tests pass".
_SPAN = r"(?:(?!沒|未|不|非|無|失敗|fail|\bnot\b|n't\b|\bnever\b|\bno\b|\bcannot\b)[^，。；]){0,200}?"
_DONE = r"(?:已經|已|都已|都|也都|也|剛剛|剛|全部|全都)\s*"
# "push 步驟", "的 commit": the word as a noun, not the deed.
_NOUN = r"(?:的|步驟|流程|指令|命令|hook|腳本|script)"
_SHA = r"`?[0-9a-f]{6,40}`?"
# "merged" is about git only when the sentence says so: blog posts and data rows get 合併 too.
_GIT_CONTEXT = re.compile(
    r"分支|branch|\bmain\b|\bmaster\b|\bpr\b|#\d+|pull request|\bgit\b|\bgh\b|\b[0-9a-f]{7,40}\b", re.I)

_PATTERNS = {
    "tests": re.compile(
        rf"(?:測試|全套|\b(?:tests?|testing|spec|phpunit|pytest|vitest|jest|unit|e2e|smoke)\b"
        # a PHPUnit class name, also glued to Chinese ("已重跑LoginTest 並通過")
        rf"|(?-i:(?<![A-Za-z0-9_])[A-Z][A-Za-z0-9]*Tests?(?![A-Za-z0-9_]))){_SPAN}"
        r"(?:全部通過|都通過|全通過|全過|都過"
        r"|通過(?!\s*(?:stdin|stdout|stderr|api|http|socket|pipe|env|cli|url|參數|管道|介面|[A-Za-z]+\s*(?:建立|傳|送|呼叫|取得|產生|載入|注入)))"
        r"|全綠|綠(?!色)|過了|\bpass(?:ed|es|ing)?\b|0\s*個?\s*失敗|0\s+fail(?:ed|ures?)\b)"
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
        r"|^committed\.?$|\bcommitted\s*\([0-9a-f]"
        rf"|{_DONE}提交(?=[^，。；]{{0,12}}commit)|提交了\s*\d+\s*個?\s*commit"
        r"|\b(?:is|are|was|were|has been|have been|got|now|already)\s+committed\b|\bi(?:'ve| have)?\s+committed\b"
        r"|\bcommitted\s*(?:,|and\b|as\b|to\b|on\b|in\b|the\b|everything\b|all\b)",
        re.I),
    "push": re.compile(
        rf"{_DONE}push(?:ed)?(?!\s*{_NOUN})|\bpush\s*(?:了|完成|完\b|好了|成功|上去了|到了)|^pushed\.?$"
        rf"|{_DONE}(?:推送(?:到|上|至)?|推上去|推上|推到|推了(?!\s*(?:什麼|甚麼|哪))|推(?=\s|$|[，。；、！!）)]))(?![去上到\s]*的)"
        r"|推上去了|推上了|推到了|推送了|推了(?!\s*的)(?!\s*(?:什麼|甚麼|哪))"
        # "已 commit 並 push", "已合進 main、push": the second verb of a done list, never a bare list, and
        # nothing negated between ("都沒有 git、push 指令")
        rf"|{_DONE}(?:(?!沒|未|不|非|無)[^，。；]){{0,24}}?(?:並|及|和|跟|、)\s*(?:push(?:ed)?\b(?!\s*{_NOUN})(?!\s*(?:失敗|fail))"
        r"|(?:推送(?:到|上|至)?|推上去|推上|推到|推了|推(?=\s|$|[，。；、！!）)]))(?![去上到\s]*的))"
        r"|\b(?:is|are|was|were|has been|have been|got|now|already)\s+pushed\b|\bi(?:'ve| have)?\s+pushed\b"
        r"|\bpushed\s+(?:to|the|it|them|everything|both)\b|\b(?:and|,)\s*pushed\b(?!\s+(?:to|the|it|them|everything|both)\b)",
        re.I),
    "merge": re.compile(
        rf"{_DONE}merge[d]?(?!\s*的)|\bmerge\s*(?:了|完成|完\b|好了|進|到)|{_DONE}合併(?!\s*的)|合併(?:了|完成|進)"
        r"|\b(?:is|are|was|were|has been|have been|got|now|already)\s+merged\b|\bi(?:'ve| have)?\s+merged\b"
        r"|\bmerged\s+(?:into|to|as|it|them)\b|\bpr\s*#?\d+\s+(?:is\s+|was\s+)?merged\b"
        r"|^merged\s+(?:pr\b|#\d+|the\b|it\b|everything\b|all\b|branch\b)|\bsquash-?merged\b",
        re.I),
}
_NOT_GIT_PUSH = re.compile(r"indexnow|notification|通知|推播", re.I)
# "The test passes a Windows path to _norm_path": the verb takes an object; "the new test passes" does not.
_PASSES_OBJECT = re.compile(
    r"\b(?:tests?|spec|pytest)\s+(?:\w+\s+){0,2}?(?:that\s+|which\s+)?pass(?:es)?\s+"
    r"(?:a|an|the|its|their|our|your|this|these|those|some|every|each|all)\b",
    re.I)
# A question or a condition counts in the claim's own clause: "測試全綠，要我 push 嗎" keeps its claim, "測試過了就
# push" does not. A sentence that ends with "?" is a question unless the question is its own later clause;
# "Do all tests pass …?" is one from its first word.
_CLAUSE_END = re.compile(r"[，,;；]|\s[—–-]\s|—|\s+and\s+", re.I)
_QUESTION_CLAUSE = re.compile(
    r"嗎|要我|要不要|是不是|\bshould i\b|\bdo you want\b|\bwant me\b|\bshall i\b|\bcan i\b|\bmay i\b|\bok\b|\bokay\b",
    re.I)
_AUX_QUESTION = re.compile(r"^(?:do|does|did|is|are|was|were|can|could|will|would|should|have|has)\b", re.I)
# "測試全綠後，再 push", "等測試全綠，再 push", "測試全綠，然後 push": the claim's clause is a condition or a step.
_PLAN_CLAUSE = re.compile(r"^(?:等|確認|待|先|請)(?!了|過|前|：|:)|(?:後|前|之後|以後|時)$")
# "已 push，就等 CI 了": a clause that is itself done is not made a step by the clause after it.
_COMPLETED = re.compile(r"已|都|剛|也|\bpassed\b|\d+\s*(?:條|個|題)?\s*(?:通過|全過|都過|綠)", re.I)
# "When CI passes, the PR is merged": a subordinator in the clause before the claim makes it a condition.
_SUBORDINATOR = re.compile(
    r"如果|若|要是|一旦|只要|除非|假如|的話|\b(?:if|when|whenever|once|unless|until|as soon as)\b", re.I)
_NEXT_PLAN = re.compile(r"^\s*(?:再|我會|然後|接著|就|才)(?!\s*(?:已|都|也|剛))")
# "1 failed, 2 passed" reports a failure, whatever else it says.
_FAILING = re.compile(r"\b[1-9]\d*\s*(?:failed|failures?|errors?)\b|[1-9]\d*\s*(?:個|條|項)?\s*失敗", re.I)
# "19 項全過" counts tests only when nothing says otherwise: a count of browser checks, migrations or reviewed
# videos ("真瀏覽器操作 19 項全過", "migrate 5 支全過", "再審核：6 支通過") is not a tests claim. Of 3,848 real
# end-of-turn messages, 16 such counts had no test word in their sentence; the 6 that were not tests all named
# their subject in the sentence or the one before it, the 10 that were tests never did.
_TEST_WORD = re.compile(
    r"測試|全套|案例|(?i:\b(?:tests?|testing|spec|suite|phpunit|pytest|vitest|jest|unit|e2e|smoke|pass(?:ed|es|ing)?)\b)"
    r"|(?<![A-Za-z0-9_])[A-Z][A-Za-z0-9]*Tests?(?![A-Za-z0-9_])")
_NOT_TESTS = re.compile(r"瀏覽器|browser|migrat|審核|影片|手動|人工|manual", re.I)
# A sentence dated on another day reports history ("PR #3，2026-04-16 已 merge 進 main", "a note from 2026-07-27
# says … were committed"), not this session's work. A date inside a branch name or a path ("wip/release-2026-11-06",
# "docs/2026-10-25.md") does not date the sentence; a full stop that ends the sentence after it does not glue it.
_ISO_DATE = re.compile(r"(?<![\w/.-])20\d\d-\d\d-\d\d(?![\w/-]|\.\S)")


def find_claims(message: str, session_days: Collection[str] | None = None) -> list[Claim]:
    """Every checkable claim in the message, one per kind per sentence, in the order they appear.

    `session_days` are the ISO dates the session spans (UTC and local): a sentence that names another day
    reports history, not this session's work, and is not a claim. Without them, dates are not judged.
    """
    claims: list[Claim] = []
    sents = sentences(message)
    for i, sentence in enumerate(sents):
        dates = _ISO_DATE.findall(sentence)
        if session_days is not None and dates and not any(d in session_days for d in dates):
            continue
        for kind in KINDS:
            # The first one outside quotes: "輸出是「245 passed」，測試全過" still says the tests pass.
            m = next((x for x in _PATTERNS[kind].finditer(sentence) if not _quoted(sentence, x)), None)
            if m is None:
                continue
            seps = list(_CLAUSE_END.finditer(sentence, 0, m.start()))
            own_start = seps[-1].end() if seps else 0
            nxt = _CLAUSE_END.search(sentence, m.end())
            own = sentence[own_start:nxt.start() if nxt else len(sentence)].strip()
            after = sentence[nxt.end():] if nxt else ""
            # "need to be committed and pushed": a modal before "and" covers both verbs.
            coordinated = bool(seps) and "and" in seps[-1].group(0).lower()
            modal_scope = sentence[:nxt.start() if nxt else len(sentence)] if coordinated else own
            asks = sentence.rstrip().endswith(("?", "？"))
            if _QUESTION.search(own) or _CONDITIONAL.search(modal_scope) or (asks and _AUX_QUESTION.match(sentence)):
                continue
            if asks and not _QUESTION_CLAUSE.search(after):
                continue
            prev = _CLAUSE_END.split(sentence[:own_start])[-2] if own_start else ""
            if _SUBORDINATOR.search(prev) and (not _PAST.search(m.group(0)) or _passive_at(sentence, m)):
                continue
            if _PLAN_CLAUSE.search(own) or (_NEXT_PLAN.match(after) and not _COMPLETED.search(own)):
                continue
            if _NEGATED_ZH_BEFORE.search(sentence[max(0, m.start() - 8):m.start()]):
                continue
            if _THEN_AFTER.match(sentence, m.end()):
                continue
            if _PLAN_AHEAD.search(sentence[:m.start()]) and (not _PAST.search(m.group(0)) or _passive_at(sentence, m)):
                continue
            if _INSTRUCTION.search(sentence[:m.start()]):
                continue
            if _BEFORE_DOING.search(sentence) and not _PAST.search(m.group(0)):
                continue
            if re.search(r"[A-Za-z]", m.group(0)) and (
                    _NEGATED_EN_BEFORE.search(sentence[:m.start()]) or _NEGATED_EN_AFTER.search(sentence[m.end():])):
                continue
            if kind == "tests" and (_FAILING.search(sentence) or _PASSES_OBJECT.match(sentence, m.start())):
                continue
            if kind == "tests" and not _TEST_WORD.search(sentence) and _NOT_TESTS.search(
                    (sents[i - 1] if i else "") + " " + sentence):
                continue
            if kind == "merge" and not _GIT_CONTEXT.search(sentence):
                continue
            if kind == "push" and _NOT_GIT_PUSH.search(sentence):
                continue
            claims.append(Claim(kind, sentence))
    return claims


def _quoted(sentence: str, m: re.Match) -> bool:
    """Whether the claim's words start inside a pair of quotes (one that closes later in the sentence)."""
    before, after = sentence[:m.start()], sentence[m.start():]
    for opening, closing in _QUOTE_PAIRS:
        if opening == closing:
            if before.count(opening) % 2 and closing in after:
                return True
        elif before.rfind(opening) > before.rfind(closing) and closing in after:
            return True
    return False


def _passive_at(sentence: str, m: re.Match) -> bool:
    """Whether the claim's own participle sits in a present passive ("is reviewed and merged into main"); one
    in an earlier clause ("after confirming the PR is merged, I also committed …") does not count."""
    start = max(0, m.start() - 48)
    return any(p.end() > m.start() - start for p in _PRESENT_PASSIVE.finditer(sentence[start:m.end()]))


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
