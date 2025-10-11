import json
from pathlib import Path
from typing import Tuple, Dict, List, Any, Optional, cast

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.impute import SimpleImputer
import lightgbm as lgb
import os
import re
from app.data.store import get_excel_path

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
    # Prefer last uploaded Excel path if available
    try:
        up = get_excel_path()
        if up:
            up_path = Path(up)
            if up_path.exists():
                return up_path
    except Exception:
        pass
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


def _normkey(s: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(s or "")).lower()


def _is_hc_col(name: Any) -> bool:
    return bool(re.match(r"(?i)^\s*hc[\s_\-]*", str(name or "").strip()))


def _extract_role_from_hc(name: Any) -> str:
    s = str(name or "").strip()
    s = re.sub(r"(?i)^\s*hc[\s_\-]*", "", s)
    s = re.sub(r"[\s_\-]+", " ", s).strip()
    return s.title() if s else "Role"


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


def _duration_hours(from_s: pd.Series | None, to_s: pd.Series | None) -> pd.Series:
    if from_s is None or to_s is None:
        # Return empty series; caller will replace with ones of appropriate length
        return pd.Series(dtype=float)
    start = _to_datetime_time(from_s)
    end = _to_datetime_time(to_s)
    delta = (end - start).dt.total_seconds() / 3600.0
    delta = delta.where(delta >= 0, delta + 24)
    # Fallback weight=1 where invalid and clip to sane bounds
    delta = delta.fillna(1.0).clip(lower=0.0, upper=24.0)
    return delta


def _pick_from_to(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    from_col = next((c for c in ["From", "from", "Start", "Open"] if c in df.columns), None)
    to_col = next((c for c in ["To", "to", "End", "Close", "Closed"] if c in df.columns), None)
    return from_col, to_col

def _has_from_to(df: pd.DataFrame) -> bool:
    f, t = _pick_from_to(df)
    return bool(f and t)

def add_period_lags(dfp: pd.DataFrame, y_cols: List[str], from_col: str, to_col: str) -> pd.DataFrame:
    """
    Add period-level autoregressive lags per slot (same From/To):
    - lag1d: previous day same slot (groupby slot then shift(1) by date order)
    - lag7d: previous week same slot (shift(7))
    Assumes one row per day per slot in chronological order.
    """
    if not (from_col and to_col):
        return dfp
    df = dfp.copy()
    # Build a stable slot key HH:MM-HH:MM
    slot = _to_datetime_time(df[from_col]).dt.strftime("%H:%M") + "-" + _to_datetime_time(df[to_col]).dt.strftime("%H:%M")
    df["__slot"] = slot
    # Ensure sortable by Date within each slot
    df = df.sort_values(["__slot", "Date"])
    for y in y_cols:
        if y in df.columns:
            df[f"{y}_lag1d"] = df.groupby("__slot")[y].shift(1)
            df[f"{y}_lag7d"] = df.groupby("__slot")[y].shift(7)
    # Keep key for further processing if needed
    return df

def build_period_frame(mod: pd.DataFrame) -> Tuple[pd.DataFrame, List[str], Dict[str, str]]:
    """
    Build a period-level feature frame from Modulation without aggregation.
    - Keeps each row (Date, From, To)
    - Targets y::<Role> from HC* (or Actual_* fallback)
    - Optional floors base::<Role> from Base_*
    - Adds features: period_hours, hour_start, dow, month, is_weekend
    - Keeps numeric/categorical drivers as-is
    """
    df = mod.copy()
    if "Date" not in df.columns:
        raise ValueError("Modulation must have a 'Date' column.")
    # Parse time columns
    from_col, to_col = _pick_from_to(df)
    if not (from_col and to_col):
        raise ValueError("Modulation must have 'From' and 'To' columns for period-level forecasting.")
    start = _to_datetime_time(df[from_col])
    end = _to_datetime_time(df[to_col])
    # Compute duration in hours (handle cross-midnight)
    period_hours = (end - start).dt.total_seconds() / 3600.0
    period_hours = period_hours.where(period_hours >= 0, period_hours + 24).fillna(0.0).clip(lower=0.0, upper=24.0)
    df["Date"] = _parse_dates(df["Date"])
    df[from_col] = start
    df[to_col] = end
    df["_period_hours"] = period_hours

    # Detect targets (HC*) and fallback to Actual_*
    hc_cols = [c for c in df.columns if _is_hc_col(c)]
    target_cols: List[Any] = list(hc_cols)
    roles: List[str] = [_extract_role_from_hc(c) for c in hc_cols]
    if not roles:
        actual_cols = [c for c in df.columns if str(c).strip().lower().startswith("actual")]
        target_cols = list(actual_cols)
        roles = []
        for c in actual_cols:
            tail = re.sub(r"(?i)^\s*actual[\s_\-]*", "", str(c)).strip()
            tail = re.sub(r"[\s_\-]+", " ", tail).strip()
            roles.append(tail.title() if tail else "Role")

    role_keys = [_normkey(r) for r in roles]
    role_display_map: Dict[str, str] = {}
    for rk, rdisp in zip(role_keys, roles):
        if rk not in role_display_map:
            role_display_map[rk] = rdisp

    # Floors
    base_cols = [c for c in df.columns if str(c).strip().lower().startswith("base")]
    base_map: Dict[str, str] = {}
    for b in base_cols:
        tail = re.sub(r"(?i)^\s*base[\s_\-]*", "", str(b)).strip()
        rk = _normkey(tail)
        if rk:
            base_map[rk] = b

    # Calendar/time features
    df = df.sort_values(["Date", from_col, to_col])
    df["dow"] = df["Date"].dt.dayofweek.astype("Int64")
    iso = df["Date"].dt.isocalendar()
    df["week"] = iso.week.astype("Int64")
    df["month"] = df["Date"].dt.month.astype("Int64")
    df["is_weekend"] = (df["dow"] >= 5).astype("Int64")
    df["_hour_start"] = df[from_col].dt.hour.astype("Int64")
    # Cyclical hour-of-day encodings
    df["hour_sin"] = np.sin(2 * np.pi * df["_hour_start"].astype(float) / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * df["_hour_start"].astype(float) / 24.0)

    # Build y::<Role> columns
    role_to_ycol: Dict[str, str] = {}
    for c in target_cols:
        if _is_hc_col(c):
            role_disp = _extract_role_from_hc(c)
        else:
            tail = re.sub(r"(?i)^\s*actual[\s_\-]*", "", str(c)).strip()
            tail = re.sub(r"[\s_\-]+", " ", tail).strip()
            role_disp = tail.title() if tail else "Role"
        rk = _normkey(role_disp)
        ycol = f"y::{role_display_map.get(rk, role_disp)}"
        df[ycol] = pd.to_numeric(df[c], errors="coerce")
        role_to_ycol[rk] = ycol

    # Rename base cols to base::<Role> for uniformity
    for rk, bcol in base_map.items():
        out_col = f"base::{role_display_map.get(rk, rk)}"
        df[out_col] = pd.to_numeric(df[bcol], errors="coerce")

    # Drop raw HC* and Base* source columns to avoid leaking targets/floors as drivers
    _drop_cols = [c for c in df.columns if _is_hc_col(c) or str(c).strip().lower().startswith("base")]
    if _drop_cols:
        df = df.drop(columns=_drop_cols, errors="ignore")

    # Fill numeric NA in generic numeric drivers later; here we just return
    return df, [role_display_map[rk] for rk in role_to_ycol.keys()], role_to_ycol


def flatten_modulation_to_daily(mod: pd.DataFrame) -> Tuple[pd.DataFrame, List[str], Dict[str, str]]:
    """
    Aggregate period-level Modulation into a daily frame:
    - Role targets from HC* columns -> y::<Role> (daily max across periods)
    - Optional floors from Base_* columns -> base::<Role> (daily max)
    - Drivers: for numeric -> *_sum and *_wmean (weighted by period hours if From/To present)
              for non-numeric low-cardinality -> daily mode
    - Calendar features: dow, week, month, is_weekend
    Returns (daily_df, roles, role_to_ycol_map)
    """
    df = mod.copy()
    if "Date" not in df.columns:
        raise ValueError("Modulation must have a 'Date' column.")
    from_col, to_col = _pick_from_to(df)
    hours = _duration_hours(df[from_col] if from_col else None, df[to_col] if to_col else None)
    if hours.empty or len(hours) != len(df):
        hours = pd.Series(1.0, index=df.index)

    # Detect role columns (HC*)
    hc_cols = [c for c in df.columns if _is_hc_col(c)]
    # Default targets from HC_*; fallback to Actual_* if no HC columns present
    target_cols: List[Any] = list(hc_cols)
    roles: List[str] = [_extract_role_from_hc(c) for c in hc_cols]

    if not roles:
        actual_cols = [c for c in df.columns if str(c).strip().lower().startswith("actual")]
        target_cols = list(actual_cols)
        roles = []
        for c in actual_cols:
            tail = re.sub(r"(?i)^\s*actual[\s_\-]*", "", str(c)).strip()
            tail = re.sub(r"[\s_\-]+", " ", tail).strip()
            roles.append(tail.title() if tail else "Role")

    role_keys = [_normkey(r) for r in roles]
    # Map role key to display (first occurrence wins)
    role_display_map: Dict[str, str] = {}
    for rk, rdisp in zip(role_keys, roles):
        if rk not in role_display_map:
            role_display_map[rk] = rdisp

    # Detect base columns (Base_<role>)
    base_cols = [c for c in df.columns if str(c).strip().lower().startswith("base")]
    base_map: Dict[str, str] = {}  # role_key -> base_col
    for b in base_cols:
        # Try to extract trailing name and map to closest role by normalized key
        tail = re.sub(r"(?i)^\s*base[\s_\-]*", "", str(b)).strip()
        rk = _normkey(tail)
        if rk:
            base_map[rk] = b

    reserved = set(["Date"])
    if from_col:
        reserved.add(from_col)
    if to_col:
        reserved.add(to_col)
    reserved.update(target_cols)
    reserved.update(base_cols)

    # Identify drivers: all other columns
    driver_cols = [c for c in df.columns if c not in reserved]

    # Split drivers by dtype
    # Use pandas inference; treat booleans as categorical
    num_driver_cols = [c for c in driver_cols if pd.api.types.is_numeric_dtype(df[c])]
    cat_driver_cols = [c for c in driver_cols if not pd.api.types.is_numeric_dtype(df[c])]

    # Group by Date
    df["_hours_w"] = hours
    grouped = df.groupby("Date", as_index=False)

    # Role daily targets (max across periods)
    y_frames = []
    role_to_ycol: Dict[str, str] = {}
    for c in target_cols:
        # Derive display role name depending on column type
        if _is_hc_col(c):
            role_disp = _extract_role_from_hc(c)
        else:
            tail = re.sub(r"(?i)^\s*actual[\s_\-]*", "", str(c)).strip()
            tail = re.sub(r"[\s_\-]+", " ", tail).strip()
            role_disp = tail.title() if tail else "Role"
        rk = _normkey(role_disp)
        ycol = f"y::{role_display_map.get(rk, role_disp)}"
        tmp = grouped[[c]].max().rename({c: ycol}, axis=1)
        y_frames.append(tmp)
        role_to_ycol[rk] = ycol

    # Floors (max across periods)
    base_frames = []
    for rk, bcol in base_map.items():
        out_col = f"base::{role_display_map.get(rk, rk)}"
        tmp = grouped[[bcol]].max().rename({bcol: out_col}, axis=1)
        base_frames.append(tmp)

    # Numeric driver aggregations
    num_sum = None
    num_wmean = None
    if num_driver_cols:
        num_sum = grouped[num_driver_cols].sum(min_count=1)
        # Weighted mean: sum(x*w)/sum(w); if sum(w)==0 use mean
        def _wmean(g: pd.DataFrame) -> pd.Series:
            w = g["_hours_w"].to_numpy(dtype=float)
            wsum = np.nansum(w)
            out = {}
            for col in num_driver_cols:
                x = pd.to_numeric(g[col], errors="coerce").to_numpy(dtype=float)
                if np.isfinite(wsum) and wsum > 0:
                    num = np.nansum(x * w)
                    den = np.nansum(w[~np.isnan(x)])
                    if den and np.isfinite(num):
                        out[col] = num / den
                    else:
                        out[col] = np.nanmean(x)
                else:
                    out[col] = np.nanmean(x)
            return pd.Series(out)
        num_wmean = grouped.apply(_wmean).reset_index()

        # Rename aggregated columns
        num_sum = num_sum.rename({c: f"{c}_sum" for c in num_driver_cols}, axis=1)
        num_wmean = num_wmean.rename({c: f"{c}_wmean" for c in num_driver_cols}, axis=1)
        # Ensure DataFrame types for static typing
        if isinstance(num_sum, pd.Series):
            num_sum = num_sum.to_frame()
        if isinstance(num_wmean, pd.Series):
            num_wmean = num_wmean.to_frame()

    # Categorical drivers: daily mode (most frequent non-null)
    cat_mode = None
    if cat_driver_cols:
        def _mode_series(s: pd.Series):
            try:
                m = s.mode(dropna=True)
                return m.iloc[0] if len(m) else np.nan
            except Exception:
                return np.nan

        cat_mode = grouped[cat_driver_cols].agg(_mode_series)
        if isinstance(cat_mode, pd.Series):
            cat_mode = cat_mode.to_frame()
        # Keep names as-is for categorical encoding later

    # Merge all parts
    parts = [cast(pd.DataFrame, df[["Date"]].drop_duplicates()).sort_values(by="Date")]
    parts += y_frames
    if base_frames:
        parts += base_frames
    if num_sum is not None:
        parts.append(num_sum)
    if num_wmean is not None:
        parts.append(num_wmean)
    if cat_mode is not None:
        parts.append(cat_mode)

    # Start with the first frame and merge others by Date
    daily = None
    for p in parts:
        if p is None or p.empty:
            continue
        if daily is None:
            daily = p
        else:
            daily = pd.merge(daily, p, on="Date", how="outer")

    if daily is None:
        # No roles detected; fallback to unique Date rows
        daily = df[["Date"]].drop_duplicates().sort_values(by="Date")

    # Calendar features
    daily = cast(pd.DataFrame, daily).sort_values(by="Date")
    # Ensure Date dtype is datetime for .dt access
    daily["Date"] = pd.to_datetime(daily["Date"], errors="coerce")
    daily["dow"] = daily["Date"].dt.dayofweek.astype("Int64")
    iso = daily["Date"].dt.isocalendar()
    daily["week"] = iso.week.astype("Int64")
    daily["month"] = daily["Date"].dt.month.astype("Int64")
    daily["is_weekend"] = (daily["dow"] >= 5).astype("Int64")

    # Lags per role (if y exists)
    for rk, ycol in role_to_ycol.items():
        if ycol in daily.columns:
            daily[f"{ycol}_lag7"] = daily[ycol].shift(7)
            daily[f"{ycol}_lag14"] = daily[ycol].shift(14)

    # Fill NaNs in numeric aggregated drivers to 0 where appropriate
    for c in daily.columns:
        if c == "Date":
            continue
        if pd.api.types.is_numeric_dtype(daily[c]):
            daily[c] = daily[c].astype(float).fillna(0.0)

    roles_disp = [role_display_map[rk] for rk in role_to_ycol.keys()]
    return daily, roles_disp, role_to_ycol


def build_daily_frame(daily_mod: pd.DataFrame, day_hours: pd.DataFrame) -> pd.DataFrame:
    df = pd.merge(daily_mod.copy(), day_hours, on="Date", how="left")
    df["OpenHours"] = df["OpenHours"].fillna(0.0)
    return df


def split_train_horizon(df: pd.DataFrame, horizon_dates: pd.Series) -> Tuple[pd.DataFrame, pd.DataFrame]:
    horizon_mask = df["Date"].isin(horizon_dates)
    train = df.loc[~horizon_mask].copy()
    horizon = df.loc[horizon_mask].copy()
    # Help static typing tools
    return cast(pd.DataFrame, train), cast(pd.DataFrame, horizon)


def make_model_pipeline() -> Pipeline:
    # Placeholder function retained for potential extension
    return Pipeline(steps=[("noop", "passthrough")])


def _gather_feature_columns(df: pd.DataFrame, y_cols_all: List[str], base_cols_all: List[str], role_y_col: str) -> Tuple[List[str], List[str]]:
    # Numeric and categorical columns, excluding Date, all y:: and base:: columns
    exclude = set(["Date"]) | set(y_cols_all) | set(base_cols_all)
    num_cols = [c for c in df.select_dtypes(include=[np.number]).columns if c not in exclude]
    cat_cols = [c for c in df.select_dtypes(include=["object", "category"]).columns if c not in exclude]
    # Exclude any raw HC*/Base* columns just in case (targets/floors should never be features)
    num_cols = [c for c in num_cols if not re.match(r"(?i)^\s*(hc|base)\b", str(c or ""))]
    cat_cols = [c for c in cat_cols if not re.match(r"(?i)^\s*(hc|base)\b", str(c or ""))]
    # Ensure lag features for this role are included (they are numeric and already present in num_cols, but keep comment for clarity)
    # e.g., f"{role_y_col}_lag7", f"{role_y_col}_lag14"
    return num_cols, cat_cols


def fit_and_predict_dynamic(train_df: pd.DataFrame, horizon_df: pd.DataFrame, role: str, y_col: str, base_col: Optional[str],
                            num_cols: List[str], cat_cols: List[str]) -> Tuple[pd.DataFrame, Dict[str, float]]:
    # Build X/y for train and horizon
    X_train_raw = train_df[num_cols + cat_cols].copy()
    X_h_raw = horizon_df[num_cols + cat_cols].copy()
    y_train = pd.to_numeric(train_df[y_col], errors="coerce")

    # Preprocessor for categorical columns
    # Impute missing values to satisfy PoissonRegressor (LightGBM can handle NaNs but Poisson cannot)
    num_pipe = Pipeline(steps=[
        ("imputer", SimpleImputer(strategy="constant", fill_value=0.0)),
    ])
    cat_pipe = Pipeline(steps=[
        ("imputer", SimpleImputer(strategy="constant", fill_value="missing")),
        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])
    pre = ColumnTransformer(
        transformers=[
            ("num", num_pipe, num_cols),
            ("cat", cat_pipe, cat_cols),
        ],
        remainder="drop",
    )

    # Drop rows with NaN targets and non-finite targets for training
    y_train_arr = y_train.to_numpy(dtype=float)
    finite_mask = np.isfinite(y_train_arr)
    notna_mask = ~np.isnan(y_train_arr)
    mask = finite_mask & notna_mask
    if mask.sum() == 0:
        # Fallback: no supervised signal available -> use base (or zeros) as prediction
        out = horizon_df[["Date"]].copy()
        out["pred"] = 0.0
        base_series: pd.Series
        if base_col and (base_col in horizon_df.columns):
            base_series = cast(pd.Series, horizon_df[base_col])
        else:
            base_series = pd.Series(0.0, index=horizon_df.index)
        out["base"] = cast(pd.Series, pd.to_numeric(base_series, errors="coerce")).fillna(0.0).to_numpy(dtype=float)
        pred_round = np.rint(out["pred"])
        out["pred_capped"] = np.maximum(pred_round, np.ceil(out["base"])).astype(int)
        out = cast(pd.DataFrame, out)
        return out, {"train_mae": float("nan")}

    X_train = pre.fit_transform(X_train_raw.loc[mask])
    X_h = pre.transform(X_h_raw)

    # Fit two models: LightGBM with Poisson + Poisson GLM
    lgbm = lgb.LGBMRegressor(
        objective="poisson",
        learning_rate=0.05,
        n_estimators=100,
        num_leaves=31,
        max_depth=-1,
        min_child_samples=10,
        reg_alpha=0.1,
        reg_lambda=0.1,
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
    pred_h = 0.6 * pred_h_lgbm + 0.4 * pred_h_pois

    # Backtest on train (optional quick check)
    pred_t_lgbm = np.maximum(lgbm.predict(X_train), 0.0)
    pred_t_pois = np.maximum(pois.predict(X_train), 0.0)
    pred_t = 0.6 * pred_t_lgbm + 0.4 * pred_t_pois
    mae = mean_absolute_error(y_train_clip, pred_t)
    
    # Extract feature importances from LightGBM
    try:
        imp = lgbm.feature_importances_
        # Map back to original feature names (before transform)
        feat_names = num_cols + cat_cols
        if len(imp) >= len(feat_names):
            # One-hot encoded features may expand cat_cols; use only first len(feat_names)
            imp_dict = {feat_names[i]: float(imp[i]) for i in range(min(len(feat_names), len(imp)))}
        else:
            imp_dict = {feat_names[i]: float(imp[i]) for i in range(len(imp))}
        # Top 10 by importance
        top_imp = dict(sorted(imp_dict.items(), key=lambda x: x[1], reverse=True)[:10])
    except Exception:
        top_imp = {}
    
    metrics = {"train_mae": float(mae), "feature_importances": top_imp}

    # Assemble prediction frame
    out = horizon_df[["Date"]].copy()
    out["pred"] = pred_h
    base_series: pd.Series
    if base_col and (base_col in horizon_df.columns):
        base_series = cast(pd.Series, horizon_df[base_col])
    else:
        base_series = pd.Series(0.0, index=horizon_df.index)
    out["base"] = cast(pd.Series, pd.to_numeric(base_series, errors="coerce")).fillna(0.0).to_numpy(dtype=float)
    pred_round = np.rint(out["pred"])
    out["pred_capped"] = np.maximum(pred_round, np.ceil(out["base"])).astype(int)
    out = cast(pd.DataFrame, out)
    return out, metrics


def _match_or_create_oh_col(oh_cols: List[str], role_display: str) -> str:
    target_key = _normkey(role_display)
    # Build map of normalized oh columns
    norm_map = { _normkey(c): c for c in oh_cols }
    if target_key in norm_map:
        return norm_map[target_key]
    # Not found -> return intended display name (creating a new column)
    return role_display


def write_forecast_into_opening_hours(excel_path: Path, oh: pd.DataFrame, preds_by_role: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Update the 'Opening Hours' sheet in-place by writing to a temporary workbook and atomically replacing the original.
    Supports arbitrary roles; creates missing columns when necessary.
    """
    oh_out = oh.copy()
    # Ensure Date dtype consistency for mapping
    for role, df in preds_by_role.items():
        if not {"Date", "pred_capped"} <= set(df.columns):
            continue
        target_col = _match_or_create_oh_col(list(oh_out.columns), role)
        if target_col not in oh_out.columns:
            oh_out[target_col] = pd.Series([pd.NA] * len(oh_out), dtype="Int64")

        # Prefer mapping by Date+From+To when available
        from_oh, to_oh = _pick_from_to(oh_out)
        from_fc, to_fc = _pick_from_to(df) if isinstance(df, pd.DataFrame) else (None, None)
        if from_oh and to_oh and from_fc and to_fc and {from_fc, to_fc} <= set(df.columns):
            # Map predictions onto exact periods using a composite key to avoid cartesian merges
            # Standardize time keys to strings HH:MM and build join keys
            df_map = df[["Date", from_fc, to_fc, "pred_capped"]].rename(columns={from_fc: "__from_fc", to_fc: "__to_fc", "pred_capped": "__tmp__"})
            # Ensure Date is datetime for consistent formatting
            df_map["Date"] = pd.to_datetime(df_map["Date"], errors="coerce")
            oh_out["Date"] = pd.to_datetime(oh_out["Date"], errors="coerce")
            oh_out["__from_oh"] = _to_datetime_time(oh_out[from_oh]).dt.strftime("%H:%M")
            oh_out["__to_oh"] = _to_datetime_time(oh_out[to_oh]).dt.strftime("%H:%M")
            df_map["__from_oh"] = _to_datetime_time(df_map["__from_fc"]).dt.strftime("%H:%M")
            df_map["__to_oh"] = _to_datetime_time(df_map["__to_fc"]).dt.strftime("%H:%M")
            # Build composite keys
            oh_out["__key_oh"] = oh_out["Date"].dt.strftime("%Y-%m-%d") + "|" + oh_out["__from_oh"] + "-" + oh_out["__to_oh"]
            df_map["__key_map"] = df_map["Date"].dt.strftime("%Y-%m-%d") + "|" + df_map["__from_oh"] + "-" + df_map["__to_oh"]
            # Drop duplicates on the mapping to prevent ambiguous matches
            df_map = df_map[["__key_map", "__tmp__"]].drop_duplicates()
            m = df_map.set_index("__key_map")["__tmp__"]
            # Assign via map
            _vals = pd.to_numeric(oh_out["__key_oh"].map(m), errors="coerce")
            oh_out[target_col] = _vals.round(0).astype("Int64")
            # Drop temp columns
            for col in ["__from_oh", "__to_oh", "__key_oh"]:
                if col in oh_out.columns:
                    oh_out.drop(columns=[col], inplace=True)
        else:
            # Fallback: map by Date only (same value for all periods on that date)
            day_map = df.set_index("Date")["pred_capped"]
            oh_out[target_col] = oh_out["Date"].map(day_map).astype("Int64")

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


def export_forecast_files(preds_by_role: Dict[str, pd.DataFrame], base_dir: Path | None = None) -> None:
    # Combine all roles into a single wide dataframe
    frames = []
    for role, df in preds_by_role.items():
        if {"Date", "pred_capped"} <= set(df.columns):
            frames.append(df[["Date", "pred_capped"]].rename({"pred_capped": f"Pred_{_normkey(role)}"}, axis=1))
    if frames:
        out = frames[0]
        for fr in frames[1:]:
            out = pd.merge(out, fr, on="Date", how="outer")
        out = cast(pd.DataFrame, out).sort_values(by="Date")
    else:
        out = pd.DataFrame(columns=["Date"])

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
    # JSON with Date as YYYY-MM-DD
    with open(out_path_json, "w", encoding="utf-8") as f:
        if "Date" in out.columns:
            _date_ser = cast(pd.Series, pd.to_datetime(out["Date"], errors="coerce"))
            records = out.assign(Date=_date_ser.dt.strftime("%Y-%m-%d")).to_dict(orient="records")
        else:
            records = out.to_dict(orient="records")
        json.dump(records, f, indent=2)


def run_forecast(excel_path: Path | None = None) -> Dict[str, object]:
    # Core pipeline without printing; returns a JSON-serializable dict
    path = resolve_excel_path(excel_path)
    mod, oh = load_excel(path)

    preds_by_role: Dict[str, pd.DataFrame] = {}
    metrics: Dict[str, float] = {}

    # Choose period-level mode if both sheets provide From/To, else fallback to daily aggregation
    if _has_from_to(mod) and _has_from_to(oh):
        # Period-level
        dfp, roles, role_to_ycol = build_period_frame(mod)
        # Add per-slot autoregressive lags (1-day and 7-day) for each target y::<Role>
        f_mod, t_mod = _pick_from_to(dfp)
        if f_mod and t_mod:
            dfp = add_period_lags(dfp, list(role_to_ycol.values()), f_mod, t_mod)

        # Prepare all y/base columns for feature selection
        y_cols_all = [role_to_ycol[_normkey(r)] for r in roles if _normkey(r) in role_to_ycol]
        base_cols_all = [c for c in dfp.columns if str(c).startswith("base::")]

        # Numeric/categorical drivers selection helper
        def feat_cols_for(df: pd.DataFrame, yc: str) -> Tuple[List[str], List[str]]:
            return _gather_feature_columns(df, y_cols_all, base_cols_all, yc)

        # Inspect info per role
        inspects: Dict[str, Any] = {}
        # Predict role-by-role on horizon rows (where target NA)
        for role in roles:
            rk = _normkey(role)
            y_col = role_to_ycol.get(rk)
            if not y_col or y_col not in dfp.columns:
                continue
            base_col = None
            for c in base_cols_all:
                if _normkey(c.replace("base::", "")) == rk:
                    base_col = c
                    break

            # Split train/horizon by target availability
            train_df_role = dfp.loc[dfp[y_col].notna()].copy()
            horizon_df_role = dfp.loc[dfp[y_col].isna()].copy()
            # If horizon is empty (e.g., all periods labeled), treat rows from Opening Hours as horizon
            if horizon_df_role.empty:
                f_oh, t_oh = _pick_from_to(oh)
                if f_oh and t_oh:
                    ohp = oh.copy()
                    ohp["Date"] = _parse_dates(ohp["Date"])
                    # Use the Modulation drivers by merging on Date/From/To if present, else only Date
                    f_mod, t_mod = _pick_from_to(dfp)
                    if f_mod and t_mod:
                        horizon_df_role = pd.merge(
                            ohp[["Date", f_oh, t_oh]],
                            dfp.drop(columns=[y_col]),
                            left_on=["Date", f_oh, t_oh],
                            right_on=["Date", f_mod, t_mod],
                            how="left",
                        )
                        # Keep only the original columns names
                        horizon_df_role = horizon_df_role[dfp.columns.intersection(horizon_df_role.columns)]
                    else:
                        # Fallback to Date-only join
                        horizon_df_role = pd.merge(
                            ohp[["Date"]],
                            dfp.drop(columns=[y_col]).drop_duplicates(subset=["Date"]),
                            on="Date", how="left"
                        )

            # Feature columns
            num_cols, cat_cols = feat_cols_for(dfp, y_col)
            # Drop constant features (no variance) in train to avoid useless predictors
            const_num = [c for c in num_cols if c in train_df_role.columns and (train_df_role[c].nunique(dropna=False) <= 1)]
            const_cat = [c for c in cat_cols if c in train_df_role.columns and (train_df_role[c].nunique(dropna=False) <= 1)]
            fnum = [c for c in num_cols if c not in const_num]
            fcat = [c for c in cat_cols if c not in const_cat]
            # Ensure at least time features exist
            fallback_candidates = [col for col in ["_hour_start", "_period_hours", "hour_sin", "hour_cos"] if col in dfp.columns]
            if not fnum and fallback_candidates:
                fnum = fallback_candidates
            # Horizon diagnostics: missing percentages before imputation
            miss = {}
            for c in fnum + fcat:
                if c in horizon_df_role.columns:
                    miss[c] = float(horizon_df_role[c].isna().mean())
            # Keep only top-10 by missing desc
            miss_top = dict(sorted(miss.items(), key=lambda x: x[1], reverse=True)[:10])
            # Fit and predict
            out_df, m = fit_and_predict_dynamic(
                cast(pd.DataFrame, train_df_role),
                cast(pd.DataFrame, horizon_df_role),
                role=role, y_col=y_col, base_col=base_col, num_cols=fnum, cat_cols=fcat
            )
            # Collect inspect info
            inspects[role] = {
                "dropped_constants": {"num": const_num[:10], "cat": const_cat[:10]},
                "used_features": {"num": fnum[:10], "cat": fcat[:10]},
                "missing_pct_top": miss_top,
                "feature_importances": m.get("feature_importances", {}),
            }
            # Attach From/To if present using row-aligned concat to avoid cartesian duplication
            f_mod, t_mod = _pick_from_to(horizon_df_role)
            if f_mod and t_mod and {f_mod, t_mod} <= set(horizon_df_role.columns):
                hkeys = horizon_df_role[["Date", f_mod, t_mod]].reset_index(drop=True)
                out_df_no_date = out_df.reset_index(drop=True)
                if "Date" in out_df_no_date.columns:
                    out_df_no_date = out_df_no_date.drop(columns=["Date"])
                out_df = pd.concat([hkeys, out_df_no_date], axis=1)
            preds_by_role[role] = out_df
            metrics[role] = float(m.get("train_mae", float("nan")))

        # Write back into Opening Hours (by Date+From+To)
        oh_out = write_forecast_into_opening_hours(path, oh, preds_by_role)

        # Build preview: show first 14 period rows with Date, From, To, OpenHours + roles
        f_oh, t_oh = _pick_from_to(oh_out)
        # Compute period open hours for preview
        if f_oh and t_oh:
            start = _to_datetime_time(oh_out[f_oh])
            end = _to_datetime_time(oh_out[t_oh])
            per_hours = (end - start).dt.total_seconds() / 3600.0
            per_hours = per_hours.where(per_hours >= 0, per_hours + 24).fillna(0.0).clip(lower=0.0, upper=24.0)
            oh_out = oh_out.assign(OpenHours=per_hours)
        present_cols = []
        for role in preds_by_role.keys():
            col = _match_or_create_oh_col(list(oh_out.columns), role)
            if col in oh_out.columns:
                present_cols.append(col)
        shown_roles = present_cols if len(present_cols) <= 6 else present_cols[:6]
        preview_df = oh_out[["Date", f_oh, t_oh, "OpenHours"] + shown_roles].drop_duplicates().sort_values(by=["Date", f_oh, t_oh]).head(14)
        _pdate_ser = cast(pd.Series, pd.to_datetime(preview_df["Date"], errors="coerce"))
        preview = preview_df.assign(Date=_pdate_ser.dt.strftime("%Y-%m-%d")).to_dict(orient="records")
        updated_dates = sorted(preview_df["Date"].dt.strftime("%Y-%m-%d").unique().tolist() if hasattr(preview_df["Date"], "dt") else sorted(set(d for d in oh_out["Date"])))
        # Build float preview from raw predictions per role if available, so variation is visible even if integers cap/round
        try:
            base_keys = oh_out[["Date", f_oh, t_oh]].drop_duplicates().copy()
            float_wide = base_keys.copy()
            for role_name, dfrole in preds_by_role.items():
                f_r, t_r = _pick_from_to(dfrole) if isinstance(dfrole, pd.DataFrame) else (None, None)
                if f_r and t_r and {"Date", f_r, t_r, "pred"} <= set(dfrole.columns):
                    tmp = dfrole[["Date", f_r, t_r, "pred"]].rename(columns={f_r: f_oh, t_r: t_oh, "pred": role_name})
                    float_wide = pd.merge(float_wide, tmp, on=["Date", f_oh, t_oh], how="left")
            # Add OpenHours if not present
            if "OpenHours" not in float_wide.columns:
                if "OpenHours" in oh_out.columns:
                    float_wide = pd.merge(float_wide, oh_out[["Date", f_oh, t_oh, "OpenHours"]], on=["Date", f_oh, t_oh], how="left")
                else:
                    _start = _to_datetime_time(oh_out[f_oh])
                    _end = _to_datetime_time(oh_out[t_oh])
                    _perh = (_end - _start).dt.total_seconds() / 3600.0
                    _perh = _perh.where(_perh >= 0, _perh + 24).fillna(0.0).clip(lower=0.0, upper=24.0)
                    oh_tmp = oh_out.assign(OpenHours=_perh)[["Date", f_oh, t_oh, "OpenHours"]]
                    float_wide = pd.merge(float_wide, oh_tmp, on=["Date", f_oh, t_oh], how="left")
            # Format date strings and round floats for display
            _dateser = pd.to_datetime(float_wide["Date"], errors="coerce")
            float_wide = float_wide.assign(Date=_dateser.dt.strftime("%Y-%m-%d"))
            # Ensure From/To are strings to avoid JSON serialization issues
            try:
                float_wide[f_oh] = _to_datetime_time(float_wide[f_oh]).dt.strftime("%H:%M:%S")
                float_wide[t_oh] = _to_datetime_time(float_wide[t_oh]).dt.strftime("%H:%M:%S")
            except Exception:
                pass
            for c in list(preds_by_role.keys()):
                if c in float_wide.columns:
                    float_wide[c] = pd.to_numeric(float_wide[c], errors="coerce").round(2)
            preview_float = float_wide.sort_values(by=["Date", f_oh, t_oh]).head(14).to_dict(orient="records")
        except Exception:
            # Fallback: build per-role float preview directly from preds_by_role without merging to OH
            preview_float = []
            try:
                rows = []
                for role_name, dfrole in preds_by_role.items():
                    f_r, t_r = _pick_from_to(dfrole) if isinstance(dfrole, pd.DataFrame) else (None, None)
                    if f_r and t_r and {"Date", f_r, t_r, "pred"} <= set(dfrole.columns):
                        tmp = dfrole[["Date", f_r, t_r, "pred"]].copy()
                        tmp["Date"] = pd.to_datetime(tmp["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                        tmp = tmp.rename(columns={f_r: "From", t_r: "To", "pred": role_name})
                        # Ensure From/To are strings
                        try:
                            tmp["From"] = pd.to_datetime(tmp["From"], errors="coerce").dt.strftime("%H:%M:%S")
                            tmp["To"] = pd.to_datetime(tmp["To"], errors="coerce").dt.strftime("%H:%M:%S")
                        except Exception:
                            pass
                        tmp[role_name] = pd.to_numeric(tmp[role_name], errors="coerce").round(2)
                        rows.extend(tmp.sort_values(by=["Date", "From", "To"]).to_dict(orient="records"))
                preview_float = rows[:14]
            except Exception:
                preview_float = []

    else:
        # Daily fallback (existing behavior)
        daily_mod, roles, role_to_ycol = flatten_modulation_to_daily(mod)
        # Compute OpenHours per day from Opening Hours and merge
        day_hours = compute_open_hours_per_day(oh)
        df = build_daily_frame(daily_mod, day_hours)

        # Horizon dates = dates present in Opening Hours
        horizon_dates = day_hours["Date"].unique()
        train_df, horizon_df = split_train_horizon(df, horizon_dates=horizon_dates)

        # If train becomes empty (edge case), fallback to all but last N days
        if train_df.empty:
            df_sorted = cast(pd.DataFrame, df).sort_values(by="Date")
            cutoff = max(0, len(df_sorted) - 14)
            train_df = df_sorted.iloc[:cutoff].copy()
            horizon_df = df_sorted.iloc[cutoff:].copy()

        # Prepare lists of all y and base columns
        y_cols_all = [role_to_ycol[_normkey(r)] for r in roles if _normkey(r) in role_to_ycol]
        base_cols_all = [c for c in df.columns if str(c).startswith("base::")]

        for role in roles:
            rk = _normkey(role)
            y_col = role_to_ycol.get(rk)
            if not y_col or y_col not in df.columns:
                continue
            # Matching potential floor column for this role
            base_col = None
            for c in base_cols_all:
                # base::<Display> where Display may equal role
                if _normkey(c.replace("base::", "")) == rk:
                    base_col = c
                    break

            # Gather features
            num_cols, cat_cols = _gather_feature_columns(df, y_cols_all, base_cols_all, y_col)

            # Fit and predict
            out_df, m = fit_and_predict_dynamic(cast(pd.DataFrame, train_df), cast(pd.DataFrame, horizon_df), role=role, y_col=y_col, base_col=base_col, num_cols=num_cols, cat_cols=cat_cols)
            preds_by_role[role] = out_df
            metrics[role] = float(m.get("train_mae", float("nan")))

        # Write back into Opening Hours
        oh_out = write_forecast_into_opening_hours(path, oh, preds_by_role)

        # Export also as CSV/JSON to the same directory as the Excel file
        export_forecast_files(preds_by_role, base_dir=path.parent)
        out_paths = {
            "csv": str((path.parent / "forecast_output.csv").as_posix()),
            "json": str((path.parent / "forecast_output.json").as_posix()),
        }

        # Build preview and response payload
        # Include Date and up to the first 6 roles for compactness (or all if <=6)
        # Map roles to actual columns present in Opening Hours (match or created)
        present_cols = []
        for role in roles:
            col = _match_or_create_oh_col(list(oh_out.columns), role)
            if col in oh_out.columns:
                present_cols.append(col)
        shown_roles = present_cols if len(present_cols) <= 6 else present_cols[:6]
        df_pre = oh_out[["Date"] + shown_roles].drop_duplicates()
        merged = pd.merge(df_pre, day_hours, on="Date", how="left")
        preview_cols = ["Date", "OpenHours"] + shown_roles
        preview_df = cast(pd.DataFrame, merged[preview_cols]).sort_values(by="Date").head(14)
        # Stringify dates
        _pdate_ser = cast(pd.Series, pd.to_datetime(preview_df["Date"], errors="coerce"))
        preview = preview_df.assign(Date=_pdate_ser.dt.strftime("%Y-%m-%d")).to_dict(orient="records")
        _dates_ser = cast(pd.Series, pd.to_datetime(pd.Series(horizon_dates), errors="coerce"))
        updated_dates = sorted(_dates_ser.dt.strftime("%Y-%m-%d").tolist())

        return {
            "metrics": metrics,
            "updated_dates": updated_dates,
            "preview": preview,
            "paths": out_paths,
        }

    # Export also as CSV/JSON to the same directory as the Excel file
    export_forecast_files(preds_by_role, base_dir=path.parent)
    out_paths = {
        "csv": str((path.parent / "forecast_output.csv").as_posix()),
        "json": str((path.parent / "forecast_output.json").as_posix()),
    }
    # If period-mode ran, include inspect info (if available)
    try:
        _inspect_payload = inspects  # type: ignore[name-defined]
    except Exception:
        _inspect_payload = {}

    return {
        "metrics": metrics,
        "updated_dates": sorted({pd.to_datetime(x).strftime("%Y-%m-%d") for x in oh_out["Date"]}),
        "preview": preview,
        "preview_float": preview_float,
        "paths": out_paths,
        "inspect": _inspect_payload,
    }


def main():
    path = resolve_excel_path(EXCEL_PATH)
    print(f"Loading: {path}")
    mod, oh = load_excel(path)
    print(f"Modulation rows: {len(mod)}, Opening Hours rows: {len(oh)}")

    daily_mod, roles, role_to_ycol = flatten_modulation_to_daily(mod)
    print(f"Detected roles from HC*: {roles}")
    day_hours = compute_open_hours_per_day(oh)
    print(f"Daily OpenHours rows: {len(day_hours)}  range: {day_hours['Date'].min().date()}..{day_hours['Date'].max().date()}")

    df = build_daily_frame(daily_mod, day_hours)

    # Horizon dates = dates present in Opening Hours
    horizon_dates = day_hours["Date"].unique()
    train_df, horizon_df = split_train_horizon(df, horizon_dates=horizon_dates)

    # If train becomes empty (edge case), fallback to all but last 14 days
    if train_df.empty:
        print("Warning: Train set is empty after horizon exclusion. Falling back to using all rows except the last 14 days.")
        df_sorted = cast(pd.DataFrame, df).sort_values(by="Date")
        cutoff = max(0, len(df_sorted) - 14)
        train_df = df_sorted.iloc[:cutoff].copy()
        horizon_df = df_sorted.iloc[cutoff:].copy()

    # Prepare lists of all y and base columns
    y_cols_all = [role_to_ycol[_normkey(r)] for r in roles if _normkey(r) in role_to_ycol]
    base_cols_all = [c for c in df.columns if str(c).startswith("base::")]

    preds_by_role: Dict[str, pd.DataFrame] = {}
    metrics: Dict[str, float] = {}
    for role in roles:
        rk = _normkey(role)
        y_col = role_to_ycol.get(rk)
        if not y_col or y_col not in df.columns:
            continue
        base_col = None
        for c in base_cols_all:
            if _normkey(c.replace("base::", "")) == rk:
                base_col = c
                break
        num_cols, cat_cols = _gather_feature_columns(df, y_cols_all, base_cols_all, y_col)
        out_df, m = fit_and_predict_dynamic(cast(pd.DataFrame, train_df), cast(pd.DataFrame, horizon_df), role=role, y_col=y_col, base_col=base_col, num_cols=num_cols, cat_cols=cat_cols)
        preds_by_role[role] = out_df
        metrics[role] = float(m.get("train_mae", float("nan")))

    oh_out = write_forecast_into_opening_hours(path, oh, preds_by_role)
    print(f"Wrote forecasts into Opening Hours columns for roles: {list(preds_by_role.keys())}")

    export_forecast_files(preds_by_role, base_dir=path.parent)
    print("Exported forecast to testdata/forecast_output.csv and testdata/forecast_output.json")

    # Summary preview
    present_cols = []
    for role in roles:
        col = _match_or_create_oh_col(list(oh_out.columns), role)
        if col in oh_out.columns:
            present_cols.append(col)
    shown_roles = present_cols[:6]
    df_pre = oh_out[["Date"] + shown_roles].drop_duplicates()
    merged = pd.merge(df_pre, day_hours, on="Date", how="left")
    preview = cast(pd.DataFrame, merged[["Date", "OpenHours"] + shown_roles]).sort_values(by="Date").head(14)
    print("\nPreview of updated Opening Hours daily staffing (first 14 unique dates):")
    print(preview.to_string(index=False))

    print("Metrics (train MAE) per role:", metrics)


if __name__ == "__main__":
    main()
