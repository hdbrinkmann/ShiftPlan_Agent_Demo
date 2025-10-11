from typing import List, Dict, Any
from datetime import datetime, timedelta


def generate_timeline_html(shifts: List[Dict[str, Any]], day: str = None) -> str:
    """
    Generate an HTML timeline visualization of shifts.
    
    Args:
        shifts: List of consolidated shifts with format:
            {day, employee_id, employee_name, role, shift_start, shift_end, hours, cost}
        day: Optional specific day to visualize (if None, uses first day found)
    
    Returns:
        HTML string with timeline visualization
    """
    if not shifts:
        return "<div>No shifts to display</div>"
    
    # Group by day if multiple days
    by_day = {}
    for shift in shifts:
        shift_day = shift.get("day", "")
        if shift_day not in by_day:
            by_day[shift_day] = []
        by_day[shift_day].append(shift)
    
    # If specific day requested, use it; otherwise use first day
    if day and day in by_day:
        target_day = day
    else:
        target_day = sorted(by_day.keys())[0] if by_day else None
    
    if not target_day:
        return "<div>No shifts found for specified day</div>"
    
    # Filter shifts for target day
    day_shifts = [s for s in shifts if s.get("day") == target_day]
    
    if not day_shifts:
        return f"<div>No shifts found for {target_day}</div>"
    
    # Build timeline
    return _build_timeline_html(day_shifts, target_day)


def _build_timeline_html(shifts: List[Dict[str, Any]], day: str) -> str:
    """Build the actual HTML timeline"""
    
    # Find time range
    min_hour, max_hour = _find_time_range(shifts)
    
    # Group shifts by employee and role
    employee_shifts = _group_by_employee_role(shifts)
    
    # Define role colors
    role_colors = {
        "store manager": "#FFD700",  # Gold/Orange
        "store_manager": "#FFD700",
        "manager": "#FFD700",
        "sales": "#90EE90",  # Light green
        "verkauf": "#90EE90",
        "cashier": "#ADD8E6",  # Light blue
        "checkout": "#ADD8E6",
        "kasse": "#ADD8E6",
    }
    
    # Build HTML
    html_parts = []
    
    # Header style
    html_parts.append("""
<style>
.timeline-container {
    width: 100%;
    overflow-x: auto;
    margin: 20px 0;
    font-family: Arial, sans-serif;
}
.timeline-header {
    background: #2c5f7c;
    color: white;
    text-align: center;
    padding: 10px;
    font-size: 18px;
    font-weight: bold;
}
.timeline-grid {
    display: grid;
    border: 1px solid #ccc;
    min-width: 1200px;
}
.timeline-time-header {
    display: contents;
}
.time-cell {
    border-right: 1px solid #ccc;
    border-bottom: 2px solid #666;
    padding: 5px;
    text-align: center;
    font-weight: bold;
    background: #f5f5f5;
}
.employee-row {
    display: contents;
}
.employee-cell {
    border-right: 1px solid #ccc;
    border-bottom: 1px solid #ddd;
    padding: 8px;
    position: relative;
    min-height: 35px;
}
.shift-block {
    padding: 4px 8px;
    border: 1px solid #333;
    border-radius: 3px;
    font-size: 13px;
    text-align: center;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
</style>
""")
    
    # Container
    html_parts.append('<div class="timeline-container">')
    
    # Header with date
    html_parts.append(f'<div class="timeline-header">{day}</div>')
    
    # Calculate grid columns
    num_hours = max_hour - min_hour
    grid_template = f"repeat({num_hours}, 1fr)"
    
    html_parts.append(f'<div class="timeline-grid" style="grid-template-columns: {grid_template};">')
    
    # Time header row
    html_parts.append('<div class="timeline-time-header">')
    for hour in range(min_hour, max_hour):
        html_parts.append(f'<div class="time-cell">{hour:02d}:00</div>')
    html_parts.append('</div>')
    
    # Define role sort priority (lower number = higher priority, appears first)
    def role_priority(role: str) -> int:
        role_lower = role.lower().strip()
        if "store" in role_lower and "manager" in role_lower:
            return 0  # Store Manager first
        elif "manager" in role_lower:
            return 1  # Other managers second
        elif "sales" in role_lower or "verkauf" in role_lower:
            return 2  # Sales third
        elif "cashier" in role_lower or "checkout" in role_lower or "kasse" in role_lower:
            return 3  # Cashier/Checkout fourth
        else:
            return 4  # Everything else last
    
    # Employee rows - sort by role priority, then employee name
    for (employee_name, employee_id, role), emp_shifts in sorted(
        employee_shifts.items(), 
        key=lambda x: (role_priority(x[0][2]), x[0][0].lower())
    ):
        html_parts.append('<div class="employee-row">')
        
        # Create a cell for each hour
        for hour in range(min_hour, max_hour):
            # Check if this employee has a shift covering this hour
            shift_info = _find_shift_at_hour(emp_shifts, hour)
            
            if shift_info:
                # Check if this is the start of a shift
                shift_start_hour = _time_to_hour(shift_info["shift_start"])
                if hour == shift_start_hour:
                    # Calculate span
                    shift_end_hour = _time_to_hour(shift_info["shift_end"])
                    span = shift_end_hour - shift_start_hour
                    
                    # Get color for role
                    role_lower = role.lower().strip()
                    color = role_colors.get(role_lower, "#E0E0E0")
                    
                    # Create shift block spanning multiple columns
                    html_parts.append(
                        f'<div class="employee-cell" style="grid-column: span {span};">'
                        f'<div class="shift-block" style="background-color: {color};">'
                        f'{employee_name} ({employee_id}) {role}'
                        f'</div></div>'
                    )
                # Skip intermediate hours (they're covered by the span)
            else:
                # Empty cell
                html_parts.append('<div class="employee-cell"></div>')
        
        html_parts.append('</div>')
    
    html_parts.append('</div>')  # Close grid
    html_parts.append('</div>')  # Close container
    
    return ''.join(html_parts)


def _find_time_range(shifts: List[Dict[str, Any]]) -> tuple:
    """Find the min and max hours across all shifts"""
    min_hour = 24
    max_hour = 0
    
    for shift in shifts:
        start = shift.get("shift_start", "00:00:00")
        end = shift.get("shift_end", "00:00:00")
        
        start_hour = _time_to_hour(start)
        end_hour = _time_to_hour(end)
        
        min_hour = min(min_hour, start_hour)
        max_hour = max(max_hour, end_hour)
    
    return min_hour, max_hour


def _time_to_hour(time_str: str) -> int:
    """Convert time string HH:MM:SS to hour integer"""
    try:
        parts = str(time_str).split(":")
        return int(parts[0])
    except Exception:
        return 0


def _group_by_employee_role(shifts: List[Dict[str, Any]]) -> Dict:
    """Group shifts by (employee_name, employee_id, role)"""
    grouped = {}
    
    for shift in shifts:
        emp_name = shift.get("employee_name", "")
        emp_id = shift.get("employee_id", "")
        role = shift.get("role", "")
        
        key = (emp_name, emp_id, role)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(shift)
    
    return grouped


def _find_shift_at_hour(shifts: List[Dict[str, Any]], hour: int) -> Dict[str, Any] | None:
    """Find if any shift covers the given hour"""
    for shift in shifts:
        start_hour = _time_to_hour(shift.get("shift_start", "00:00:00"))
        end_hour = _time_to_hour(shift.get("shift_end", "00:00:00"))
        
        if start_hour <= hour < end_hour:
            return shift
    
    return None
