from typing import Any

from pydantic import BaseModel


class Event(BaseModel):
    type: str
    data: dict[str, Any] | None = None
