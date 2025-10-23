# =============================
# File: lib/ui_format.py
# =============================
from typing import Optional, Union

Number = Union[int, float]


def format_currency(val: Optional[Number]) -> str:
    """Return $-formatted currency for numeric values; blank for None/NaN."""
    try:
        if val is None:
            return ""
        v = float(val)
        return f"${v:,.2f}"
    except Exception:
        return ""