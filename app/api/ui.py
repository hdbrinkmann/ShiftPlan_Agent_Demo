from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.telemetry import event_stream

router = APIRouter()

HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>ShiftPlan Agent Monitor</title>
    <style>
      body { font: 14px/1.4 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; margin: 20px; }
      #status { margin-bottom: 10px; }
      .log { background: #f5f5f7; padding: 8px; border-radius: 6px; margin: 4px 0; }
      .active { color: #0070f3; font-weight: 600; }
      table { border-collapse: collapse; margin-top: 8px; }
      th, td { border: 1px solid #ddd; padding: 6px 8px; }
      th { background: #f5f5f7; text-align: left; }
  #result { margin-top: 16px; padding-top: 8px; border-top: 1px solid #eee; }
      #resultMeta { margin-top: 6px; color: #333; }
      .muted { color: #777; }
  .pill { display:inline-block; padding:2px 6px; border-radius: 10px; font-size: 12px; }
  .pill-ok { background:#e6ffed; color:#067d36; border:1px solid #a6f3bf; }
  .pill-warn { background:#fff7e6; color:#8a6d3b; border:1px solid #ffe0a3; }
  #agents { margin-top: 16px; }
  #agents table { border-collapse: collapse; }
  #agents th, #agents td { border:1px solid #ddd; padding:4px 6px; }
  .hidden { display: none; }
    </style>
  </head>
  <body>
    <h1>ShiftPlan Agent Monitor</h1>
    <div>
      <label>Run ID: <input id="runId" value="default" /></label>
      <label>Budget: <input id="budget" type="number" step="0.01" placeholder="optional" /></label>
      <label><input id="autoApprove" type="checkbox" checked /> Auto-approve</label>
      <button id="connect" class="hidden">Connect</button>
      <button id="startRun" class="hidden">Start Run</button>
    </div>
    <div style="margin:8px 0;">
      <form id="uploadForm">
        <input type="file" id="file" accept=".xlsx,.xls" />
        <button id="uploadBtn" type="button">Upload Excel</button>
        <span id="uploadStatus" style="margin-left:8px;color:#555"></span>
      </form>
    </div>
    <div id="status">Not connected.</div>
    <div id="active">Active node: <span class="active" id="node">-</span></div>
    <div id="forecast" style="margin-top:16px; padding-top:8px; border-top:1px solid #eee;">
      <h3>Forecast</h3>
      <button id="runForecast" class="hidden">Run Forecast</button>
      <span id="forecastStatus" class="muted" style="margin-left:8px;"></span>
      <div id="forecastPreview" style="margin-top:8px;"></div>
    </div>
    <div id="agents">
      <h3>Agent details</h3>
      <div class="muted">Letzte Meldung je Agent</div>
      <div id="agentPanel"></div>
    </div>


    <div id="result">
      <h3>Result</h3>
      <div id="resultMeta" class="muted">Noch kein Ergebnis.</div>
      <div style="margin: 10px 0;">
        <button id="viewTimeline" style="padding: 8px 16px; background: #2c5f7c; color: white; border: none; border-radius: 4px; cursor: pointer;">📅 View Timeline</button>
      </div>
      <div id="resultTableWrap"></div>
      <div id="resultStepsWrap"></div>
      <div id="auditWrap"></div>
    </div>
    <div id="chat" style="margin-top:16px; padding-top:8px; border-top:1px solid #eee;">
      <h3>Chat</h3>
      <div class="muted">Beispiel: "Stefan ist bis Freitag krank"</div>
      <input id="chatMsg" placeholder="Nachricht eingeben" style="width:60%;" />
      <button id="chatSend">Senden</button>
      <div id="chatNotes" class="muted" style="margin-top:6px;"></div>
    </div>
    <script>
  const btn = document.getElementById('connect');
  const startBtn = document.getElementById('startRun');
  const runFcBtn = document.getElementById('runForecast');
  const budgetEl = document.getElementById('budget');
  const autoApproveEl = document.getElementById('autoApprove');
  const uploadForm = document.getElementById('uploadForm');
  const uploadBtn = document.getElementById('uploadBtn');
  const fileEl = document.getElementById('file');
  const uploadStatus = document.getElementById('uploadStatus');
      const runInput = document.getElementById('runId');
      const statusEl = document.getElementById('status');
      const nodeEl = document.getElementById('node');
      const eventsEl = document.getElementById('events');
    const resultMetaEl = document.getElementById('resultMeta');
      const resultTableWrap = document.getElementById('resultTableWrap');
  const resultStepsWrap = document.getElementById('resultStepsWrap');
  const auditWrap = document.getElementById('auditWrap');
  const chatInput = document.getElementById('chatMsg');
  const chatSend = document.getElementById('chatSend');
  const chatNotes = document.getElementById('chatNotes');
    const agentPanel = document.getElementById('agentPanel');
    const nodeInsights = {};
  const fcStatus = document.getElementById('forecastStatus');
  const fcPreview = document.getElementById('forecastPreview');
      let es;
      btn.onclick = () => {
        if (es) es.close();
        const runId = encodeURIComponent(runInput.value || 'default');
        es = new EventSource(`/ui/stream/${runId}`);
        es.onopen = () => { statusEl.textContent = 'Connected.'; };
        es.addEventListener('hello', (e) => add(`hello: ${e.data}`));
        es.addEventListener('update', (e) => {
          try {
            const data = JSON.parse(e.data);
            if (data.active_node) {
              nodeEl.textContent = data.active_node;
              // Prefer rich, live messages. Do not overwrite with generic "completed"
              // if we already have a meaningful message.
              const msg = (typeof data.message === 'string' && data.message.trim()) ? data.message : '(update)';
              const prev = nodeInsights[data.active_node];
              const isCompletion = (msg === 'completed');
              const isPlaceholder = (!prev || prev === '-' || prev === '(update)');
              if (!isCompletion || isPlaceholder) {
                nodeInsights[data.active_node] = msg;
                renderAgentPanel();
              }
            }
            if (data.message){
              const prefix = data.active_node ? `[${data.active_node}] ` : '';
              add(prefix + data.message);
            }
          } catch(err) { add('bad event: ' + e.data) }
        });
        es.onerror = (e) => { statusEl.textContent = 'Error / disconnected'; };
      }
      startBtn.onclick = async () => {
        const runId = runInput.value || 'default';
        const body = { run_id: runId, auto_approve: !!autoApproveEl.checked };
        const b = parseFloat(budgetEl.value);
        if (!isNaN(b)) body.budget = b;
        // Clear previous visible result
        resultMetaEl.textContent = 'Berechne...';
        resultTableWrap.innerHTML = '';
        resultStepsWrap.innerHTML = '';
        const res = await fetch('/run', { method:'POST', headers: { 'Content-Type':'application/json' }, body: JSON.stringify(body) });
        const json = await res.json();
        add('Run finished.');
        renderResult(json);
      }
      // Forecast button handler (async with status polling)
      runFcBtn.onclick = async () => {
        fcStatus.textContent = 'Starting forecast...';
        fcStatus.style.color = '#555';
        fcPreview.innerHTML = '';
        runFcBtn.disabled = true;

        let pollTimer = null;
        const stopPolling = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } };

        const renderPreview = (preview) => {
          const arr = Array.isArray(preview) ? preview : [];
          if (!arr.length) {
            fcPreview.innerHTML = '<div class="muted">No preview</div>';
            return;
          }
          // Determine dynamic columns from preview keys
          const keysSet = new Set();
          arr.forEach(r => Object.keys(r || {}).forEach(k => keysSet.add(k)));
          // Ensure Date first, then OpenHours if present, then the rest
          const keys = Array.from(keysSet);
          const hasOpenHours = keys.includes('OpenHours');
          const others = keys.filter(k => k !== 'Date' && k !== 'OpenHours');
          const ordered = ['Date'].concat(hasOpenHours ? ['OpenHours'] : []).concat(others);
          const thead = `<thead><tr>${ordered.map(k => `<th>${k}</th>`).join('')}</tr></thead>`;
          const tbody = `<tbody>${arr.map(r => `<tr>${ordered.map(k => `<td>${(r && (r[k] ?? ''))}</td>`).join('')}</tr>`).join('')}</tbody>`;
          fcPreview.innerHTML = `<table>${thead}${tbody}</table>`;
        };

        try {
          const res = await fetch('/forecast/run', { method: 'POST' });
          const json = await res.json();
          if (!res.ok || json.ok === false) {
            const msg = json.detail || 'Forecast failed to start';
            fcStatus.textContent = 'Error: ' + msg;
            fcStatus.style.color = '#c00';
            add('Forecast error: ' + msg);
            runFcBtn.disabled = false;
            return;
          }
          // Poll for status until done or error
          const poll = async () => {
            try {
              const sres = await fetch('/forecast/status');
              const sjson = await sres.json();
              if (!sres.ok || sjson.ok === false) {
                const msg = sjson.detail || 'Status fetch failed';
                fcStatus.textContent = 'Error: ' + msg;
                fcStatus.style.color = '#c00';
                add('Forecast status error: ' + msg);
                stopPolling();
                runFcBtn.disabled = false;
                return;
              }
              const status = sjson.status || 'idle';
              if (status === 'running') {
                fcStatus.textContent = 'Forecast running...';
                fcStatus.style.color = '#555';
              } else if (status === 'done') {
                const payload = sjson.payload || {};
                const m = payload.metrics || {};
                // Build dynamic metrics string: Role: value pairs
                const parts = Object.keys(m).map(k => `${k}: ${m[k]}`);
                const metricsStr = parts.length ? parts.join(', ') : '-';
                fcStatus.textContent = `Done. Metrics (train MAE) — ${metricsStr}`;
                fcStatus.style.color = '#0a0';
                add('Forecast finished.');
                renderPreview(payload.preview || []);
                stopPolling();
                runFcBtn.disabled = false;
              } else if (status === 'error') {
                const emsg = sjson.error || 'unknown error';
                fcStatus.textContent = 'Error: ' + emsg;
                fcStatus.style.color = '#c00';
                add('Forecast error: ' + emsg);
                stopPolling();
                runFcBtn.disabled = false;
              } else {
                fcStatus.textContent = 'Idle.';
                fcStatus.style.color = '#777';
                stopPolling();
                runFcBtn.disabled = false;
              }
            } catch (e) {
              fcStatus.textContent = 'Error: ' + e.message;
              fcStatus.style.color = '#c00';
              add('Forecast status error: ' + e.message);
              stopPolling();
              runFcBtn.disabled = false;
            }
          };
          // Start polling every 1s
          pollTimer = setInterval(poll, 1000);
          // Also poll once immediately
          poll();
        } catch (err) {
          fcStatus.textContent = 'Error: ' + err.message;
          fcStatus.style.color = '#c00';
          add('Forecast error: ' + err.message);
          runFcBtn.disabled = false;
        }
      };

      // Helpers for auto pipeline: forecast -> connect SSE -> run graph
      async function runForecastPipeline() {
        fcStatus.textContent = 'Starting forecast...';
        fcStatus.style.color = '#555';
        fcPreview.innerHTML = '';

        let pollTimer = null;
        const stopPolling = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } };

        const renderPreview = (preview) => {
          const arr = Array.isArray(preview) ? preview : [];
          if (!arr.length) {
            fcPreview.innerHTML = '<div class="muted">No preview</div>';
            return;
          }
          const keysSet = new Set();
          arr.forEach(r => Object.keys(r || {}).forEach(k => keysSet.add(k)));
          const keys = Array.from(keysSet);
          const hasOpenHours = keys.includes('OpenHours');
          const others = keys.filter(k => k !== 'Date' && k !== 'OpenHours');
          const ordered = ['Date'].concat(hasOpenHours ? ['OpenHours'] : []).concat(others);
          const thead = `<thead><tr>${ordered.map(k => `<th>${k}</th>`).join('')}</tr></thead>`;
          const tbody = `<tbody>${arr.map(r => `<tr>${ordered.map(k => `<td>${(r && (r[k] ?? ''))}</td>`).join('')}</tr>`).join('')}</tbody>`;
          fcPreview.innerHTML = `<table>${thead}${tbody}</table>`;
        };

        const startForecast = async () => {
          const res = await fetch('/forecast/run', { method: 'POST' });
          const json = await res.json();
          if (!res.ok || json.ok === false) {
            const msg = json.detail || 'Forecast failed to start';
            throw new Error(msg);
          }
        };

        const pollOnce = async () => {
          const sres = await fetch('/forecast/status');
          const sjson = await sres.json();
          if (!sres.ok || sjson.ok === false) {
            const msg = sjson.detail || 'Status fetch failed';
            throw new Error(msg);
          }
          const status = sjson.status || 'idle';
          if (status === 'running') {
            fcStatus.textContent = 'Forecast running...';
            fcStatus.style.color = '#555';
            return { done: false };
          } else if (status === 'done') {
            const payload = sjson.payload || {};
            const m = payload.metrics || {};
            const parts = Object.keys(m).map(k => `${k}: ${m[k]}`);
            const metricsStr = parts.length ? parts.join(', ') : '-';
            fcStatus.textContent = `Done. Metrics (train MAE) — ${metricsStr}`;
            fcStatus.style.color = '#0a0';
            add('Forecast finished.');
            renderPreview(payload.preview || []);
            return { done: true, payload };
          } else if (status === 'error') {
            const emsg = sjson.error || 'unknown error';
            fcStatus.textContent = 'Error: ' + emsg;
            fcStatus.style.color = '#c00';
            add('Forecast error: ' + emsg);
            throw new Error(emsg);
          } else {
            fcStatus.textContent = 'Idle.';
            fcStatus.style.color = '#777';
            return { done: true };
          }
        };

        await startForecast();
        return await new Promise((resolve, reject) => {
          pollTimer = setInterval(async () => {
            try {
              const r = await pollOnce();
              if (r.done) { stopPolling(); resolve(r.payload || {}); }
            } catch (e) { stopPolling(); reject(e); }
          }, 1000);
          // initial poll
          pollOnce().then(r => { if (r.done) { stopPolling(); resolve(r.payload || {}); } }).catch(e => { stopPolling(); reject(e); });
        });
      }

      function connectSSEWithRunId(runId) {
        if (es) es.close();
        const rid = encodeURIComponent(runId || 'default');
        es = new EventSource(`/ui/stream/${rid}`);
        es.onopen = () => { statusEl.textContent = 'Connected.'; };
        es.addEventListener('hello', (e) => add(`hello: ${e.data}`));
        es.addEventListener('update', (e) => {
          try {
            const data = JSON.parse(e.data);
            if (data.active_node) {
              nodeEl.textContent = data.active_node;
              // Prefer rich, live messages. Do not overwrite with generic "completed"
              // if we already have a meaningful message.
              const msg = (typeof data.message === 'string' && data.message.trim()) ? data.message : '(update)';
              const prev = nodeInsights[data.active_node];
              const isCompletion = (msg === 'completed');
              const isPlaceholder = (!prev || prev === '-' || prev === '(update)');
              if (!isCompletion || isPlaceholder) {
                nodeInsights[data.active_node] = msg;
                renderAgentPanel();
              }
            }
            if (data.message){
              const prefix = data.active_node ? `[${data.active_node}] ` : '';
              add(prefix + data.message);
            }
          } catch(err) { add('bad event: ' + e.data) }
        });
        es.onerror = (e) => { statusEl.textContent = 'Error / disconnected'; };
      }

      async function runGraphWithRunId(runId) {
        const body = { run_id: runId || 'default', auto_approve: !!autoApproveEl.checked };
        const b = parseFloat(budgetEl.value);
        if (!isNaN(b)) body.budget = b;
        resultMetaEl.textContent = 'Berechne...';
        resultTableWrap.innerHTML = '';
        resultStepsWrap.innerHTML = '';
        const res = await fetch('/run', { method:'POST', headers: { 'Content-Type':'application/json' }, body: JSON.stringify(body) });
        const json = await res.json();
        add('Run finished.');
        renderResult(json);
        // Fallback sync update: if SSE missed some node updates, populate agent panel from executed steps
        try {
          const steps = Array.isArray(json?.steps) ? json.steps : [];
          if (steps.length){
            steps.forEach(s => {
              const prev = nodeInsights[s];
              if (!prev || prev === '-' || prev === '(update)') {
                nodeInsights[s] = 'completed';
              }
            });
            renderAgentPanel();
          }
        } catch (_e) { /* noop */ }
        return json;
      }

      uploadBtn.onclick = async (e) => {
        e.preventDefault();
        const f = fileEl.files[0];
        if (!f){
          uploadStatus.textContent = 'Bitte zuerst eine Excel-Datei (.xlsx/.xls) auswählen.';
          uploadStatus.style.color = '#c00';
          return;
        }
        const fd = new FormData();
        fd.append('file', f);
        uploadStatus.textContent = 'Uploading...';
        uploadStatus.style.color = '#555';
        uploadBtn.disabled = true;
        try {
          const res = await fetch('/upload', { method:'POST', body: fd });
          const json = await res.json();
          if (!res.ok) throw new Error(json.detail || 'Upload failed');
          uploadStatus.textContent = `Uploaded. rows: emp=${json.counts.employees}, abs=${json.counts.absences}, demand=${json.counts.demand}`;
          uploadStatus.style.color = '#0a0';
          add('Upload success');
          // Verify what backend has stored
          try {
            const insp = await fetch('/inspect').then(r=>r.json());
            add('Store counts -> employees: ' + insp.counts.employees + ', absences: ' + insp.counts.absences + ', demand: ' + insp.counts.demand);
          } catch(e) { /* ignore */ }

          // Auto pipeline: forecast -> connect SSE -> run agentic flow
          try {
            await runForecastPipeline();
            const rid = String(Date.now());
            runInput.value = rid;
            connectSSEWithRunId(rid);
            await runGraphWithRunId(rid);
          } catch(e) {
            add('Auto pipeline error: ' + (e && e.message ? e.message : e));
          }
        } catch(err){
          uploadStatus.textContent = 'Error: ' + err.message;
          uploadStatus.style.color = '#c00';
        }
        uploadBtn.disabled = false;
      }

      chatSend.onclick = async () => {
        const runId = runInput.value || 'default';
        const msg = (chatInput.value || '').trim();
        if (!msg){
          chatNotes.textContent = 'Bitte eine Nachricht eingeben.';
          chatNotes.style.color = '#c00';
          return;
        }
        chatNotes.textContent = 'Sende...';
        chatNotes.style.color = '#555';
        try {
          const res = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ run_id: runId, message: msg, auto_approve: !!autoApproveEl.checked })
          });
          const json = await res.json();
          if (!res.ok || json.ok === false){
            const errorMsg = json.error || json.detail || 'Chat failed';
            chatNotes.textContent = 'Fehler: ' + errorMsg;
            chatNotes.style.color = '#c00';
            // Show notes even on error
            if (json.notes && json.notes.length > 0) {
              chatNotes.textContent += ' | Info: ' + json.notes.join(' | ');
            }
            if (json.apply_logs && json.apply_logs.length > 0) {
              chatNotes.textContent += ' | Logs: ' + json.apply_logs.join(' | ');
            }
            add('Chat Fehler: ' + errorMsg);
            return;
          }
          add('Chat angewendet: ' + msg);
          const allNotes = (json.notes || []).concat(json.apply_logs || []);
          if (allNotes.length > 0) {
            chatNotes.textContent = allNotes.join(' | ');
            chatNotes.style.color = '#0a0';
          } else {
            chatNotes.textContent = 'Erfolgreich angewendet';
            chatNotes.style.color = '#0a0';
          }
          // Show parsed intents for debugging
          if (json.intents && json.intents.length > 0) {
            add('Erkannte Intents: ' + JSON.stringify(json.intents));
          }
          renderResult(json);
          chatInput.value = ''; // Clear input on success
        } catch(err){
          chatNotes.textContent = 'Fehler: ' + err.message;
          chatNotes.style.color = '#c00';
          add('Chat Fehler: ' + err.message);
        }
      };
      function add(msg){
        if (!eventsEl) return;
        const div = document.createElement('div');
        div.className = 'log';
        div.textContent = msg;
        eventsEl.prepend(div);
      }

      function renderResult(data){
        const kpis = (data && data.kpis) || {};
        const status = data && data.status ? data.status : '';
        const cost = (kpis.cost !== undefined) ? kpis.cost : '-';
        const coverage = (kpis.coverage !== undefined) ? kpis.coverage : '-';
        const budget = (kpis.budget !== undefined) ? kpis.budget : undefined;
        const overBudget = (budget !== undefined && cost !== '-' && Number(cost) > Number(budget));
        const budgetHtml = (budget !== undefined)
          ? ` | <b>Budget:</b> ${budget} ` + (overBudget ? `<span class="pill pill-warn">over budget</span>` : `<span class="pill pill-ok">within budget</span>`) 
          : '';
        resultMetaEl.innerHTML = `<b>Status:</b> ${status} | <b>Cost:</b> ${cost} | <b>Coverage:</b> ${coverage}${budgetHtml}`;

        // Check if we have consolidated shifts (employee-centric view)
        const shifts = (data && data.solution && Array.isArray(data.solution.shifts)) ? data.solution.shifts : [];
        const assignments = (data && data.solution && Array.isArray(data.solution.assignments)) ? data.solution.assignments : [];
        
        if (!shifts.length && !assignments.length){
          resultTableWrap.innerHTML = '<div class="muted">Keine Zuweisungen erzeugt.</div>';
        } else if (shifts.length) {
          // Employee-centric view with consolidated shifts
          let rows = shifts.map(s => {
            const day = s.day ?? '';
            const empName = s.employee_name ?? s.employee_id ?? '';
            const empId = s.employee_id ?? '';
            const role = s.role ?? '';
            const start = (s.shift_start ?? '').substring(0, 5); // HH:MM
            const end = (s.shift_end ?? '').substring(0, 5); // HH:MM
            const time = `${start}-${end}`;
            const hours = s.hours ?? '';
            const cost = s.cost ?? '';
            return `<tr><td>${day}</td><td>${empName} (${empId})</td><td>${role}</td><td>${time}</td><td>${hours}</td><td>${cost}</td></tr>`;
          }).join('');
          const table = `
            <table>
              <thead>
                <tr><th>Day</th><th>Employee</th><th>Role</th><th>Shift (From-To)</th><th>Hours</th><th>Cost</th></tr>
              </thead>
              <tbody>${rows}</tbody>
            </table>`;
          resultTableWrap.innerHTML = table;
        } else {
          // Fallback: raw assignments view
          let rows = assignments.map(a => {
            const day = a.day ?? '';
            const time = a.time ?? '';
            const role = a.role ?? '';
            const emp = a.employee_id ?? '';
            const hours = a.hours ?? '';
            const cph = a.cost_per_hour ?? '';
            return `<tr><td>${day}</td><td>${time}</td><td>${role}</td><td>${emp}</td><td>${hours}</td><td>${cph}</td></tr>`;
          }).join('');
          const table = `
            <table>
              <thead>
                <tr><th>Day</th><th>Time</th><th>Role</th><th>Employee</th><th>Hours</th><th>Cost/h</th></tr>
              </thead>
              <tbody>${rows}</tbody>
            </table>`;
          resultTableWrap.innerHTML = table;
        }

        const steps = Array.isArray(data?.steps) ? data.steps : [];
        if (steps.length){
          resultStepsWrap.innerHTML = '<h4>Executed Steps</h4><ol>' + steps.map(s => `<li>${s}</li>`).join('') + '</ol>';
        } else {
          resultStepsWrap.innerHTML = '';
        }

        // Audit details
        const violations = (data && data.audit && Array.isArray(data.audit.violations)) ? data.audit.violations : [];
        if (violations.length){
          const rows = violations.map(v => {
            const type = v.type ?? '';
            const day = v.day ?? '';
            const time = v.time ?? '';
            const role = v.role ?? '';
            const req = v.required ?? '';
            const act = v.actual ?? '';
            const sev = v.severity ?? '';
            return `<tr><td>${type}</td><td>${sev}</td><td>${day}</td><td>${time}</td><td>${role}</td><td>${req}</td><td>${act}</td></tr>`;
          }).join('');
          auditWrap.innerHTML = `
            <h4>Audit</h4>
            <table>
              <thead>
                <tr><th>Type</th><th>Severity</th><th>Day</th><th>Time</th><th>Role</th><th>Required</th><th>Actual</th></tr>
              </thead>
              <tbody>${rows}</tbody>
            </table>`;
        } else {
          auditWrap.innerHTML = '<h4>Audit</h4><div class="pill pill-ok">No violations</div>';
        }

        // Scroll into view for convenience
        document.getElementById('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
      }

      function renderAgentPanel(){
        const order = ['ingest','rules','demand_step','solve','audit_step','kpi','triage','human_gate','export'];
        const rows = order.map(name => {
          const msg = nodeInsights[name] || '-';
          return `<tr><td>${name}</td><td>${msg}</td></tr>`;
        }).join('');
        agentPanel.innerHTML = `
          <table>
            <thead><tr><th>Agent</th><th>Last message</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>`;
      }
      
      // Timeline view button handler
      document.getElementById('viewTimeline').onclick = () => {
        window.open('/timeline', '_blank');
      };
    </script>
  </body>
  </html>
"""

@router.get("/", response_class=HTMLResponse)
def index():
    return HTML

@router.get("/stream/{run_id}")
async def stream(run_id: str, request: Request):
    # Return Server-Sent Events stream for a given run_id
    return StreamingResponse(event_stream(run_id), media_type="text/event-stream")
