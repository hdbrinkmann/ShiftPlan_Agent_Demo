# Shift Planning Enhancements

## Overview

This document describes the implementation of two major enhancements to the ShiftPlan solver:

1. **Employee-Centric Shift Output**: Consolidates consecutive timeslots into continuous shifts
2. **Dynamic Block Splitting with 37.5h Weekly Limit**: Supports hourly granularity and employee replacement within forecast blocks

## Changes Made

### 1. Shift Formatter Module (`app/services/shift_formatter.py`)

**Purpose**: Post-processes solver assignments to create employee-centric shifts.

**Key Functions**:
- `consolidate_shifts()`: Merges consecutive timeslots for the same employee/day/role
- `format_shifts_for_display()`: Formats shifts for UI display

**Example**:
```
Input (raw assignments):
  - Employee 123, 2025-09-22, 08:00-09:00, Sales, 1h
  - Employee 123, 2025-09-22, 09:00-10:00, Sales, 1h
  - Employee 123, 2025-09-22, 10:00-11:00, Sales, 1h
  - Employee 123, 2025-09-22, 11:00-12:00, Sales, 1h

Output (consolidated shift):
  - Employee 123, 2025-09-22, 08:00-12:00, Sales, 4h
```

### 2. Demand Processor Module (`app/services/demand_processor.py`)

**Purpose**: Converts multi-hour forecast blocks into 1-hour granularity for flexible assignment.

**Key Functions**:
- `split_demand_to_hourly()`: Splits 4-hour blocks into 1-hour entries
- `convert_forecast_to_demand()`: Converts forecast CSV/JSON format to solver demand format
- `aggregate_demand_by_block()`: Aggregates hourly fulfillment back to original blocks for reporting

**Example**:
```
Input:
  - 2025-09-22, 08:00-12:00, Sales, qty=5 (need 5 people for 4 hours)

Output:
  - 2025-09-22, 08:00-09:00, Sales, qty=5
  - 2025-09-22, 09:00-10:00, Sales, qty=5
  - 2025-09-22, 10:00-11:00, Sales, qty=5
  - 2025-09-22, 11:00-12:00, Sales, qty=5
```

### 3. Solver Enhancements (`app/services/solver.py`)

**Changes**:
- Added `default_max_week` parameter (37.5h) for employees without explicit weekly limits
- Enhanced weekly limit checking to use employee-specific limit or default
- Now processes hourly demand entries for finer-grained assignment control

**Key Logic**:
```python
# Check weekly limit (employee-specific or default 37.5h)
emp_max_week = float(e.get("max_hours_week", 0) or 0)
max_week = emp_max_week if emp_max_week > 0 else default_max_week
if max_week > 0 and hours_week[eid] + hours > max_week + 1e-6:
    continue  # Skip this employee, they'd exceed their limit
```

### 4. Graph Integration (`app/graph/nodes.py`)

**Changes in `demand_node`**:
- Calls `demand_processor.split_demand_to_hourly()` to create hourly demand
- Stores both original and hourly demand in state

**Changes in `solve_node`**:
- Calls `shift_formatter.consolidate_shifts()` after solving
- Stores both raw assignments and consolidated shifts in solution

**Changes in `rules_node`**:
- Added `max_hours_per_week: 37.5` to constraints

### 5. UI Updates (`app/api/ui.py`)

**Changes**:
- Modified result table to prioritize displaying consolidated shifts
- New column headers: Day | Employee | Role | Shift (From-To) | Hours | Cost
- Falls back to raw assignment view if shifts not available

**Display Format**:
```
Day          Employee         Role    Shift (From-To)  Hours  Cost
2025-09-22   John Doe (123)   Sales   08:00-12:00     4.0    80.00
2025-09-22   Jane Smith (456) Sales   12:00-16:00     4.0    85.00
```

### 6. State Definition (`app/graph/state.py`)

**Changes**:
- Added `demand_original` field to track original demand before hourly splitting

## How It Works: Dynamic Block Splitting

### Scenario: Employee with 35h Used This Week

**Situation**:
- Employee "John" has worked 35h this week (limit: 37.5h)
- Remaining capacity: 2.5h
- A 4-hour block (08:00-12:00) needs coverage

**Solver Behavior** (with hourly demand):

1. Processes 08:00-09:00 (1h): Assigns to John ✓ (35h → 36h)
2. Processes 09:00-10:00 (1h): Assigns to John ✓ (36h → 37h)
3. Processes 10:00-11:00 (1h): Assigns to John ✓ (37h → 38h, but wait...)
   - Actually, this would exceed limit (37.5h), so John is SKIPPED
4. Processes 10:00-11:00 (1h): Finds alternative employee "Jane" ✓
5. Processes 11:00-12:00 (1h): Assigns to Jane ✓

**Result** (raw assignments):
- John: 08:00-09:00 (1h), 09:00-10:00 (1h) → Total: 2h
- Jane: 10:00-11:00 (1h), 11:00-12:00 (1h) → Total: 2h

**Result** (consolidated shifts):
- John: 08:00-10:00 (2h)
- Jane: 10:00-12:00 (2h)

## Testing

### Unit Testing

Test the shift formatter:
```python
from app.services.shift_formatter import consolidate_shifts

assignments = [
    {"employee_id": "123", "day": "2025-09-22", "time": "08:00:00-09:00:00", 
     "role": "Sales", "hours": 1.0, "cost_per_hour": 20.0},
    {"employee_id": "123", "day": "2025-09-22", "time": "09:00:00-10:00:00", 
     "role": "Sales", "hours": 1.0, "cost_per_hour": 20.0},
]

shifts = consolidate_shifts(assignments)
# Should produce one shift: 08:00:00-10:00:00, 2h, cost 40.0
```

Test the demand processor:
```python
from app.services.demand_processor import split_demand_to_hourly

demand = [
    {"day": "2025-09-22", "time": "08:00:00-12:00:00", "role": "Sales", "qty": 5}
]

hourly = split_demand_to_hourly(demand)
# Should produce 4 entries: 08:00-09:00, 09:00-10:00, 10:00-11:00, 11:00-12:00
# Each with qty=5
```

### Integration Testing

1. **Upload test data** with forecast (4-hour blocks)
2. **Run forecast** to generate demand
3. **Start solver run**
4. **Verify output**:
   - UI shows employee-centric shifts (not individual hours)
   - Employees respect 37.5h weekly limit
   - Consecutive hours are merged

### Manual Testing Scenario

**Setup**:
- Employee A: 35h worked this week, available all day
- Employee B: 0h worked this week, available all day
- Demand: 08:00-12:00 needs 1 Sales person (4 hours)

**Expected Result**:
- Employee A gets partial assignment (2.5h max)
- Employee B covers the remainder
- UI shows two separate shifts for the same time block

## Benefits

### 1. Better User Experience
- Employees see their full shift duration (e.g., "08:00-16:00") not fragmented hours
- Clearer schedule visualization
- Easier to understand shift patterns

### 2. Compliance with Labor Laws
- Enforces 37.5h weekly limit (configurable per employee or default)
- Prevents overtime violations
- Automatically handles split coverage when employees hit limits

### 3. Flexible Staffing
- Can replace employees mid-block when they hit weekly limits
- Maximizes utilization of available capacity
- Reduces understaffing due to rigid block assignments

### 4. Maintains Forecast Alignment
- Original forecast blocks (4h) are tracked
- Can report coverage per original block
- Audit and KPI calculations remain accurate

## Configuration

### Weekly Limit

Default is 37.5h, configured in `app/graph/nodes.py`:

```python
constraints = {
    "hard": {
        "max_hours_per_week": 37.5,  # Default for all employees
        ...
    }
}
```

Override per employee in employee data:
```python
{
    "id": "123",
    "name": "John Doe",
    "max_hours_week": 40.0,  # Custom limit for this employee
    ...
}
```

### Hourly Splitting

To disable hourly splitting (use original blocks):
Comment out in `app/graph/nodes.py`:
```python
# hourly_demand = demand_processor.split_demand_to_hourly(demand)
# Use demand directly instead
```

### Shift Consolidation

To disable shift consolidation (show raw hours):
Comment out in `app/graph/nodes.py`:
```python
# consolidated_shifts = shift_formatter.consolidate_shifts(raw_assignments, employees)
# solution["shifts"] = consolidated_shifts
```

## Future Enhancements

### 1. Break Management
- Add breaks to shifts (e.g., 30min lunch after 5h)
- Split shifts around breaks automatically

### 2. Shift Pattern Preferences
- Morning/afternoon/evening preferences
- Consecutive day limits
- Preferred shift lengths

### 3. Real-Time Optimization
- Dynamic reassignment when absences occur
- Swap recommendations to improve coverage

### 4. Advanced Splitting
- Split at 30-minute boundaries (not just hourly)
- Prefer splits at forecast block boundaries
- Minimize number of employee changes per day

## Troubleshooting

### Issue: Shifts Not Consolidating

**Symptom**: UI still shows hourly assignments instead of consolidated shifts

**Solution**: Check that `solution["shifts"]` exists in the result. Debug in `solve_node`:
```python
print(f"Raw assignments: {len(raw_assignments)}")
print(f"Consolidated shifts: {len(consolidated_shifts)}")
```

### Issue: Weekly Limit Not Enforced

**Symptom**: Employees assigned more than 37.5h

**Solution**: 
1. Check constraints are being passed to solver
2. Verify `max_hours_per_week` is set in constraints
3. Check employee-specific limits in data

### Issue: Demand Not Splitting

**Symptom**: Hourly demand same length as original demand

**Solution**: Check that `split_demand_to_hourly()` is being called and time ranges are valid format (HH:MM:SS-HH:MM:SS)

## Summary

These enhancements provide:
- ✅ Employee-centric shift visualization
- ✅ 37.5h weekly limit enforcement
- ✅ Dynamic block splitting for flexible assignment
- ✅ Hourly granularity while maintaining forecast alignment
- ✅ Automatic employee replacement within blocks
- ✅ Backward-compatible (falls back to raw assignments if needed)
