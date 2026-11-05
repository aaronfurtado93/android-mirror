// Android Mirror — front end. Talks to the local server via JSON + Server-Sent Events.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

let state = null;
let logs = [];
const busy = new Set(); // in-flight UI actions, keyed "action:id"

// ------------------------------------------------------------------ API

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: method === 'GET' ? undefined : JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || data.message || `Request failed (${res.status})`);
  return data;
}

/** Runs an action with a busy flag + toast on error. */
async function act(key, fn, okMsg) {
  if (busy.has(key)) return;
  busy.add(key);
  render();
  try {
    const r = await fn();
    if (okMsg) toast(typeof okMsg === 'function' ? okMsg(r) : okMsg, 'ok');
    return r;
  } catch (e) {
    toast(e.message, 'bad');
  } finally {
    busy.delete(key);
    render();
  }
}

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), kind === 'bad' ? 7000 : 3500);
  return el;
}

// ------------------------------------------------------------------ live updates

function connectEvents() {
  const es = new EventSource('/api/events');
  es.addEventListener('state', (e) => {
    state = JSON.parse(e.data);
    state.receivedAt = Date.now();
    $('#conn').classList.remove('down');
    render();
  });
  es.addEventListener('log', (e) => {
    logs.push(JSON.parse(e.data));
    if (logs.length > 400) logs.shift();
    renderLog();
    if (state && state.setup && state.setup.running) renderSetup();
  });
  es.onerror = () => {
    $('#conn').classList.add('down');
    $('#conn').title = 'Lost connection to the Android Mirror server — retrying…';
  };
}

// ------------------------------------------------------------------ helpers

function ago(ts) {
  if (!ts) return 'never';
  const s = Math.round((Date.now() - ts) / 1000);
  if (s < 10) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(ts).toLocaleDateString();
}

const nameOf = (d) => d.nickname || d.deviceName || [d.manufacturer, d.model].filter(Boolean).join(' ') || d.id;

const icons = {
  phone: '<svg class="ico" viewBox="0 0 24 24" style="width:20px;height:20px"><rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M11 18h2"/></svg>',
  play: '<svg class="ico" viewBox="0 0 24 24"><path d="M7 4l13 8-13 8z" fill="currentColor"/></svg>',
  stop: '<svg class="ico" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>',
  gear: '<svg class="ico" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  wrench: '<svg class="ico" viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 0 0 5 5L21 13l-8 8-4-4 1.3-1.3M14.7 6.3L13 4.6a4 4 0 0 0-5.6 5.6l1.7 1.7"/><path d="M3 21l6-6"/></svg>',
  link: '<svg class="ico" viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/></svg>',
  unlink: '<svg class="ico" viewBox="0 0 24 24"><path d="M18.8 13.4l1.7-1.7a5 5 0 0 0-7-7l-1.7 1.7M5.2 10.6l-1.7 1.7a5 5 0 0 0 7 7l1.7-1.7M8 2v3M2 8h3M16 22v-3M22 16h-3"/></svg>',
};

function deviceStatus(d) {
  const s = d.status;
  if (s.mirroring) return ['mirror', 'Mirroring'];
  if (s.restartPending) return ['warn busy', 'Resuming mirror…'];
  if (s.busy) return ['info busy', 'Connecting…'];
  if (s.connected) return ['ok', `Connected · ${s.via === 'usb' ? 'USB' : 'Wi-Fi'}`];
  if (s.unauthorized) return ['warn', 'Approve on phone'];
  if (s.needsPairing) return ['warn', 'Needs pairing'];
  if (s.onNetwork) return ['info', 'On network'];
  if (s.paused) return ['', 'Disconnected'];
  return ['', 'Offline'];
}

// ------------------------------------------------------------------ rendering

function render() {
  if (!state) return;
  renderTools();
  renderBanner();
  renderDevices();
  renderFound();
  renderAddDialog();
  renderTrouble();
  renderSetup();
  renderDev();
  if (!setupAutoOpened && state.tools && state.tools.missing) {
    setupAutoOpened = true;
    if (state.tools.missing.length) openSetup();
  }
}

function renderTools() {
  const t = state.tools || {};
  const tool = (name, i) => {
    if (!i) return '';
    if (i.ok) return `<span class="pill ok" title="${esc(i.path)}">${name} ${esc(i.version)}</span>`;
    return `<button class="pill bad" data-act="setup">${name} ${i.outdated ? 'outdated' : 'missing'}</button>`;
  };
  const pills = [
    tool('adb', t.adb),
    tool('scrcpy', t.scrcpy),
    state.mdns.mode === 'off' ? `<span class="pill warn" title="${esc(state.mdns.error)}">auto-discovery off</span>` : `<span class="pill ok" title="${esc(state.mdns.interfaces.join(', '))}">auto-discovery on</span>`,
    state.network.length ? `<span class="pill">${esc(state.network.map((n) => n.address).join(', '))}</span>` : `<span class="pill bad">no network</span>`,
  ];
  $('#tools').innerHTML = pills.join('');
}

function renderBanner() {
  const t = state.tools || {};
  const su = state.setup || {};
  const msgs = [];
  if (su.running) msgs.push(['', `<b>Installing ${esc((t.missing || []).join(' and '))}…</b> <a href="#" data-act="setup">Show progress</a>`]);
  else if (t.missing && t.missing.length) {
    const what = t.missing.map((n) => (t[n].outdated ? `${n} is out of date` : `${n} is not installed`)).join(' and ');
    msgs.push(['bad', `<span><b>Almost ready: ${esc(what)}.</b></span> <button class="btn sm primary" data-act="setup">Set up now</button>`]);
  }
  if (t.adb && t.adb.ok && t.display === false) msgs.push(['', 'The server was started without a desktop session, so mirror windows may not open. Start it from your desktop (app menu → Android Mirror).']);
  $('#banner').innerHTML = msgs.map(([k, m]) => `<div class="banner ${k}">${m}</div>`).join('');
}

// ------------------------------------------------------------------ about & credits

async function openAbout() {
  const dlg = $('#about-dialog');
  const body = $('#about-body');
  body.innerHTML = '<p class="muted small">Loading…</p>';
  dlg.showModal();
  let a;
  try {
    a = await api('GET', '/api/about');
  } catch (e) {
    body.innerHTML = `<div class="banner bad">${esc(e.message)}</div>`;
    return;
  }
  const link = (url, text) => (url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text));
  const tools = a.tools.map((t) => `
    <div class="credit">
      <div><b>${link(t.url, t.name)}</b>${t.version ? ` <span class="muted small">${esc(t.version)}</span>` : ''}
        <div class="small muted">${esc(t.role)} By ${esc(t.author)}.${t.path ? ` Using <code>${esc(t.path)}</code>.` : ' Not installed.'}</div></div>
      <span class="lic">${esc(t.license)}</span>
    </div>`).join('');
  const libs = a.libraries.map((l) => `
    <tr><td>${link(l.url, l.name)}${l.direct ? '' : ' <span class="muted small">(indirect)</span>'}</td>
      <td class="muted">${esc(l.version)}</td><td><span class="lic">${esc(l.license)}</span></td></tr>`).join('');
  body.innerHTML = `
    <div class="about-head">
      <img src="icon.svg" alt="" width="56" height="56" data-act="dev-tap" class="dev-tap">
      <div><h2>${esc(a.name)}</h2>
        <div class="muted small">Version ${esc(a.version)} · ${esc(a.license)} license · ${esc(a.copyright)}</div>
        <div class="muted small">${esc(a.python)} · ${esc(a.platform)}</div></div>
    </div>
    <h4>Built on</h4>
    <p class="small muted">Android Mirror is a control panel; the hard work is done by these projects. They are separate programs with their own licenses and are not included in this app.</p>
    ${tools}
    <h4>Python libraries</h4>
    <table class="credits-table"><thead><tr><th>Library</th><th>Version</th><th>License</th></tr></thead><tbody>
      ${libs}
      <tr><td>${link('https://www.python.org', 'Python')}</td><td class="muted">${esc(a.python.split(' ').pop())}</td><td><span class="lic">${esc(a.pythonLicense)}</span></td></tr>
    </tbody></table>
    <p class="small muted">Thank you to everyone who builds and maintains these projects.</p>
    <details><summary>Android Mirror license (${esc(a.license)})</summary><pre class="license">${esc(a.licenseText)}</pre></details>
    <p class="small muted">Remembered devices are stored in <code>${esc(a.dataDir)}</code>.</p>`;
}

// ------------------------------------------------------------------ developer options (easter egg)
// Like Android's "tap Build number 7 times": tap the app icon in About 7 times.

const DEV_TAPS = 7;
let devTaps = 0;
let devTapTimer = null;
let devMode = false;
let devToast = null;
const devSay = (msg, kind) => {
  if (devToast) devToast.remove(); // like Android: each tap replaces the previous message
  devToast = toast(msg, kind);
};
try {
  devMode = localStorage.getItem('devMode') === '1';
} catch {}

function setDevMode(on) {
  devMode = on;
  try {
    if (on) localStorage.setItem('devMode', '1');
    else localStorage.removeItem('devMode');
  } catch {}
  $('#menu-dev').hidden = !on;
}

function devTap(el) {
  el.classList.remove('tapped');
  void el.offsetWidth; // restart the animation
  el.classList.add('tapped');
  if (devMode) {
    devSay('No need, you are already a developer.');
    return;
  }
  clearTimeout(devTapTimer);
  devTapTimer = setTimeout(() => (devTaps = 0), 3000);
  devTaps += 1;
  const left = DEV_TAPS - devTaps;
  if (left <= 0) {
    devTaps = 0;
    setDevMode(true);
    el.classList.add('unlocked');
    devSay('You are now a developer!', 'ok');
  } else if (devTaps >= 3) {
    devSay(`You are now ${left} step${left === 1 ? '' : 's'} away from being a developer.`);
  }
}

const devDlg = $('#dev-dialog');
const devOpenDetails = new Set();
let devCommands = {};

async function openDev() {
  devDlg.showModal();
  devCommands = {};
  renderDev();
  await Promise.all((state.devices || []).map(async (d) => {
    try {
      devCommands[d.id] = await api('GET', `/api/devices/${encodeURIComponent(d.id)}/command`);
    } catch (e) {
      devCommands[d.id] = { error: e.message };
    }
  }));
  renderDev();
}

function renderDev() {
  if (!devDlg.open || !state) return;
  $$('#dev-body details[data-key]').forEach((d) => (d.open ? devOpenDetails.add(d.dataset.key) : devOpenDetails.delete(d.dataset.key)));
  const open = (k) => (devOpenDetails.has(k) ? 'open' : '');
  const t = state.tools || {};
  const rows = [];
  for (const d of state.devices) {
    for (const tr of d.status.transports) rows.push([tr.serial, tr.state, tr.kind, nameOf(d)]);
  }
  for (const tr of state.unknownTransports) rows.push([tr.serial, tr.state, tr.kind, tr.model || '(not remembered)']);
  const table = (head, body) => `<table class="credits-table"><thead><tr>${head.map((h) => `<th>${h}</th>`).join('')}</tr></thead><tbody>${
    body.length ? body.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${head.length}" class="muted">none</td></tr>`
  }</tbody></table>`;
  const cmds = (state.devices || []).map((d) => {
    const c = devCommands[d.id];
    if (!c) return `<div class="small muted">${esc(nameOf(d))}: loading…</div>`;
    if (c.error) return `<div class="small muted">${esc(nameOf(d))}: ${esc(c.error)}</div>`;
    const line = c.command.map((a) => (/[\s'"$]/.test(a) ? `'${a.replace(/'/g, `'\\''`)}'` : a)).join(' ');
    const full = `ADB=${c.adb} ${line}`;
    return `<div><b class="small">${esc(nameOf(d))}</b>${c.connected ? '' : ' <span class="muted small">(not connected)</span>'}
      <div class="cmd"><code>${esc(full)}</code><button class="btn sm" data-act="copy" data-text="${esc(full)}">Copy</button></div></div>`;
  }).join('');
  $('#dev-body').innerHTML = `
    <p class="small muted">Under the hood, live. Turn developer mode off at the bottom.</p>
    <h4>adb transports</h4>
    ${table(['Serial', 'State', 'Kind', 'Device'], rows.map((r) => r.map(esc)))}
    <h4>mDNS (${esc(state.mdns.mode)})</h4>
    <p class="small muted">${esc(state.mdns.interfaces.join(', ') || 'no interfaces')}${state.mdns.error ? ` · ${esc(state.mdns.error)}` : ''}</p>
    ${table(['Instance', 'Kind', 'Target', 'Seen'], (state.discovered || []).filter((f) => f.source === 'mdns').map((f) => [esc(f.instance), esc(f.kind), esc(f.target), esc(ago(f.lastSeen))]))}
    <h4>Tools</h4>
    ${table(['Tool', 'Version', 'Source', 'Path'], ['adb', 'scrcpy'].map((n) => [n, esc((t[n] || {}).version || '—'), esc((t[n] || {}).source || ''), `<code>${esc((t[n] || {}).path || '')}</code>`]))}
    <h4>Mirror command</h4>
    ${cmds || '<p class="small muted">No remembered devices.</p>'}
    <details data-key="state" ${open('state')}><summary>Raw state</summary><pre class="license">${esc(JSON.stringify(state, null, 2))}</pre></details>
    <div class="dlg-foot">
      <button class="btn" data-act="copy" data-text="${esc(JSON.stringify(state, null, 2))}">Copy state JSON</button>
      <button class="btn danger" data-act="dev-off">Turn off developer mode</button>
    </div>`;
}

// ------------------------------------------------------------------ setup / dependencies

const setupDlg = $('#setup-dialog');
let setupAutoOpened = false;
let setupStartedAt = 0;

function openSetup() {
  if (!setupDlg.open) setupDlg.showModal();
  renderSetup();
}

function renderSetup() {
  if (!setupDlg.open) return;
  const t = state.tools || {};
  const su = state.setup || {};
  if (!t.adb) return;
  const row = (name, label, i) => {
    let st;
    if (i.ok) st = `<span class="pill ok">${esc(i.version)}</span>`;
    else if (i.outdated) st = `<span class="pill warn">${esc(i.version)} · needs ${esc(i.minimum)}+</span>`;
    else st = '<span class="pill bad">missing</span>';
    return `<div class="dep"><div><b>${name}</b><div class="small muted">${label}${i.path ? ` · <code>${esc(i.path)}</code>` : ''}</div></div>${st}</div>`;
  };
  const methods = (t.methods || []).map((m) => {
    const covers = m.covers.length ? `Installs ${m.covers.join(' + ')}` : 'Not available';
    const mine = su.running && su.method === m.id;
    const action = m.runnable && m.covers.length
      ? `<button class="btn ${m.recommended ? 'primary' : ''} ${mine ? 'busy' : ''}" data-act="install-deps" data-method="${esc(m.id)}" ${su.running ? 'disabled' : ''}>${mine ? '' : 'Install'}</button>`
      : '';
    const cmd = m.terminal && m.covers.length
      ? `<div class="cmd"><code>${esc(m.terminal)}</code><button class="btn sm" data-act="copy" data-text="${esc(m.terminal)}">Copy</button></div>`
      : '';
    return `<div class="method ${m.recommended ? 'rec' : ''}">
      <div class="method-head"><div><b>${esc(m.label)}</b>${m.recommended ? ' <span class="pill ok">recommended</span>' : ''}<div class="small muted">${esc(covers)}. ${esc(m.description || '')}</div></div>${action}</div>
      ${cmd}
      ${m.note ? `<div class="small muted">${esc(m.note)}</div>` : ''}
    </div>`;
  }).join('');
  const recent = setupStartedAt && (su.running || su.result)
    ? logs.filter((l) => l.ts >= setupStartedAt - 1000).slice(-8).map((l) => `<div class="${esc(l.level)}">${esc(l.msg)}</div>`).join('')
    : '';
  const result = su.result && !su.running ? `<div class="banner ${su.result.ok ? 'ok' : 'bad'}">${esc(su.result.message)}</div>` : '';
  const none = '<p class="small">No automatic method for this system. Install <b>adb</b> (android-tools) and <b>scrcpy</b> with your package manager, then press <i>Check again</i>.</p>';
  $('#setup-body').innerHTML = `
    <p class="small muted">${esc(t.platform || '')}</p>
    ${row('adb', 'Android Debug Bridge: talks to the phone', t.adb)}
    ${row('scrcpy', 'Shows and controls the phone screen', t.scrcpy)}
    ${t.display === false ? '<div class="banner">No desktop session detected: mirror windows can only open when the server runs inside your desktop session.</div>' : ''}
    ${result}
    ${t.ready ? '<div class="banner ok"><b>All set.</b> adb and scrcpy are installed.</div>' : `<h4>Install what's missing</h4>${methods || none}`}
    ${recent ? `<div class="log setup-log">${recent}</div>` : ''}
    <div class="dlg-foot"><button class="btn ${busy.has('setup-check') ? 'busy' : ''}" data-act="setup-check">Check again</button></div>`;
}

function renderDevices() {
  const grid = $('#device-grid');
  const devs = state.devices;
  if (!devs.length) {
    const spotted = state.discovered.filter((f) => f.kind === 'connect').length;
    grid.innerHTML = `<div class="empty">
      ${icons.phone}
      <h3>No devices yet</h3>
      <p>Pair your phone once over Wi-Fi with a QR code and it will be remembered. After that it reconnects on its own whenever Wireless debugging is on.</p>
      ${spotted ? `<p><b>${spotted} phone${spotted > 1 ? 's' : ''} with Wireless debugging spotted below.</b> If you've paired before, just press Connect.</p>` : ''}
      <button class="btn primary" data-act="add">Add your first device</button>
    </div>`;
    return;
  }
  grid.innerHTML = devs.map(deviceCard).join('');
}

function deviceCard(d) {
  const s = d.status;
  const [cls, label] = deviceStatus(d);
  const sub = [d.manufacturer, d.model, d.androidVersion && `Android ${d.androidVersion}`].filter(Boolean).join(' · ');
  const addr = s.transport && s.via === 'wifi' ? s.transport : s.onNetwork || (d.lastIp ? `${d.lastIp}:${d.lastPort ?? ''}` : '—');
  const b = (k) => busy.has(`${k}:${d.id}`);
  let primary;
  if (s.mirroring || s.restartPending) primary = `<button class="btn danger" data-act="stop" data-id="${esc(d.id)}">${icons.stop}Stop mirroring</button>`;
  else if (s.connected) primary = `<button class="btn primary ${b('mirror') ? 'busy' : ''}" data-act="mirror" data-id="${esc(d.id)}" ${b('mirror') ? 'disabled' : ''}>${b('mirror') ? '' : icons.play}Start mirroring</button>`;
  else primary = `<button class="btn primary ${b('mirror') ? 'busy' : ''}" data-act="mirror" data-id="${esc(d.id)}" ${b('mirror') || s.busy ? 'disabled' : ''}>${b('mirror') ? '' : icons.play}Connect &amp; mirror</button>`;

  const secondary = s.connected
    ? `<button class="btn" data-act="disconnect" data-id="${esc(d.id)}" title="Disconnect (pauses auto-reconnect)">${icons.unlink}Disconnect</button>`
    : `<button class="btn ${b('connect') || s.busy ? 'busy' : ''}" data-act="connect" data-id="${esc(d.id)}" ${b('connect') || s.busy ? 'disabled' : ''}>${b('connect') || s.busy ? '' : icons.link}Connect</button>`;
  const trouble = d.troubleshoot?.running;
  const needsHelp = !s.connected && (s.lastError || s.unauthorized);
  const usbWifi = s.via === 'usb' ? `<button class="btn sm" data-act="usb-wifi" data-serial="${esc(s.transport)}">Switch to Wi-Fi</button>` : '';

  let retry = '';
  if (!s.connected && s.nextRetry != null && d.autoConnect) retry = ` · retrying in ${Math.max(1, Math.ceil((s.nextRetry - (Date.now() - state.receivedAt)) / 1000))}s`;

  return `<article class="card ${s.connected ? 'live' : ''}">
    <div class="card-top">
      <div class="phone-ico">${icons.phone}</div>
      <div class="card-title">
        <h3 title="${esc(d.id)}">${esc(nameOf(d))}</h3>
        <div class="sub">${esc(sub || d.id)}</div>
      </div>
      <span class="status ${cls}"><span class="dot"></span>${esc(label)}</span>
    </div>
    <dl class="meta">
      <dt>Address</dt><dd>${esc(addr)}${s.onNetwork && !s.connected ? ' <span class="muted">(seen now)</span>' : ''}</dd>
      <dt>${s.connected ? 'Connected' : 'Last connected'}</dt><dd>${s.connected ? 'now' : d.lastConnected ? esc(ago(d.lastConnected)) : 'not yet'}${esc(s.needsPairing ? '' : retry)}</dd>
      ${s.mirroring ? `<dt>Mirroring</dt><dd>since ${esc(new Date(s.mirroring.since).toLocaleTimeString())}</dd>` : ''}
    </dl>
    ${s.unauthorized
      ? `<div class="err">Unlock the phone and tap <b>Allow</b> on the debugging prompt.</div>`
      : s.needsPairing
        ? `<div class="err">The phone is on the network but no longer trusts this computer (pairing was reset). <button class="btn sm primary" data-act="add" data-tab="qr">Pair again</button></div>`
        : needsHelp && s.lastError ? `<div class="err">${esc(s.lastError)}</div>` : ''}
    ${s.pairingAdvertised && !s.connected ? `<div class="err">Phone is showing a pairing screen. <a href="#" data-act="add" data-tab="code">Pair with code</a></div>` : ''}
    <div class="card-actions">
      ${primary}
      ${secondary}
      ${usbWifi}
      <span class="spacer"></span>
      <button class="btn ${needsHelp ? 'warn' : ''} ${trouble ? 'busy' : ''}" data-act="troubleshoot" data-id="${esc(d.id)}" title="Diagnose &amp; fix the connection">${trouble ? '' : icons.wrench}${needsHelp || trouble ? 'Troubleshoot' : ''}</button>
      <button class="btn icon" data-act="settings" data-id="${esc(d.id)}" title="Settings" aria-label="Settings">${icons.gear}</button>
    </div>
    <div class="card-toggles">
      <label class="switch"><input type="checkbox" data-toggle="autoConnect" data-id="${esc(d.id)}" ${d.autoConnect ? 'checked' : ''}><span></span>Auto-reconnect</label>
      <label class="switch"><input type="checkbox" data-toggle="autoMirror" data-id="${esc(d.id)}" ${d.autoMirror ? 'checked' : ''}><span></span>Mirror on connect</label>
      <label class="switch"><input type="checkbox" data-toggle="keepAlive" data-id="${esc(d.id)}" ${d.keepAlive ? 'checked' : ''}><span></span>Keep alive</label>
    </div>
  </article>`;
}

const KIND_LABEL = { connect: 'Wireless debugging', pairing: 'Ready to pair', legacy: 'ADB', tcpip: 'ADB (port 5555)', 'known-host': 'Remembered device' };

function renderFound() {
  const scan = state.scan || {};
  const prog = $('#scan-progress');
  prog.hidden = !scan.running;
  prog.classList.toggle('indeterminate', !!scan.running && !scan.progress);
  prog.firstElementChild.style.width = scan.progress ? `${Math.round(scan.progress * 100)}%` : '';
  $('#scan-status').textContent = scan.running ? scan.phase : scan.finishedAt ? `Last scan ${ago(scan.finishedAt)} · phones with Wireless debugging on appear here automatically` : 'Phones with Wireless debugging on appear here automatically';
  const btn = $('#btn-scan');
  btn.disabled = !!scan.running;
  btn.classList.toggle('busy', !!scan.running);

  const rows = [];
  for (const t of state.unknownTransports) {
    rows.push(`<div class="row">
      <div class="grow"><div class="kind">${t.kind === 'usb' ? 'USB' : 'adb'}</div><div class="title">${esc(t.model || t.serial)}</div>
      <div class="small muted">${t.state === 'unauthorized' ? 'Tap "Allow" on the phone to authorise this computer.' : t.state === 'no permissions' ? 'No USB permission: install the udev rules for Android (Debian/Ubuntu: sudo apt install android-sdk-platform-tools-common), then replug the cable.' : esc(t.state)}</div></div>
    </div>`);
  }
  for (const f of state.discovered) {
    if (f.deviceId && f.connected) continue; // already shown as connected card
    let action = '';
    const k = `found:${f.key}`;
    if (f.kind === 'pairing') action = `<button class="btn primary sm" data-act="pair-this" data-target="${esc(f.target)}">Pair</button>`;
    else if (f.kind === 'known-host') action = `<button class="btn sm" data-act="troubleshoot" data-id="${esc(f.deviceId)}">Reconnect</button>`;
    else if (!f.connected) action = `<button class="btn ${f.deviceId ? 'primary' : ''} sm ${busy.has(k) ? 'busy' : ''}" data-act="connect-target" data-key="${esc(f.key)}" data-target="${esc(f.target)}">${f.deviceId ? 'Reconnect' : 'Connect'}</button>`;
    else action = `<span class="status ok"><span class="dot"></span>Connected</span>`;
    const title = f.name || (f.serial ? `Phone ${f.serial}` : f.instance || f.ip);
    const desc = f.kind === 'known-host' ? `Seen at ${f.ip} (matched by hardware address)` : `${f.target}${f.kind === 'connect' && !f.deviceId ? ' · works if this computer was paired before, otherwise pair first' : ''}`;
    rows.push(`<div class="row">
      <div class="grow"><div class="kind">${esc(KIND_LABEL[f.kind] || f.kind)}${f.deviceId ? ' · remembered' : ''}</div><div class="title">${esc(title)}</div><div class="small muted">${esc(desc)}</div></div>
      ${action}
    </div>`);
  }
  $('#found-list').innerHTML = rows.length
    ? rows.join('')
    : `<div class="row"><div class="grow muted">Nothing found yet. Turn on <b>Wireless debugging</b> on your phone, or press <b>Scan network</b>.</div></div>`;
}

// ------------------------------------------------------------------ Add-device dialog

const addDlg = $('#add-dialog');
let addTab = 'qr';

function openAdd(tab = 'qr') {
  if (!addDlg.open) addDlg.showModal();
  setTab(tab);
}

function setTab(tab) {
  addTab = tab;
  $$('.tabs button', addDlg).forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  $$('.tab-body', addDlg).forEach((p) => (p.hidden = p.dataset.panel !== tab));
  if (tab === 'qr' && (!state?.pairing || ['expired', 'error', 'done'].includes(state.pairing.status))) startQr();
  renderAddDialog();
}

async function startQr() {
  $('#qr-box').innerHTML = 'Generating…';
  try {
    await api('POST', '/api/pair/qr');
  } catch (e) {
    $('#qr-box').textContent = e.message;
  }
}

addDlg.addEventListener('close', () => {
  if (state?.pairing && !['done'].includes(state.pairing.status)) api('POST', '/api/pair/cancel').catch(() => {});
});

let lastPairingDone = null;
function renderAddDialog() {
  if (!addDlg.open || !state) return;
  const p = state.pairing;
  if (addTab === 'qr') {
    const box = $('#qr-box');
    const st = $('#qr-status');
    if (p?.qrSvg && box.dataset.id !== p.id) {
      box.innerHTML = p.qrSvg;
      box.dataset.id = p.id;
    }
    if (!p) {
      st.className = 'status-line';
      st.innerHTML = '';
    } else {
      const spinning = ['waiting', 'pairing', 'connecting'].includes(p.status);
      st.className = `status-line ${p.status === 'done' ? 'ok' : p.status === 'error' ? 'bad' : ''}`;
      st.innerHTML = `${spinning ? '<span class="sicon running"></span>' : ''}${esc(p.message)}
        ${['expired', 'error'].includes(p.status) ? '<button class="btn sm" data-act="qr-new">New code</button>' : ''}
        ${p.status === 'paired' ? '<button class="btn sm" data-act="tab" data-tab="ip">Enter port</button>' : ''}`;
      if (p.status === 'done' && lastPairingDone !== p.id) {
        lastPairingDone = p.id;
        toast(p.message, 'ok');
        setTimeout(() => addDlg.open && addDlg.close(), 1500);
      }
    }
  }
  if (addTab === 'code') {
    const cands = state.discovered.filter((f) => f.kind === 'pairing');
    $('#pair-candidates').innerHTML = cands.length
      ? cands.map((c) => `<button type="button" class="chip" data-act="fill-pair" data-target="${esc(c.target)}">${esc(c.serial ? `Phone ${c.serial}` : c.instance)} · ${esc(c.target)}</button>`).join('')
      : '<span class="small muted">Waiting for a phone on the "Pair with pairing code" screen… (or type the address)</span>';
  }
  if (addTab === 'usb') {
    const usb = [
      ...state.devices.filter((d) => d.status.transports.some((t) => t.kind === 'usb')).map((d) => ({ name: nameOf(d), id: d.id, t: d.status.transports.find((t) => t.kind === 'usb') })),
      ...state.unknownTransports.filter((t) => t.kind === 'usb').map((t) => ({ name: t.model || t.serial, t })),
    ];
    $('#usb-list').innerHTML = usb.length
      ? usb
          .map(
            (u) => `<div class="row"><div class="grow"><div class="title">${esc(u.name)}</div><div class="small muted">${u.t.state === 'device' ? 'Connected over USB' : u.t.state === 'unauthorized' ? 'Tap “Allow” on the phone' : esc(u.t.state)}</div></div>
          ${u.t.state === 'device' ? `${u.id ? `<button class="btn sm" data-act="mirror" data-id="${esc(u.id)}">Mirror over USB</button>` : ''}<button class="btn primary sm ${busy.has('usbwifi:' + u.t.serial) ? 'busy' : ''}" data-act="usb-wifi" data-serial="${esc(u.t.serial)}">Switch to Wi-Fi</button>` : ''}</div>`,
          )
          .join('')
      : '<div class="row"><div class="grow muted"><span class="sicon running" style="display:inline-block;vertical-align:middle;margin-right:8px"></span>Waiting for a phone on USB…</div></div>';
  }
  if (addTab === 'ip') {
    const hs = state.hostScan;
    const prog = $('#hostscan-progress');
    prog.hidden = !hs?.running;
    if (hs?.running) prog.firstElementChild.style.width = `${Math.round((hs.progress || 0) * 100)}%`;
    $('#btn-find-port').disabled = !!hs?.running;
    $('#btn-find-port').classList.toggle('busy', !!hs?.running);
  }
}

$('.tabs', addDlg).addEventListener('click', (e) => {
  const b = e.target.closest('button[data-tab]');
  if (b) setTab(b.dataset.tab);
});

$('#pair-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const out = $('#pair-result');
  out.className = 'status-line';
  out.innerHTML = '<span class="sicon running"></span>Pairing…';
  try {
    const r = await api('POST', '/api/pair', { target: f.get('target').trim(), code: f.get('code').trim() });
    if (r.connected) {
      out.className = 'status-line ok';
      out.textContent = 'Paired and connected — the device is now remembered.';
      toast('Device added', 'ok');
      setTimeout(() => addDlg.close(), 1400);
    } else {
      out.className = 'status-line';
      out.innerHTML = `${esc(r.message || 'Paired.')} <button class="btn sm" data-act="tab" data-tab="ip">Enter port</button>`;
      $('#ip-form [name=target]').value = f.get('target').split(':')[0] + ':';
    }
  } catch (err) {
    out.className = 'status-line bad';
    out.textContent = err.message;
  }
});

$('#ip-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const target = new FormData(e.target).get('target').trim();
  const out = $('#ip-result');
  out.className = 'status-line';
  out.innerHTML = '<span class="sicon running"></span>Connecting…';
  try {
    const r = await api('POST', '/api/connect', { target });
    if (!r.ok) throw new Error(r.message);
    out.className = 'status-line ok';
    out.textContent = 'Connected — the device is now remembered.';
    setTimeout(() => addDlg.close(), 1200);
  } catch (err) {
    out.className = 'status-line bad';
    out.innerHTML = `${esc(err.message)}<br><span class="small muted">Not paired yet? Use the QR code tab. Only have the IP? Press "Find port".</span>`;
  }
});

$('#btn-find-port').addEventListener('click', async () => {
  const ip = $('#ip-form [name=target]').value.trim().split(':')[0];
  const out = $('#ip-result');
  out.className = 'status-line';
  out.innerHTML = `<span class="sicon running"></span>Scanning ${esc(ip)} for the Wireless debugging port… (can take up to a minute)`;
  try {
    const r = await api('POST', '/api/find-port', { ip });
    if (!r.ok) throw new Error(r.message);
    out.className = 'status-line ok';
    out.textContent = `Found and connected on ${r.target}.`;
    $('#ip-form [name=target]').value = r.target;
    setTimeout(() => addDlg.close(), 1400);
  } catch (err) {
    out.className = 'status-line bad';
    out.textContent = err.message;
  }
});

// ------------------------------------------------------------------ Settings dialog

const setDlg = $('#settings-dialog');
const PRESETS = {
  smooth: { maxSize: 1280, videoBitRate: 4, maxFps: 60, videoCodec: 'h264' },
  balanced: { maxSize: 1920, videoBitRate: 8, maxFps: 60, videoCodec: 'h264' },
  sharp: { maxSize: 0, videoBitRate: 16, maxFps: 60, videoCodec: 'h265' },
};
let editing = null;

function openSettings(id) {
  const d = state.devices.find((x) => x.id === id);
  if (!d) return;
  editing = id;
  $('#settings-title').textContent = `${nameOf(d)} — settings`;
  const form = $('#settings-form');
  for (const el of form.elements) {
    if (!el.name) continue;
    const [scope, key] = el.name.startsWith('s.') ? ['s', el.name.slice(2)] : ['d', el.name];
    const v = scope === 's' ? d.scrcpy[key] : d[key];
    if (el.type === 'checkbox') el.checked = !!v;
    else el.value = v ?? '';
  }
  form.nickname.placeholder = d.deviceName || [d.manufacturer, d.model].filter(Boolean).join(' ') || 'Name';
  setPreset(d.scrcpy.preset || 'custom', false);
  setDlg.showModal();
}

function setPreset(p, apply = true) {
  $$('#presets button').forEach((b) => b.classList.toggle('active', b.dataset.preset === p));
  $('#settings-form').dataset.preset = p;
  if (apply && PRESETS[p]) for (const [k, v] of Object.entries(PRESETS[p])) $('#settings-form').elements['s.' + k].value = v;
}

$('#presets').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-preset]');
  if (b) setPreset(b.dataset.preset);
});
$$('#settings-form [name="s.maxSize"], #settings-form [name="s.videoBitRate"], #settings-form [name="s.maxFps"], #settings-form [name="s.videoCodec"]').forEach((el) =>
  el.addEventListener('input', () => setPreset('custom', false)),
);

$('#settings-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const patch = { scrcpy: { preset: form.dataset.preset } };
  for (const el of form.elements) {
    if (!el.name) continue;
    const s = el.name.startsWith('s.');
    const key = s ? el.name.slice(2) : el.name;
    const v = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value || 0) : el.value;
    (s ? patch.scrcpy : patch)[key] = v;
  }
  await act('save', () => api('PATCH', `/api/devices/${encodeURIComponent(editing)}`, patch), 'Settings saved — applies next time mirroring starts');
  setDlg.close();
});
$('[data-close]', setDlg).addEventListener('click', () => setDlg.close());
$('#btn-forget').addEventListener('click', async () => {
  const d = state.devices.find((x) => x.id === editing);
  const btn = $('#btn-forget');
  if (btn.dataset.confirm !== editing) {
    btn.dataset.confirm = editing;
    btn.textContent = 'Click again to forget';
    setTimeout(() => {
      btn.dataset.confirm = '';
      btn.textContent = 'Forget device';
    }, 3000);
    return;
  }
  btn.dataset.confirm = '';
  btn.textContent = 'Forget device';
  setDlg.close();
  await act('forget', () => api('DELETE', `/api/devices/${encodeURIComponent(editing)}`), `Forgot ${d ? nameOf(d) : 'device'}`);
});

// ------------------------------------------------------------------ Troubleshooter dialog

const trDlg = $('#trouble-dialog');
let troubleId = null;

async function openTrouble(id, run = true) {
  troubleId = id;
  if (!trDlg.open) trDlg.showModal();
  if (run) {
    try {
      await api('POST', `/api/devices/${encodeURIComponent(id)}/troubleshoot`);
    } catch (e) {
      toast(e.message, 'bad');
    }
  }
  renderTrouble();
}

function renderTrouble() {
  if (!trDlg.open || !state) return;
  const d = state.devices.find((x) => x.id === troubleId);
  if (!d) return;
  $('#trouble-title').textContent = `Troubleshoot ${nameOf(d)}`;
  const r = d.troubleshoot;
  const mark = { ok: '✓', warn: '!', fail: '✕', skip: '–', running: '' };
  let html = `<p class="muted small">Checks each part of the connection, finds the phone even if its address changed, and repairs what it can.</p>`;
  if (r) {
    html += `<ul class="tsteps">${r.steps
      .map((s) => `<li><span class="sicon ${s.status}">${mark[s.status] ?? ''}</span><div><div class="t">${esc(s.title)}</div>${s.detail ? `<div class="d">${esc(s.detail)}</div>` : ''}</div></li>`)
      .join('')}</ul>`;
    if (!r.running && r.verdict) {
      html += `<div class="verdict ${r.ok ? 'ok' : 'bad'}"><b>${esc(r.verdict)}</b>
        ${r.advice?.length ? `<ul>${r.advice.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>` : ''}
        <div class="actions">
          ${r.ok ? `<button class="btn primary" data-act="mirror" data-id="${esc(d.id)}">${icons.play}Start mirroring</button>` : ''}
          ${r.action === 'pair' ? `<button class="btn primary" data-act="add" data-tab="qr">Pair again with QR code</button>` : ''}
          ${!r.ok ? `<button class="btn" data-act="troubleshoot-again" data-id="${esc(d.id)}">Run again</button>` : ''}
          ${!r.ok ? `<button class="btn" data-act="add" data-tab="usb">Use USB instead</button>` : ''}
        </div></div>`;
    }
  }
  $('#trouble-body').innerHTML = html;
}

// ------------------------------------------------------------------ Activity log

const drawer = $('#activity');
function renderLog() {
  if (drawer.hidden) return;
  const showDebug = $('#log-debug').checked;
  const box = $('#log');
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  box.innerHTML = logs
    .filter((l) => showDebug || l.level !== 'debug')
    .map((l) => `<div class="${l.level}"><time>${new Date(l.ts).toLocaleTimeString()}</time>${esc(l.msg)}</div>`)
    .join('');
  if (atBottom) box.scrollTop = box.scrollHeight;
}
$('#log-debug').addEventListener('change', renderLog);
$('[data-close-drawer]').addEventListener('click', () => (drawer.hidden = true));

// ------------------------------------------------------------------ global click handling

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act]');
  const menu = $('#more-menu');
  if (!e.target.closest('.menu-wrap')) menu.hidden = true;
  if (!el) return;
  const { act: a, id, target, serial, tab } = el.dataset;
  if (el.tagName === 'A') e.preventDefault();
  const enc = id && encodeURIComponent(id);
  switch (a) {
    case 'add':
      if (trDlg.open) trDlg.close();
      $('#help-dialog').open && $('#help-dialog').close();
      openAdd(tab || 'qr');
      break;
    case 'tab':
      setTab(tab);
      break;
    case 'qr-new':
      startQr();
      break;
    case 'mirror':
      if (trDlg.open) trDlg.close();
      await act(`mirror:${id}`, () => api('POST', `/api/devices/${enc}/mirror`), 'Mirroring — the window opens on this computer');
      break;
    case 'stop':
      await act(`stop:${id}`, () => api('POST', `/api/devices/${enc}/stop`));
      break;
    case 'connect': {
      const r = await act(`connect:${id}`, () => api('POST', `/api/devices/${enc}/connect`), 'Connected');
      if (r === undefined) {
        const d = state.devices.find((x) => x.id === id);
        if (d && !d.status.connected) toast('Tip: press Troubleshoot to find the phone and fix the connection.');
      }
      break;
    }
    case 'disconnect':
      await act(`disconnect:${id}`, () => api('POST', `/api/devices/${enc}/disconnect`), 'Disconnected — auto-reconnect paused');
      break;
    case 'troubleshoot':
      openTrouble(id);
      break;
    case 'troubleshoot-again':
      openTrouble(id, true);
      break;
    case 'settings':
      openSettings(id);
      break;
    case 'connect-target':
      await act(`found:${el.dataset.key}`, async () => {
        const r = await api('POST', '/api/connect', { target });
        if (!r.ok) throw new Error(`${r.message} — if this phone was never paired with this computer, use Add device › QR code.`);
        return r;
      }, 'Connected — device remembered');
      break;
    case 'pair-this':
      openAdd('code');
      $('#pair-form [name=target]').value = target;
      $('#pair-form [name=code]').focus();
      break;
    case 'fill-pair':
      $('#pair-form [name=target]').value = target;
      $('#pair-form [name=code]').focus();
      break;
    case 'usb-wifi':
      await act(`usbwifi:${serial}`, async () => {
        const r = await api('POST', `/api/usb/${encodeURIComponent(serial)}/wifi`);
        if (!r.ok) throw new Error(r.message);
        return r;
      }, 'Now connected over Wi-Fi — you can unplug the cable');
      break;
    case 'activity':
      drawer.hidden = false;
      renderLog();
      break;
    case 'restart-adb':
      await act('adb', () => api('POST', '/api/adb/restart'), 'adb restarted');
      break;
    case 'setup':
      openSetup();
      break;
    case 'setup-check':
      await act('setup-check', () => api('POST', '/api/setup/check'));
      break;
    case 'install-deps':
      setupStartedAt = Date.now();
      await act(`install:${el.dataset.method}`, () => api('POST', '/api/setup/install', { method: el.dataset.method }));
      break;
    case 'copy':
      try {
        await navigator.clipboard.writeText(el.dataset.text);
        toast('Copied. Paste it into a terminal.', 'ok');
      } catch {
        toast('Could not copy; select the command and copy it manually.', 'bad');
      }
      break;
    case 'about':
      openAbout();
      break;
    case 'toggle-window':
      toggleWindowMode();
      break;
    case 'dev-tap':
      devTap(el);
      break;
    case 'dev':
      openDev();
      break;
    case 'dev-off':
      setDevMode(false);
      devDlg.close();
      toast('Developer mode turned off. Tap the icon in About 7 times to turn it back on.');
      break;
    case 'help':
      $('#help-dialog').showModal();
      break;
  }
});

document.addEventListener('change', (e) => {
  const t = e.target.closest('[data-toggle]');
  if (!t) return;
  act(`toggle:${t.dataset.id}`, () => api('PATCH', `/api/devices/${encodeURIComponent(t.dataset.id)}`, { [t.dataset.toggle]: t.checked }));
});

$('#btn-scan').addEventListener('click', () => act('scan', () => api('POST', '/api/scan')));
$('#btn-add').addEventListener('click', () => openAdd('qr'));
$('#btn-more').addEventListener('click', () => ($('#more-menu').hidden = !$('#more-menu').hidden));

// Refresh relative times ("retrying in 12s", "5 min ago").
setInterval(() => {
  if (state && !document.querySelector('.card-toggles input:focus')) renderDevices();
}, 5000);

api('GET', '/api/logs').then((l) => (logs = l)).catch(() => {});
connectEvents();

$('#menu-dev').hidden = !devMode;

// ------------------------------------------------------------------ app window preference

let appSettings = null;

function renderAppSettings() {
  const b = $('#menu-window');
  if (!appSettings) return;
  const w = appSettings.window;
  const on = appSettings.openMode === 'window';
  b.classList.toggle('on', on && w.available);
  b.disabled = !w.available;
  b.title = w.available
    ? `When you open Android Mirror, show it in its own ${w.browser} app window instead of a browser tab.`
    : 'Needs Google Chrome, Chromium, Microsoft Edge or Brave. Opens in your default browser for now.';
}

async function toggleWindowMode() {
  const next = appSettings && appSettings.openMode === 'window' ? 'browser' : 'window';
  const r = await act('app-settings', () => api('PATCH', '/api/app-settings', { openMode: next }));
  if (!r) return;
  appSettings = r;
  renderAppSettings();
  toast(next === 'window' ? `Next time it opens in its own ${r.window.browser} window.` : 'Next time it opens in a browser tab.', 'ok');
}

api('GET', '/api/app-settings').then((r) => {
  appSettings = r;
  renderAppSettings();
}).catch(() => {});

// Deep links: #about, #setup
if (location.hash === '#about') openAbout();
