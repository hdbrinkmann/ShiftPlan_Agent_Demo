from typing import List, Dict, Any, Tuple, Set
from collections import defaultdict
from datetime import datetime, timedelta


def solve(employees: List[Dict[str, Any]], absences: List[Dict[str, Any]], 
          constraints: Dict[str, Any], demand: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Shift-First Optimizer:
    1. Analyzes daily demand patterns per role
    2. Generates optimal shift templates (8h preferred, then 7h, 6h, 4h)
    3. Selects shift patterns that minimize employees
    4. Assigns employees to complete shifts
    """
    
    hard = (constraints or {}).get("hard", {})
    max_hours_per_day = float(hard.get("max_hours_per_day", 8))
    max_hours_per_week = float(hard.get("max_hours_per_week", 37.5))
    
    # Track employee hours
    hours_week: Dict[str, float] = defaultdict(float)
    hours_day: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    
    # Parse absences
    blocked = _parse_absences(absences)
    
    # Normalize employee skills
    emp_skills = _normalize_skills(employees)
    
    # Group demand by day and role
    demand_by_day_role = _group_demand_by_day_role(demand)
    
    assignments = []
    
    # Process each day
    for (day, role), role_demand in sorted(demand_by_day_role.items()):
        print(f"\n[SOLVER] Processing {day} - {role}")
        
        # Step 1: Generate shift templates for this role/day
        shift_templates = _generate_shift_templates(role_demand, day, role)
        print(f"[SOLVER] Generated {len(shift_templates)} shift templates")
        
        # Step 2: Select optimal shift pattern
        selected_pattern = _select_optimal_pattern(shift_templates, role_demand)
        print(f"[SOLVER] Selected {len(selected_pattern)} shifts")
        
        # Step 3: Assign employees to each shift
        for shift_info in selected_pattern:
            assigned = _assign_employees_to_shift(
                shift_info, employees, emp_skills, blocked,
                hours_day, hours_week, max_hours_per_day, max_hours_per_week,
                day, role
            )
            assignments.extend(assigned)
    
    return {"assignments": assignments}


def _parse_absences(absences: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[Tuple[int, int]]]]:
    """Parse absences into blocked[employee_id][day] = [(start_min, end_min)]"""
    blocked = defaultdict(lambda: defaultdict(list))
    
    for a in absences or []:
        emp = str(a.get("employee_id", ""))
        day = _normalize_date(a.get("day", ""))
        time_span = str(a.get("time", ""))
        start_min, end_min = _parse_time_range(time_span)
        if emp and day and start_min is not None and end_min is not None:
            blocked[emp][day].append((start_min, end_min))
    
    return blocked


def _normalize_skills(employees: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Normalize employee skills to lowercase"""
    emp_skills = {}
    for e in employees:
        eid = str(e.get("id"))
        skills = [str(s).strip().lower() for s in (e.get("skills") or []) if str(s).strip()]
        emp_skills[eid] = skills
    return emp_skills


def _normalize_date(date_str: str) -> str:
    """Normalize date to YYYY-MM-DD format"""
    s = str(date_str or "").strip()
    if " " in s:
        s = s.split(" ")[0]
    
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return s


def _group_demand_by_day_role(demand: List[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """Group demand entries by (day, role)"""
    grouped = defaultdict(list)
    
    for d in demand:
        day = _normalize_date(d.get("day", ""))
        role = str(d.get("role", "")).strip()
        if day and role:
            grouped[(day, role)].append(d)
    
    return grouped


def _generate_shift_templates(role_demand: List[Dict[str, Any]], day: str, role: str) -> List[Dict[str, Any]]:
    """
    Generate shift template candidates for a role on a given day.
    Returns list of: {start_min, end_min, duration_h, quantity, score}
    """
    # Parse all time blocks and quantities
    time_blocks = []
    for d in role_demand:
        time_span = d.get("time", "")
        qty = int(d.get("qty", 0) or 0)
        start_min, end_min = _parse_time_range(time_span)
        if start_min is not None and end_min is not None and qty > 0:
            time_blocks.append((start_min, end_min, qty))
    
    if not time_blocks:
        return []
    
    # Sort by start time
    time_blocks.sort()
    
    # Find the overall time range needed
    min_start = min(b[0] for b in time_blocks)
    max_end = max(b[1] for b in time_blocks)
    
    templates = []
    
    # Generate 8-hour shift candidates (preferred)
    for start in range(min_start, max_end - 420, 60):  # 420 = 7 hours minimum
        end_8h = start + 480  # 8 hours
        if end_8h <= max_end:
            coverage = _calculate_coverage(start, end_8h, time_blocks)
            if coverage > 0:
                templates.append({
                    "start_min": start,
                    "end_min": end_8h,
                    "duration_h": 8.0,
                    "coverage": coverage,
                    "score": 100 + coverage * 10  # High score for 8h shifts
                })
    
    # Generate 7-hour shift candidates
    for start in range(min_start, max_end - 360, 60):
        end_7h = start + 420  # 7 hours
        if end_7h <= max_end:
            coverage = _calculate_coverage(start, end_7h, time_blocks)
            if coverage > 0:
                templates.append({
                    "start_min": start,
                    "end_min": end_7h,
                    "duration_h": 7.0,
                    "coverage": coverage,
                    "score": 85 + coverage * 10
                })
    
    # Generate 6-hour shift candidates
    for start in range(min_start, max_end - 300, 60):
        end_6h = start + 360  # 6 hours
        if end_6h <= max_end:
            coverage = _calculate_coverage(start, end_6h, time_blocks)
            if coverage > 0:
                templates.append({
                    "start_min": start,
                    "end_min": end_6h,
                    "duration_h": 6.0,
                    "coverage": coverage,
                    "score": 70 + coverage * 10
                })
    
    # Generate 4-hour shift candidates (for gaps)
    for start in range(min_start, max_end - 180, 60):
        end_4h = start + 240  # 4 hours
        if end_4h <= max_end:
            coverage = _calculate_coverage(start, end_4h, time_blocks)
            if coverage > 0:
                templates.append({
                    "start_min": start,
                    "end_min": end_4h,
                    "duration_h": 4.0,
                    "coverage": coverage,
                    "score": 40 + coverage * 10
                })
    
    return templates


def _calculate_coverage(start_min: int, end_min: int, time_blocks: List[Tuple[int, int, int]]) -> int:
    """Calculate how many person-hours this shift covers"""
    total_coverage = 0
    for block_start, block_end, qty in time_blocks:
        # Calculate overlap
        overlap_start = max(start_min, block_start)
        overlap_end = min(end_min, block_end)
        if overlap_start < overlap_end:
            overlap_hours = (overlap_end - overlap_start) / 60.0
            total_coverage += overlap_hours
    return int(total_coverage)


def _select_optimal_pattern(templates: List[Dict[str, Any]], role_demand: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Select optimal combination of shifts to cover all demand.
    Uses a smarter approach that considers the actual demand per time block.
    """
    # Build demand coverage map: hour -> people needed
    # Parse demand blocks in order and map to hours
    demand_blocks = []
    for d in role_demand:
        time_span = d.get("time", "")
        qty = int(d.get("qty", 0) or 0)
        start_min, end_min = _parse_time_range(time_span)
        if start_min is not None and end_min is not None and qty > 0:
            demand_blocks.append((start_min, end_min, qty))
    
    demand_blocks.sort()  # Sort by start time
    
    # Map to hourly demand
    demand_by_hour = {}
    for start_min, end_min, qty in demand_blocks:
        for minute in range(start_min, end_min, 60):
            hour = minute // 60
            # For each hour, use the quantity from the block that contains it
            # If already set (shouldn't happen with non-overlapping blocks), keep existing
            if hour not in demand_by_hour:
                demand_by_hour[hour] = qty
    
    print(f"[SOLVER] Demand blocks: {demand_blocks}")
    print(f"[SOLVER] Demand by hour: {demand_by_hour}")
    
    selected = []
    remaining = dict(demand_by_hour)
    
    # Sort templates: prefer longer shifts first, then by start time
    sorted_templates = sorted(templates, key=lambda t: (-t["duration_h"], t["start_min"]))
    
    iteration = 0
    max_iterations = 100
    
    while any(v > 0 for v in remaining.values()) and iteration < max_iterations:
        iteration += 1
        best_template = None
        best_score = -1
        best_qty = 0
        
        # Find the best template for remaining demand
        # Prefer templates that cover high-demand hours
        for template in sorted_templates:
            start_h = template["start_min"] // 60
            end_h = template["end_min"] // 60
            
            # Calculate how much this template helps
            min_coverage = float('inf')
            max_coverage = 0
            total_coverage = 0
            covers_any = False
            hours_covered = 0
            
            for h in range(start_h, end_h):
                if remaining.get(h, 0) > 0:
                    covers_any = True
                    hours_covered += 1
                    min_coverage = min(min_coverage, remaining.get(h, 0))
                    max_coverage = max(max_coverage, remaining.get(h, 0))
                    total_coverage += remaining.get(h, 0)
            
            if not covers_any:
                continue
            
            # Use minimum coverage to avoid over-staffing low-demand periods
            qty = int(min_coverage) if min_coverage != float('inf') else 0
            if qty <= 0:
                continue
            
            # Calculate efficiency: how well does this shift match the demand pattern
            # Prefer shifts that have consistent demand across their duration
            avg_demand = total_coverage / hours_covered if hours_covered > 0 else 0
            demand_variance = max_coverage - min_coverage
            
            # Score: prefer longer shifts, but penalize high variance (mismatch)
            # Higher score = better match
            base_score = template["duration_h"] * 100
            efficiency_bonus = hours_covered * 10  # Bonus for covering more hours
            variance_penalty = demand_variance * 5  # Penalty for uneven demand
            score = base_score + efficiency_bonus - variance_penalty
            
            if score > best_score:
                best_template = template
                best_score = score
                best_qty = qty
        
        if best_template is None or best_qty == 0:
            # No suitable template found, try with qty=1 for any uncovered hour
            for template in sorted_templates:
                start_h = template["start_min"] // 60
                end_h = template["end_min"] // 60
                
                for h in range(start_h, end_h):
                    if remaining.get(h, 0) > 0:
                        best_template = template
                        best_qty = 1
                        break
                if best_template:
                    break
        
        if best_template is None or best_qty == 0:
            print(f"[SOLVER] Warning: Could not find template to cover remaining demand: {remaining}")
            break
        
        # Add this shift
        print(f"[SOLVER] Selecting shift {_minutes_to_time_str(best_template['start_min'])}-{_minutes_to_time_str(best_template['end_min'])} x{best_qty}")
        selected.append({
            "start_min": best_template["start_min"],
            "end_min": best_template["end_min"],
            "duration_h": best_template["duration_h"],
            "quantity": best_qty
        })
        
        # Update remaining demand
        start_h = best_template["start_min"] // 60
        end_h = best_template["end_min"] // 60
        for h in range(start_h, end_h):
            remaining[h] = max(0, remaining.get(h, 0) - best_qty)
        
        print(f"[SOLVER] Remaining demand after assignment: {remaining}")
    
    # Final check: if any demand remains, warn
    if any(v > 0 for v in remaining.values()):
        print(f"[SOLVER] WARNING: Unmet demand remains: {remaining}")
    
    return selected


def _assign_employees_to_shift(
    shift_info: Dict[str, Any], employees: List[Dict[str, Any]], 
    emp_skills: Dict[str, List[str]], blocked: Dict,
    hours_day: Dict, hours_week: Dict, 
    max_hours_per_day: float, max_hours_per_week: float,
    day: str, role: str
) -> List[Dict[str, Any]]:
    """Assign employees to a specific shift"""
    
    start_min = shift_info["start_min"]
    end_min = shift_info["end_min"]
    duration = shift_info["duration_h"]
    quantity = shift_info["quantity"]
    
    start_time = _minutes_to_time_str(start_min)
    end_time = _minutes_to_time_str(end_min)
    time_span = f"{start_time}-{end_time}"
    
    assignments = []
    
    # Find eligible employees
    role_norm = role.strip().lower()
    candidates = []
    for e in employees:
        eid = str(e.get("id"))
        skills = emp_skills.get(eid, [])
        
        # Check if employee has required skill
        has_skill = False
        if role_norm in skills:
            has_skill = True
        elif "manager" in role_norm and any("manager" in s for s in skills):
            has_skill = True
        elif "sales" in role_norm and ("sales" in skills or "verkauf" in skills):
            has_skill = True
        elif "cashier" in role_norm or "checkout" in role_norm:
            if "cashier" in skills or "kasse" in skills or "checkout" in skills:
                has_skill = True
        
        if not has_skill:
            continue
        
        # Check availability
        if not _is_available(eid, day, start_min, end_min, blocked):
            continue
        
        # Check daily limit
        if hours_day[eid][day] + duration > max_hours_per_day + 0.01:
            continue
        
        # Check weekly limit
        emp_max_week = float(e.get("max_hours_week", 0) or 0)
        week_limit = emp_max_week if emp_max_week > 0 else max_hours_per_week
        if hours_week[eid] + duration > week_limit + 0.01:
            continue
        
        cost = float(e.get("hourly_cost", 0) or 0)
        candidates.append((cost, hours_week[eid], eid, e))
    
    # Sort by cost, then by current weekly hours (load balancing)
    candidates.sort()
    
    # Assign to best candidates
    for i in range(min(quantity, len(candidates))):
        _, _, eid, e = candidates[i]
        cost_per_hour = float(e.get("hourly_cost", 0) or 0)
        
        assignments.append({
            "employee_id": eid,
            "role": role,
            "day": day,
            "time": time_span,
            "hours": duration,
            "cost_per_hour": cost_per_hour,
        })
        
        # Update tracking
        hours_day[eid][day] += duration
        hours_week[eid] += duration
        
        print(f"[SOLVER] Assigned {eid} to {time_span} ({duration}h)")
    
    return assignments


def _is_available(emp_id: str, day: str, start_min: int, end_min: int, blocked: Dict) -> bool:
    """Check if employee is available for this time range"""
    day_norm = _normalize_date(day)
    if emp_id not in blocked:
        return True
    if day_norm not in blocked[emp_id]:
        return True
    
    # Check for overlaps with blocked times
    for blocked_start, blocked_end in blocked[emp_id][day_norm]:
        if _time_ranges_overlap(start_min, end_min, blocked_start, blocked_end):
            return False
    
    return True


def _time_ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Check if two time ranges overlap"""
    return max(a_start, b_start) < min(a_end, b_end)


def _parse_time_range(time_str: str) -> Tuple[int, int]:
    """Parse 'HH:MM-HH:MM' to (start_minutes, end_minutes)"""
    try:
        if not time_str or "-" not in time_str:
            return None, None
        start, end = time_str.split("-", 1)
        
        start_parts = start.strip().split(":")
        start_h = int(start_parts[0])
        start_m = int(start_parts[1]) if len(start_parts) > 1 else 0
        start_min = start_h * 60 + start_m
        
        end_parts = end.strip().split(":")
        end_h = int(end_parts[0])
        end_m = int(end_parts[1]) if len(end_parts) > 1 else 0
        end_min = end_h * 60 + end_m
        
        return start_min, end_min
    except Exception:
        return None, None


def _minutes_to_time_str(minutes: int) -> str:
    """Convert minutes since midnight to HH:MM:SS"""
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}:00"
