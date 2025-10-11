from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from app.graph.build import build_graph
from app.api.ui import router as ui_router
from app.telemetry import publish_event
from app.data.store import set_data, set_excel_path
import time
import pandas as pd
from fastapi.responses import HTMLResponse
from app.data.store import get_data
from pathlib import Path
from app.services.chat_intents import parse_message_to_intents, apply_intents
from app.services.llm import ScalewayLLM
from app.services.forecast import (
    run_forecast as run_forecast_service,
    run_forecast_to_status,
    resolve_status_path,
)
import threading
import json

app = FastAPI(title="Shift Planning Sample (LangGraph)")
app.include_router(ui_router, prefix="/ui", tags=["ui"])

@app.get("/")
def root():
    return {"ok": True, "message": "Shift Planning Sample (LangGraph). POST /run to execute."}

class RunRequest(BaseModel):
    auto_approve: bool = True
    budget: float | None = None
    run_id: str | None = None

class ChatRequest(BaseModel):
    message: str
    run_id: str | None = None
    auto_approve: bool = True
    budget: float | None = None

@app.post("/run")
def run(req: RunRequest):
    graph = build_graph()
    run_id = req.run_id or str(int(time.time()*1000))
    # Initial input/state seed
    initial_state = {
        "status": "INIT",
        "needs_approval": False,
        "awaiting_approval": False,
        "kpis": {"budget": req.budget} if req.budget is not None else {},
        "logs": [],
        "run_id": run_id,
    }
    publish_event(run_id, {"message": "Run started", "active_node": "ingest"})
    final_state = graph.invoke(initial_state, config={"auto_approve": req.auto_approve})
    publish_event(run_id, {"message": "Run finished", "active_node": None})
    return {"run_id": run_id, **final_state}

@app.get("/llm_status")
def llm_status():
    llm = ScalewayLLM()
    status = {
        "enabled": bool(getattr(llm, "enabled", False)),
        "base_url": getattr(llm, "base_url", None),
        "model": getattr(llm, "model", None),
    }
    # Hinweis: Keine Secrets zurückgeben!
    return status

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Please upload an Excel file (.xlsx or .xls)")
    # Robust parsing: accept case-insensitive sheet names, handle long and wide demand formats
    try:
        content = await file.read()
        import io
        buf = io.BytesIO(content)
        xls = pd.read_excel(buf, sheet_name=None)

        # Persist uploaded Excel and remember its path for forecasting
        try:
            base_dir = Path(__file__).resolve().parents[1] / "testdata"
            base_dir.mkdir(parents=True, exist_ok=True)
            saved_path = base_dir / "uploaded.xlsx"
            saved_path.write_bytes(content)
            set_excel_path(str(saved_path.resolve()))
        except Exception as e:
            # Do not fail upload on save issues; just continue without persisting
            print(f"[UPLOAD] Warning: failed to persist uploaded Excel: {e}")

        # Normalize sheet-name dict to lowercase
        sheets_lower = {(name or "").strip().lower(): df for name, df in xls.items()}

        def pick_sheet(possible_names):
            for key in possible_names:
                k = str(key).strip().lower()
                if k in sheets_lower:
                    return sheets_lower[k]
            return None

        # Known sheets
        emp_df = pick_sheet(["employees", "employee", "staff", "mitarbeiter"]) 
        abs_df = pick_sheet(["absences", "absence", "abwesenheiten", "urlaub"]) 
        dem_df = pick_sheet(["demand", "requirements", "bedarf", "needs", "shifts", "opening hours", "opening_hours", "openinghours"]) 

        # Helpers
        def norm_df(df):
            return df.rename(columns=lambda c: str(c).strip().lower()).fillna("")

        def build_time(row):
            t = row.get("time") or row.get("zeit")
            if not t:
                f = str(row.get("from") or row.get("start") or "").strip()
                to = str(row.get("to") or row.get("end") or "").strip()
                if f or to:
                    t = f"{f}-{to}".strip("-")
            return t or ""

        employees = []
        if emp_df is not None and not emp_df.empty:
            df = norm_df(emp_df)
            import re

            def parse_rate(val) -> float:
                try:
                    if val is None or val == "":
                        return 0.0
                    if isinstance(val, (int, float)):
                        return float(val)
                    s = str(val).lower().strip()
                    # Währung/Suffixe entfernen
                    s = s.replace("€", "").replace("eur", "").replace("per hour", "").replace("/h", "").strip()
                    # Nur Ziffern und Trennzeichen behalten
                    cleaned = re.sub(r"[^0-9.,-]", "", s)
                    # Wenn sowohl Punkt als auch Komma vorkommen: letztes Vorkommen entscheidet Dezimaltrennzeichen
                    if "." in cleaned and "," in cleaned:
                        last_dot = cleaned.rfind(".")
                        last_comma = cleaned.rfind(",")
                        if last_comma > last_dot:
                            # deutsches Format: 1.234,56 -> 1234.56
                            cleaned = cleaned.replace(".", "")
                            cleaned = cleaned.replace(",", ".")
                        else:
                            # US-Format: 1,234.56 -> 1234.56
                            cleaned = cleaned.replace(",", "")
                    else:
                        # Nur ein Trennzeichen vorhanden: falls Komma, als Dezimalpunkt interpretieren
                        cleaned = cleaned.replace(",", ".")
                    # Fallback: erste Fließkommazahl extrahieren
                    m = re.findall(r"[-+]?[0-9]*\.?[0-9]+", cleaned)
                    return float(m[0]) if m else 0.0
                except Exception:
                    return 0.0

            # Kandidatenspalten für Stundensatz erkennen
            def pick_rate_from_row(r: dict) -> float:
                # bevorzugte Spalten (alle bereits lowercased)
                preferred = [
                    "hourly_cost", "hourly rate", "hourly_rate", "wage",
                    "cost per hour in eur", "cost per hour", "cost/hour", "€/h", "eur/h",
                    "cost", "rate",
                ]
                for key in preferred:
                    if key in r and str(r.get(key, "")).strip() != "":
                        return parse_rate(r.get(key))
                # heuristisch: Spaltennamen mit cost+hour
                for c in r.keys():
                    name = str(c).strip().lower()
                    if ("cost" in name or "rate" in name) and ("hour" in name or "/h" in name or "€/h" in name or "eur/h" in name):
                        v = r.get(c)
                        if v not in (None, ""):
                            return parse_rate(v)
                # letzter Versuch: eine Einzelzahl in einer cost-ähnlichen Spalte
                for c in r.keys():
                    name = str(c).strip().lower()
                    if "cost" in name or "rate" in name or "eur" in name:
                        v = r.get(c)
                        if v not in (None, ""):
                            return parse_rate(v)
                return 0.0

            for _, r in df.iterrows():
                rid = r.get("id") or r.get("employee_id") or r.get("emp_id") or r.get("nummer") or ""
                name = r.get("name") or r.get("employee") or r.get("full_name") or r.get("mitarbeiter") or ""
                # Stundensatz aus möglichen Spalten robust extrahieren
                rate = pick_rate_from_row(r.to_dict())
                skills_raw = r.get("skills") or r.get("skillset") or r.get("kompetenzen") or ""
                max_week = r.get("max_hours_week") or r.get("max_week_hours") or r.get("max_weekly_hours") or 0
                if isinstance(skills_raw, str):
                    sep = ";" if ";" in skills_raw else ","
                    skills = [s.strip() for s in skills_raw.split(sep) if s.strip()]
                elif isinstance(skills_raw, (list, tuple)):
                    skills = [str(s).strip() for s in skills_raw if str(s).strip()]
                else:
                    skills = []
                # Falls keine Skills-Spalte gepflegt ist: Rolle/Position als Skill interpretieren
                if not skills:
                    role_val = (
                        r.get("role") or r.get("position") or r.get("job") or r.get("funktion") or r.get("rolle") or r.get("title") or ""
                    )
                    if str(role_val).strip():
                        skills = [str(role_val).strip()]
                employees.append({
                    "id": str(rid),
                    "name": str(name),
                    "hourly_cost": float(rate or 0.0),
                    "skills": skills,
                    "max_hours_week": float(max_week or 0),
                })

        absences = []
        if abs_df is not None and not abs_df.empty:
            df = norm_df(abs_df)
            for _, r in df.iterrows():
                emp = r.get("employee_id") or r.get("id") or r.get("emp_id") or ""
                day = r.get("day") or r.get("datum") or r.get("date") or ""
                time = build_time(r)
                typ = r.get("type") or r.get("reason") or r.get("art") or ""
                absences.append({
                    "employee_id": str(emp),
                    "day": str(day),
                    "time": str(time),
                    "type": str(typ),
                })

        demand = []

        def parse_demand_long(df):
            nonlocal demand
            df2 = norm_df(df)
            for _, r in df2.iterrows():
                day = r.get("day") or r.get("datum") or r.get("date") or ""
                time = build_time(r)
                role = r.get("role") or r.get("position") or r.get("skill") or r.get("funktion") or r.get("rolle") or ""
                qty = r.get("qty") or r.get("quantity") or r.get("count") or r.get("needed") or r.get("anzahl") or r.get("soll") or 0
                try:
                    qty = int(qty or 0)
                except Exception:
                    qty = 0
                if str(role).strip() == "" and qty == 0:
                    continue
                demand.append({"day": str(day), "time": str(time), "role": str(role), "qty": qty})

        def parse_demand_wide(df):
            nonlocal demand
            df2 = norm_df(df)
            meta_cols = {"date", "day", "datum", "week", "from", "to", "open hours", "openhours", "open_hours", "zeit", "time"}
            for _, r in df2.iterrows():
                day = r.get("day") or r.get("datum") or r.get("date") or ""
                time = build_time(r)
                for col in df2.columns:
                    c = str(col).strip().lower()
                    if c in meta_cols:
                        continue
                    val = r.get(c)
                    try:
                        qty = int(val) if val not in (None, "") else 0
                    except Exception:
                        continue
                    if qty > 0:
                        demand.append({"day": str(day), "time": str(time), "role": str(col).strip(), "qty": qty})

        if dem_df is not None and not dem_df.empty:
            # Prefer long format if columns present, else wide
            cols = {str(c).strip().lower() for c in dem_df.columns}
            if {"role"} & cols or {"qty", "quantity", "count", "needed", "anzahl", "soll"} & cols:
                parse_demand_long(dem_df)
            else:
                parse_demand_wide(dem_df)

        # Heuristic fallback: scan all sheets for a wide-format demand like "Opening Hours"
        if not demand:
            for name, df in sheets_lower.items():
                if df is None or df.empty:
                    continue
                cols = {str(c).strip().lower() for c in df.columns}
                if ("from" in cols or "start" in cols) and ("to" in cols or "end" in cols):
                    parse_demand_wide(df)
                    if demand:
                        break

        set_data(employees=employees, absences=absences, demand=demand)
        return {"ok": True, "counts": {"employees": len(employees), "absences": len(absences), "demand": len(demand)}}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse Excel: {e}")

@app.post("/result", response_class=HTMLResponse)
def result(req: RunRequest):
    graph = build_graph()
    run_id = req.run_id or str(int(time.time()*1000))
    initial_state = {
        "status": "INIT",
        "needs_approval": False,
        "awaiting_approval": False,
        "kpis": {"budget": req.budget} if req.budget is not None else {},
        "logs": [],
        "steps": [],
        "run_id": run_id,
    }
    final_state = graph.invoke(initial_state, config={"auto_approve": req.auto_approve})
    # Build simple table for assignments
    rows = []
    for a in final_state.get("solution", {}).get("assignments", []):
        rows.append(f"<tr><td>{a.get('day')}</td><td>{a.get('time')}</td><td>{a.get('role')}</td><td>{a.get('employee_id')}</td><td>{a.get('hours')}</td><td>{a.get('cost_per_hour')}</td></tr>")
    table = """
    <style>table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}th{background:#f5f5f7}</style>
    <h2>Assignments</h2>
    <table>
      <tr><th>Day</th><th>Time</th><th>Role</th><th>Employee</th><th>Hours</th><th>Cost/h</th></tr>
      %s
    </table>
    """ % ("\n".join(rows) or "<tr><td colspan=6>No assignments</td></tr>")
    # Steps list
    steps = final_state.get("steps", [])
    steps_html = "<ol>" + "".join(f"<li>{s}</li>" for s in steps) + "</ol>"
    meta = final_state.get("kpis", {})
    kpi_html = f"<p><b>Cost:</b> {meta.get('cost')} | <b>Coverage:</b> {meta.get('coverage')}</p>"
    return HTMLResponse(content=f"<h1>ShiftPlan Result</h1>{kpi_html}{table}<h2>Executed Steps</h2>{steps_html}")

@app.get("/inspect")
def inspect():
    employees, absences, demand = get_data()
    return {
        "ok": True,
        "counts": {
            "employees": len(employees),
            "absences": len(absences),
            "demand": len(demand),
        },
        "samples": {
            "employee": employees[0] if employees else None,
            "absence": absences[0] if absences else None,
            "demand": demand[0] if demand else None,
        }
    }

@app.post("/forecast/run")
def forecast_run():
    try:
        # Launch forecast in background and return immediately
        t = threading.Thread(target=run_forecast_to_status, daemon=True)
        t.start()
        return {"ok": True, "started": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Forecast failed to start: {e}")

@app.get("/forecast/status")
def forecast_status():
    try:
        p = resolve_status_path()
        if not p.exists():
            return {"ok": True, "status": "idle"}
        data = json.loads(p.read_text() or "{}")
        return {"ok": True, **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Status read failed: {e}")

@app.post("/chat")
def chat(req: ChatRequest):
    try:
        # 1) Vorhandenen State laden
        employees, absences, demand = get_data()
        
        # Check if data is available
        if not employees and not demand:
            raise HTTPException(
                status_code=400, 
                detail="Keine Daten geladen. Bitte zuerst eine Excel-Datei hochladen."
            )
        
        print(f"Chat request: '{req.message}' with {len(employees)} employees")
        
        state = {
            "employees": employees,
            "absences": absences,
            "demand": demand,
        }
        
        # 2) Nachricht in Intents parsen
        intents, notes = parse_message_to_intents(req.message, employees=employees)
        print(f"Parsed intents: {intents}")
        print(f"Notes: {notes}")
        
        # Wenn keine Intents erkannt wurden
        if not intents:
            return {
                "ok": False,
                "error": "Konnte keine Aktion aus der Nachricht erkennen.",
                "notes": notes,
                "intents": [],
                "apply_logs": [],
            }
        
        # 3) Intents anwenden
        state2, logs = apply_intents(intents, state)
        print(f"Apply logs: {logs}")
        print(f"[CHAT] Absences after apply_intents: {len(state2.get('absences', []))} total")
        # Show last 3 absences for debug
        for a in state2.get('absences', [])[-3:]:
            print(f"[CHAT] Absence: emp={a.get('employee_id')}, day={a.get('day')}, time={a.get('time')}")
        
        # 4) Store aktualisieren
        set_data(employees=state2.get("employees"), absences=state2.get("absences"), demand=state2.get("demand"))
        
        # Verify store was updated
        _, stored_abs, _ = get_data()
        print(f"[CHAT] Store now has {len(stored_abs)} absences after set_data")
        
        # 5) Graph erneut laufen lassen
        graph = build_graph()
        run_id = req.run_id or str(int(time.time()*1000))
        # IMPORTANT: Include updated absences in initial state so ingest_node uses them
        initial_state = {
            "status": "INIT",
            "needs_approval": False,
            "awaiting_approval": False,
            "kpis": {"budget": req.budget} if req.budget is not None else {},
            "logs": [],
            "steps": [],
            "run_id": run_id,
            "absences": state2.get("absences", []),  # Include updated absences!
        }
        
        publish_event(run_id, {"message": "Chat-Änderung wird angewendet", "active_node": "ingest"})
        final_state = graph.invoke(initial_state, config={"auto_approve": req.auto_approve})
        publish_event(run_id, {"message": "Chat-Änderung abgeschlossen", "active_node": None})
        
        return {
            "ok": True,
            "run_id": run_id,
            "intents": intents,
            "notes": notes,
            "apply_logs": logs,
            "kpis": final_state.get("kpis", {}),
            "solution": final_state.get("solution", {}),
            "steps": final_state.get("steps", []),
            "audit": final_state.get("audit", {}),
            "status": final_state.get("status", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Chat endpoint error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Chat-Fehler: {str(e)}")

@app.get("/timeline", response_class=HTMLResponse)
def timeline(day: str = None):
    """Generate timeline visualization for shifts"""
    try:
        from app.services.shift_visualizer import generate_timeline_html
        
        # Get current solution from running the graph
        graph = build_graph()
        initial_state = {
            "status": "INIT",
            "needs_approval": False,
            "awaiting_approval": False,
            "logs": [],
        }
        final_state = graph.invoke(initial_state, config={"auto_approve": True})
        
        # Get consolidated shifts
        shifts = final_state.get("solution", {}).get("shifts", [])
        
        if not shifts:
            return HTMLResponse(content="<h2>No shifts available. Please run the solver first.</h2>")
        
        # Get unique days and sort them chronologically (handle DD.MM.YYYY format)
        unique_days_raw = set(s.get("day", "") for s in shifts if s.get("day"))
        
        def parse_german_date(date_str):
            """Parse DD.MM.YYYY to sortable tuple"""
            try:
                parts = date_str.split(".")
                if len(parts) == 3:
                    return (int(parts[2]), int(parts[1]), int(parts[0]))  # (year, month, day)
            except:
                pass
            return (9999, 12, 31)
        
        unique_days = sorted(unique_days_raw, key=parse_german_date)
        
        # If no day specified, use first day (chronologically)
        selected_day = day if day in unique_days else (unique_days[0] if unique_days else None)
        
        if not selected_day:
            return HTMLResponse(content="<h2>No valid days found in shifts</h2>")
        
        # Generate timeline HTML
        timeline_html = generate_timeline_html(shifts, selected_day)
        
        # Build day selector dropdown
        day_options = ''.join(
            f'<option value="{d}" {"selected" if d == selected_day else ""}>{d}</option>'
            for d in unique_days
        )
        
        # Wrap in a complete page with day selector
        page = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Shift Timeline</title>
    <style>
        .controls {{
            text-align: center;
            margin: 20px;
            font-family: Arial, sans-serif;
        }}
        .controls select {{
            padding: 8px 16px;
            font-size: 16px;
            border: 1px solid #ccc;
            border-radius: 4px;
            margin-left: 10px;
        }}
        .controls label {{
            font-size: 16px;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <h1 style="text-align: center; font-family: Arial;">Shift Plan Timeline</h1>
    <div class="controls">
        <label for="daySelect">Select Day:</label>
        <select id="daySelect" onchange="window.location.href='/timeline?day=' + this.value;">
            {day_options}
        </select>
    </div>
    {timeline_html}
    <div style="margin: 20px; text-align: center;">
        <a href="/ui" style="padding: 10px 20px; background: #2c5f7c; color: white; text-decoration: none; border-radius: 5px;">Back to Monitor</a>
    </div>
</body>
</html>
        """
        
        return HTMLResponse(content=page)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return HTMLResponse(content=f"<h2>Error generating timeline: {str(e)}</h2>")
