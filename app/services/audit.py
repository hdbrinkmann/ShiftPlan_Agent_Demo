from typing import Dict, Any, List
from datetime import datetime
import re

def check(solution: Dict[str, Any], constraints: Dict[str, Any], demand: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Audit shift plan assignments against demand requirements.
    Checks for under-coverage and constraint violations.

    Important: Assignments may span multiple demand blocks (e.g., one 8h shift covering two 4h blocks).
    Therefore we compute coverage on an hourly grid and evaluate each demand block by the minimum
    hourly coverage across its hours. This avoids false under-coverage when timespans don't match exactly.
    """
    violations: List[Dict[str, Any]] = []

    def _parse_time_range(time_str: str) -> tuple[int | None, int | None]:
        try:
            if not time_str or "-" not in time_str:
                return None, None
            start, end = time_str.split("-", 1)
            def _part(s: str) -> int:
                parts = s.strip().split(":")
                h = int(parts[0]); m = int(parts[1]) if len(parts) > 1 else 0
                return h * 60 + m
            return _part(start), _part(end)
        except Exception:
            return None, None

    def _to_hhmm_range(start_min: int, end_min: int) -> str:
        h1, m1 = divmod(start_min, 60)
        h2, m2 = divmod(end_min, 60)
        return f"{h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}"

    # Get assignments - handle both old and new format
    assignments = solution.get("assignments", [])
    if not assignments and "assignments_raw" in solution:
        assignments = solution.get("assignments_raw", [])

    # Build hourly coverage by (day, role, hour)
    coverage_hr: Dict[tuple[str, str, int], int] = {}
    legacy_bucket: Dict[tuple[str, str, str], int] = {}
    intervals: Dict[tuple[str, str], List[tuple[int, int]]] = {}
    for a in assignments:
        try:
            day = _normalize_day_str(a.get("day", ""))
            role = _norm_role(a.get("role", ""))
            time_s = str(a.get("time", "")).strip()
            if not day or not role or not time_s:
                continue

            start_min, end_min = _parse_time_range(time_s)
            if start_min is None or end_min is None:
                # Fallback to legacy exact bucket if we cannot parse
                tkey = _normalize_time_format(time_s)
                legacy_bucket[(day, role, tkey)] = legacy_bucket.get((day, role, tkey), 0) + 1
                continue

            # Record full assignment interval for exact containment checks
            intervals.setdefault((day, role), []).append((start_min, end_min))

            for minute in range(start_min, end_min, 60):
                hour = minute // 60
                coverage_hr[(day, role, hour)] = coverage_hr.get((day, role, hour), 0) + 1
        except Exception as e:
            print(f"[AUDIT] Error processing assignment: {e}")
            continue

    # Check demand coverage per block by evaluating minimum hourly coverage across the block
    for need in demand:
        try:
            day = _normalize_day_str(need.get("day", ""))
            role = _norm_role(need.get("role", ""))
            qty = int(need.get("qty", 0) or 0)
            t_raw = _normalize_time_format(need.get("time", ""))

            if not day or not role or not t_raw:
                continue

            start_min, end_min = _parse_time_range(t_raw)
            if start_min is None or end_min is None:
                # If we cannot parse the demand range, fallback to legacy exact bucket comparison
                actual = legacy_bucket.get((day, role, t_raw), 0)
                if actual < qty:
                    violations.append({
                        "type": "under_coverage",
                        "day": day,
                        "time": t_raw,
                        "role": role,
                        "required": qty,
                        "actual": actual,
                        "severity": "medium" if qty - actual == 1 else "high",
                    })
                continue

            # 1) Exact containment: count assignments whose intervals fully cover the demand block
            full_cover = 0
            for (a_start, a_end) in intervals.get((day, role), []):
                if a_start <= start_min and a_end >= end_min:
                    full_cover += 1

            # 2) Hourly minimum across the block (robust against alignment)
            hours_in_block = [m // 60 for m in range(start_min, end_min, 60)]
            min_hour_cov = 0
            if hours_in_block:
                min_hour_cov = min(coverage_hr.get((day, role, h), 0) for h in hours_in_block)

            # Use the stronger of the two signals
            actual = max(full_cover, min_hour_cov)

            if actual < qty:
                violations.append({
                    "type": "under_coverage",
                    "day": day,
                    "time": _to_hhmm_range(start_min, end_min),
                    "role": role,
                    "required": qty,
                    "actual": actual,
                    "severity": "medium" if qty - actual == 1 else "high",
                })
        except Exception as e:
            print(f"[AUDIT] Error checking demand: {e}")
            continue

    print(f"[AUDIT] Checked {len(assignments)} assignments against {len(demand)} demand entries")
    print(f"[AUDIT] Found {len(violations)} violations")

    return {"violations": violations}


def _normalize_day_str(val: Any) -> str:
    """
    Normalize a variety of day formats to ISO YYYY-MM-DD so assignments and demand match.
    Accepts strings like '2025-09-22', '2025-09-22 00:00:00', '22.09.2025', '09/22/2025', '2025/09/22'.
    """
    s = str(val or "").strip()
    if not s:
        return ""
    # Handle T or space-separated datetime
    if " " in s:
        s = s.split(" ")[0]
    if "T" in s:
        s = s.split("T")[0]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return s

def _norm_role(val: Any) -> str:
    """
    Normalize role strings to a canonical form for matching between demand and assignments.
    Lowercase, collapse whitespace, and treat underscores/hyphens as spaces.
    """
    s = str(val or "").strip().lower()
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    return s

def _normalize_time_format(time_str: str) -> str:
    """
    Normalize time format to be consistent.
    Converts "09:00:00-17:00:00" to "09:00-17:00" for comparison.
    """
    if not time_str:
        return ""
    
    try:
        # If format is HH:MM:SS-HH:MM:SS, convert to HH:MM-HH:MM
        if time_str.count(":") >= 4:  # Has seconds
            parts = time_str.split("-")
            if len(parts) == 2:
                start = ":".join(parts[0].split(":")[:2])  # Take HH:MM only
                end = ":".join(parts[1].split(":")[:2])
                return f"{start}-{end}"
        return time_str
    except Exception:
        return time_str
