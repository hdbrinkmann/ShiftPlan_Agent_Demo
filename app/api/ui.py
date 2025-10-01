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
    </style>
  </head>
  <body>
    <h1>ShiftPlan Agent Monitor</h1>
    <div>
      <label>Run ID: <input id="runId" value="default" /></label>
      <label>Budget: <input id="budget" type="number" step="0.01" placeholder="optional" /></label>
      <label><input id="autoApprove" type="checkbox" checked /> Auto-approve</label>
      <button id="connect">Connect</button>
      <button id="startRun">Start Run</button>
    </div>
    <div style="margin:8px 0;">
      <form id="uploadForm">
        <input type="file" id="file" accept=".xlsx,.xls" />
        <button id="uploadBtn" type="submit">Upload Excel</button>
        <span id="uploadStatus" style="margin-left:8px;color:#555"></span>
      </form>
    </div>
    <div id="status">Not connected.</div>
    <div id="active">Active node: <span class="active" id="node">-</span></div>
    <div id="agents">
      <h3>Agent details</h3>
      <div class="muted">Letzte Meldung je Agent</div>
      <div id="agentPanel"></div>
    </div>
    <h3>Events</h3>
    <div id="events"></div>

    <div id="result">
      <h3>Result</h3>
      <div id="resultMeta" class="muted">Noch kein Ergebnis.</div>
      <div id="resultTableWrap"></div>
      <div id="resultStepsWrap"></div>
      <div id="auditWrap"></div>
    </div>
    <script>
  const btn = document.getElementById('connect');
  const startBtn = document.getElementById('startRun');
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
    const agentPanel = document.getElementById('agentPanel');
    const nodeInsights = {};
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
            if (data.active_node) nodeEl.textContent = data.active_node;
            if (data.message){
              const prefix = data.active_node ? `[${data.active_node}] ` : '';
              add(prefix + data.message);
              if (data.active_node){
                nodeInsights[data.active_node] = data.message;
                renderAgentPanel();
              }
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
      uploadForm.onsubmit = async (e) => {
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
        } catch(err){
          uploadStatus.textContent = 'Error: ' + err.message;
          uploadStatus.style.color = '#c00';
        }
        uploadBtn.disabled = false;
      }
      function add(msg){
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

        const assignments = (data && data.solution && Array.isArray(data.solution.assignments)) ? data.solution.assignments : [];
        if (!assignments.length){
          resultTableWrap.innerHTML = '<div class="muted">Keine Zuweisungen erzeugt.</div>';
        } else {
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
