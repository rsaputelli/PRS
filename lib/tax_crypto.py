# lib/tax_crypto.py
from __future__ import annotations
import re
from cryptography.fernet import Fernet

import re

def normalize_tin(raw) -> str:
    """Keep digits only. Accepts SSN/EIN formats with dashes/spaces. Robust to floats/NaN."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if s.lower() == "nan":
        return ""
    return re.sub(r"\D+", "", s)

def last4(tin_digits: str) -> str:
    return tin_digits[-4:] if tin_digits and len(tin_digits) >= 4 else ""

def get_fernet(key_str: str) -> Fernet:
    key = (key_str or "").strip().encode("utf-8")
    return Fernet(key)

def encrypt_tin(tin_plain: str, f: Fernet) -> str:
    tin_digits = normalize_tin(tin_plain)
    token = f.encrypt(tin_digits.encode("utf-8"))
    return token.decode("utf-8")

def decrypt_tin(ciphertext: str, f: Fernet) -> str:
    plain = f.decrypt((ciphertext or "").encode("utf-8")).decode("utf-8")
    return plain
