/* app.js — F1 Race DALI Dashboard */

let PANES = [];
// clearSnapshot[id] = text that was displayed at last clear → only show new text after it
const clearSnapshot = {};
// Whether auto-scroll is active for each pane
const pinned = {};
// Last full text received from API per pane (for snapshot diff)
const currentText = {};

/* ── Bootstrap: fetch config, build UI, start polling ── */
fetch('/api/config')
  .then(r => r.json())
  .then(cfg => {
    PANES = cfg.panes;
    buildGrid();
    buildAgentSelect();
    poll();
    setInterval(poll, 1000);
  })
  .catch(() => {
    document.getElementById('lbl').textContent = 'config error';
  });

/* ── Build grid panes ─────────────────────────────────── */
function buildGrid() {
  const g = document.getElementById('grid');
  g.innerHTML = '';
  PANES.forEach(p => {
    pinned[p.id] = true;
    currentText[p.id] = '';

    const d = document.createElement('div');
    d.className = 'pane';
    d.style.cssText = `background:${p.color};border-color:${p.border}44`;

    d.innerHTML =
      `<div class="pane-hdr" style="background:${p.border}1a;color:${p.border}">` +
        `<span>${p.label}</span>` +
        `<span class="pane-hdr-btns">` +
          `<span class="pane-btn" id="clear-${p.id}" onclick="clearPane('${p.id}')" title="Clear this pane">&#10005;</span>` +
          `<span class="pane-btn" id="pin-${p.id}"   onclick="togglePin('${p.id}')" title="Toggle auto-scroll">&#8595;</span>` +
        `</span>` +
      `</div>` +
      `<div class="pane-body" id="p-${p.id}"></div>`;

    d.querySelector('.pane-body').addEventListener('scroll', function () {
      pinned[p.id] = this.scrollTop + this.clientHeight >= this.scrollHeight - 20;
      updatePinIcon(p.id);
    });
    g.appendChild(d);
  });
}

function buildAgentSelect() {
  const sel = document.getElementById('tgt');
  sel.innerHTML = '';
  PANES.filter(p => p.id !== 'server').forEach(p => {
    const o = document.createElement('option');
    o.value = p.id;
    o.textContent = p.id;
    sel.appendChild(o);
  });
}

/* ── Pin / scroll helpers ─────────────────────────────── */
function updatePinIcon(id) {
  const el = document.getElementById('pin-' + id);
  if (el) el.style.opacity = pinned[id] ? '.45' : '1';
}
function togglePin(id) {
  pinned[id] = !pinned[id];
  updatePinIcon(id);
  if (pinned[id]) {
    const el = document.getElementById('p-' + id);
    if (el) el.scrollTop = el.scrollHeight;
  }
}

/* ── Clear helpers ────────────────────────────────────── */
function clearPane(id) {
  clearSnapshot[id] = currentText[id] || '';
  const el = document.getElementById('p-' + id);
  if (el) el.textContent = '';
}
function clearAll() {
  PANES.forEach(p => clearPane(p.id));
}

/* Lines filtered from all panes (plain-string match, case-insensitive) */
const FILTERED_LINES = [
  'External event preconditions not verified: no DeltaTime',
  'This is updated list:',
  'This is list without duplicates:',
  'This is list of past event:',
  'This event is first events:',
  'Do not arrive all events',
];

/**
 * Remove noisy DALI internal lines from the output.
 */
function filterNoise(text) {
  return text
    .split('\n')
    .filter(line => !FILTERED_LINES.some(f => line.includes(f)))
    .join('\n');
}

/**
 * Given the full text from tmux, return only the part the user
 * should see (everything after the clear snapshot, if any).
 * If the text has scrolled past the snapshot (old lines dropped
 * off the top of tmux's 400-line buffer), just show everything.
 */
function visibleText(id, full) {
  const snap = clearSnapshot[id];
  if (!snap) return full;
  const idx = full.indexOf(snap);
  if (idx !== -1) return full.slice(idx + snap.length);
  // Snapshot is no longer in the buffer — reset and show all
  delete clearSnapshot[id];
  return full;
}

/* ── Polling ──────────────────────────────────────────── */
let failCount = 0;
function poll() {
  fetch('/api/panes')
    .then(r => r.json())
    .then(data => {
      failCount = 0;
      document.getElementById('led').className = 'on';
      document.getElementById('lbl').textContent = 'live \u2022 1s refresh';

      PANES.forEach(p => {
        const el = document.getElementById('p-' + p.id);
        if (!el) return;
        const full = data[p.id] || '';
        currentText[p.id] = full;
        const text = filterNoise(visibleText(p.id, full));
        const snap = pinned[p.id];
        el.textContent = text;
        if (snap) el.scrollTop = el.scrollHeight;
      });
    })
    .catch(() => {
      if (++failCount > 2) {
        document.getElementById('led').className = 'off';
        document.getElementById('lbl').textContent = 'session offline';
      }
    });
}

/* ── Send helpers ─────────────────────────────────────── */
function post(w, c) {
  return fetch('/api/send', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ window: w, cmd: c }),
  });
}
const wait = ms => new Promise(r => setTimeout(r, ms));
async function seq(...pairs) {
  for (let i = 0; i < pairs.length; i += 2) {
    await post(pairs[i], pairs[i + 1]);
    await wait(300);
  }
}

/* ── Quick action buttons ─────────────────────────────── */
function startRace() { seq('user', 'ferrari.', 'user', 'user.', 'user', 'send_message(start_race, user).'); }
function deploySC()  { seq('user', 'safety_car.', 'user', 'user.', 'user', 'send_message(deploy, user).'); }
function recallSC()  { seq('user', 'safety_car.', 'user', 'user.', 'user', 'send_message(recall, user).'); }

/* ── Custom command bar ───────────────────────────────── */
function doSend() {
  const w = document.getElementById('tgt').value;
  const c = document.getElementById('cmd').value.trim();
  if (!c) return;
  post(w, c);
  document.getElementById('cmd').value = '';
}
