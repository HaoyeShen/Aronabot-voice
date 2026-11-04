"""Local, extractive evidence views; original documents remain in storage."""
import re


def evidence_excerpt(text, query='', max_chars=1200):
    text=str(text or '')
    sentences=re.split(r'(?<=[。！？!?；;])\s*|\n+',text)
    pieces=[];seen=set()
    for sentence in sentences:
        sentence=re.sub(r'\s+',' ',sentence).strip()
        if not sentence or sentence in seen:
            continue
        seen.add(sentence);pieces.append(sentence)
    if sum(map(len,pieces))<=max_chars:
        return '\n'.join(pieces)
    tokens=set(re.findall(r'[a-zA-Z][a-zA-Z0-9_]+|[\u4e00-\u9fff]{2,}',str(query).lower()))
    tokens.update(term[i:i+2] for term in list(tokens) if len(term)>3 for i in range(len(term)-1))
    ranked=sorted(range(len(pieces)),key=lambda i:(-(sum(t in pieces[i].lower() for t in tokens)),i))
    chosen=set();used=0
    for index in ranked:
        sentence=pieces[index]
        if used+len(sentence)+6<=max_chars:
            chosen.add(index);used+=len(sentence)+6
    if not chosen:
        return text[:max_chars]  # Single long unpunctuated source; retain legacy bound.
    result='';previous=-1
    for i in sorted(chosen):
        result+= ('\n' if i==previous+1 else '\n[…]\n')+pieces[i]
        previous=i
    return result.strip()
