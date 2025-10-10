import json
from pathlib import Path
from typing import Tuple, Dict

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
import lightgbm as lgb
import os
import tempfile

STATUS_CANDIDATES = [
    Path("testdata/forecast_status.json"),
    Path("ShiftPlan_Agent_Demo/testdata/forecast_status.json"),
    Path(__file__).resolve().parents[1] / "testdata" / "forecast_status.json",
    Path(__file__).resolve().parents[2] / "ShiftPlan_Agent_Demo" / "testdata" / "forecast_status.json",
]


def resolve_status_path() -> Path:
    for p in STATUS_CANDIDATES:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            continue
    # Fallback to current dir
    return Path("forecast_status.json")


def run_forecast_to_status() -> None:
    status_path = resolve_status_path()
    try:
        status_path.write_text(json.dumps({"status": "running"}))
        payload = run_forecast()
        status_path.write_text(json.dumps({"status": "done", "payload": payload}))
    except Exception as e:
        status_path.write_text(json.dumps({"status": "error", "error": str(e)}))


EXCEL_PATH = Path("ShiftPlan_Agent_Demo/testdata/Simple_Shift_Plan_Request.xlsx")
SHEET_MOD = "Modulation"
SHEET_OH = "Opening Hours"


def resolve_excel_path(explicit: Path | None = None) -> Path:
    """
    Resolve the Excel path robustly depending on current working directory.
    Tries a list of candidates and returns the first one that exists.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    # Common locations whether server is started from project root or ShiftPlan_Agent_Demo/
    candidates += [
        Path("testdata/Simple_Shift_Plan_Request.xlsx"),
        Path("ShiftPlan_Agent_Demo/testdata/Simple_Shift_Plan_Request.xlsx"),
        Path(__file__).resolve().parents[1] / "testdata" / "Simple_Shift_Plan_Request.xlsx",
        Path(__file__).resolve().parents[2] / "ShiftPlan_Agent_Demo" / "testdata" / "Simple_Shift_Plan_Request.xlsx",
    ]
    for p in candidates:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    raise FileNotFoundError(f"Could not locate Excel file. Tried: {', '.join(str(p) for p in candidates)}")


def _parse_dates(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.normalize()


def _to_datetime_time(s: pd.Series) -> pd.Series:
    # Accepts Excel datetimes or strings ("08:00:00"), returns pandas datetime
    if np.issubdtype(s.dtype, np.datetime64):
        return pd.to_datetime(s, errors="coerce")
    return pd.to_datetime(s.astype(str), errors="coerce")


def load_excel(excel_path: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    xls = pd.ExcelFile(excel_path)
    mod = pd.read_excel(xls, sheet_name=SHEET_MOD)
    oh = pd.read_excel(xls, sheet_name=SHEET_OH)
    # Normalize date columns
    if "Date" not in mod.columns or "Date" not in oh.columns:
        raise ValueError("Both Modulation and Opening Hours must have a 'Date' column.")
    mod["Date"] = _parse_dates(mod["Date"])
    oh["Date"] = _parse_dates(oh["Date"])
    return mod, oh


def compute_open_hours_per_day(oh: pd.DataFrame) -> pd.DataFrame:
    # Try to compute hours from From/To if available, else parse 'open hours' string
    hours = pd.Series(index=oh.index, dtype=float)
    from_col = None
    to_col = None
    for cand in ["From", "from", "Start", "Open"]:
        if cand in oh.columns:
            from_col = cand
            break
    for cand in ["To", "to", "End", "Close", "Closed"]:
        if cand in oh.columns:
            to_col = cand
            break

    if from_col and to_col:
        start = _to_datetime_time(oh[from_col])
        end = _to_datetime_time(oh[to_col])
        delta = (end - start).dt.total_seconds() / 3600.0
        # handle cross-midnight
        delta = delta.where(delta >= 0, delta + 24)
        hours = delta
    elif "open hours" in oh.columns:
        # parse "HH:MM:SS" -> hours float
        td = pd.to_timedelta(oh["open hours"].astype(str), errors="coerce")
        hours = td.dt.total_seconds() / 3600.0
    else:
        # default 0
        hours = pd.Series(0.0, index=oh.index)

    day_hours = (
        pd.DataFrame({"Date": oh["Date"], "_open_hours": hours})
        .groupby("Date", as_index=False)["_open_hours"]
        .sum()
        .rename(columns={"_open_hours": "OpenHours"})
    )
    return day_hours


def build_daily_frame(mod: pd.DataFrame, day_hours: pd.DataFrame) -> pd.DataFrame:
    # Merge modulation with daily open hours
    df = pd.merge(mod.copy(), day_hours, on="Date", how="left")
    # Calendar features
    df["dow"] = df["Date"].dt.dayofweek.astype("Int64")  # 0=Mon
    iso = df["Date"].dt.isocalendar()
    df["week"] = iso.week.astype("Int64")
    df["month"] = df["Date"].dt.month.astype("Int64")
    df["is_weekend"] = (df["dow"] >= 5).astype("Int64")

    # Lags (if enough history; safe even if NA)
    df = df.sort_values("Date")
    for col in ["Actual_StoreManager", "Actual_Sales"]:
        if col in df.columns:
            df[f"{col}_lag7"] = df[col].shift(7)
            df[f"{col}_lag14"] = df[col].shift(14)

    # Ensure base floors exist
    for col in ["Base_StoreManager", "Base_Sales"]:
        if col not in df.columns:
            raise ValueError(f"Missing required column '{col}' in Modulation sheet.")
    # Ensure exogenous
    if "Weather" not in df.columns or "SpecialOffer" not in df.columns:
        raise ValueError("Missing Weather or SpecialOffer in Modulation sheet.")

    # Fill OpenHours na with 0
    df["OpenHours"] = df["OpenHours"].fillna(0.0)
    return df


def split_train_horizon(df: pd.DataFrame, horizon_dates: pd.Series) -> Tuple[pd.DataFrame, pd.DataFrame]:
    horizon_mask = df["Date"].isin(horizon_dates)
    train = df.loc[~horizon_mask].copy()
    horizon = df.loc[horizon_mask].copy()
    return train, horizon


def make_features_targets(df: pd.DataFrame, role: str) -> Tuple[pd.DataFrame, pd.Series, Dict[str, str]]:
    assert role in ("SM", "Sales")
    if role == "SM":
        base_col = "Base_StoreManager"
        y_col = "Actual_StoreManager"
        lag7 = "Actual_StoreManager_lag7"
        lag14 = "Actual_StoreManager_lag14"
    else:
        base_col = "Base_Sales"
        y_col = "Actual_Sales"
        lag7 = "Actual_Sales_lag7"
        lag14 = "Actual_Sales_lag14"

    # Features
    num_cols = [base_col, "OpenHours", "Weather", "SpecialOffer", lag7, lag14]
    cat_cols = ["dow", "month"]
    # Some lags may be NA on early rows; will be handled by imputer inside HGB or filled
    X = df[num_cols + cat_cols].copy()
    # Fill numeric NA with 0 for simplicity here (robust on small data)
    for c in num_cols:
        if c in X.columns:
            X[c] = X[c].fillna(0.0)
    # Target (may be NA in future/horizon)
    y = df[y_col] if y_col in df.columns else pd.Series(index=df.index, dtype=float)

    return X, y, {"base_col": base_col, "y_col": y_col}


def make_model_pipeline() -> Pipeline:
    # Placeholder function retained for potential extension
    # The actual models (LightGBM + Poisson GLM) are built directly in fit_and_predict
    return Pipeline(steps=[("noop", "passthrough")])


def fit_and_predict(train_df: pd.DataFrame, horizon_df: pd.DataFrame, role: str) -> Tuple[pd.DataFrame, Dict[str, float]]:
    # Build X/y for train and horizon
    X_train_raw, y_train, info = make_features_targets(train_df, role)
    X_h_raw, _, _ = make_features_targets(horizon_df, role)

    # Prepare encoder for categorical columns
    cat_cols = ["dow", "month"]
    num_cols = [c for c in X_train_raw.columns if c not in cat_cols]
    pre = ColumnTransformer(
        transformers=[
            ("num", "passthrough", num_cols),
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cat_cols),
        ]
    )

    # Drop rows with NaN targets and non-finite targets for training
    y_train_arr = y_train.to_numpy(dtype=float)
    finite_mask = np.isfinite(y_train_arr)
    notna_mask = ~np.isnan(y_train_arr)
    mask = finite_mask & notna_mask
    if mask.sum() == 0:
        # Fallback: no supervised signal available -> use base as prediction
        out = horizon_df[["Date"]].copy()
        out["pred"] = 0.0
        out["base"] = horizon_df[info["base_col"]].to_numpy(dtype=float)
        out["pred_capped"] = np.maximum(np.ceil(out["pred"]).astype(int), out["base"].astype(int))
        return out, {"train_mae": float("nan")}

    X_train = pre.fit_transform(X_train_raw.loc[mask])
    X_h = pre.transform(X_h_raw)

    # Monotone constraints: for LightGBM, we need to specify per feature in the transformed X
    # After OneHotEncoder, we have: [num_cols..., one-hot dow, one-hot month]
    # Monotone increasing for: base_col (index 0), OpenHours (1), Weather (2), SpecialOffer (3)
    # Lags and categorical: no constraint (0)
    num_features_count = len(num_cols)
    cat_features_count = X_train.shape[1] - num_features_count
    # base, OpenHours, Weather, SpecialOffer are first 4 in num_cols; lags are next 2
    monotone_constraints_list = [1, 1, 1, 1, 0, 0] + [0] * cat_features_count

    # Fit two models: LightGBM with Poisson + monotonic constraints, and Poisson GLM
    lgbm = lgb.LGBMRegressor(
        objective="poisson",
        learning_rate=0.05,
        n_estimators=100,
        num_leaves=31,
        max_depth=-1,
        min_child_samples=10,
        reg_alpha=0.1,
        reg_lambda=0.1,
        monotone_constraints=monotone_constraints_list,
        random_state=42,
        verbosity=-1,
    )
    pois = PoissonRegressor(alpha=0.5, max_iter=1000, tol=1e-8)

    # Guard: clip negatives to 0
    y_train_clip = np.clip(y_train_arr[mask], a_min=0, a_max=None)

    lgbm.fit(X_train, y_train_clip)
    pois.fit(X_train, y_train_clip)

    pred_h_lgbm = np.maximum(lgbm.predict(X_h), 0.0)
    pred_h_pois = np.maximum(pois.predict(X_h), 0.0)

    # Blend weights (we could tune; simple average for now)
    pred_h = 0.6 * pred_h_lgbm + 0.4 * pred_h_pois

    # Backtest on train (optional quick check)
    pred_t_lgbm = np.maximum(lgbm.predict(X_train), 0.0)
    pred_t_pois = np.maximum(pois.predict(X_train), 0.0)
    pred_t = 0.6 * pred_t_lgbm + 0.4 * pred_t_pois
    mae = mean_absolute_error(y_train_clip, pred_t)
    metrics = {"train_mae": float(mae)}

    # Assemble prediction frame
    out = horizon_df[["Date"]].copy()
    out["pred"] = pred_h
    out["base"] = horizon_df[info["base_col"]].to_numpy(dtype=float)
    if role == "Sales":
        # Round up to integer and enforce integer base
        pred_round = np.ceil(out["pred"])
        out["pred_capped"] = np.maximum(pred_round, np.ceil(out["base"])).astype(int)
    else:
        # Store Managers: round up to integer and enforce integer base
        pred_round = np.ceil(out["pred"])
        out["pred_capped"] = np.maximum(pred_round, np.ceil(out["base"])).astype(int)

    return out, metrics


def write_forecast_into_opening_hours(excel_path: Path, oh: pd.DataFrame, fc_sm: pd.DataFrame, fc_sales: pd.DataFrame) -> pd.DataFrame:
    """
    Update the 'Opening Hours' sheet in-place by writing to a temporary workbook and atomically replacing the original.
    This avoids append-mode issues (locks or hangs) when the file is open in other apps.
    """
    # Map daily predictions onto all rows per date
    fc_sm_day = fc_sm.set_index("Date")["pred_capped"]
    fc_sales_day = fc_sales.set_index("Date")["pred_capped"]

    oh_out = oh.copy()
    oh_out["Store Manager"] = oh_out["Date"].map(fc_sm_day).astype("Int64")
    oh_out["Sales"] = oh_out["Date"].map(fc_sales_day).astype("Int64")

    # Read entire workbook, replace only Opening Hours, write to temp, then atomic replace
    sheets = pd.read_excel(excel_path, sheet_name=None)
    sheets[SHEET_OH] = oh_out

    tmp_dir = excel_path.parent
    tmp_path = tmp_dir / f"{excel_path.stem}.__tmp__.xlsx"
    # Ensure any stale tmp is removed
    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except Exception:
        pass

    with pd.ExcelWriter(tmp_path, engine="openpyxl", mode="w") as writer:
        for name, df in sheets.items():
            # Ensure DataFrame for safety
            if isinstance(df, pd.DataFrame):
                df.to_excel(writer, sheet_name=name, index=False)

    # Atomic replace
    os.replace(tmp_path, excel_path)
    return sheets[SHEET_OH]


def export_forecast_files(fc_sm: pd.DataFrame, fc_sales: pd.DataFrame, base_dir: Path | None = None) -> None:
    out = pd.merge(
        fc_sm[["Date", "pred_capped"]].rename(columns={"pred_capped": "Pred_StoreManager"}),
        fc_sales[["Date", "pred_capped"]].rename(columns={"pred_capped": "Pred_Sales"}),
        on="Date",
        how="outer",
    ).sort_values("Date")

    if base_dir is None:
        candidates = [
            Path("testdata"),
            Path("ShiftPlan_Agent_Demo/testdata"),
            Path(__file__).resolve().parents[1] / "testdata",
            Path(__file__).resolve().parents[2] / "ShiftPlan_Agent_Demo" / "testdata",
        ]
        # pick first existing, else first candidate
        base_dir = next((p for p in candidates if p.exists()), candidates[0])

    base_dir.mkdir(parents=True, exist_ok=True)
    out_path_csv = base_dir / "forecast_output.csv"
    out_path_json = base_dir / "forecast_output.json"

    out.to_csv(out_path_csv, index=False)
    with open(out_path_json, "w", encoding="utf-8") as f:
        json.dump(out.assign(Date=out["Date"].dt.strftime("%Y-%m-%d")).to_dict(orient="records"), f, indent=2)


def run_forecast(excel_path: Path | None = None) -> Dict[str, object]:
    # Core pipeline without printing; returns a JSON-serializable dict
    path = resolve_excel_path(excel_path)
    mod, oh = load_excel(path)
    day_hours = compute_open_hours_per_day(oh)
    df = build_daily_frame(mod, day_hours)

    # Horizon dates = dates present in Opening Hours
    horizon_dates = day_hours["Date"].unique()
    train_df, horizon_df = split_train_horizon(df, horizon_dates=horizon_dates)

    # If train becomes empty (edge case), fallback to all but last N days
    if train_df.empty:
        df_sorted = df.sort_values("Date")
        cutoff = max(0, len(df_sorted) - 14)
        train_df = df_sorted.iloc[:cutoff].copy()
        horizon_df = df_sorted.iloc[cutoff:].copy()

    # Fit and predict for each role
    fc_sm, m_sm = fit_and_predict(train_df, horizon_df, role="SM")
    fc_sales, m_sales = fit_and_predict(train_df, horizon_df, role="Sales")

    # Write back into Opening Hours
    oh_out = write_forecast_into_opening_hours(path, oh, fc_sm, fc_sales)

    # Export also as CSV/JSON to the same directory as the Excel file
    export_forecast_files(fc_sm, fc_sales, base_dir=path.parent)
    out_paths = {
        "csv": str((path.parent / "forecast_output.csv").as_posix()),
        "json": str((path.parent / "forecast_output.json").as_posix()),
    }

    # Build preview and response payload
    preview_df = (
        oh_out[["Date", "Store Manager", "Sales"]]
        .drop_duplicates()
        .sort_values("Date")
        .head(14)
    )
    preview = (
        preview_df.assign(Date=preview_df["Date"].dt.strftime("%Y-%m-%d"))
        .to_dict(orient="records")
    )
    updated_dates = sorted(
        pd.to_datetime(pd.Series(horizon_dates)).dt.strftime("%Y-%m-%d").tolist()
    )

    return {
        "metrics": {"SM": float(m_sm.get("train_mae", float("nan"))), "Sales": float(m_sales.get("train_mae", float("nan")))},
        "updated_dates": updated_dates,
        "preview": preview,
        "paths": out_paths,
    }


def main():
    path = resolve_excel_path(EXCEL_PATH)
    print(f"Loading: {path}")
    mod, oh = load_excel(path)
    print(f"Modulation rows: {len(mod)}, Opening Hours rows: {len(oh)}")
    day_hours = compute_open_hours_per_day(oh)
    print(f"Daily OpenHours rows: {len(day_hours)}  range: {day_hours['Date'].min().date()}..{day_hours['Date'].max().date()}")

    df = build_daily_frame(mod, day_hours)

    # Horizon dates = dates present in Opening Hours
    horizon_dates = day_hours["Date"].unique()
    train_df, horizon_df = split_train_horizon(df, horizon_dates=horizon_dates)

    # If train becomes empty (edge case), fallback to all but last N days
    if train_df.empty:
        print("Warning: Train set is empty after horizon exclusion. Falling back to using all rows except the last 14 days.")
        df_sorted = df.sort_values("Date")
        cutoff = max(0, len(df_sorted) - 14)
        train_df = df_sorted.iloc[:cutoff].copy()
        horizon_df = df_sorted.iloc[cutoff:].copy()

    # Fit and predict for each role
    fc_sm, m_sm = fit_and_predict(train_df, horizon_df, role="SM")
    fc_sales, m_sales = fit_and_predict(train_df, horizon_df, role="Sales")

    print("Metrics (train MAE):", {"SM": m_sm["train_mae"], "Sales": m_sales["train_mae"]})

    # Write back into Opening Hours
    oh_out = write_forecast_into_opening_hours(path, oh, fc_sm, fc_sales)
    print("Wrote forecasts into Opening Hours columns: ['Store Manager', 'Sales'] for dates found in Opening Hours.")

    # Export also as CSV/JSON
    export_forecast_files(fc_sm, fc_sales, base_dir=path.parent)
    print("Exported forecast to testdata/forecast_output.csv and testdata/forecast_output.json")

    # Summary preview
    preview = oh_out[["Date", "Store Manager", "Sales"]].drop_duplicates().sort_values("Date").head(14)
    print("\nPreview of updated Opening Hours daily staffing (first 14 unique dates):")
    print(preview.to_string(index=False))


if __name__ == "__main__":
    main()
