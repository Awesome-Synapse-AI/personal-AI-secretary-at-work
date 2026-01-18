from collections.abc import Iterable


def iter_tokens(text: str) -> Iterable[str]:
    parts = text.split(" ")
    for index, part in enumerate(parts):
        if not part:
            continue
        suffix = " " if index < len(parts) - 1 else ""
        yield f"{part}{suffix}"
