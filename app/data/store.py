from __future__ import annotations
from typing import List, Dict, Any, Tuple
from copy import deepcopy
import time

_STORE: Dict[str, Any] = {
    "employees": [],
    "absences": [],
    "demand": [],
    "excel_path": None,
    "updated_at": None,
}

def set_data(employees: List[Dict[str, Any]] | None = None,
             absences: List[Dict[str, Any]] | None = None,
             demand: List[Dict[str, Any]] | None = None) -> None:
    if employees is not None:
        _STORE["employees"] = deepcopy(employees)
    if absences is not None:
        _STORE["absences"] = deepcopy(absences)
    if demand is not None:
        _STORE["demand"] = deepcopy(demand)
    _STORE["updated_at"] = time.time()

def get_data() -> Tuple[list[dict], list[dict], list[dict]]:
    return (
        deepcopy(_STORE.get("employees", [])),
        deepcopy(_STORE.get("absences", [])),
        deepcopy(_STORE.get("demand", [])),
    )

def has_any() -> bool:
    return bool(_STORE.get("employees") or _STORE.get("demand"))

def set_excel_path(path: str | None) -> None:
    _STORE["excel_path"] = str(path) if path else None
    _STORE["updated_at"] = time.time()

def get_excel_path() -> str | None:
    p = _STORE.get("excel_path")
    return str(p) if p else None
