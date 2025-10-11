from typing import List, Dict, Any
from datetime import datetime, timedelta


def split_demand_to_hourly(demand: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Split demand entries with multi-hour timeslots into 1-hour granularity.
    
    Args:
        demand: List with format [{day, time, role, qty}, ...]
                where time can be e.g., "08:00-12:00" (4 hours)
    
    Returns:
        List with 1-hour entries, e.g., "08:00-09:00", "09:00-10:00", etc.
        Each entry retains the same qty (headcount requirement) per hour.
    """
    if not demand:
        return []
    
    hourly_demand = []
    
    for entry in demand:
        day = entry.get("day", "")
        time_str = entry.get("time", "")
        role = entry.get("role", "")
        qty = entry.get("qty", 0)
        
        # Parse time range
        start_min, end_min = _parse_time_range(time_str)
        if start_min is None or end_min is None:
            # Invalid time format - keep as-is
            hourly_demand.append(entry)
            continue
        
        # Calculate duration in hours
        duration_minutes = end_min - start_min
        if duration_minutes <= 0:
            # Invalid or zero duration - keep as-is
            hourly_demand.append(entry)
            continue
        
        # Split into 1-hour blocks
        current_min = start_min
        while current_min < end_min:
            next_min = min(current_min + 60, end_min)
            
            hourly_entry = {
                "day": day,
                "time": f"{_minutes_to_time_str(current_min)}-{_minutes_to_time_str(next_min)}",
                "role": role,
                "qty": qty,
                "_original_block": time_str,  # Track original forecast block
            }
            hourly_demand.append(hourly_entry)
            current_min = next_min
    
    return hourly_demand


def _parse_time_range(time_str: str) -> tuple:
    """Parse time range string like '08:00-12:00' or '08:00:00-12:00:00' into (start_minutes, end_minutes)."""
    try:
        if not time_str or "-" not in time_str:
            return (None, None)
        start, end = time_str.split("-", 1)
        
        # Parse start time
        start_parts = start.strip().split(":")
        start_h = int(start_parts[0])
        start_m = int(start_parts[1]) if len(start_parts) > 1 else 0
        start_minutes = start_h * 60 + start_m
        
        # Parse end time
        end_parts = end.strip().split(":")
        end_h = int(end_parts[0])
        end_m = int(end_parts[1]) if len(end_parts) > 1 else 0
        end_minutes = end_h * 60 + end_m
        
        return (start_minutes, end_minutes)
    except Exception:
        return (None, None)


def _minutes_to_time_str(minutes: int) -> str:
    """Convert minutes since midnight to HH:MM:SS format."""
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}:00"


def aggregate_demand_by_block(hourly_demand: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Aggregate hourly demand back to original forecast blocks for reporting.
    
    Args:
        hourly_demand: List of 1-hour demand entries
    
    Returns:
        Dict mapping original_block to aggregated stats:
        {
            "08:00-12:00": {
                "required_hours": 16,  # 4 hours * 4 people
                "fulfilled_hours": 14,
                "coverage_pct": 87.5
            }
        }
    """
    blocks = {}
    
    for entry in hourly_demand:
        original = entry.get("_original_block")
        if not original:
            continue
        
        if original not in blocks:
            blocks[original] = {
                "required_hours": 0,
                "fulfilled_hours": 0,
            }
        
        # Each hour requires qty people
        qty = int(entry.get("qty", 0))
        blocks[original]["required_hours"] += qty
    
    return blocks


def convert_forecast_to_demand(forecast_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert forecast output format to demand format for solver.
    
    Forecast format:
        {Date, OpenHours, From, To, Store Manager, Sales, Checkout, ...}
    
    Demand format:
        [{day, time, role, qty}, ...]
    """
    demand = []
    
    for row in forecast_data:
        date = row.get("Date", "")
        from_time = row.get("From", "")
        to_time = row.get("To", "")
        
        # Skip if missing critical fields
        if not date or not from_time or not to_time:
            continue
        
        # Format time range
        time_range = f"{_format_time(from_time)}-{_format_time(to_time)}"
        
        # Extract role columns (anything that's not Date, OpenHours, From, To)
        reserved_cols = {"Date", "OpenHours", "From", "To", "date", "openhours", "from", "to"}
        
        for col, value in row.items():
            if col in reserved_cols:
                continue
            
            # Try to parse as quantity
            try:
                qty = int(value) if value else 0
                if qty > 0:
                    demand.append({
                        "day": date,
                        "time": time_range,
                        "role": col,
                        "qty": qty,
                    })
            except (ValueError, TypeError):
                # Not a numeric column, skip
                continue
    
    return demand


def _format_time(time_value: Any) -> str:
    """Format time value to HH:MM:SS string."""
    if isinstance(time_value, str):
        # Already a string, ensure proper format
        parts = time_value.strip().split(":")
        if len(parts) >= 2:
            h = int(parts[0])
            m = int(parts[1])
            return f"{h:02d}:{m:02d}:00"
        return time_value
    
    # Try datetime
    try:
        if hasattr(time_value, 'hour'):
            return f"{time_value.hour:02d}:{time_value.minute:02d}:00"
    except Exception:
        pass
    
    return str(time_value)
