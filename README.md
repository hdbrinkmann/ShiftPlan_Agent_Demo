# ShiftPlan Agent Demo

Diese Demo zeigt, wie ein kleiner „Agenten‑Schwarm“ gemeinsam einen Schichtplan erstellt – von den Eingangsdaten (Excel) bis zum fertigen Ergebnis im Browser. Zielgruppe sind Nicht‑Techniker: alles ist absichtlich einfach und nachvollziehbar erklärt.

## Was die Anwendung tut – in einem Satz

Sie lädt Mitarbeiter‑, Abwesenheits‑ und Öffnungszeiten‑Bedarf aus Excel, verteilt die passenden Mitarbeiter kostengünstig auf die Zeitblöcke (Store Manager inkl. Assistant als Ersatz, Sales), prüft Regeln und zeigt das Ergebnis als Tabelle im Browser.

## So startest du die Demo

1) Voraussetzungen installieren
    - Python 3.11
    - Im Projektordner ein virtuelles Environment anlegen und Abhängigkeiten installieren (siehe requirements.txt)

2) Server starten
    - Start im Projektstamm: `python3 -m uvicorn app.api.main:app --host 127.0.0.1 --port 7001`

3) Browser öffnen
    - UI unter `http://127.0.0.1:7001/ui/`
    - Dort die Excel hochladen und anschließend „Run“ ausführen.

## Die Agenten – wer macht was?

Die Logik ist als Kette von „Agenten“ (Knoten) umgesetzt. Jeder Agent hat eine klar umrissene Aufgabe:

- Ingest‑Agent (`ingest_node`)
   - Aufgabe: Eingabedaten laden. Wenn du eine Excel hochlädst, werden Mitarbeiter, Abwesenheiten und der Bedarf (Headcount je Zeitblock) daraus entnommen.
   - Ergebnis: Eine saubere Liste von Mitarbeitern (inkl. Kosten pro Stunde und Rollen/Skills), Abwesenheiten und Bedarfszeilen.

- Regel‑Agent (`rules_node`)
   - Aufgabe: Einfache Regeln definieren, z. B. max. Stunden pro Tag, Ruhezeit zwischen Tagen, Skill‑Pflicht.
   - Ergebnis: Ein „Constraints“-Paket, das alle weiteren Agenten kennen.

- Demand‑Agent (`demand_node`)
   - Aufgabe: Den Bedarf zusammenstellen. Bei einem „Opening Hours“-Blatt werden die Rollen als Spalten gelesen (z. B. „Store Manager“, „Sales“) – die Zahlen sind Headcount.
   - Ergebnis: Eine Liste von Zeilen wie: Tag, Zeitspanne, Rolle, Anzahl.

- Solver‑Agent (`solve_node`)
   - Aufgabe: Mitarbeiter auf die Bedarfsspitzen verteilen – kostengünstig und regelkonform.
   - Vorgehen (vereinfacht):
      - Für „Store Manager“ werden zuerst echte Store Manager besetzt, danach Assistant/Deputy als Ersatz.
      - Für „Sales“ werden Sales‑Profile genommen; optional darf „Cashier“ aushelfen.
      - Kandidaten werden nach (Trefferqualität, Kosten) und einem kleinen Fairness‑Anteil sortiert. Eine leichte Rotation verhindert, dass immer dieselben zuerst genommen werden.
      - Doppelbelegung derselben Person im identischen Zeitblock wird verhindert. Abwesenheiten und einfache Max‑Stunden‑/Ruhezeit‑Regeln werden berücksichtigt.
   - Ergebnis: Eine Liste von „Assignments“ mit Tag, Zeit, Rolle, Mitarbeiter, Stunden und Kosten/h.

- Audit‑Agent (`audit_node`)
   - Aufgabe: Prüft, ob der Bedarf je Block wirklich abgedeckt ist (z. B. Headcount erfüllt).
   - Ergebnis: Liste von Abweichungen (z. B. Unterdeckung) für die spätere Bewertung.

- KPI‑Agent (`kpi_node`)
   - Aufgabe: Einfache Kennzahlen berechnen – v. a. Gesamtkosten und Abdeckungsgrad.
   - Ergebnis: KPIs anzeigen, damit man sieht, ob die Lösung „gut genug“ ist.

- Triage‑Agent (`triage_node`) und Human‑Gate (`human_gate_node`)
   - Aufgabe: Wenn Regeln verletzt oder Budget überschritten ist, schlägt die Triage kleine Lockerungen vor (z. B. +0,5 Stunden max/Tag). Der Human‑Gate entscheidet: automatisch freigeben (Demo) oder auf Freigabe warten.

- Export‑Agent (`export_node`)
   - Aufgabe: Abschluss der Planung (in der Demo nur ein „ok“ – hier könnte ein Export in Excel/CSV/ERP folgen).

Die UI zeigt parallel live, welcher Agent gerade aktiv ist und was er tut (SSE‑Telemetrie).

## Wie ist das mit LangGraph umgesetzt?

Stell dir die Agenten wie Stationen auf einer Kette vor. LangGraph erlaubt, diese Stationen klar zu definieren und zu verbinden:

- Wir bauen einen Graphen mit festen Knoten (Ingest → Regeln → Bedarf → Solver → Audit → KPI …).
- Zwischen den Knoten laufen einfache Datenpakete („State“). Jeder Knoten liest, was er braucht (z. B. Mitarbeiter, Bedarf) und hängt sein Ergebnis an.
- Nach dem KPI‑Agenten entscheidet eine einfache „Weiche“: Wenn alles passt, geht’s direkt zum Export. Wenn nicht, geht’s über Triage und (optional) Human‑Gate zurück zum Lösen.
- Jeder Knoten meldet Status‑Informationen (per Ereignissen) an die UI, damit man den Verlauf live mitlesen kann.

Das klingt technisch, ist aber im Kern simpel: eine Pipeline aus Arbeitsschritten, die jeweils ihr Teilergebnis anreichern und zusammen ein Ziel erreichen: einen praktikablen Schichtplan.

## Excel‑Upload – worauf achten?

- Mitarbeitende: Spalten wie Name, eine Rollen-/Positionsangabe (z. B. „Store Manager“, „Assistant Store Manager“, „Sales“) und „Cost per hour in EUR“ (wird automatisch erkannt). Fehlt die „Skills“-Spalte, interpretieren wir die Position als Skill.
- Abwesenheiten: optional, aber hilfreich (Tag, von/bis, Typ).
- Bedarf (z. B. Blatt „Opening Hours“): Spalten für Datum/Tag und „From/To“ für die Zeit. Rollen (Store Manager, Sales, …) als Spalten; die Zahlen sind der Headcount.

## Kosten, Regeln und Kennzahlen

- Kosten: Summe aus „Stunden im Block × Kosten pro Stunde“ über alle Zuteilungen (pro Mitarbeiter). Stundensätze werden robust aus der Excel gelesen (verschiedene Schreibweisen werden erkannt).
- Regeln: Einfach gehalten, aber wirksam – Skill‑Match, keine Doppelbelegung im selben Zeitblock, Abwesenheiten, max. Stunden pro Tag/Woche (falls gesetzt), und Ruhezeiten zwischen Tagen.
- KPIs: Gesamtkosten und Abdeckungsgrad (wie viel des Headcounts pro Block abgedeckt wurde).

## Chat‑Funktion – Plan während der Laufzeit ändern

Nach dem Erstellen eines Plans kannst du im Textfeld unten eine Nachricht eingeben, z. B.:
- „Knut ist am 22.09.2025 krank"
- „Stefan ist bis Freitag krank"
- „Maria ist vom 01.10.2025 bis 05.10.2025 krank"

Das System verarbeitet deine Nachricht automatisch:
1. Erkennt den Mitarbeiter (z. B. „Knut" → Personalnummer 10118)
2. Erkennt das Datum oder den Zeitraum
3. Fügt die Abwesenheit hinzu
4. Erstellt den Plan neu unter Berücksichtigung der Änderung

### Wie funktioniert das?

Die Chat‑Funktion nutzt zwei Ansätze:

**LLM‑basiert (wenn aktiviert):**
- Ein KI‑Modell analysiert deine Nachricht in beliebiger Sprache
- Extrahiert automatisch Mitarbeiter-ID, Datum und Art der Abwesenheit
- Funktioniert auch mit flexibleren Formulierungen wie „Knut ist ab morgen 3 Tage krank"

**Regelbasiert (Fallback):**
- Falls das LLM nicht verfügbar ist, greift ein einfacher regelbasierter Parser
- Erkennt deutsche Sätze wie „Name ist am/bis/vom Datum krank"
- Weniger flexibel, aber zuverlässig für Standardfälle

Du kannst zwischen beiden Modi wählen über die Umgebungsvariable `SHIFTPLAN_USE_LLM_INTENTS` in der `.env` Datei:
- `SHIFTPLAN_USE_LLM_INTENTS=1` → LLM-Modus (flexibler, sprachunabhängig)
- `SHIFTPLAN_USE_LLM_INTENTS=0` → Nur regelbasiert (einfacher, offline-fähig)

### Intelligente Ersatzplanung

Wenn ein Store Manager ausfällt (z. B. Knut), versucht das System automatisch:
1. Einen **anderen Store Manager** zu finden
2. Falls nicht verfügbar: einen **Assistant Store Manager** als Ersatz einzusetzen
3. Falls auch das nicht möglich ist: meldet der Audit‑Agent die Unterbesetzung als Warnung

Das System berücksichtigt dabei:
- Wer ist verfügbar (keine Doppelbelegung, keine Abwesenheiten)
- Wer ist qualifiziert (Skills müssen passen)
- Wer ist kostengünstig (bevorzugt günstigere Mitarbeiter)
- Wer ist fair verteilt (verhindert Überlastung einzelner Mitarbeiter)

## LLM‑Integration (optional)

Ein Client für Scaleway‑LLM (oder OpenAI-kompatible APIs) ist integriert. Das LLM wird für zwei Zwecke genutzt:

1. **Chat‑Intent‑Erkennung**: Versteht deine Nachrichten in natürlicher Sprache und extrahiert strukturierte Informationen
2. **Schritt‑Zusammenfassungen**: Kommentiert die einzelnen Agenten‑Schritte in der UI (optional)

Wenn keine Zugangsdaten gesetzt sind, läuft die Demo offline weiter mit regelbasierten Fallbacks. Das LLM ist nicht kritisch für die Kernfunktionalität.

## Endpunkte & UI

- UI: `GET /ui/` → Upload, Start, Live‑Verlauf, Ergebnis‑Tabelle, Chat‑Eingabe.
- API:
   - `POST /upload` → Excel hochladen.
   - `POST /run` → Graph ausführen (JSON‑Body: `{ "auto_approve": true }`).
   - `POST /chat` → Nachricht senden, um Plan zu ändern (JSON‑Body: `{ "message": "Knut ist am 22.09.2025 krank", "run_id": "default", "auto_approve": true }`).
   - `POST /result` → Liefert die Ergebnis‑HTML mit Tabelle.
   - `GET /inspect` → Zeigt die geladenen Daten (Counts/Samples).
   - `GET /llm_status` → Zeigt ob LLM aktiviert ist und welches Modell verwendet wird.

## Grenzen der Demo und Ausblick

- Der Solver ist bewusst einfach (greedy), liefert aber schon brauchbare Ergebnisse. Für komplexe Pläne kann ein Optimierer (z. B. OR‑Tools) eingebaut werden.
- Die Regeln sind minimal und können erweitert werden (Pausen, Tarifregeln, Schichtfolgen, Wünsche …).
- Export ist aktuell ein Platzhalter – hier ließen sich Dateien oder System‑Schnittstellen anbinden.

Die Stärke der Lösung liegt in der klaren Struktur: Jeder Schritt ist eigenständig und nachvollziehbar. Dadurch kann man die Logik Schritt für Schritt verfeinern, ohne das Gesamtsystem zu verkomplizieren.
