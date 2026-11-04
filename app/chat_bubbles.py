"""Choose readable chat pauses without clipping the answer or adding words."""
from __future__ import annotations

import re
import unicodedata


_PAUSES = '。！？!?；;，,：:'
_CLOSING = '。！？!?；;，,：:、）》】」』”’)]}…'
_OPENING = '（《【「『“‘([{'
_CONNECTIVE = re.compile(r'不过|但是|所以|然后|而且|因为|如果|其实|另外|也可以|还可以|这样|不如|要不|至于')
_DANGLING = re.compile(r'(?:因为|所以|但是|然后|如果|虽然|而且|或者|以及|并且|一个|这个|那个|把|被|的|地|很|太|跟|和|给|让|在|能|要)$')
_TOKEN = re.compile(r'https?://[^\s<>"，。！？；]+|\d+(?:\.\d+)?(?:%|℃|分钟|小时|公斤|毫升|公里|个|岁|元|秒|天|年|米)|[A-Za-z0-9_]+(?:[-./:@][A-Za-z0-9_]+)*')


def split_chat_bubbles(text: str, limit: int, max_bubbles: int,
                       *, allow_soft_split: bool = True) -> list[str]:
    """Keep model line breaks; balance punctuation, clause pauses and short tails.

    Each paragraph uses a bounded path search so an earlier pleasant pause
    cannot force an orphan final word or exceed the configured bubble count.
    A URL or Latin identifier is indivisible; a too-long token needs rewriting.
    """
    # Strict callers (e.g. video captions) require complete sentences and
    # retain their original sentence-count policy.
    lines = text.splitlines() if allow_soft_split else re.split(r'(?<=[。！？!?；;])|\n',text)
    paragraphs = [line.strip() for line in lines if line.strip()]
    if not paragraphs or max_bubbles < 1:
        return []
    if sum(len(p) for p in paragraphs) > limit * max_bubbles:
        return []
    # A lone address or acknowledgement belongs with the next thought, even
    # when the model happens to put it on its own line.
    for index in range(len(paragraphs)-2,-1,-1):
        if (allow_soft_split and len(paragraphs[index]) < 6
                and not paragraphs[index].endswith(('？','?'))):
            paragraphs[index:index+2] = [''.join(paragraphs[index:index+2])]
    output = []
    for line_number, paragraph in enumerate(paragraphs):
        # Reserve enough bubbles for every remaining explicit paragraph.
        reserved = sum((len(p)+limit-1)//limit for p in paragraphs[line_number+1:])
        budget = max_bubbles-len(output)-reserved
        if budget < 1:
            return []
        n = len(paragraph)
        if n <= limit:
            output.append(paragraph)
            continue
        blocked = set()
        for token in _TOKEN.finditer(paragraph):
            blocked.update(range(token.start()+1,token.end()))
        for i in range(1,n):
            if (paragraph[i] in _CLOSING or paragraph[i-1] in _OPENING
                    or unicodedata.combining(paragraph[i])
                    or paragraph[i] in '\ufe0f\u200d' or paragraph[i-1] == '\u200d'):
                blocked.add(i)
        connective = {match.start() for match in _CONNECTIVE.finditer(paragraph)}
        penalties = {}
        for end in range(1,n+1):
            if end in blocked:
                continue
            previous = paragraph[:end].rstrip('）》】」』”’)]}')[-1:]
            if end == n or previous in '。！？!?；;':
                penalty = 0
            elif previous in _PAUSES:
                penalty = 3
            elif allow_soft_split:
                penalty = 5 if previous.isspace() or end in connective else 24
            else:
                continue
            if end < n and _DANGLING.search(paragraph[:end]):
                penalty += 22
            if end < n and paragraph[end] in '的地得着':
                penalty += 16
            penalties[end] = penalty
        # (position, bubble count) -> (cost, preceding position).
        paths = {(0,0):(0.0,0)}
        for count in range(1,min(budget,n)+1):
            for end, penalty in penalties.items():
                if n-end > (budget-count)*limit:
                    continue
                best = None
                for start in range(max(0,end-limit),end):
                    preceding = paths.get((start,count-1))
                    if preceding is None or not paragraph[start:end].strip():
                        continue
                    length = len(paragraph[start:end].strip())
                    # Favor fewer meaningful bubbles over many tiny reactions.
                    cost = preceding[0]+8+penalty+max(0,6-length)*4
                    if best is None or cost < best[0]:
                        best = (cost,start)
                if best is not None:
                    paths[end,count] = best
        finals = [(value[0],count) for (end,count),value in paths.items() if end == n]
        if not finals:
            return []
        _, count = min(finals)
        parts = []
        end = n
        while count:
            start = paths[end,count][1]
            parts.append(paragraph[start:end].strip())
            end, count = start, count-1
        output.extend(reversed(parts))
    return output
