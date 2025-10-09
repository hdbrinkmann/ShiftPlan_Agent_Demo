import pandas as pd
import numpy as np
from pathlib import Path


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


def parse_dates(s: pd.Series):
    return pd.to_datetime(s, errors="coerce").dt.date


def to_datetime_series(s: pd.Series):
    # Handle Excel datetime or strings like "09:00"/"18:00"
    if np.issubdtype(s.dtype, np.datetime64):
        return pd.to_datetime(s, errors="coerce")
    return pd.to_datetime(s.astype(str), errors="coerce")


def main():
    path = Path("ShiftPlan_Agent_Demo/testdata/Simple_Shift_Plan_Request.xlsx")
    print(f"Loading Excel: {path}")
    if not path.exists():
        print("ERROR: Excel file not found at", path)
        return

    xls = pd.ExcelFile(path)
    print("Sheets:", xls.sheet_names)

    # ===== Modulation sheet =====
    print("\n===== Modulation sheet summary =====")
    mod_name = next((n for n in xls.sheet_names if str(n).strip().lower() == "modulation"), None)
    if not mod_name:
        print("Modulation sheet not found")
    else:
        mod = pd.read_excel(xls, sheet_name=mod_name)
        print("Columns:", list(mod.columns))
        print("Dtypes:\n", mod.dtypes)
        print("Head:\n", mod.head(5))

        dcol = guess_date_col(mod)
        if dcol is not None:
            mod["_date"] = parse_dates(mod[dcol])
            print(f"Date column inferred: {dcol}; range: {mod['_date'].min()} .. {mod['_date'].max()}  (nulls={mod['_date'].isna().sum()})")
        else:
            print("No date-like column found in Modulation")

        base_sm = find_col(mod, ["base", "store", "manager"]) or find_col(mod, ["base", "manager"])
        base_sales = find_col(mod, ["base", "sales"])
        act_sm = find_col(mod, ["actual", "store", "manager"]) or find_col(mod, ["actual", "manager"])
        act_sales = find_col(mod, ["actual", "sales"])
        weather = find_col(mod, ["weather"])
        offer = find_col(mod, ["special", "offer"]) or find_col(mod, ["offer"])

        print("Detected columns:")
        print("  Base_StoreManager:", base_sm)
        print("  Base_Sales:", base_sales)
        print("  Actual_StoreManager:", act_sm)
        print("  Actual_Sales:", act_sales)
        print("  Weather:", weather)
        print("  Special Offer:", offer)

        if act_sm is not None and act_sales is not None:
            hist_mask = mod[act_sm].notna() | mod[act_sales].notna()
            fut_mask = (~mod[act_sm].notna()) & (~mod[act_sales].notna())
            print(f"Historical rows (any Actual present): {int(hist_mask.sum())}")
            print(f"Future rows (both Actual missing): {int(fut_mask.sum())}")
            if fut_mask.any():
                if weather is not None:
                    fut_weather_na = int(mod.loc[fut_mask, weather].isna().sum())
                    print(f"  Future Weather missing: {fut_weather_na}")
                if offer is not None:
                    fut_offer_na = int(mod.loc[fut_mask, offer].isna().sum())
                    print(f"  Future Special Offer missing: {fut_offer_na}")

    # ===== Opening Hours sheet =====
    print("\n===== Opening Hours sheet summary =====")
    oh_name = next((n for n in xls.sheet_names if str(n).strip().lower() == "opening hours"), None)
    if not oh_name:
        print("Opening Hours sheet not found")
    else:
        oh = pd.read_excel(xls, sheet_name=oh_name)
        print("Columns:", list(oh.columns))
        print("Dtypes:\n", oh.dtypes)
        print("Head:\n", oh.head(5))

        dcol_oh = guess_date_col(oh)
        if dcol_oh is not None:
            oh["_date"] = parse_dates(oh[dcol_oh])
            print(f"Date column inferred (Opening Hours): {dcol_oh}; range: {oh['_date'].min()} .. {oh['_date'].max()}  (nulls={oh['_date'].isna().sum()})")
        else:
            print("No date-like column found in Opening Hours")

        # Compute opening duration if From/To present
        from_col = find_col(oh, ["from"]) or find_col(oh, ["start"]) or find_col(oh, ["open"])
        to_col = find_col(oh, ["to"]) or find_col(oh, ["end"]) or find_col(oh, ["close"])
        if from_col and to_col:
            start = to_datetime_series(oh[from_col])
            end = to_datetime_series(oh[to_col])
            delta = (end - start).dt.total_seconds() / 3600.0
            # If negative (cross midnight), add 24
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

    # Cross-sheet alignment by date
    try:
        if "mod" in locals() and "_date" in mod.columns and "oh" in locals() and "_date" in oh.columns:
            mdates = set([d for d in mod["_date"] if pd.notna(d)])
            odates = set([d for d in oh["_date"] if pd.notna(d)])
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
    except Exception as e:
        print("Cross-sheet alignment error:", e)

    print("\nInspection complete.")


if __name__ == "__main__":
    main()
