from typing import List, Dict, Any
import pandas as pd
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

def parse_sources() -> tuple[list[dict], list[dict], list[dict]]:
    """Parse Excel file for employees, absences, and demand data.
    
    Returns:
        tuple: (employees, absences, demand) - Falls back to stub data if file/sheets are missing.
    """
    excel_path = Path("./datafiles/Simple_Shift_Plan_Request.xlsx")
    
    # Stub data as fallback
    stub_employees: List[Dict[str, Any]] = [
        {"id": "E1", "name": "Alice", "hourly_cost": 18.0, "skills": ["cashier", "sales"], "max_hours_week": 30},
        {"id": "E2", "name": "Bob", "hourly_cost": 20.0, "skills": ["cashier"], "max_hours_week": 20},
        {"id": "E3", "name": "Cora", "hourly_cost": 22.0, "skills": ["sales"], "max_hours_week": 35},
    ]
    stub_absences: List[Dict[str, Any]] = []
    stub_demand: List[Dict[str, Any]] = []
    
    # Try to read Excel file
    if not excel_path.exists():
        logger.info(f"Excel file not found at {excel_path}, using stub data")
        return stub_employees, stub_absences, stub_demand
    
    try:
        # Read all sheets
        excel_file = pd.ExcelFile(excel_path)
        
        # Parse Employees sheet (required)
        employees = _parse_employees_sheet(excel_file)
        if not employees:
            logger.warning("No employees found in Excel, using stub data")
            return stub_employees, stub_absences, stub_demand
            
        # Parse Absences sheet (optional)
        absences = _parse_absences_sheet(excel_file)
        
        # Parse Demand sheet
        demand = _parse_demand_sheet(excel_file)
        
        logger.info(f"Loaded {len(employees)} employees, {len(absences)} absences, {len(demand)} demand rows (from Excel).")
        return employees, absences, demand
        
    except Exception as e:
        logger.error(f"Error reading Excel file: {e}, using stub data")
        return stub_employees, stub_absences, stub_demand


def _parse_employees_sheet(excel_file: pd.ExcelFile) -> List[Dict[str, Any]]:
    """Parse Employees sheet with flexible column matching."""
    if "Employees" not in excel_file.sheet_names:
        logger.warning("Employees sheet not found")
        return []
    
    df = pd.read_excel(excel_file, sheet_name="Employees")
    
    # Normalize column names to lowercase for flexible matching
    df.columns = df.columns.str.strip().str.lower()
    
    # Check for required columns
    if "name" not in df.columns:
        logger.error("Required 'name' column not found in Employees sheet")
        return []
    
    # Find hourly cost column (flexible matching)
    cost_col = None
    for col in ["hourly_cost", "hourly_rate", "cost", "rate"]:
        if col in df.columns:
            cost_col = col
            break
    
    if not cost_col:
        logger.error("Required hourly_cost/hourly_rate column not found in Employees sheet")
        return []
    
    employees = []
    for idx, row in df.iterrows():
        # Generate ID from name if not provided
        emp_id = row.get("id", "")
        if pd.isna(emp_id) or str(emp_id).strip() == "":
            emp_id = f"E{idx + 1}"
        
        name = row["name"]
        if pd.isna(name) or str(name).strip() == "":
            continue  # Skip rows without name
            
        hourly_cost = row[cost_col]
        if pd.isna(hourly_cost):
            logger.warning(f"Skipping employee {name} - no hourly cost")
            continue
        
        # Parse skills (comma or semicolon separated)
        skills_str = row.get("skills", "")
        skills = []
        if not pd.isna(skills_str) and str(skills_str).strip():
            skills = re.split(r'[,;]+', str(skills_str).strip())
            skills = [s.strip() for s in skills if s.strip()]
        
        # Parse max_hours_week (optional)
        max_hours = row.get("max_hours_week", None)
        if pd.isna(max_hours):
            max_hours = None
        
        employee = {
            "id": str(emp_id),
            "name": str(name).strip(),
            "hourly_cost": float(hourly_cost),
            "skills": skills,
        }
        
        if max_hours is not None:
            employee["max_hours_week"] = float(max_hours)
        
        employees.append(employee)
    
    return employees


def _parse_absences_sheet(excel_file: pd.ExcelFile) -> List[Dict[str, Any]]:
    """Parse Absences sheet (optional)."""
    if "Absences" not in excel_file.sheet_names:
        return []
    
    df = pd.read_excel(excel_file, sheet_name="Absences")
    
    # Normalize column names
    df.columns = df.columns.str.strip().str.lower()
    
    # Check for required columns
    required_cols = ["date", "start_time", "end_time"]
    for col in required_cols:
        if col not in df.columns:
            logger.warning(f"Missing required column '{col}' in Absences sheet")
            return []
    
    # Need either employee_id or name
    emp_identifier = None
    if "employee_id" in df.columns:
        emp_identifier = "employee_id"
    elif "name" in df.columns:
        emp_identifier = "name"
    else:
        logger.warning("Absences sheet missing employee identifier (employee_id or name)")
        return []
    
    absences = []
    for _, row in df.iterrows():
        emp_value = row.get(emp_identifier, "")
        if pd.isna(emp_value) or str(emp_value).strip() == "":
            continue
        
        date = row.get("date", "")
        start_time = row.get("start_time", "")
        end_time = row.get("end_time", "")
        abs_type = row.get("type", "absence")
        
        if pd.isna(date) or pd.isna(start_time) or pd.isna(end_time):
            continue
        
        # Convert date to string if it's a datetime
        if isinstance(date, pd.Timestamp):
            date = date.strftime("%Y-%m-%d")
        
        # Format time as HH:MM-HH:MM
        time_str = f"{str(start_time).strip()}-{str(end_time).strip()}"
        
        absence = {
            emp_identifier: str(emp_value).strip(),
            "date": str(date).strip(),
            "time": time_str,
        }
        
        if not pd.isna(abs_type):
            absence["type"] = str(abs_type).strip()
        
        absences.append(absence)
    
    return absences


def _parse_demand_sheet(excel_file: pd.ExcelFile) -> List[Dict[str, Any]]:
    """Parse Demand sheet."""
    if "Demand" not in excel_file.sheet_names:
        logger.warning("Demand sheet not found")
        return []
    
    df = pd.read_excel(excel_file, sheet_name="Demand")
    
    # Normalize column names
    df.columns = df.columns.str.strip().str.lower()
    
    # Check for required columns
    required_cols = ["day", "start_time", "end_time", "role"]
    for col in required_cols:
        if col not in df.columns:
            logger.warning(f"Missing required column '{col}' in Demand sheet")
            return []
    
    # Find qty column (flexible matching)
    qty_col = None
    for col in ["qty", "required", "quantity"]:
        if col in df.columns:
            qty_col = col
            break
    
    if not qty_col:
        logger.warning("Required qty/required column not found in Demand sheet")
        return []
    
    demand = []
    for _, row in df.iterrows():
        day = row.get("day", "")
        start_time = row.get("start_time", "")
        end_time = row.get("end_time", "")
        role = row.get("role", "")
        qty = row.get(qty_col, 0)
        
        if pd.isna(day) or pd.isna(start_time) or pd.isna(end_time) or pd.isna(role) or pd.isna(qty):
            continue
        
        # Convert date to weekday if it's a datetime
        day_str = str(day).strip()
        if isinstance(day, pd.Timestamp):
            # Extract weekday name
            day_str = day.strftime("%a")  # Mon, Tue, Wed, etc.
        
        # Format time as HH:MM-HH:MM
        time_str = f"{str(start_time).strip()}-{str(end_time).strip()}"
        
        demand_entry = {
            "day": day_str,
            "time": time_str,
            "role": str(role).strip(),
            "qty": int(qty),
        }
        
        demand.append(demand_entry)
    
    return demand