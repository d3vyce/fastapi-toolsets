"""Optional dependency helpers."""


def require_extra(package: str, extra: str) -> None:
    """Raise *ImportError* with an actionable install instruction."""
    raise ImportError(
        f"'{package}' is required to use this module. "
        f"Install it with: pip install fastapi-toolsets[{extra}]"
    )
