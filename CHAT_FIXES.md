# Chat-Funktionalität - Behobene Probleme und Verbesserungen

## Update: 2025-10-02 (10:40 Uhr)

### Neue Verbesserungen: LLM-basierte Intent-Erkennung optimiert

Die LLM-Integration wurde grundlegend verbessert:

1. **Sprachunabhängige Prompts** ✓
   - System-Prompt auf Englisch umgestellt
   - Funktioniert jetzt mit Eingaben in beliebigen Sprachen

2. **Direkte ID-Auflösung durch LLM** ✓
   - Das LLM gibt jetzt direkt `employee_id` zurück (nicht `employee_name`)
   - Keine nachgelagerte Namensauflösung in Python mehr nötig
   - Reduziert Fehlerquellen erheblich

3. **Verbessertes Mitarbeiter-Matching**
   - Mitarbeiterliste wird klar formatiert an LLM übergeben
   - Format: `"Name: Max Mustermann, ID: 12345, Skills: [Sales, Cashier]"`
   - LLM validiert IDs gegen die verfügbare Liste

4. **Rückwärtskompatibilität**
   - `apply_intents()` unterstützt beide Formate:
     - `employee_id` (von LLM)
     - `employee_name` (vom regelbasierten Parser)

### JSON-Schema vom LLM:
```json
{
  "intents": [
    {
      "type": "add_absence",
      "employee_id": "12345",  // Direkt die ID, nicht der Name!
      "from_date": "2025-09-22",
      "to_date": "2025-09-22",
      "times": ["00:00-24:00"]
    }
  ],
  "notes": ["Interpretation notes"]
}
```

---

## Ursprüngliche Fixes: 2025-10-02 (10:35 Uhr)

## Identifizierte und behobene Probleme:

### 1. LLM-Timeout zu kurz (KRITISCH)
**Problem**: Der HTTP-Client hatte nur 5 Sekunden Timeout für LLM-Anfragen.
**Lösung**: Timeout auf 30 Sekunden erhöht in `app/services/llm.py`.
```python
self._client = httpx.Client(timeout=30.0)  # Vorher: timeout=5
```

### 2. Fehlerbehandlung im LLM-Service
**Problem**: Fehler wurden verschluckt und nicht geloggt.
**Lösung**: Explizite Exception-Handling mit Logging für Timeout, HTTP-Fehler und andere Exceptions.

### 3. Fehlerbehandlung in chat_intents.py
**Problem**: Fehler wurden nicht deutlich geloggt.
**Lösung**: 
- Print-Statements für Debugging hinzugefügt
- Bessere Fehlermeldungen mit Exception-Typen

### 4. Schwaches Mitarbeiter-Matching
**Problem**: Nur einfacher Substring-Match, der z.B. "Knut" nicht findet, wenn nur "Knut Müller" in der DB ist.
**Lösung**: Implementierung eines mehrstufigen Matching-Systems:
1. **Exakte Übereinstimmung** (höchste Priorität)
2. **Substring-Match** (mittlere Priorität)
3. **Fuzzy-Match** mit Edit-Distance (niedrige Priorität)

### 5. Chat-Endpoint Fehlerbehandlung
**Problem**: Keine Validierung, ob Daten geladen wurden; schlechte Fehlermeldungen.
**Lösung**:
- Prüfung, ob Daten verfügbar sind
- Klare Fehlermeldungen, wenn keine Intents erkannt wurden
- Telemetrie-Events für Chat-Operationen
- Try-Catch mit Stack-Trace-Logging

### 6. UI-Fehleranzeige
**Problem**: Fehler wurden nicht deutlich angezeigt.
**Lösung**:
- Farbcodierung (rot für Fehler, grün für Erfolg)
- Anzeige von Notes und Logs auch bei Fehlern
- Input-Feld wird bei Erfolg geleert
- Erkannte Intents werden im Event-Log angezeigt

## Debug-Hinweise:

### Console-Logs prüfen
Die Anwendung gibt jetzt detaillierte Debug-Informationen aus:
```bash
# Beim Start des Servers sollten folgende Logs erscheinen:
- "Sending message to LLM: ..."
- "LLM raw response: ..."
- "LLM parsed X intents"
- "Exact/Substring/Fuzzy match found for 'Name': ID"
- "Chat request: '...' with X employees"
```

### Häufige Probleme und Lösungen:

1. **"Konnte Mitarbeiter nicht eindeutig auflösen"**
   - Prüfen: Ist der Mitarbeiter in der hochgeladenen Excel?
   - Prüfen: Sind die Namen korrekt geschrieben?
   - Console-Log zeigt verfügbare Mitarbeiter

2. **"LLM-Fehler"**
   - Prüfen: Sind die Scaleway API-Credentials korrekt in .env?
   - Prüfen: Ist `SHIFTPLAN_USE_LLM_INTENTS=1` gesetzt?
   - Console-Log zeigt den genauen Fehler

3. **"Konnte keine Aktion aus der Nachricht erkennen"**
   - Format prüfen: "Name ist am/bis/vom Datum krank"
   - LLM-Modus prüfen (siehe .env)
   - Console-Log zeigt Notes vom Parser

## Test-Szenarien:

### Regel-basiertes Parsing (SHIFTPLAN_USE_LLM_INTENTS=0):
```
"Knut ist am 22.09.2025 krank"
"Stefan ist bis Freitag krank"
"Maria ist vom 01.10.2025 bis 05.10.2025 krank"
```

### LLM-basiertes Parsing (SHIFTPLAN_USE_LLM_INTENTS=1):
Sollte flexibler sein und auch verstehen:
```
"Knut ist ab morgen 3 Tage krank"
"Maria fällt nächste Woche aus"
```

## Umgebungsvariablen in .env:

```bash
# LLM-Parsing aktivieren (1) oder deaktivieren (0)
SHIFTPLAN_USE_LLM_INTENTS=1

# Scaleway API Credentials
SCW_ACCESS_KEY=...
SCW_SECRET_KEY=...
SCW_BASE_URL=https://api.scaleway.ai/v1
LLM_MODEL=gpt-oss-120b
