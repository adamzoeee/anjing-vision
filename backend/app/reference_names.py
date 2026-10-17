"""用户可读参考项名称到标准参考维度的映射。"""

from __future__ import annotations

import unicodedata


_OBJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "door": ("door", "门"),
    "bed": ("bed", "床"),
    "sofa": ("sofa", "沙发"),
    "table": ("table", "桌子", "餐桌", "书桌", "桌"),
    "cabinet": ("cabinet", "电视柜", "储物柜", "衣柜", "橱柜", "柜子", "柜"),
    "bookshelf": ("bookshelf", "书架"),
}

_DIMENSION_ALIASES: dict[str, tuple[str, ...]] = {
    "length": ("length", "长度", "长"),
    "width": ("width", "宽度", "宽"),
    "height": ("height", "高度", "高"),
}


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and character not in {"-", "_"}
        and not unicodedata.category(character).startswith("P")
    )


def _alias_index() -> tuple[tuple[str, tuple[str, str]], ...]:
    entries: list[tuple[str, tuple[str, str]]] = []
    for object_type, object_aliases in _OBJECT_ALIASES.items():
        for dimension, dimension_aliases in _DIMENSION_ALIASES.items():
            for object_alias in object_aliases:
                for dimension_alias in dimension_aliases:
                    entries.append(
                        (
                            _normalize_name(f"{object_alias}{dimension_alias}"),
                            (object_type, dimension),
                        )
                    )
    # 长名称优先，避免“柜”抢先匹配“电视柜”这类包含关系。
    return tuple(sorted(entries, key=lambda item: len(item[0]), reverse=True))


_ALIASES = _alias_index()


def parse_reference_name(name: str) -> tuple[str, str] | None:
    """将“床长度 / Bed Length”等名称解析为标准 (object_type, dimension)。"""

    normalized = _normalize_name(name)
    if not normalized:
        return None
    for alias, parsed in _ALIASES:
        if normalized == alias:
            return parsed
    return None
