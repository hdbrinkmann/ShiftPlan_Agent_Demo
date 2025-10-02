from typing import List, Dict, Any, Tuple, DefaultDict
from collections import defaultdict
from datetime import datetime

def solve(employees: List[Dict[str, Any]], absences: List[Dict[str, Any]], constraints: Dict[str, Any], demand: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Greedy Headcount-Solver:
    - Interpretiert Zahlen in Demand-Zeilen als Headcount je Zeitblock.
    - Deckt "Store Manager" mit Präferenz für echte Store Manager, dann Assistant/Deputy als Fallback.
    - Deckt "Sales" inkl. üblicher Synonyme; optionaler Fallback auf "Cashier" falls nötig.
    - Wählt kostengünstigste verfügbare Mitarbeiter und vermeidet Doppelbelegung im selben Zeitblock.
    - Berücksichtigt einfache Abwesenheiten (Zeitüberschneidung blockiert Zuweisung).
    """

    # Normalisierung und Synonyme
    def norm(s: str) -> str:
        return " ".join(str(s or "").strip().lower().replace("_", " ").replace("-", " ").replace(".", "").split())

    # Zielrollen-Erkennung (aus Demand)
    def role_kind(role: str) -> str:
        r = norm(role)
        if ("store" in r and "manager" in r) or ("filialleiter" in r):
            return "store_manager"
        if "sales" in r or "verkauf" in r:
            return "sales"
        if "cashier" in r or "kasse" in r:
            return "cashier"
        return r  # Fallback: exakter (normalisierter) Rollenname

    # Kandidatenlisten pro Zielrolle mit Match-Rang (0 = ideal, 1 = Fallback)
    ROLE_MATCHERS_BASE: Dict[str, Tuple[List[str], List[str]]] = {
        # (ideal, fallback)
        "store_manager": (
            [
                "store manager", "manager", "shop manager", "filialleiter", "storemanager",
            ],
            [
                "assistant store manager", "asst store manager", "assistant manager", "deputy manager",
                "stellvertretender filialleiter", "stellv filialleiter",
            ],
        ),
        "sales": (
            [
                "sales", "sales associate", "verkauf", "verkauf mitarbeiter", "verkaufsmitarbeiter",
                "salesperson", "berater", "verkaufskraft",
            ],
            [
                # optionaler Fallback: Kasse kann einfachen Sales-Bedarf stützen
                "cashier", "kasse",
            ],
        ),
        "cashier": (
            ["cashier", "kasse"],
            [],
        ),
    }

    # Konfigurierbare Fallbacks aus Constraints
    hard = (constraints or {}).get("hard", {})
    allow_assistant_for_manager = bool(hard.get("allow_assistant_for_manager", True))
    allow_cashier_for_sales = bool(hard.get("allow_cashier_for_sales", True))

    ROLE_MATCHERS: Dict[str, Tuple[List[str], List[str]]] = {}
    for rk, (ideal, fallback) in ROLE_MATCHERS_BASE.items():
        if rk == "store_manager" and not allow_assistant_for_manager:
            ROLE_MATCHERS[rk] = (ideal, [])
        elif rk == "sales" and not allow_cashier_for_sales:
            ROLE_MATCHERS[rk] = (ideal, [])
        else:
            ROLE_MATCHERS[rk] = (ideal, fallback)

    # Hilfsfunktion: Tagesstring robust in ISO (YYYY-MM-DD) überführen
    def _day_to_iso(s: str) -> str:
        s = str(s or "").strip()
        # Handle timestamps (e.g., "2025-09-22 00:00:00") by extracting date part
        if " " in s:
            s = s.split(" ")[0]
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                    "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y",
                    "%m/%d/%Y"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except Exception:
                pass
        return s  # Unverändert, wenn kein Datumsformat erkennbar (z. B. Wochentagsname)

    # Abwesenheiten in schnelle Nachschlageform bringen: blocked[emp][iso_day] -> List[(start,end)]
    blocked: DefaultDict[str, DefaultDict[str, List[Tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for a in absences or []:
        emp = str(a.get("employee_id", ""))
        day = _day_to_iso(a.get("day", ""))
        span = str(a.get("time", ""))
        st, en = _span_minutes(span)
        if emp and day and st is not None and en is not None:
            blocked[emp][day].append((st, en))
            print(f"[SOLVER] Blocked: emp={emp}, day={day}, time={st}-{en} minutes")

    def is_available(emp_id: str, day: str, span: str) -> bool:
        st, en = _span_minutes(span)
        if st is None or en is None:
            return True
        iso_day = _day_to_iso(day)
        
        # DEBUG for emp 10118
        if emp_id == "10118":
            print(f"[SOLVER is_available] emp=10118, input day='{day}', iso_day='{iso_day}', span='{span}' -> {st}-{en} min")
            print(f"[SOLVER is_available] blocked days for 10118: {list(blocked.get('10118', {}).keys())}")
            if iso_day in blocked.get("10118", {}):
                print(f"[SOLVER is_available] blocked intervals on {iso_day}: {blocked.get('10118', {}).get(iso_day, [])}")
        
        # 1) Exakte Tagesübereinstimmung (ISO)
        for bst, ben in blocked.get(emp_id, {}).get(iso_day, []):
            if _overlaps((st, en), (bst, ben)):
                print(f"[SOLVER] Employee {emp_id} NOT available on {iso_day} {span} (blocked {bst}-{ben})")
                return False
        # 2) Wochentagsabgleich: Wenn Demand einen Wochentag (z. B. "Mon"/"Mo"/"Montag") nutzt,
        #    blocke, falls irgendeine Abwesenheit an einem Datum mit gleichem Wochentag liegt.
        wk_map = {
            "mon": 0, "mo": 0, "montag": 0,
            "tue": 1, "di": 1, "dienstag": 1,
            "wed": 2, "mi": 2, "mittwoch": 2,
            "thu": 3, "do": 3, "donnerstag": 3,
            "fri": 4, "fr": 4, "freitag": 4,
            "sat": 5, "sa": 5, "samstag": 5,
            "sun": 6, "so": 6, "sonntag": 6,
        }
        day_token = str(day or "").strip().lower()
        wanted_wd = wk_map.get(day_token[:3]) if day_token else None
        if wanted_wd is not None:
            for bd, intervals in blocked.get(emp_id, {}).items():
                # nur echte ISO-Daten berücksichtigen
                try:
                    bd_wd = datetime.strptime(bd, "%Y-%m-%d").weekday()
                except Exception:
                    continue
                if bd_wd == wanted_wd:
                    for bst, ben in intervals:
                        if _overlaps((st, en), (bst, ben)):
                            return False
        return True

    # Skills pro Mitarbeiter normalisieren
    emp_skills_norm: Dict[str, List[str]] = {}
    for e in employees:
        sk = [norm(s) for s in (e.get("skills") or []) if str(s).strip()]
        emp_skills_norm[str(e.get("id"))] = sk

    # Bereits belegte Mitarbeiter je (day,time) vermeiden
    taken: DefaultDict[Tuple[str, str], set] = defaultdict(set)
    # Stundenkonten und Ruhezeit-Tracking
    hours_day: DefaultDict[str, DefaultDict[str, float]] = defaultdict(lambda: defaultdict(float))
    hours_week: DefaultDict[str, float] = defaultdict(float)
    last_end: Dict[str, Tuple[int, int]] = {}

    assignments: List[Dict[str, Any]] = []

    # Für deterministischere/faire Verteilung die Demand-Zeilen stabil sortieren
    # Tagesreihenfolge vorbereiten
    unique_days: List[str] = []
    _seen_days = set()
    for n in demand or []:
        d = str(n.get("day", ""))
        if d not in _seen_days:
            _seen_days.add(d)
            unique_days.append(d)

    def _weekday_index(name: str) -> int | None:
        m = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6,
             "mo":0,"di":1,"mi":2,"do":3,"fr":4,"sa":5,"so":6}
        return m.get(name.strip().lower()[:2])

    def _parse_day_key(s: str) -> Tuple[int, int]:
        for fmt in ("%Y-%m-%d","%d.%m.%Y","%m/%d/%Y","%Y/%m/%d"):
            try:
                dt = datetime.strptime(s.strip(), fmt)
                return int(dt.timestamp() // 86400), 0
            except Exception:
                pass
        wi = _weekday_index(s)
        if wi is not None:
            return wi, 1
        return hash(s) & 0x7FFFFFFF, 2

    _day_map: Dict[str, Tuple[int, int]] = {d: _parse_day_key(d) for d in unique_days}

    def demand_sort_key(need: Dict[str, Any]):
        d = str(need.get("day", ""))
        t = str(need.get("time", ""))
        st, _ = _span_minutes(t)
        st = st if st is not None else 0
        return (_day_map.get(d, (0,3)), st, role_kind(str(need.get("role", ""))))

    for need in sorted(demand or [], key=demand_sort_key):
        role_raw = str(need.get("role", "")).strip()
        day = str(need.get("day", ""))
        time_span = str(need.get("time", ""))
        qty = int(need.get("qty", 0) or 0)
        if qty <= 0:
            continue
        rk = role_kind(role_raw)

        # Kandidatenliste aufbauen mit Rang (0 ideal, 1 fallback), danach nach Kosten sortieren
        ideal, fallback = ROLE_MATCHERS.get(rk, ([rk], []))
        candidates: List[Tuple[int, Dict[str, Any]]] = []
        for e in employees:
            eid = str(e.get("id"))
            if eid in taken[(day, time_span)]:
                continue
            skills = emp_skills_norm.get(eid, [])
            m_rank = None
            # ideal
            if any(k in skills for k in map(norm, ideal)):
                m_rank = 0
            # fallback
            elif any(k in skills for k in map(norm, fallback)):
                m_rank = 1
            # exakter Rollenname als letzter Versuch
            elif norm(role_raw) in skills:
                m_rank = 0
            if m_rank is None:
                continue
            # DEBUG: Check availability for emp 10118
            if eid == "10118":
                print(f"[SOLVER] Checking availability for 10118: day={day}, time={time_span}")
            avail = is_available(eid, day, time_span)
            if eid == "10118":
                print(f"[SOLVER] Employee 10118 available={avail}")
            if not avail:
                continue
            candidates.append((m_rank, e))

        # Fairness + Kosten: (Match-Rang, Kosten + alpha*Wochensumme, Name/ID)
        alpha = 0.1
        candidates.sort(
            key=lambda t: (
                t[0],
                float(t[1].get("hourly_cost", 0) or 0) + alpha * hours_week.get(str(t[1].get("id")), 0.0),
                str(t[1].get("name") or t[1].get("id")),
            )
        )

        # Leichte Round-Robin-Rotation pro Rolle (globaler Offset)
        rr_key = (rk,)
        if not hasattr(solve, "_rr_offsets"):
            setattr(solve, "_rr_offsets", defaultdict(int))
        rr_offsets = getattr(solve, "_rr_offsets")
        if candidates:
            off = rr_offsets[rr_key] % len(candidates)
            candidates = candidates[off:] + candidates[:off]

        count = 0
        hours = _span_hours(time_span)
        for rank, e in candidates:
            if count >= qty:
                break
            eid = str(e.get("id"))
            if eid in taken[(day, time_span)]:
                continue
            # Max/Tag
            max_hours_per_day = float(hard.get("max_hours_per_day", 24))
            if hours_day[eid][day] + hours > max_hours_per_day + 1e-6:
                continue
            # Max/Woche (falls pro MA gesetzt)
            max_week = float(e.get("max_hours_week", 0) or 0)
            if max_week > 0 and hours_week[eid] + hours > max_week + 1e-6:
                continue
            # Ruhezeit (zwischen Tagen)
            st_min, en_min = _span_minutes(time_span)
            if st_min is None or en_min is None:
                st_min, en_min = 0, int(hours * 60)
            d_idx, _prec = _day_map.get(day, (0, 3))
            min_rest_hours = float(hard.get("min_rest_hours", 0))
            if eid in last_end and min_rest_hours > 0:
                ld_idx, ld_end = last_end[eid]
                if d_idx > ld_idx:
                    gap = (d_idx - ld_idx) * 24 * 60 + (st_min - ld_end)
                    if gap < min_rest_hours * 60 - 1e-6:
                        continue
            # Zuweisung schreiben
            assignments.append({
                "employee_id": eid,
                "role": role_raw,
                "day": day,
                "time": time_span,
                "hours": hours,
                "cost_per_hour": float(e.get("hourly_cost", 0) or 0),
            })
            taken[(day, time_span)].add(eid)
            hours_day[eid][day] += hours
            hours_week[eid] += hours
            last_end[eid] = (_day_map.get(day, (0, 3))[0], en_min)
            count += 1
            rr_offsets[rr_key] = rr_offsets[rr_key] + 1

    return {"assignments": assignments}

def _span_minutes(span: str) -> Tuple[int | None, int | None]:
    try:
        if not span or "-" not in span:
            return None, None
        start, end = span.split("-", 1)
        # Handle both HH:MM and HH:MM:SS formats
        start_parts = start.strip().split(":")
        end_parts = end.strip().split(":")
        sh = int(start_parts[0])
        sm = int(start_parts[1]) if len(start_parts) > 1 else 0
        eh = int(end_parts[0])
        em = int(end_parts[1]) if len(end_parts) > 1 else 0
        return sh * 60 + sm, eh * 60 + em
    except Exception:
        return None, None

def _overlaps(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    (a1, a2), (b1, b2) = a, b
    return max(a1, b1) < min(a2, b2)

def _span_hours(span: str) -> float:
    # "09:00-13:00" -> 4.0
    try:
        mins = _span_minutes(span)
        if mins[0] is None or mins[1] is None:
            return 4.0
        return (mins[1] - mins[0]) / 60.0
    except Exception:
        return 4.0
