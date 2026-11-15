const schema = [{"name": "attribute", "label": "测量属性", "options": ["strength", "thickness"]}];
const defaults = {"operation": "summary", "attribute": "strength"};
const toleranceDefaults = {"operation": "tolerance", "mode": "claim", "attributes": ["strength"],
  "side": "two-sided", "coverage": 0.95, "confidence": 0.95};
const sources = ["measurements", "specifications", "batches"];
let study = null;
let latestRequest = {...defaults};

async function api(route, payload) {
  const options = payload === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
  };
  const response = await fetch(route, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || String(response.status));
  return result;
}

function display(target, value) {
  document.getElementById(target).textContent = JSON.stringify(value, null, 2);
}

function renderTable(source) {
  const holder = document.getElementById('records');
  holder.replaceChildren();
  const raw = study[source];
  const rows = Array.isArray(raw) ? raw : Object.entries(raw).map(([key, value]) => ({key, value}));
  if (!rows.length) { holder.textContent = '当前集合没有记录'; return; }
  const columns = [...new Set(rows.flatMap(row => Object.keys(row)))];
  const table = document.createElement('table');
  const head = table.createTHead().insertRow();
  for (const column of columns) {
    const cell = document.createElement('th'); cell.textContent = column; head.append(cell);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const rendered = body.insertRow();
    for (const column of columns) {
      const value = row[column];
      rendered.insertCell().textContent = value === null ? '缺失' : typeof value === 'object' ? JSON.stringify(value) : String(value ?? '');
    }
  }
  holder.append(table);
  document.getElementById('record-count').textContent = String(rows.length) + ' 条记录';
}

function renderControls() {
  const form = document.getElementById('action-form');
  for (const control of schema) {
    const label = document.createElement('label'); label.textContent = control.label;
    let input;
    if (control.options) {
      input = document.createElement('select');
      input.multiple = Array.isArray(defaults[control.name]);
      for (const value of control.options) {
        const option = document.createElement('option'); option.value = value; option.textContent = value;
        option.selected = input.multiple ? defaults[control.name].includes(value) : defaults[control.name] === value;
        input.append(option);
      }
    } else {
      input = document.createElement('input'); input.type = 'number'; input.value = defaults[control.name];
    }
    input.name = control.name; label.append(input); form.append(label);
  }
  const button = document.createElement('button'); button.type = 'submit'; button.textContent = "查看精确观测描述";
  form.append(button);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    latestRequest = {operation: defaults.operation};
    for (const control of schema) {
      const field = form.elements.namedItem(control.name);
      latestRequest[control.name] = field.multiple ? [...field.selectedOptions].map(option => option.value)
        : control.options ? field.value : Number(field.value);
    }
    try { display('result', await api('/api/action', latestRequest)); }
    catch (error) { display('result', {error: error.message}); }
  });
}

async function refreshNotes() {
  const result = await api('/api/notes');
  const holder = document.getElementById('notes'); holder.replaceChildren();
  for (const note of result.notes) {
    const item = document.createElement('li'); item.textContent = note.text + ' · ' + note.created_at; holder.append(item);
  }
}

function renderToleranceControls() {
  const form = document.getElementById('tolerance-form');
  const fields = [
    {name: 'attributes', label: '测量属性（可多选）', type: 'multi', options: Object.keys(study.specifications)},
    {name: 'mode', label: '分析方向', type: 'select', options: [
      ['claim', '正向：给定覆盖比例与置信程度，求可支持的声明'],
      ['spec', '反向：给定规格界限，评估覆盖证据']]},
    {name: 'side', label: '区间侧别（正向声明用）', type: 'select', options: [
      ['two-sided', '双侧区间'], ['lower', '下侧界'], ['upper', '上侧界']]},
    {name: 'coverage', label: '目标覆盖比例（0 到 1）', type: 'number'},
    {name: 'confidence', label: '要求置信程度（0 到 1）', type: 'number'}
  ];
  for (const field of fields) {
    const label = document.createElement('label'); label.textContent = field.label;
    let input;
    if (field.type === 'number') {
      input = document.createElement('input');
      input.type = 'number'; input.step = '0.01'; input.min = '0'; input.max = '1';
      input.value = toleranceDefaults[field.name];
    } else {
      input = document.createElement('select');
      input.multiple = field.type === 'multi';
      for (const option of field.options) {
        const [value, text] = Array.isArray(option) ? option : [option, option];
        const node = document.createElement('option'); node.value = value; node.textContent = text;
        const fallback = toleranceDefaults[field.name];
        node.selected = Array.isArray(fallback) ? fallback.includes(value) : fallback === value;
        input.append(node);
      }
    }
    input.name = field.name; input.id = 'tolerance-' + field.name;
    label.append(input); form.append(label);
  }
  const modeField = form.elements.namedItem('mode');
  const sideField = form.elements.namedItem('side');
  const syncSide = () => { sideField.disabled = modeField.value !== 'claim'; };
  modeField.addEventListener('change', syncSide); syncSide();
  const button = document.createElement('button'); button.type = 'submit'; button.textContent = '评估容忍声明';
  form.append(button);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const request = {operation: 'tolerance', mode: modeField.value,
      attributes: [...form.elements.namedItem('attributes').selectedOptions].map(option => option.value),
      coverage: Number(form.elements.namedItem('coverage').value),
      confidence: Number(form.elements.namedItem('confidence').value)};
    if (request.mode === 'claim') request.side = sideField.value;
    latestRequest = request;
    try { display('tolerance-result', await api('/api/action', request)); }
    catch (error) { display('tolerance-result', {error: error.message}); }
  });
}

async function start() {
  study = await api('/api/study');
  display('summary', await api('/api/summary'));
  const selector = document.getElementById('source');
  for (const source of sources) {
    const option = document.createElement('option'); option.value = source; option.textContent = source; selector.append(option);
  }
  selector.addEventListener('change', () => renderTable(selector.value));
  renderTable(sources[0]); renderControls(); renderToleranceControls();
  document.getElementById('note-form').addEventListener('submit', async event => {
    event.preventDefault();
    const field = document.getElementById('note');
    try { await api('/api/notes', {text: field.value, selection: latestRequest}); field.value = ''; await refreshNotes(); }
    catch (error) { display('result', {error: error.message}); }
  });
  await refreshNotes();
}

start().catch(error => display('summary', {error: error.message}));
