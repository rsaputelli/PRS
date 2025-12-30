# ============================================================
# Unit-Test Stub for _compose_span + safe span expansion
# ============================================================

import pandas as pd
from datetime import datetime, date, time, timedelta

# ---- COPY from your Edit Gig file ----
def _to_time_obj(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    try:
        if isinstance(x, time):
            return x
        ts = pd.to_datetime(x, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.time()
    except Exception:
        return None

def _compose_span(row):
    d = row.get("event_date")
    st_raw = row.get("start_time")
    en_raw = row.get("end_time")

    if pd.isna(d):
        return (pd.NaT, pd.NaT)

    d: date = d
    st_t = _to_time_obj(st_raw) or time(0, 0)
    en_t = _to_time_obj(en_raw) or st_t

    start_dt = datetime.combine(d, st_t)
    end_dt   = datetime.combine(d, en_t)

    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    return (pd.Timestamp(start_dt), pd.Timestamp(end_dt))


# ---- TEST HARNESS ----
def run_test(df: pd.DataFrame, label: str):
    print(f"\n=== TEST: {label} ===")
    print(df)

    spans = df.apply(
        lambda r: pd.Series(_compose_span(r),
                            index=["_start_dt_raw", "_end_dt_raw"]),
        axis=1
    )

    print("\nExpanded spans:")
    print(spans)

    # Should never error
    try:
        df["_start_dt"] = pd.to_datetime(spans["_start_dt_raw"], errors="coerce")
        df["_end_dt"]   = pd.to_datetime(spans["_end_dt_raw"], errors="coerce")
        print("\n✓ SUCCESS: Safe assignment OK")
        print(df[["event_date", "_start_dt", "_end_dt"]])
    except Exception as e:
        print("\n✗ FAILURE:", type(e), e)


# ============================================================
# TEST CASES
# ============================================================

# 1) Normal multi-row test
df1 = pd.DataFrame([
    {"event_date": date(2025, 5, 1), "start_time": "7:00 PM", "end_time": "9:00 PM"},
    {"event_date": date(2025, 5, 2), "start_time": "10:00",   "end_time": "01:00"},  # midnight span
])
run_test(df1, "Multiple rows, including after-midnight show")

# 2) Single-row test (this is where your bug occurred)
df2 = pd.DataFrame([
    {"event_date": date(2025, 5, 3), "start_time": None, "end_time": None},
])
run_test(df2, "Single row — ensures result_type=expand never collapses")

# 3) Empty DataFrame
df3 = pd.DataFrame(columns=["event_date", "start_time", "end_time"])
run_test(df3, "Empty DataFrame")

# 4) Missing date (NaN)
df4 = pd.DataFrame([
    {"event_date": pd.NaT, "start_time": "7 PM", "end_time": "9 PM"},
])
run_test(df4, "Missing event_date")

# 5) Mixed valid/invalid times
df5 = pd.DataFrame([
    {"event_date": date(2025, 5, 4), "start_time": "BAD", "end_time": "9 PM"},
    {"event_date": date(2025, 5, 5), "start_time": "8 PM", "end_time": "BAD"},
])
run_test(df5, "Mixed valid + invalid times")

print("\nAll tests complete.")
