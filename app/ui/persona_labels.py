"""Display labels distinguish duplicate names; selections use stable IDs."""
from collections import Counter

def persona_labels(people):
    counts = Counter(p.get('name', '未命名') for p in people)
    return [str(p.get('name', '未命名')) + (f" · {p['id']}" if counts[p.get('name', '未命名')] > 1 else '') for p in people]
