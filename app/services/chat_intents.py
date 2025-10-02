from __future__ import annotations
from typing import List, Dict, Any, Tuple
import os
import json
import re
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
from pathlib import Path

# Load .env early so SHIFTPLAN_USE_LLM_INTENTS is visible on first call
load_dotenv()
try:
    _proj_root = Path(__file__).resolve().parents[2]
    _dotenv_path = _proj_root / ".env"
    if _dotenv_path.exists():
        load_dotenv(dotenv_path=str(_dotenv_path), override=False)
except Exception:
    pass


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _one_edit_away(a: str, b: str) -> bool:
    a = a.lower().strip()
    b = b.lower().strip()
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    # allow one insertion/deletion/substitution
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1; j += 1
        else:
            edits += 1
            if edits > 1:
                return False
            if len(a) > len(b):
                i += 1
            elif len(b) > len(a):
                j += 1
            else:
                i += 1; j += 1
    # account for trailing char
    if i < len(a) or j < len(b):
        edits += 1
    return edits <= 1


def _has_token_like(text: str, token: str) -> bool:
    t = _norm(text)
    if token in t:
        return True
    # check word-wise near matches
    words = re.split(r"[^a-z0-9äöüß]+", t)
    return any(_one_edit_away(w, token) for w in words if w)


def _parse_message_to_intents_rule_based(msg: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Sehr einfacher regelbasierter Parser für einen MVP:
    - erkennt Sätze wie "X ist bis Freitag krank" oder "X ist morgen krank" und erzeugt add_absence-Intents
    - Mehrdeutigkeiten werden aktuell nicht geklärt (MVP)
    Rückgabe: (intents, notes)
    """
    intents: List[Dict[str, Any]] = []
    notes: List[str] = []
    s = _norm(msg)

    # einfache Datums-Hilfen
    today = datetime.today().date()
    weekdays = {
        "montag": 0, "mo": 0, "monday": 0,
        "dienstag": 1, "di": 1, "tuesday": 1,
        "mittwoch": 2, "mi": 2, "wednesday": 2,
        "donnerstag": 3, "do": 3, "thursday": 3,
        "freitag": 4, "fr": 4, "friday": 4,
        "samstag": 5, "sa": 5, "saturday": 5,
        "sonntag": 6, "so": 6, "sunday": 6,
        "heute": None, "morgen": None, "today": None, "tomorrow": None,
    }

    DATE_PATTERN = r"(?:[0-9]{4}[./-][0-9]{1,2}[./-][0-9]{1,2}|[0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4})"

    def parse_relative_date(token: str) -> date | None:
        t = token.lower()
        if t in ("heute", "today"):
            return today
        if t in ("morgen", "tomorrow"):
            return today + timedelta(days=1)
        if t in weekdays and weekdays[t] is not None:
            target = weekdays[t]
            # nächste Vorkommen des Wochentags (inkl. heute)
            diff = (target - today.weekday()) % 7
            return today + timedelta(days=diff)
        # absolute Formate versuchen
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(token, fmt).date()
            except Exception:
                pass
        return None

    sick_like = _has_token_like(s, "krank") or ("sick" in s or "ill" in s)

    # 0) Name extrahieren (optional)
    mname = re.search(r"^\s*([a-zäöüß\-\s]+?)\s+ist\b", s)

    # 1) "<name> ist ... vom <date> bis <date>" (unabhängig von der exakten Stellung von 'krank')
    mv = re.search(
        rf"([a-zäöüß\-\s]+?)\s+ist\s+.*?vom\s+({DATE_PATTERN})\s+(?:bis|\-)\s+({DATE_PATTERN})",
        s,
    )
    if mv:
        if not sick_like:
            # ohne Krankheits-Hinweis keine Aktion (zu unspezifisch)
            return intents, notes
        name = mv.group(1).strip()
        d0 = parse_relative_date(mv.group(2)) or today
        d1 = parse_relative_date(mv.group(3)) or d0
        if d1 < d0:
            d0, d1 = d1, d0
        intents.append({
            "type": "add_absence",
            "employee_name": name,
            "from_date": d0.isoformat(),
            "to_date": d1.isoformat(),
            "times": ["00:00-24:00"],
        })
        notes.append(f"Interpretiere: {name} krank vom {d0.isoformat()} bis {d1.isoformat()}.")
        return intents, notes

    # 2) "<name> ist ... am <date>" (mit Krankheits-Hinweis tolerant)
    ma = re.search(
        rf"([a-zäöüß\-\s]+?)\s+ist\s+.*?am\s+({DATE_PATTERN})",
        s,
    )
    if ma:
        if not sick_like:
            return intents, notes
        name = ma.group(1).strip()
        d = parse_relative_date(ma.group(2)) or today
        intents.append({
            "type": "add_absence",
            "employee_name": name,
            "from_date": d.isoformat(),
            "to_date": d.isoformat(),
            "times": ["00:00-24:00"],
        })
        notes.append(f"Interpretiere: {name} krank am {d.isoformat()}.")
        return intents, notes

    # 3) Fallback: Name + irgendein Datum + Krankheits-Hinweis ungefähr
    if not sick_like:
        return intents, notes
    m = mname or re.search(r"([a-zäöüß\-\s]+?)\s+ist\b", s)
    if m:
        name = m.group(1).strip()
        start_date = today
        # Falls irgendwo ein absolutes Datum vorkommt, nutze dieses als Single-Day
        mdate = re.search(rf"{DATE_PATTERN}", s)
        if mdate:
            d = parse_relative_date(mdate.group(1)) or today
            end_date = d
            start_date = d
            note_detail = f"am {d.isoformat()}"
        else:
            # Suche nach 'bis <token>' optional
            muntil = re.search(r"\bbis\s+([a-z0-9\./\-äöüß]+)", s)
            until_token = muntil.group(1) if muntil else ""
            end_date = parse_relative_date(until_token) if until_token else today
            if end_date is None:
                end_date = today
            note_detail = f"bis {end_date.isoformat()}"
        intents.append({
            "type": "add_absence",
            "employee_name": name,
            "from_date": start_date.isoformat(),
            "to_date": end_date.isoformat(),
            "times": ["00:00-24:00"],
        })
        notes.append(f"Interpretiere: {name} krank {note_detail}.")

    return intents, notes


def _parse_message_to_intents_llm(msg: str, employees: List[Dict[str, Any]] | None = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Versucht, mittels LLM strukturierte Intents als JSON zu extrahieren.
    Fallback auf leere Liste bei Fehlern, der Aufrufer soll dann regelbasiert arbeiten.
    Erwartetes JSON-Schema:
    {
      "intents": [
        {
          "type": "add_absence",
          "employee_name": "...",
          "from_date": "YYYY-MM-DD",
          "to_date": "YYYY-MM-DD",
          "times": ["00:00-24:00"]
        }
      ],
      "notes": ["..."]
    }
    """
    try:
        from app.services.llm import ScalewayLLM
    except Exception as e:
        print(f"LLM module import failed: {e}")
        return [], ["LLM-Modul nicht verfügbar; fallback auf Regeln"]

    llm = ScalewayLLM()
    if not getattr(llm, "enabled", False):
        print("LLM is disabled; using rule-based parser")
        return [], ["LLM ist nicht aktiviert; fallback auf Regeln"]

    today = datetime.today().date().isoformat()
    system = (
        "You are an NLU parser for shift planning. Extract structured intents from user input (any language). "
        "Respond ONLY with valid JSON, no additional text. Today's date (TODAY) is %s. "
        "Supported intent types: add_absence. "
        "add_absence fields: employee_id (String - MUST use the exact ID from the employee list), "
        "from_date (YYYY-MM-DD), to_date (YYYY-MM-DD), times (Array of time ranges like ['00:00-24:00']). "
        "Date interpretation: 'on <date>' or 'am <date>' => same from_date and to_date; "
        "'from <date> to <date>' or 'vom <date> bis <date>' => date range; "
        "weekday names relative to TODAY (e.g., 'Monday' => next Monday from TODAY, including today if today is Monday). "
        "If no intents recognizable: return {\"intents\": [], \"notes\": [\"reason\"]}."
    ) % today

    roster_lines = []
    if employees:
        for e in employees[:100]:  # hard limit to keep prompt manageable
            name = str(e.get("name") or e.get("id") or "").strip()
            eid = str(e.get("id") or "").strip()
            skills = ", ".join([str(s) for s in (e.get("skills") or [])])
            roster_lines.append(f"- Name: {name}, ID: {eid}, Skills: [{skills}]")
    roster_text = ("\n\nAvailable employees (use ONLY IDs from this list in your response):\n" + "\n".join(roster_lines)) if roster_lines else ""

    user = (
        "User input: " + msg + roster_text + "\n\n"
        "Return ONLY this JSON structure: {\"intents\": [...], \"notes\": [...]}"
    )

    def _strip_code_fences(s: str) -> str:
        t = s.strip()
        if t.startswith("```"):
            # Entferne ```json ... ``` oder ``` ... ```
            t = t.strip("`")
            # Falls eine Sprachangabe vorhanden ist, erste Zeile entfernen
            if "\n" in t:
                first, rest = t.split("\n", 1)
                if first.strip().lower().startswith("json"):
                    return rest.strip()
            return t.strip()
        return s

    try:
        print(f"Sending message to LLM: {msg[:100]}...")
        out = llm.chat(system, user)
        print(f"LLM raw response: {out[:200]}...")
        out_clean = _strip_code_fences(out)
        data = json.loads(out_clean)
        intents = data.get("intents") if isinstance(data, dict) else None
        notes = data.get("notes") if isinstance(data, dict) else None
        if not isinstance(intents, list):
            intents = []
        if not isinstance(notes, list):
            notes = []
        print(f"LLM parsed {len(intents)} intents")
        # Validate fields - LLM should return employee_id directly
        norm_intents: List[Dict[str, Any]] = []
        for it in intents:
            if not isinstance(it, dict):
                continue
            if it.get("type") != "add_absence":
                continue
            
            emp_id = str(it.get("employee_id", "")).strip()
            if not emp_id:
                print(f"Intent missing employee_id: {it}")
                continue
            
            # Verify employee_id exists
            if employees:
                valid_ids = {str(e.get("id", "")).strip() for e in employees}
                if emp_id not in valid_ids:
                    print(f"Invalid employee_id from LLM: {emp_id}, valid IDs: {list(valid_ids)[:5]}")
                    continue
            
            fd = it.get("from_date")
            td = it.get("to_date") or fd
            times = it.get("times") or ["00:00-24:00"]
            try:
                # Validate dates
                d0 = datetime.fromisoformat(str(fd)).date()
                d1 = datetime.fromisoformat(str(td)).date()
                if d1 < d0:
                    d0, d1 = d1, d0
                norm_intents.append({
                    "type": "add_absence",
                    "employee_id": emp_id,
                    "from_date": d0.isoformat(),
                    "to_date": d1.isoformat(),
                    "times": list(times) if isinstance(times, list) else ["00:00-24:00"],
                })
                print(f"Validated intent: employee_id={emp_id}, {d0} to {d1}")
            except Exception as e:
                print(f"Failed to validate intent dates: {e}")
                continue
        return norm_intents, notes
    except Exception as e:
        print(f"LLM parsing failed: {e}")
        return [], [f"LLM-Fehler ({type(e).__name__}); fallback auf Regeln"]


def parse_message_to_intents(msg: str, employees: List[Dict[str, Any]] | None = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Wrapper: Wenn aktiviert, zuerst LLM-Parsing; bei Fehlern/leerem Ergebnis regelbasiert.
    Aktivierung über Umgebungsvariable SHIFTPLAN_USE_LLM_INTENTS=1
    """
    use_llm = os.getenv("SHIFTPLAN_USE_LLM_INTENTS", "0") == "1"
    if use_llm:
        intents, notes = _parse_message_to_intents_llm(msg, employees=employees)
        if intents:
            return intents, notes
        # Kein oder ungültiges Ergebnis -> Regelparser zusätzlich versuchen und Notes zusammenführen
        rb_intents, rb_notes = _parse_message_to_intents_rule_based(msg)
        return rb_intents, (notes + rb_notes)
    # Standard: rein regelbasiert
    return _parse_message_to_intents_rule_based(msg)


def apply_intents(intents: List[Dict[str, Any]], state: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Wendet Intents auf den aktuellen State/Store-ähnliche Daten an.
    Unterstützt im MVP nur add_absence.
    """
    logs: List[str] = []
    employees = state.get("employees", [])
    absences = list(state.get("absences", []) or [])

    def resolve_employee_id(name: str) -> str | None:
        n = _norm(name)
        if not n:
            return None
        # Versuche verschiedene Matching-Strategien
        exact_matches = []
        substring_matches = []
        fuzzy_matches = []
        
        for e in employees:
            en = _norm(e.get("name") or e.get("id") or "")
            eid = str(e.get("id") or "")
            
            # 1. Exakte Übereinstimmung (mit und ohne ID)
            if n == en or n == _norm(eid):
                exact_matches.append(eid)
            # 2. Substring-Match (Input ist Teil des Namens)
            elif n in en:
                substring_matches.append(eid)
            # 3. Fuzzy-Match (Name enthält alle Wörter des Inputs)
            else:
                input_words = set(n.split())
                name_words = set(en.split())
                # Wenn alle Input-Wörter im Namen vorkommen
                if input_words and input_words.issubset(name_words):
                    fuzzy_matches.append(eid)
                # Oder wenn mindestens ein Wort mit einem Edit-Abstand übereinstimmt
                elif any(_one_edit_away(iw, nw) for iw in input_words for nw in name_words):
                    fuzzy_matches.append(eid)
        
        # Bevorzuge exakte Matches, dann Substring, dann Fuzzy
        if len(exact_matches) == 1:
            print(f"Exact match found for '{name}': {exact_matches[0]}")
            return exact_matches[0]
        elif len(substring_matches) == 1:
            print(f"Substring match found for '{name}': {substring_matches[0]}")
            return substring_matches[0]
        elif len(fuzzy_matches) == 1:
            print(f"Fuzzy match found for '{name}': {fuzzy_matches[0]}")
            return fuzzy_matches[0]
        
        # Bei mehreren Matches: nicht eindeutig
        all_matches = exact_matches + substring_matches + fuzzy_matches
        if len(all_matches) > 1:
            print(f"Multiple matches for '{name}': {all_matches}")
        else:
            print(f"No match found for '{name}'")
            # Debug: Liste verfügbare Mitarbeiter
            available = [_norm(e.get("name") or e.get("id") or "") for e in employees[:10]]
            print(f"Available employees: {available}")
        
        return None

    for it in intents:
        if it.get("type") == "add_absence":
            # Support both employee_id (from LLM) and employee_name (from rule-based parser)
            emp_id = it.get("employee_id", "").strip()
            emp_name = it.get("employee_name", "").strip()
            
            # If we have employee_id directly (from LLM), use it
            if emp_id:
                # Verify it exists
                valid_ids = {str(e.get("id", "")).strip() for e in employees}
                if emp_id not in valid_ids:
                    logs.append(f"Unbekannte employee_id: {emp_id}")
                    print(f"Unknown employee_id: {emp_id}, valid IDs: {list(valid_ids)[:5]}")
                    continue
                # Get name for logging
                emp_name_for_log = emp_id
                for e in employees:
                    if str(e.get("id", "")).strip() == emp_id:
                        emp_name_for_log = e.get("name", emp_id)
                        break
            # Otherwise, try to resolve from employee_name (rule-based parser)
            elif emp_name:
                emp_id = resolve_employee_id(emp_name)
                if not emp_id:
                    logs.append(f"Konnte Mitarbeiter nicht eindeutig auflösen: {emp_name}")
                    continue
                emp_name_for_log = emp_name
            else:
                logs.append("Intent hat weder employee_id noch employee_name")
                continue
            
            try:
                d0 = datetime.fromisoformat(it["from_date"]).date()
                d1 = datetime.fromisoformat(it["to_date"]).date()
            except Exception:
                logs.append("Ungültige Datumsangabe im Intent.")
                continue
            times = it.get("times") or ["00:00-24:00"]
            if d1 < d0:
                d0, d1 = d1, d0
            cur = d0
            while cur <= d1:
                for t in times:
                    absences.append({
                        "employee_id": emp_id,
                        "day": cur.isoformat(),
                        "time": t,
                        "type": "sick",
                    })
                cur += timedelta(days=1)
            logs.append(f"Abwesenheit hinzugefügt für {emp_name_for_log} ({emp_id}) {d0.isoformat()}–{d1.isoformat()}")

    new_state = {**state, "absences": absences}
    return new_state, logs
