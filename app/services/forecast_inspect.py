import pandas as pd
import numpy as np
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast
import re


def find_col(df: pd.DataFrame, keywords, prefer=None):
    keys = [k.lower() for k in (keywords if isinstance(keywords, (list, tuple)) else [keywords])]
    cand = []
    for c in df.columns:
        cl = str(c).lower()
        if all(k in cl for k in keys):
            cand.append(c)
    if prefer is not None:
        for p in (prefer if isinstance(prefer, (list, tuple)) else [prefer]):
            for c in cand:
                if str(c).lower() == str(p).lower():
                    return c
    return cand[0] if cand else None


def guess_date_col(df: pd.DataFrame):
    for k in ["date", "day", "datum"]:
        exact = [c for c in df.columns if str(c).strip().lower() == k]
        if exact:
            return exact[0]
    contains = [c for c in df.columns if any(k in str(c).lower() for k in ["date", "day", "datum"])]
    return contains[0] if contains else None


def parse_dates(s: pd.Series) -> pd.Series:
    ser = pd.to_datetime(s, errors="coerce")
    return cast(pd.Series, ser).dt.date


def to_datetime_series(s: pd.Series):
    # Handle Excel datetime or strings like "09:00"/"18:00"
    if np.issubdtype(s.dtype, np.datetime64):
        return pd.to_datetime(s, errors="coerce")
    return pd.to_datetime(s.astype(str), errors="coerce")


def _normkey(s: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(s or "")).lower()


def _is_hc_col(name: Any) -> bool:
    return bool(re.match(r"(?i)^\s*hc[\s_\-]*", str(name or "").strip()))


def _extract_role_from_hc(name: Any) -> str:
    s = str(name or "").strip()
    s = re.sub(r"(?i)^\s*hc[\s_\-]*", "", s)
    s = re.sub(r"[\s_\-]+", " ", s).strip()
    return s.title() if s else "Role"


def _pick_from_to(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    from_col = next((c for c in ["From", "from", "Start", "Open"] if c in df.columns), None)
    to_col = next((c for c in ["To", "to", "End", "Close", "Closed"] if c in df.columns), None)
    return from_col, to_col


def _duration_hours(from_s: Optional[pd.Series], to_s: Optional[pd.Series]) -> pd.Series:
    if from_s is None or to_s is None:
        return pd.Series(1.0, index=(from_s.index if from_s is not None else (to_s.index if to_s is not None else pd.RangeIndex(0))))
    start = cast(pd.Series, to_datetime_series(from_s))
    end = cast(pd.Series, to_datetime_series(to_s))
    delta_td = cast(pd.Series, end - start)
    delta_hours = cast(pd.Series, delta_td.dt.total_seconds()) / 3600.0
    delta_hours = delta_hours.where(delta_hours >= 0, delta_hours + 24)
    return cast(pd.Series, delta_hours.fillna(1.0).clip(lower=0.0, upper=24.0))


def main():
    path_candidates = [
        Path("ShiftPlan_Agent_Demo/testdata/Simple_Shift_Plan_Request.xlsx"),
        Path("testdata/Simple_Shift_Plan_Request.xlsx"),
    ]
    path = next((p for p in path_candidates if p.exists()), path_candidates[0])
    print(f"Loading Excel: {path}")
    if not path.exists():
        print("ERROR: Excel file not found at", path)
        return

    xls = pd.ExcelFile(path)
    print("Sheets:", xls.sheet_names)

    # ===== Opening Hours sheet (for horizon dates) =====
    oh_name = next((n for n in xls.sheet_names if str(n).strip().lower() == "opening hours"), None)
    oh = None
    oh_dates_set: set = set()
    if not oh_name:
        print("Opening Hours sheet not found")
    else:
        oh = pd.read_excel(xls, sheet_name=oh_name)
        print("\n===== Opening Hours sheet summary =====")
        print("Columns:", list(oh.columns))
        print("Dtypes:\n", oh.dtypes)
        print("Head:\n", oh.head(5))
        dcol_oh = guess_date_col(oh)
        if dcol_oh is not None:
            oh["_date"] = parse_dates(oh[dcol_oh])
            oh_dates_set = {d for d in oh["_date"] if pd.notna(d)}
            print(f"Date column inferred (Opening Hours): {dcol_oh}; range: {oh['_date'].min()} .. {oh['_date'].max()}  (nulls={oh['_date'].isna().sum()})")
        else:
            print("No date-like column found in Opening Hours")

        # Compute opening duration if From/To present
        from_col = find_col(oh, ["from"]) or find_col(oh, ["start"]) or find_col(oh, ["open"])
        to_col = find_col(oh, ["to"]) or find_col(oh, ["end"]) or find_col(oh, ["close"])
        if from_col and to_col:
            start = cast(pd.Series, to_datetime_series(oh[from_col]))
            end = cast(pd.Series, to_datetime_series(oh[to_col]))
            delta_td = cast(pd.Series, end - start)
            delta = cast(pd.Series, delta_td.dt.total_seconds()) / 3600.0
            delta = delta.where(delta >= 0, delta + 24)
            oh["_open_hours"] = delta
            print(
                "Opening hours stats (computed): count=",
                int(delta.notna().sum()),
                " min=",
                float(np.nanmin(delta)),
                " mean=",
                float(np.nanmean(delta)),
                " max=",
                float(np.nanmax(delta)),
            )
        else:
            print("Could not find From/To (or Start/End/Open/Close) columns to compute duration")

    # ===== Modulation sheet =====
    print("\n===== Modulation sheet summary =====")
    mod_name = next((n for n in xls.sheet_names if str(n).strip().lower() == "modulation"), None)
    if not mod_name:
        print("Modulation sheet not found")
        print("\nInspection complete.")
        return

    mod = pd.read_excel(xls, sheet_name=mod_name)
    print("Columns:", list(mod.columns))
    print("Dtypes:\n", mod.dtypes)
    print("Head:\n", mod.head(5))

    dcol = guess_date_col(mod)
    if dcol is not None:
        mod["_date"] = parse_dates(mod[dcol])
        print(f"Date column inferred (Modulation): {dcol}; range: {mod['_date'].min()} .. {mod['_date'].max()}  (nulls={mod['_date'].isna().sum()})")
    else:
        print("No date-like column found in Modulation")

    # Identify roles and drivers dynamically
    hc_cols = [c for c in mod.columns if _is_hc_col(c)]
    roles = [_extract_role_from_hc(c) for c in hc_cols]
    role_keys = [_normkey(r) for r in roles]
    role_display_map: Dict[str, str] = {}
    for rk, disp in zip(role_keys, roles):
        if rk not in role_display_map:
            role_display_map[rk] = disp

    base_cols = [c for c in mod.columns if str(c).strip().lower().startswith("base")]
    from_c, to_c = _pick_from_to(mod)
    reserved = set([dcol or "Date"])
    if from_c:
        reserved.add(from_c)
    if to_c:
        reserved.add(to_c)
    reserved.update(hc_cols)
    reserved.update(base_cols)
    driver_cols = [c for c in mod.columns if c not in reserved]

    print("\nDetected roles (from HC* columns):")
    if not roles:
        print("  None")
    else:
        for hc, r in zip(hc_cols, roles):
            print(f"  {hc} -> Role '{r}'")

    print("\nDetected drivers (candidate exogenous features):")
    if not driver_cols:
        print("  None")
    else:
        for c in driver_cols:
            dtype = str(mod[c].dtype)
            nulls = int(mod[c].isna().sum())
            print(f"  {c} (dtype={dtype}, nulls={nulls})")

    # Period weighting and daily aggregation overview
    hours = _duration_hours(mod[from_c] if from_c in mod.columns else None, mod[to_c] if to_c in mod.columns else None)
    mod["_hours_w"] = hours

    # Daily summaries for roles: report non-null counts and min/max per role
    if hc_cols and "_date" in mod.columns:
        g = mod.groupby("_date", as_index=False)
        print("\nPer-role daily target (max across periods) summary:")
        for hc, r in zip(hc_cols, roles):
            y_df = cast(pd.DataFrame, g[[hc]].max().rename({hc: f"y::{r}"}, axis=1))
            col = f"y::{r}"
            y_ser = cast(pd.Series, pd.to_numeric(y_df[col], errors="coerce"))
            mins = float(y_ser.min(skipna=True)) if y_ser.notna().any() else float("nan")
            maxs = float(y_ser.max(skipna=True)) if y_ser.notna().any() else float("nan")
            nnon = int(y_ser.notna().sum())
            print(f"  Role '{r}': non-null days={nnon}, min={mins}, max={maxs}")

    # Horizon readiness: driver NAs on Opening Hours dates (if both date sets present)
    if oh is not None and "_date" in mod.columns and "_date" in oh.columns:
        mdates = {d for d in mod["_date"] if pd.notna(d)}
        odates = {d for d in oh["_date"] if pd.notna(d)}
        inter = sorted(mdates & odates)
        only_mod = sorted(mdates - odates)
        only_oh = sorted(odates - mdates)
        print("\nDate alignment:")
        print("  Intersection dates:", len(inter))
        print("  In Modulation only:", len(only_mod))
        print("  In Opening Hours only:", len(only_oh))
        if inter[:5]:
            print("  Sample intersecting dates:", inter[:5])
        if only_mod[:5]:
            print("  Sample Modulation-only dates:", only_mod[:5])
        if only_oh[:5]:
            print("  Sample OpeningHours-only dates:", only_oh[:5])

        if driver_cols:
            # Build driver NA stats on horizon dates
            mod_hor = mod.loc[mod["_date"].isin(odates)].copy()
            # Aggregate per day: consider weighted mean for numeric to assess availability
            num_drivers = [c for c in driver_cols if pd.api.types.is_numeric_dtype(mod[c])]
            cat_drivers = [c for c in driver_cols if not pd.api.types.is_numeric_dtype(mod[c])]
            print("\nDriver availability on Opening Hours horizon (NA counts by driver):")
            if num_drivers:
                # if value is NA on all periods of a day -> counted as NA for that day
                g = mod_hor.groupby("_date", as_index=False)
                num_na_by_day = {}
                for c in num_drivers:
                    # if all periods NA -> daily NA
                    daily_na = g[c].apply(lambda s: s.isna().all())
                    num_na_by_day[c] = int(bool(daily_na.any()) and int(daily_na.sum()))
                for c in num_drivers:
                    # count days with all-NA for that driver
                    g = mod_hor.groupby("_date")[c].apply(lambda s: s.isna().all())
                    print(f"  {c}: days_all_NA={int(g.sum())}")
            for c in cat_drivers:
                g = mod_hor.groupby("_date")[c].apply(lambda s: s.isna().all() or (s.astype(str).str.strip() == '').all())
                print(f"  {c}: days_all_NA_or_empty={int(g.sum())}")

    print("\nInspection complete.")


if __name__ == "__main__":
    main()
