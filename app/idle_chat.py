"""Short, persona-aware conversation starters without network or factual claims."""
from __future__ import annotations

import random
from datetime import datetime


def conversation_starter(persona: dict, max_chars: int = 23, previous: str = "") -> str:
    hour = datetime.now().hour
    greeting = ("早上好" if 6 <= hour < 11 else "中午好" if 11 <= hour < 14 else
                "下午好" if 14 <= hour < 18 else "晚上好")
    if persona.get("id") == "plana" or "普拉娜" in str(persona.get("name", "")):
        choices = [f"各位老师，{greeting}。", f"{greeting}。"]
    else:
        choices = [f"{greeting}，各位老师～", "早呀～" if 6 <= hour < 11 else f"{greeting}呀。"]
    choices = [text for text in choices if len(text) <= min(23, max_chars)]
    different = [text for text in choices if text != previous]
    return random.choice(different or choices) if choices else ""
