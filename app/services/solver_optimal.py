"""
Optimal Shift Solver using OR-Tools Constraint Programming
Finds the optimal shift assignment that minimizes total employees while meeting all demand.
"""
from typing import List, Dict, Any, Tuple, Optional
from collections import defaultdict
from datetime import datetime
from ortools.sat.python import cp_model  # type: ignore


def solve(employees: List[Dict[str, Any]], absences: List[Dict[str, Any]], 
          constraints: Dict[str, Any], demand: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Optimal shift solver using OR-Tools CP-SAT.
    Minimizes number of employees while meeting exact demand at every hour.
    """
    
    hard = (constraints or {}).get("hard", {})
    max_hours_per_day = float(hard.get("max_hours_per_day", 8))
    max_hours_per_week = float(hard.get("max_hours_per_week", 37.5))
    
    # Parse and group data
    blocked = _parse_absences(absences)
    emp_skills = _normalize_skills(employees)
    demand_by_day_role = _group_demand_by_day_role(demand)
    
    all_assignments = []
    
    # Solve each day+role independently
    for (day, role), role_demand in sorted(demand_by_day_role.items()):
        print(f"\n[OPTIMAL_SOLVER] Processing {day} - {role}")
        
        # Get eligible employees for this role
        eligible_emps = _get_eligible_employees(employees, emp_skills, role, day, blocked)
        
        if not eligible_emps:
            print(f"[OPTIMAL_SOLVER] No eligible employees for {role} on {day}")
            continue
        
        # Build shift opportunities (8h, 4h based on demand blocks)
        shifts = _build_shift_opportunities(role_demand)
        
        # Solve using OR-Tools
        assignments = _solve_with_ortools(
            eligible_emps, shifts, role_demand, day, role,
            max_hours_per_day, max_hours_per_week, blocked
        )
        
        all_assignments.extend(assignments)
    
    return {"assignments": all_assignments}


def _solve_with_ortools(eligible_emps: List[Dict], shifts: List[Dict], 
                        role_demand: List[Dict], day: str, role: str,
                        max_hours_day: float, max_hours_week: float, blocked: Dict) -> List[Dict]:
    """Use OR-Tools CP-SAT to find optimal assignment"""
    
    model = cp_model.CpModel()
    
    # Variables: x[(employee, shift)] = 1 if employee is assigned (and available), 0 otherwise
    x = {}
    emp_shift_vars = defaultdict(list)
    for emp in eligible_emps:
        eid = emp["id"]
        for i, shift in enumerate(shifts):
            if _is_available_for_shift(eid, day, shift["start_min"], shift["end_min"], blocked):
                var = model.NewBoolVar(f"assign_{eid}_shift_{i}")
                x[(eid, i)] = var
                emp_shift_vars[eid].append(var)
    
    # Build hourly demand requirements
    hours_demand = _build_hourly_demand(role_demand)
    
    # Constraint: Each hour must have required number of people
    for hour, required_qty in hours_demand.items():
        # Find which available shifts cover this hour
        covering_vars = []
        for emp in eligible_emps:
            eid = emp["id"]
            for i, shift in enumerate(shifts):
                if shift["start_h"] <= hour < shift["end_h"] and (eid, i) in x:
                    covering_vars.append(x[(eid, i)])
        
        if covering_vars:
            model.Add(sum(covering_vars) >= required_qty)
        else:
            # If nobody is available to cover this hour, enforce infeasibility and log
            print(f"[OPTIMAL_SOLVER] WARNING: No available staff can cover hour={hour} on day={day} role={role} (required={required_qty})")
            model.Add(0 >= required_qty)
    
    # Constraint: Each employee can work max one shift per day
    for emp in eligible_emps:
        eid = emp["id"]
        emp_shifts = emp_shift_vars.get(eid, [])
        model.Add(sum(emp_shifts) <= 1)
    
    # Objective: Minimize total employees used
    employees_used = []
    for emp in eligible_emps:
        eid = emp["id"]
        # Create a variable that is 1 if employee works any shift
        emp_works = model.NewBoolVar(f"emp_works_{eid}")
        emp_shifts = emp_shift_vars.get(eid, [])
        
        # emp_works = 1 if any shift assigned; else forced to 0
        if emp_shifts:
            model.AddMaxEquality(emp_works, emp_shifts)
        else:
            model.Add(emp_works == 0)
        employees_used.append(emp_works)
    
    # Minimize employees + small penalty for cost
    total_cost_cents = []
    cost_cents_by_eid = {emp["id"]: int(emp.get("hourly_cost", 0) * 100) for emp in eligible_emps}
    for (eid, i), var in x.items():
        hours = shifts[i]["duration_h"]
        total_cost_cents.append(var * cost_cents_by_eid.get(eid, 0) * int(hours))
    
    # Primary objective: minimize employees
    # Secondary: minimize cost (much smaller weight)
    model.Minimize(
        sum(employees_used) * 100000 +  # Primary: minimize employees
        sum(total_cost_cents)  # Secondary: minimize cost
    )
    
    # Solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.Solve(model)
    
    assignments = []
    
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        print(f"[OPTIMAL_SOLVER] Solution found (status={status})")
        print(f"[OPTIMAL_SOLVER] Employees used: {sum(solver.Value(e) for e in employees_used)}")
        
        # Extract assignments
        for emp in eligible_emps:
            eid = emp["id"]
            for i, shift in enumerate(shifts):
                if (eid, i) in x and solver.Value(x[(eid, i)]) == 1:
                    assignments.append({
                        "employee_id": eid,
                        "role": role,
                        "day": day,
                        "time": f"{_minutes_to_time(shift['start_min'])}-{_minutes_to_time(shift['end_min'])}",
                        "hours": shift["duration_h"],
                        "cost_per_hour": emp.get("hourly_cost", 0),
                    })
                    print(f"[OPTIMAL_SOLVER] Assigned {eid} to {shift['start_min']//60}:00-{shift['end_min']//60}:00")
    else:
        print(f"[OPTIMAL_SOLVER] No solution found (status={status})")
    
    return assignments


def _build_shift_opportunities(role_demand: List[Dict]) -> List[Dict]:
    """Build possible shift types from demand blocks"""
    blocks = []
    for d in role_demand:
        time_span = d.get("time", "")
        start_min, end_min = _parse_time_range(time_span)
        if start_min is not None and end_min is not None:
            blocks.append((start_min, end_min))
    
    blocks.sort()
    min_start = min(b[0] for b in blocks)
    max_end = max(b[1] for b in blocks)
    
    shifts = []
    
    # Generate 8-hour shifts
    for start in range(min_start, max_end - 420, 60):
        end = start + 480
        if end <= max_end:
            shifts.append({
                "start_min": start,
                "end_min": end,
                "start_h": start // 60,
                "end_h": end // 60,
                "duration_h": 8.0
            })
    
    # Generate 4-hour shifts matching demand blocks
    for block_start, block_end in blocks:
        shifts.append({
            "start_min": block_start,
            "end_min": block_end,
            "start_h": block_start // 60,
            "end_h": block_end // 60,
            "duration_h": (block_end - block_start) / 60.0
        })
    
    # Deduplicate
    seen = set()
    unique_shifts = []
    for s in shifts:
        key = (s["start_min"], s["end_min"])
        if key not in seen:
            seen.add(key)
            unique_shifts.append(s)
    
    return unique_shifts


def _build_hourly_demand(role_demand: List[Dict]) -> Dict[int, int]:
    """Build hour -> quantity map"""
    demand_by_hour = {}
    for d in role_demand:
        time_span = d.get("time", "")
        qty = int(d.get("qty", 0) or 0)
        start_min, end_min = _parse_time_range(time_span)
        if start_min is not None and end_min is not None:
            for minute in range(start_min, end_min, 60):
                hour = minute // 60
                demand_by_hour[hour] = qty
    return demand_by_hour


def _get_eligible_employees(employees: List[Dict], emp_skills: Dict, 
                           role: str, day: str, blocked: Dict) -> List[Dict]:
    """Get employees eligible for this role who are available"""
    role_norm = role.strip().lower()
    eligible = []
    
    for e in employees:
        eid = str(e.get("id"))
        skills = emp_skills.get(eid, [])
        
        # Check skill match
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
        
        # For now, skip detailed availability check (can add later)
        eligible.append({
            "id": eid,
            "name": e.get("name", eid),
            "hourly_cost": float(e.get("hourly_cost", 0) or 0)
        })
    
    return eligible


def _parse_absences(absences: List[Dict]) -> Dict:
    """Parse absences"""
    blocked = defaultdict(lambda: defaultdict(list))
    for a in absences or []:
        emp = str(a.get("employee_id", ""))
        day = _normalize_date(a.get("day", ""))
        time_span = str(a.get("time", ""))
        start_min, end_min = _parse_time_range(time_span)
        if emp and day and start_min is not None and end_min is not None:
            blocked[emp][day].append((start_min, end_min))
    return blocked


def _normalize_skills(employees: List[Dict]) -> Dict[str, List[str]]:
    """Normalize employee skills"""
    emp_skills = {}
    for e in employees:
        eid = str(e.get("id"))
        skills = [str(s).strip().lower() for s in (e.get("skills") or []) if str(s).strip()]
        emp_skills[eid] = skills
    return emp_skills


def _normalize_date(date_str: str) -> str:
    """Normalize date to YYYY-MM-DD"""
    s = str(date_str or "").strip()
    if " " in s:
        s = s.split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except:
            pass
    return s


def _group_demand_by_day_role(demand: List[Dict]) -> Dict[Tuple[str, str], List[Dict]]:
    """Group demand by (day, role)"""
    grouped = defaultdict(list)
    for d in demand:
        day = _normalize_date(d.get("day", ""))
        role = str(d.get("role", "")).strip()
        if day and role:
            grouped[(day, role)].append(d)
    return grouped


def _parse_time_range(time_str: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse time range to (start_min, end_min)"""
    try:
        if not time_str or "-" not in time_str:
            return None, None
        start, end = time_str.split("-", 1)
        
        start_parts = start.strip().split(":")
        start_h = int(start_parts[0])
        start_m = int(start_parts[1]) if len(start_parts) > 1 else 0
        
        end_parts = end.strip().split(":")
        end_h = int(end_parts[0])
        end_m = int(end_parts[1]) if len(end_parts) > 1 else 0
        
        return start_h * 60 + start_m, end_h * 60 + end_m
    except:
        return None, None


def _time_ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Return True if [a_start, a_end) overlaps [b_start, b_end) in minutes."""
    return max(a_start, b_start) < min(a_end, b_end)


def _is_available_for_shift(emp_id: str, day: str, start_min: int, end_min: int, blocked: Dict) -> bool:
    """Check availability: True if no absence interval overlaps the shift on that day."""
    day_norm = _normalize_date(day)
    if not emp_id or not day_norm:
        return True
    if emp_id not in blocked or day_norm not in blocked[emp_id]:
        return True
    for blocked_start, blocked_end in blocked[emp_id][day_norm]:
        if _time_ranges_overlap(start_min, end_min, blocked_start, blocked_end):
            return False
    return True


def _minutes_to_time(minutes: int) -> str:
    """Convert minutes to HH:MM:SS"""
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}:00"
