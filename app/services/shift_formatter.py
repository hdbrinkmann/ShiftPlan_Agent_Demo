from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from collections import defaultdict


def consolidate_shifts(assignments: List[Dict[str, Any]], employees: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Consolidate assignments into employee-centric shifts by merging consecutive timeslots.
    
    Args:
        assignments: List of raw assignments from solver with format:
            {employee_id, role, day, time, hours, cost_per_hour}
        employees: Optional list of employee data for enrichment (names, etc.)
    
    Returns:
        List of consolidated shifts with format:
            {day, employee_id, employee_name, role, shift_start, shift_end, hours, cost}
    """
    if not assignments:
        return []
    
    # Build employee name lookup if available
    emp_names = {}
    if employees:
        for emp in employees:
            emp_names[str(emp.get("id", ""))] = emp.get("name", "")
    
    # Group assignments by (employee_id, day, role)
    grouped: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for a in assignments:
        key = (str(a.get("employee_id", "")), str(a.get("day", "")), str(a.get("role", "")))
        grouped[key].append(a)
    
    shifts = []
    
    for (emp_id, day, role), group in grouped.items():
        # Sort by time
        sorted_group = sorted(group, key=lambda x: _parse_time_start(x.get("time", "")))
        
        # Merge consecutive timeslots
        merged = _merge_consecutive_slots(sorted_group)
        
        # Format output
        for shift_data in merged:
            emp_name = emp_names.get(emp_id, emp_id)
            shifts.append({
                "day": day,
                "employee_id": emp_id,
                "employee_name": emp_name,
                "role": role,
                "shift_start": shift_data["start"],
                "shift_end": shift_data["end"],
                "hours": shift_data["hours"],
                "cost": shift_data["cost"],
            })
    
    # Sort by day, then employee name, then shift start time
    shifts.sort(key=lambda x: (_parse_date_for_sort(x["day"]), x["employee_name"].lower(), x["shift_start"]))
    
    # Format days as date only (no time)
    for shift in shifts:
        shift["day"] = _format_date_only(shift["day"])
    
    return shifts


def _parse_time_start(time_str: str) -> int:
    """Parse time string and return start time in minutes since midnight."""
    try:
        if not time_str or "-" not in time_str:
            return 0
        start = time_str.split("-")[0].strip()
        parts = start.split(":")
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        return hours * 60 + minutes
    except Exception:
        return 0


def _parse_time_range(time_str: str) -> tuple:
    """Parse time range string like '08:00-12:00' into (start_minutes, end_minutes)."""
    try:
        if not time_str or "-" not in time_str:
            return (0, 0)
        start, end = time_str.split("-", 1)
        
        start_parts = start.strip().split(":")
        start_h = int(start_parts[0])
        start_m = int(start_parts[1]) if len(start_parts) > 1 else 0
        start_minutes = start_h * 60 + start_m
        
        end_parts = end.strip().split(":")
        end_h = int(end_parts[0])
        end_m = int(end_parts[1]) if len(end_parts) > 1 else 0
        end_minutes = end_h * 60 + end_m
        
        return (start_minutes, end_minutes)
    except Exception:
        return (0, 0)


def _minutes_to_time_str(minutes: int) -> str:
    """Convert minutes since midnight to HH:MM:SS format."""
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}:00"


def _merge_consecutive_slots(slots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge consecutive timeslots into continuous shifts.
    
    Args:
        slots: Sorted list of assignments for same employee/day/role
    
    Returns:
        List of merged shift segments with start, end, hours, cost
    """
    if not slots:
        return []
    
    merged = []
    current_start = None
    current_end = None
    current_hours = 0.0
    current_cost = 0.0
    
    for slot in slots:
        time_str = slot.get("time", "")
        slot_start, slot_end = _parse_time_range(time_str)
        slot_hours = float(slot.get("hours", 0) or 0)
        slot_cost_per_hour = float(slot.get("cost_per_hour", 0) or 0)
        slot_cost = slot_hours * slot_cost_per_hour
        
        if current_start is None:
            # First slot in this shift
            current_start = slot_start
            current_end = slot_end
            current_hours = slot_hours
            current_cost = slot_cost
        elif slot_start == current_end:
            # Consecutive - extend current shift
            current_end = slot_end
            current_hours += slot_hours
            current_cost += slot_cost
        else:
            # Gap detected - save current shift and start new one
            if current_start is not None and current_end is not None:
                merged.append({
                    "start": _minutes_to_time_str(current_start),
                    "end": _minutes_to_time_str(current_end),
                    "hours": current_hours,
                    "cost": current_cost,
                })
            current_start = slot_start
            current_end = slot_end
            current_hours = slot_hours
            current_cost = slot_cost
    
    # Don't forget the last shift
    if current_start is not None and current_end is not None:
        merged.append({
            "start": _minutes_to_time_str(current_start),
            "end": _minutes_to_time_str(current_end),
            "hours": current_hours,
            "cost": current_cost,
        })
    
    return merged


def _parse_date_for_sort(date_str: str) -> tuple:
    """Parse date string into sortable tuple (year, month, day)."""
    from datetime import datetime
    
    # Try to parse as datetime first
    try:
        # Handle ISO format with or without time
        if " " in str(date_str):
            date_str = str(date_str).split(" ")[0]
        
        # Try various date formats
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(str(date_str).strip(), fmt)
                return (dt.year, dt.month, dt.day)
            except Exception:
                continue
    except Exception:
        pass
    
    # Fallback: return as-is for string sorting
    return (9999, 12, 31)  # Put unparseable dates at end


def _format_date_only(date_str: str) -> str:
    """Format date string to DD.MM.YYYY without time portion."""
    from datetime import datetime
    
    # Handle empty/None
    if not date_str:
        return str(date_str)
    
    # Remove time portion if present
    date_part = str(date_str).strip()
    if " " in date_part:
        date_part = date_part.split(" ")[0]
    
    # Try to parse and reformat to DD.MM.YYYY
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(date_part, fmt)
            return dt.strftime("%d.%m.%Y")
        except Exception:
            continue
    
    # If parsing fails, return cleaned string (no time)
    return date_part


def format_shifts_for_display(shifts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Format consolidated shifts for UI display.
    
    Returns shifts sorted by day and employee with formatted time ranges.
    """
    display = []
    for shift in shifts:
        display.append({
            "day": shift["day"],
            "employee": f"{shift['employee_name']} ({shift['employee_id']})",
            "role": shift["role"],
            "time": f"{shift['shift_start'][:5]}-{shift['shift_end'][:5]}",  # HH:MM format
            "hours": round(shift["hours"], 2),
            "cost": round(shift["cost"], 2),
        })
    return display
