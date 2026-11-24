from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Dict

MESSAGES_PATH = Path(__file__).with_name("messages.json")


@lru_cache
def get_messages() -> Dict[str, Any]:
    with MESSAGES_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def msg(key: str, **kwargs: object) -> str:
    template = str(get_messages()[key])
    return template.format(**kwargs) if kwargs else template
