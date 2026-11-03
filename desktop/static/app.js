/* Cinesip Recap — frontend (M1: upload + ingest) */
'use strict';

const $ = (id) => document.getElementById(id);

const state = {
  config: null,
  file: null,
  fileDuration: 0,
  projectId: null,
  upload: null,          // { total, chunkSize, received:Set }
  resume: null,          // { id, name, size } — lets a failed upload continue
  uploading: false,
  pollTimer: null,
  lastProject: null,

  // M3 editor
  edit: null,
  editProject: null,
  editSig: null,
  editGeometry: null,
  editTimeline: null,
  editFont: null,
  editVideoSrc: '',
  editTime: 0,
  editDirty: false,
  editRev: 0,
  editSaving: false,
  editPending: false,
  editSaveTimer: null,
  editLoading: false,
  editLoadToken: 0,
  openToken: 0,
  exact: false,
  exactTimer: null,
  seekTimer: null,
  drag: null,
  stage: null,
  blurSel: -1,
  presets: [],
  voiceUrl: '',
};

const STORE_KEY = 'cinesip:settings';
const ACTIVE_KEY = 'cinesip:activeProject';
const RESUME_KEY = 'cinesip:resumeUpload';

/* ---------- helpers ---------- */

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function bytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return `${n.toFixed(n >= 100 ? 0 : 1)} ${units[i]}`;
}

function hms(seconds) {
  seconds = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = h ? String(m).padStart(2, '0') : String(m);
  return h ? `${h}:${mm}:${String(s).padStart(2, '0')}` : `${mm}:${String(s).padStart(2, '0')}`;
}

function toast(message, ms = 6000) {
  const old = document.querySelector('.toast');
  if (old) old.remove();
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || res.statusText;
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data;
}

/* ---------- settings ---------- */

function loadSaved() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {}; } catch { return {}; }
}

function saveSettings() {
  const settings = collectSettings();
  localStorage.setItem(STORE_KEY, JSON.stringify(settings));
}

function collectSettings() {
  return {
    target_pct: Number($('targetPct').value || 50),
    style: state.selectedStyle || 'style_1_recap',
    fairuse: Number($('fairuse').value || 30),
    language: $('language').value || 'my',
    narrator_id: $('narrator').value || 'F01',
    ratio: $('ratio').value || '16:9',
    resolution: $('resolution').value || '1280:720',
    watermark_text: ($('watermark').value || '').trim(),
    scene_sec: Number($('sceneSec').value || 5),
    voice_rate: Number($('voiceRate').value || 0),
    voice_pitch: Number($('voicePitch').value || 0),
  };
}

function applySettingsToForm(settings) {
  const s = settings || {};
  if (s.target_pct != null) $('targetPct').value = String(s.target_pct);
  if (s.fairuse != null) $('fairuse').value = String(s.fairuse);
  if (s.language) $('language').value = s.language;
  if (s.narrator_id) $('narrator').value = s.narrator_id;
  if (s.ratio) $('ratio').value = s.ratio;
  if (s.resolution) $('resolution').value = s.resolution;
  if (s.watermark_text != null) $('watermark').value = s.watermark_text;
  if (s.scene_sec != null) $('sceneSec').value = String(s.scene_sec);
  if (s.voice_rate != null) $('voiceRate').value = String(s.voice_rate);
  if (s.voice_pitch != null) $('voicePitch').value = String(s.voice_pitch);
  selectStyle(s.style || 'style_1_recap');
  syncVoiceLabels();
  syncFairuseInfo();
  updateEstimate();
  saveSettings();
}

/* ---------- config / selects ---------- */

async function loadConfig() {
  const cfg = await api('/api/config');
  state.config = cfg;
  const d = cfg.defaults || {};
  const saved = loadSaved();

  // target length
  $('targetPct').innerHTML = cfg.target_pcts
    .map((p) => `<option value="${p}">${p}%${p === 50 ? ' · တစ်ဝက်' : ''}</option>`)
    .join('');

  // fairuse
  $('fairuse').innerHTML = (cfg.fairuse_levels || [])
    .map((l) => {
      const tech = (l.techniques || []).join(' + ');
      return `<option value="${l.level}">${l.level} · ${esc(l.label || '')}${tech ? ' · ' + esc(tech) : ''}</option>`;
    })
    .join('');

  // languages
  $('language').innerHTML = (cfg.languages || [])
    .map((l) => `<option value="${esc(l.code)}">${esc(l.label_mm)} (${esc(l.label_en)})</option>`)
    .join('');

  // voices: 10 female + 10 male, no user mapping beyond choosing one
  const women = (cfg.voices || []).filter((v) => v.gender === 'female');
  const men = (cfg.voices || []).filter((v) => v.gender === 'male');
  $('narrator').innerHTML =
    `<optgroup label="အမျိုးသမီး · Female">${
      women.map((v) => `<option value="${esc(v.id)}">${esc(v.id)} · ${esc(v.name)}</option>`).join('')
    }</optgroup>` +
    `<optgroup label="အမျိုးအစား · Male">${
      men.map((v) => `<option value="${esc(v.id)}">${esc(v.id)} · ${esc(v.name)}</option>`).join('')
    }</optgroup>`;

  // ratio / resolution
  $('ratio').innerHTML = cfg.ratios.map((r) => `<option value="${r}">${r}</option>`).join('');
  $('resolution').innerHTML = cfg.resolutions
    .map((r) => `<option value="${r}">${r.replace(':', ' × ')}</option>`).join('');
  ['editRatio', 'editResolution'].forEach((id) => {
    const isRatio = id === 'editRatio';
    const list = isRatio ? cfg.ratios : cfg.resolutions;
    $(id).innerHTML = list
      .map((r) => `<option value="${esc(r)}">${esc(isRatio ? r : r.replace(':', ' × '))}</option>`)
      .join('');
  });

  // scene length (seconds of source shown per scene)
  $('sceneSec').innerHTML = [3, 4, 5, 6, 7, 8, 10, 12, 15]
    .map((n) => `<option value="${n}">${n} စက္ကန့်${n === 5 ? ' · default' : ''}</option>`)
    .join('');

  // caption fonts — only ones whose file actually exists on this machine
  const fontList = (cfg.fonts || []).filter((f) => f.available);
  $('capFont').innerHTML = fontList
    .map((f) => `<option value="${esc(f.id)}" data-css="${esc(f.css || '')}">${esc(f.label)}</option>`)
    .join('');
  state.fontCss = {};
  fontList.forEach((f) => { state.fontCss[f.id] = f.css || ''; });

  // style cards
  $('styleGrid').innerHTML = cfg.styles.map((s) => `
    <div class="style-opt" data-style="${esc(s.id)}">
      <b>${esc(s.label)}</b>
      <small>${esc(s.short)}</small>
    </div>`).join('');
  $('styleGrid').querySelectorAll('.style-opt').forEach((el) => {
    el.addEventListener('click', () => selectStyle(el.dataset.style));
  });

  const merged = { ...d, ...saved };
  $('targetPct').value = String(merged.target_pct || d.target_pct || 50);
  $('fairuse').value = String(merged.fairuse || d.fairuse || 30);
  $('language').value = merged.language || d.language || 'my';
  $('narrator').value = merged.narrator_id || d.narrator_id || 'F01';
  $('ratio').value = merged.ratio || d.ratio || '16:9';
  $('resolution').value = merged.resolution || d.resolution || '1280:720';
  $('watermark').value = merged.watermark_text || '';
  $('sceneSec').value = String(merged.scene_sec || d.scene_sec || 5);
  $('voiceRate').value = String(merged.voice_rate != null ? merged.voice_rate : (d.voice_rate || 0));
  $('voicePitch').value = String(merged.voice_pitch != null ? merged.voice_pitch : (d.voice_pitch || 0));
  selectStyle(merged.style || d.style || 'style_1_recap');
  syncVoiceLabels();
  loadPresets();

  const chunkText = cfg.gemini_chunk_sec >= 60
    ? `${Math.round(cfg.gemini_chunk_sec / 60)} မိနစ်`
    : `${cfg.gemini_chunk_sec} စက္ကန့်`;
  $('configNote').textContent =
    `Gemini ကို ${cfg.proxy_height}p နဲ့ ${chunkText} chunk အလိုက် ပို့မယ်။ ` +
    `Upload chunk ${bytes(cfg.chunk_size)} · အများဆုံး ${bytes(cfg.max_upload_bytes)}။`;

  document.querySelector('.pill').innerHTML = `M3 · <b>Editor + Render</b> · :${location.port || 80}`;

  syncFairuseInfo();
}

function fairuseEntry(level) {
  const list = (state.config && state.config.fairuse_levels) || [];
  return list.find((x) => Number(x.level) === Number(level)) || null;
}

/* Human-readable explanation of what the selected fairuse level actually does. */
function fairuseSummary(level, sourceSec) {
  const item = fairuseEntry(level);
  if (!item) return '';
  const r = item.recipe || {};
  const parts = [];
  if (r.zoom) parts.push(`${Math.round(r.zoom * 100)}% zoom (scene တစ်ခုစီ push-in)`);
  if (r.fade_in || r.fade_out) {
    parts.push(`fade in ${Number(r.fade_in || 0).toFixed(2)}s / out ${Number(r.fade_out || 0).toFixed(2)}s (scene တစ်ခုစီ)`);
  }
  if (r.freeze) parts.push(`freeze ${Number(r.freeze).toFixed(1)}s (scene အစ frame ကို ရပ်ထား)`);
  if (r.flip) parts.push('flip (မှန်ချည် — Editor toggle က အပြီးသတ်)');
  if (r.transition) parts.push('transition (scene ကြား dip-to-black ~0.18s)');
  if (r.face_zoom) parts.push('face zoom (scene အလယ် မျက်နှာကြီးကို ချိန်၊ track မလုပ်)');
  if (r.zoom_out) parts.push('zoom out (push-in အစား ဆွဲထုတ်)');
  const c = r.colour || {};
  const cparts = [];
  if (c.sat) cparts.push(`sat ${Number(c.sat).toFixed(2)}`);
  if (c.con) cparts.push(`con ${Number(c.con).toFixed(2)}`);
  if (c.gamma) cparts.push(`gamma ${Number(c.gamma).toFixed(2)}`);
  if (c.hue) cparts.push(`hue ${Number(c.hue).toFixed(0)}`);
  if (cparts.length) parts.push(`colour ${cparts.join(' / ')}`);

  const every = Number(item.cut_every || 0);
  const cut = Number(item.cut_sec || 5);
  let cutText = `${cut}s block မဖြတ်ပါ`;
  if (every > cut) {
    cutText = `${cut}s block ကို ${Math.round(every)}s တစ်ခါ ဖြတ်မည် (အကြမ်းဖျင်း — scene ကို ဖြတ်ရင် jump cut ဖြစ်နိုင်)`;
    if (sourceSec > cut) {
      const count = Math.max(0, Math.floor((sourceSec - cut) / every));
      const removed = count * cut;
      cutText += ` · ခန့်မှန်း ${count} ခု / ${Math.round(removed)}s (${(removed / sourceSec * 100).toFixed(1)}%)`;
    }
  }
  return `Fairuse ${item.level} · ${item.label} — ${cutText}`
    + (parts.length ? ` · ${parts.join(' · ')}` : '');
}

function syncFairuseInfo(sourceSec) {
  const el = $('fairuseInfo');
  if (!el) return;
  const dur = Number(sourceSec != null ? sourceSec : ((state.lastProject && state.lastProject.duration) || 0));
  el.textContent = fairuseSummary($('fairuse').value, dur);
}

function selectStyle(id) {
  state.selectedStyle = id;
  $('styleGrid').querySelectorAll('.style-opt').forEach((el) => {
    el.classList.toggle('active', el.dataset.style === id);
  });
  saveSettings();
}

/* ---------- narrator audition ---------- */

function syncVoiceLabels() {
  const rate = Number($('voiceRate').value || 0);
  const pitch = Number($('voicePitch').value || 0);
  $('voiceRateVal').textContent = `${rate >= 0 ? '+' : ''}${rate}%`;
  $('voicePitchVal').textContent = `${pitch >= 0 ? '+' : ''}${pitch}Hz`;
}

async function previewVoice() {
  const btn = $('voicePreviewBtn');
  const audio = $('voicePreviewAudio');
  const note = $('voicePreviewNote');
  const params = new URLSearchParams({
    language: $('language').value || 'my',
    narrator_id: $('narrator').value || 'F01',
    rate: String(Number($('voiceRate').value || 0)),
    pitch: String(Number($('voicePitch').value || 0)),
    v: String(Date.now()),
  });
  btn.disabled = true;
  note.textContent = 'အသံ ထုတ်နေသည်…';
  try {
    audio.classList.remove('hidden');
    audio.src = `/api/voice/preview?${params}`;
    audio.load();
    await audio.play();
    note.textContent = `${$('narrator').value} · ${$('voiceRate').value >= 0 ? '+' : ''}${$('voiceRate').value}% · ${$('voicePitch').value >= 0 ? '+' : ''}${$('voicePitch').value}Hz`;
  } catch (err) {
    note.textContent = `နားထောင်လို့ မရပါ: ${err.message || err}`;
    toast(`Voice preview မရပါ: ${err.message || err}`, 8000);
  } finally {
    btn.disabled = false;
  }
}

/* ---------- saved settings presets ---------- */

async function loadPresets() {
  try {
    const data = await api('/api/presets');
    state.presets = data.presets || [];
  } catch {
    state.presets = [];
  }
  renderPresetSelect();
}

function renderPresetSelect() {
  const sel = $('presetSelect');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">— ရွေးပါ —</option>' + state.presets
    .map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`)
    .join('');
  if (current) sel.value = current;
}

async function savePreset() {
  const name = ($('presetName').value || '').trim();
  if (!name) { toast('Preset အမည် ထည့်ပါ'); return; }
  try {
    const body = {
      name,
      settings: collectSettings(),
      project_id: state.editProject || '',
    };
    if (state.edit) body.edit = state.edit;
    const preset = await api('/api/presets', { method: 'POST', body: JSON.stringify(body) });
    $('presetName').value = '';
    await loadPresets();
    $('presetSelect').value = preset.id;
    toast(`Preset "${preset.name}" သိမ်းပြီး ✓`, 4000);
  } catch (err) {
    toast(`Preset မသိမ်းနိုင်ပါ: ${err.message || err}`, 8000);
  }
}

async function applyPreset() {
  const id = $('presetSelect').value;
  if (!id) { toast('Preset ရွေးပါ'); return; }
  const preset = state.presets.find((p) => p.id === id);
  if (!preset) return;
  const pid = state.editProject || state.projectId || '';
  if (!pid) {
    applySettingsToForm({ ...(preset.settings || {}), style: state.selectedStyle });
    toast(`Preset "${preset.name}" ကို settings ထဲ ထည့်ပြီး`, 4000);
    return;
  }
  try {
    const data = await api(`/api/presets/${id}/apply`, {
      method: 'POST',
      body: JSON.stringify({ project_id: pid }),
    });
    applySettingsToForm(data.project ? data.project.settings : preset.settings);
    if (data.edit) applyEditorData(data);
    toast(`Preset "${preset.name}" ကို ပြန်သုံးပြီး ✓`, 4000);
  } catch (err) {
    toast(`Preset မသုံးနိုင်ပါ: ${err.message || err}`, 8000);
  }
}

async function deletePreset() {
  const id = $('presetSelect').value;
  if (!id) { toast('Preset ရွေးပါ'); return; }
  if (!confirm('ဒီ preset ကို ဖျက်မလား?')) return;
  try {
    await api(`/api/presets/${id}`, { method: 'DELETE' });
    await loadPresets();
    toast('Preset ဖျက်ပြီး', 3000);
  } catch (err) {
    toast(`မဖျက်နိုင်ပါ: ${err.message || err}`, 8000);
  }
}

/* ---------- file selection ---------- */

function bindFilePicker() {
  const drop = $('dropZone');
  const input = $('fileInput');

  drop.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    if (input.files && input.files[0]) setFile(input.files[0]);
  });

  ['dragenter', 'dragover'].forEach((evt) => {
    drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.add('over'); });
  });
  ['dragleave', 'drop'].forEach((evt) => {
    drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.remove('over'); });
  });
  drop.addEventListener('drop', (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) setFile(f);
  });
}

function setFile(file) {
  const allowed = ['.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi'];
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  if (!allowed.includes(ext)) {
    toast(`ဒီဖိုင်အမျိုးအစား မရဘူး: ${ext || 'unknown'}`);
    return;
  }
  if (state.config && file.size > state.config.max_upload_bytes) {
    toast(`ဖိုင်ကြီးလွန်းတယ် (အများဆုံး ${bytes(state.config.max_upload_bytes)})`);
    return;
  }

  state.file = file;
  state.fileDuration = 0;
  $('fileRow').classList.remove('hidden');
  $('fileName').textContent = file.name;
  $('fileName').title = file.name;
  $('fileSize').textContent = bytes(file.size);
  if (!$('projectName').value) {
    $('projectName').value = file.name.replace(/\.[^.]+$/, '').slice(0, 90);
  }

  const url = URL.createObjectURL(file);
  const probe = document.createElement('video');
  probe.preload = 'metadata';
  probe.onloadedmetadata = () => {
    state.fileDuration = Number(probe.duration) || 0;
    URL.revokeObjectURL(url);
    updateEstimate();
  };
  probe.onerror = () => { URL.revokeObjectURL(url); updateEstimate(); };
  probe.src = url;
  updateEstimate();
}

function updateEstimate() {
  const pct = Number($('targetPct').value || 50);
  const el = $('localEstimate');
  if (!state.file) { el.textContent = 'ဗီဒီယို ရွေးပါ'; return; }
  if (!state.fileDuration) { el.textContent = `${pct}% · duration ဖတ်နေသည်...`; return; }
  const target = state.fileDuration * pct / 100;
  el.textContent = `${hms(state.fileDuration)} → ${hms(target)} (${pct}%)`;
}

/* ---------- upload ---------- */

function chunkBlob(file, index, chunkSize) {
  const start = index * chunkSize;
  const end = Math.min(file.size, start + chunkSize);
  return file.slice(start, end);
}

function putChunk(pid, index, blob, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', `/api/projects/${pid}/upload/chunk/${index}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch { resolve({ ok: true }); }
      } else {
        let detail = `HTTP ${xhr.status}`;
        try {
          const body = JSON.parse(xhr.responseText);
          detail = body.detail || detail;
        } catch { /* ignore */ }
        reject(new Error(detail));
      }
    };
    xhr.onerror = () => reject(new Error('network error'));
    xhr.ontimeout = () => reject(new Error('timeout'));
    xhr.timeout = 0;
    xhr.send(blob);
  });
}

function setUploadProgress(fraction, label) {
  const pct = Math.max(0, Math.min(100, fraction * 100));
  $('barFill').style.width = `${pct}%`;
  $('statusMessage').textContent = label || `ဗီဒီယို တင်နေသည်... ${pct.toFixed(0)}%`;
}

async function uploadRemaining() {
  const { projectId, file, upload } = state;
  if (!projectId || !file || !upload) return;
  const pending = [];
  for (let i = 0; i < upload.total; i++) {
    if (!upload.received.has(i)) pending.push(i);
  }

  const totalBytes = file.size;
  let doneBytes = 0;
  upload.received.forEach((i) => {
    doneBytes += chunkBlob(file, i, upload.chunkSize).size;
  });

  const queue = pending.slice();
  const workers = [];
  let failed = null;

  const worker = async () => {
    while (queue.length) {
      if (failed) return;
      const index = queue.shift();
      const blob = chunkBlob(file, index, upload.chunkSize);
      let attempt = 0;
      let ok = false;
      while (!ok && attempt < 4 && !failed) {
        attempt++;
        try {
          await putChunk(projectId, index, blob, (frac) => {
            const sent = doneBytes + blob.size * frac;
            setUploadProgress(sent / totalBytes,
              `ဗီဒီယို တင်နေသည်... ${(sent / totalBytes * 100).toFixed(1)}% · chunk ${index + 1}/${upload.total}`);
          });
          upload.received.add(index);
          doneBytes += blob.size;
          ok = true;
          setUploadProgress(doneBytes / totalBytes,
            `ဗီဒီယို တင်နေသည်... ${(doneBytes / totalBytes * 100).toFixed(1)}%`);
        } catch (err) {
          if (attempt >= 4) {
            failed = `Chunk ${index + 1} တင်၍မရပါ: ${err.message}`;
            return;
          }
          await new Promise((r) => setTimeout(r, 800 * attempt));
        }
      }
    }
  };

  for (let i = 0; i < 3; i++) workers.push(worker());
  await Promise.all(workers);
  if (failed) throw new Error(failed);
}

async function startJob() {
  if (state.uploading) return;
  if (!state.file) { toast('အရင်ဆုံး ဗီဒီယိုဖိုင် ရွေးပါ'); return; }

  const settings = collectSettings();
  saveSettings();

  state.uploading = true;
  $('startBtn').disabled = true;
  $('startBtn').textContent = 'တင်နေသည်...';

  try {
    const name = ($('projectName').value || '').trim() || 'Long Movie Recap';

    let project = null;
    if (state.resume && state.resume.name === state.file.name && state.resume.size === state.file.size) {
      try {
        project = await api(`/api/projects/${state.resume.id}`);
        if (project.status !== 'uploading' && project.status !== 'created') project = null;
      } catch { project = null; }
    }
    if (!project) {
      project = await api('/api/projects', {
        method: 'POST',
        body: JSON.stringify({ name, settings }),
      });
    }
    state.resume = { id: project.id, name: state.file.name, size: state.file.size };
    localStorage.setItem(RESUME_KEY, JSON.stringify(state.resume));
    state.projectId = project.id;
    localStorage.setItem(ACTIVE_KEY, project.id);
    showProject(project);

    const init = await api(`/api/projects/${project.id}/upload/init`, {
      method: 'POST',
      body: JSON.stringify({ filename: state.file.name, size: state.file.size }),
    });

    state.upload = {
      total: init.total_chunks,
      chunkSize: init.chunk_size,
      received: new Set(init.received || []),
    };

    if (state.upload.received.size) {
      toast(`တင်ပြီးသား ${state.upload.received.size}/${state.upload.total} chunk ကို ပြန်ဆက်မယ်`);
    }

    await uploadRemaining();
    setUploadProgress(1, 'Upload ပြီး — ဗီဒီယို စစ်ဆေးနေသည်...');

    const done = await api(`/api/projects/${project.id}/upload/complete`, {
      method: 'POST',
      body: JSON.stringify({
        filename: state.file.name,
        size: state.file.size,
        total_chunks: state.upload.total,
      }),
    });
    showProject(done);
    state.resume = null;
    localStorage.removeItem(RESUME_KEY);
    startPolling(project.id);
  } catch (err) {
    toast(err.message || String(err), 9000);
    if (state.projectId) {
      try { showProject(await api(`/api/projects/${state.projectId}`)); } catch { /* ignore */ }
    }
  } finally {
    state.uploading = false;
    $('startBtn').disabled = false;
    $('startBtn').textContent = 'စတင်မယ် · Upload + Ingest';
  }
}

/* ---------- project rendering ---------- */

const INGESTING = new Set(['queued', 'probing', 'proxying', 'audio', 'chunking', 'thumbnail']);
const RUNNING = new Set(['uploading', ...INGESTING]);

function showEmpty() {
  $('statusEmpty').classList.remove('hidden');
  $('statusBody').classList.add('hidden');
  $('projectIdLabel').textContent = '';
}

function showProject(p) {
  if (!p) { showEmpty(); return; }
  state.lastProject = p;
  $('statusEmpty').classList.add('hidden');
  $('statusBody').classList.remove('hidden');
  $('projectIdLabel').textContent = p.id;

  $('statusName').textContent = p.name || 'Untitled';

  const tag = $('statusTag');
  tag.textContent = statusLabel(p.status);
  tag.className = 'status-tag' +
    (p.status === 'ready' ? ' ready' : p.status === 'error' ? ' error' : RUNNING.has(p.status) ? ' running' : '');

  const bar = $('bar');
  bar.classList.toggle('error', p.status === 'error');
  $('barFill').style.width = `${Math.max(0, Math.min(100, Number(p.progress) || 0))}%`;

  let msg = p.message || '';
  if (p.status === 'error' && p.error) msg = `❌ ${p.error}`;
  $('statusMessage').textContent = msg;

  const s = p.settings || {};
  const meta = [
    ['ဗီဒီယို အရှည်', p.duration_label || '—'],
    ['Target output', p.target_label ? `${p.target_label} (${s.target_pct || '—'}%)` : '—'],
    ['Resolution', p.width && p.height ? `${p.width}×${p.height} @ ${(p.fps || 0).toFixed(2)}fps` : '—'],
    ['Codec', [p.video_codec, p.audio_codec].filter(Boolean).join(' / ') || '—'],
    ['ဖိုင်အရွယ်', p.size_bytes ? bytes(p.size_bytes) : '—'],
    ['Fairuse', s.fairuse ? `${s.fairuse}` : '—'],
    ['ဘာသာစကား', languageLabel(s.language)],
    ['Narrator', s.narrator_id || '—'],
  ];
  $('metaGrid').innerHTML = meta
    .map(([k, v]) => `<div class="meta"><span>${esc(k)}</span><b>${esc(v)}</b></div>`)
    .join('');

  const videoWrap = $('videoWrap');
  const video = $('previewVideo');
  const src = (p.files && (p.files.proxy || p.files.source)) || '';
  if (src) {
    videoWrap.classList.remove('hidden');
    if (video.getAttribute('src') !== src) {
      video.setAttribute('src', src);
      video.load();
    }
  } else {
    videoWrap.classList.add('hidden');
    video.removeAttribute('src');
  }

  $('retryBtn').classList.toggle('hidden', !(p.status === 'error' && p.files && p.files.source));

  $('chunkList').innerHTML = (p.chunks || []).map((c) => `
    <div class="chunk">
      <span class="mono">#${Number(c.index) + 1} · ${hms(c.start)} → ${hms(c.end)} · ${bytes(c.size)}</span>
      <a href="${c.url}" target="_blank" rel="noopener">ကြည့်</a>
    </div>`).join('');

  const log = $('log');
  log.textContent = p.log || '(log မရှိသေးပါ)';
  log.scrollTop = log.scrollHeight;

  renderScript(p);
  renderEditor(p);
}

const SCRIPT_BUSY = new Set(['queued', 'analyzing']);
const TTS_BUSY = new Set(['queued', 'synthesizing']);

function scriptStatusLabel(status) {
  return {
    '': 'မစရသေး',
    queued: 'တန်းစီ',
    analyzing: 'Gemini ဖတ်နေသည်',
    ready: 'Script ပြီး',
    error: 'အမှား',
    synthesizing: 'အသံထွက်နေသည်',
  }[status] || status || 'မစရသေး';
}

function renderScript(p) {
  const card = $('scriptCard');
  const s = p.script || {};
  const tts = s.tts || {};
  if (p.status !== 'ready') {
    card.classList.add('hidden');
    return;
  }
  card.classList.remove('hidden');

  const scriptBusy = SCRIPT_BUSY.has(s.status);
  const ttsBusy = TTS_BUSY.has(tts.status);
  const busy = scriptBusy || ttsBusy;

  // What the card reports right now: the voice pass wins when it is the most
  // recent finished/failed step, otherwise the script pass is shown.
  let phase = 'script';
  let status = s.status || '';
  let progress = Number(s.progress) || 0;
  let message = s.message || '';
  if (ttsBusy) {
    phase = 'voice'; status = tts.status || 'synthesizing';
    progress = Number(tts.progress) || 0; message = tts.message || '';
  } else if (scriptBusy) {
    phase = 'script'; status = s.status;
    message = s.message || '';
  } else if (tts.status === 'error') {
    phase = 'voice'; status = 'error';
    progress = Number(tts.progress) || 0;
    message = `❌ ${tts.error || tts.message || ''}`;
  } else if (s.status === 'error') {
    phase = 'script'; status = 'error';
    message = `❌ ${s.error || s.message || ''}`;
  } else if (tts.status === 'ready') {
    phase = 'voice'; status = 'voice-ready'; progress = 100;
    message = tts.message || '';
  } else if (s.status === 'ready') {
    phase = 'script'; status = 'ready'; progress = 100;
  }

  $('scriptState').textContent = phase === 'voice' ? 'Voice' : 'Script';
  const tag = $('scriptTag');
  tag.textContent = status === 'voice-ready' ? 'Voice ပြီး' : scriptStatusLabel(status);
  tag.className = 'status-tag' +
    (status === 'ready' || status === 'voice-ready' ? ' ready' : status === 'error' ? ' error' : busy ? ' running' : '');

  const bar = $('scriptBar');
  bar.classList.toggle('error', status === 'error');
  $('scriptBarFill').style.width = `${Math.max(0, Math.min(100, Number(progress) || 0))}%`;

  $('scriptMessage').textContent = message || '—';

  $('scriptHint').textContent = s.available
    ? `${s.segment_count || 0} ကြောင်း · ${s.model || ''}`
    : `${s.chunk_count || 0} chunk`;

  const scriptBtn = $('scriptBtn');
  scriptBtn.disabled = busy || !!(state.config && state.config.gemini && !state.config.gemini.ready);
  scriptBtn.textContent = s.available ? 'ပြန်ထုတ် · Script + Voice' : 'စတင် · Script + Voice';

  const ttsBtn = $('ttsBtn');
  ttsBtn.disabled = busy || !s.available;
  ttsBtn.textContent = tts.available ? 'Voice ပြန်ထုတ်' : 'Voice ထုတ်';

  const meta = [
    ['Target', s.target_label || (p.target_label || '—')],
    ['ခန့်မှန်း / အမှန်', tts.actual_label ? `${tts.actual_label}` : (s.est_label || '—')],
    ['ဘာသာစကား', languageLabel(s.language || (p.settings || {}).language)],
    ['Style', (p.settings || {}).style === 'style_2_dub' ? 'Dialogue dub' : 'Recap'],
    ['Narrator', (p.settings || {}).narrator_id || '—'],
    ['Voice rate', tts.rate == null ? '—' : `${tts.rate > 0 ? '+' : ''}${tts.rate}%`],
  ];
  $('scriptMeta').innerHTML = meta
    .map(([k, v]) => `<div class="meta"><span>${esc(k)}</span><b>${esc(v)}</b></div>`)
    .join('');

  const audioWrap = $('audioWrap');
  const audio = $('narrationAudio');
  if (tts.audio_url) {
    audioWrap.classList.remove('hidden');
    if (audio.getAttribute('src') !== tts.audio_url) {
      audio.setAttribute('src', tts.audio_url);
      audio.load();
    }
    $('audioMeta').textContent = `${tts.actual_label || '—'} · target ${s.target_label || '—'} · ${tts.voice || ''}`;
  } else {
    audioWrap.classList.add('hidden');
    audio.removeAttribute('src');
  }

  const srtLink = $('srtLink');
  srtLink.classList.toggle('hidden', !tts.srt_url);
  if (tts.srt_url) srtLink.href = `${tts.srt_url}?download=1`;

  const jsonLink = $('scriptJsonLink');
  jsonLink.classList.toggle('hidden', !s.available);
  if (s.available) jsonLink.href = `/api/projects/${p.id}/file/script`;

  const segs = s.segments || [];
  const list = $('scriptList');
  if (!segs.length) {
    list.innerHTML = '<div class="empty">Script မရှိသေးပါ</div>';
  } else {
    const shown = segs.slice(0, 400);
    list.innerHTML = shown.map((seg) => `
      <div class="script-line ${seg.kind === 'dialogue' ? 'dialogue' : ''}">
        <span class="t">${hms(seg.src_start || 0)}</span>
        <span class="k">${seg.kind === 'dialogue' ? 'D' : 'N'}</span>
        <span class="x">${esc(seg.text)}</span>
      </div>`).join('') +
      (segs.length > shown.length ? `<div class="script-more">နောက်ထပ် ${segs.length - shown.length} ကြောင်း…</div>` : '');
  }

  const g = (state.config && state.config.gemini) || {};
  const loop = 'အပိုင်း တစ်ပိုင်းချင်း — Gemini ဖတ် → Edge TTS အသံထွက် → နောက်အပိုင်း → အားလုံးပြီးမှ အသံပေါင်း။ ပြီးရင် Setting/Editor ချိန်ပြီး Render ကို ကိုယ်တိုင် နှိပ်ပါ။ Gemini error ရင် model လဲ၊ မရရင် စောင့်ပြီး ပြန်လုပ်မယ် — ရပ်မသွားပါ။';
  $('scriptNote').textContent = !g.ready
    ? (g.reason || 'Gemini မရသေးပါ')
    : `Model: ${g.active && g.active !== g.model ? `${g.model} → ${g.active}` : g.model} · Voice: Edge TTS။ ${loop}`;
}


/* ---------- M3 editor ---------- */

function clampNum(value, low, high, fallback = low) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(low, Math.min(high, n));
}

function renderStatusLabel(status) {
  return {
    '': 'မထုတ်ရသေး',
    queued: 'တန်းစီ',
    rendering: 'Render နေသည်',
    ready: 'ပြီး',
    error: 'အမှား',
  }[status] || status || 'မထုတ်ရသေး';
}

function editorBusy() {
  const s = (state.lastProject && state.lastProject.render || {}).status;
  return s === 'queued' || s === 'rendering';
}

function renderEditor(p) {
  const card = $('editorCard');
  const script = p.script || {};
  const tts = script.tts || {};
  const ready = p.status === 'ready' && script.available && tts.available;
  if (!ready) {
    card.classList.add('hidden');
    return;
  }
  card.classList.remove('hidden');
  $('editorHint').textContent = `${script.segment_count || 0} ကြောင်း · ${tts.actual_label || ''} · ${tts.voice || ''}`;

  const sig = `${p.id}|${script.segment_count || 0}|${tts.actual_sec || 0}|${tts.timeline_sig || ''}`;
  if (state.editProject !== p.id || state.editSig !== sig) {
    if (state.editProject !== p.id) {
      state.edit = null;
      state.editTimeline = null;
      state.editGeometry = null;
      state.editRecent = null;
      state.editTime = 0;
      state.editDirty = false;
      state.exact = false;
      hideExact();
    }
    state.editProject = p.id;
    state.editSig = sig;
    loadEdit(p);
  }
  updateRenderBox(p.render || {});
}

async function loadEdit(p) {
  const token = ++state.editLoadToken;
  state.editLoading = true;
  try {
    const data = await api(`/api/projects/${p.id}/edit`);
    if (token !== state.editLoadToken || state.projectId !== p.id) return;
    applyEditorData(data);
    ensureEditVideos(p);
  } catch (err) {
    toast(`Editor မရပါ: ${err.message || err}`, 8000);
  } finally {
    if (token === state.editLoadToken) state.editLoading = false;
  }
}

function applyEditorData(data) {
  if (!data || !data.edit) return;
  state.edit = data.edit;
  state.editGeometry = data.geometry;
  state.editTimeline = data.timeline || { ready: false, duration: 0, segments: [] };
  state.editFont = data.font || { family: 'Arial', css: '"Arial", sans-serif' };
  state.editTime = clampNum(state.editTime, 0, state.editTimeline.duration || 0, 0);
  state.editDirty = false;
  state.editRev += 1;
  hydrateEditor();
  layoutStage();
  setEditTime(state.editTime);
  updateRenderBox(data.render || {});
}

function ensureEditVideos(p) {
  const src = (p.files && (p.files.proxy || p.files.source)) || '';
  if (!src || src === state.editVideoSrc) return;
  state.editVideoSrc = src;
  [$('editBg'), $('editFg')].forEach((video) => {
    video.setAttribute('src', src);
    video.load();
  });
}

function hydrateEditor() {
  const e = state.edit;
  if (!e) return;
  const g = state.editGeometry || {};
  const W = g.width || 1280;
  const H = g.height || 720;

  $('bgMode').value = e.bg.mode;
  $('bgColor').value = e.bg.color;
  $('bgBlur').value = String(e.bg.blur);
  $('zoom').value = String(e.canvas.zoom);
  $('flip').checked = !!e.canvas.flip;
  $('editRatio').value = e.ratio || '16:9';
  $('editResolution').value = e.resolution || '1280:720';
  $('editFit').value = e.canvas.fit === 'contain' ? 'contain' : 'cover';

  $('capOn').checked = !!e.caption.on;
  $('capColor').value = e.caption.color;
  $('capBoxColor').value = e.caption.box_color;
  $('capSize').value = String(e.caption.size);
  $('capY').value = String(e.caption.y);
  $('capAlpha').value = String(e.caption.box_alpha);
  if (e.caption.font) $('capFont').value = e.caption.font;
  $('capBoldWrap').value = e.caption.bold ? '1' : '0';
  $('capOutlineColor').value = e.caption.outline_color || '#000000';
  $('capOutline').value = String(e.caption.outline != null ? e.caption.outline : 0.07);
  $('capShadow').value = String(e.caption.shadow || 0);

  $('logoOn').checked = !!e.logo.on;
  $('logoW').value = String(e.logo.w);
  $('logoOpacity').value = String(e.logo.opacity);
  $('logoShape').value = e.logo.shape === 'circle' ? 'circle' : 'rect';
  $('logoBorder').value = String(e.logo.border_width || 0);
  $('logoBorderColor').value = e.logo.border_color || '#f5c542';

  $('wmOn').checked = !!e.watermark.on;
  $('wmText').value = e.watermark.text || '';
  $('wmSize').value = String(e.watermark.size);
  $('wmOpacity').value = String(e.watermark.opacity);

  $('frameOn').checked = !!e.frame.on;
  $('frameColor').value = e.frame.color;
  $('frameWidth').value = String(e.frame.width);

  state.blurSel = -1;
  renderBlurList();

  const time = $('editTime');
  time.max = String(state.editTimeline && state.editTimeline.duration ? state.editTimeline.duration : 1);
  time.value = String(state.editTime);
  $('editTimeLabel').textContent = `${hms(state.editTime)} / ${hms(state.editTimeline ? state.editTimeline.duration : 0)}`;

  const level = (state.config && state.config.fairuse_levels || []).find((x) => Number(x.level) === Number(e.fairuse));
  const tech = level && level.techniques && level.techniques.length
    ? ` · ${level.techniques.join(' + ')}`
    : '';
  $('editPresetLabel').textContent = level
    ? `${e.fairuse} · ${level.label}${tech}`
    : `Fairuse ${e.fairuse}`;
  const srcSec = Number((state.lastProject && state.lastProject.duration) || 0);
  $('editFairuseInfo').textContent = fairuseSummary(e.fairuse, srcSec);
  $('editFontNote').textContent =
    `Caption font: ${(state.editFont || {}).family || '—'} · output ${W}×${H} @ ${g.fps || 30}fps · ${state.editTimeline && state.editTimeline.segment_count || 0} segments`;
  syncLabels();
  hideExact();
}

function syncLabels() {
  const e = state.edit;
  if (!e) return;
  const H = (state.editGeometry && state.editGeometry.height) || 720;
  const W = (state.editGeometry && state.editGeometry.width) || 1280;
  $('bgBlurVal').textContent = String(Math.round(Number(e.bg.blur) || 0));
  $('zoomVal').textContent = Number(e.canvas.zoom || 1).toFixed(2);
  $('capSizeVal').textContent = `${Math.round((Number(e.caption.size) || 0.044) * H)}px`;
  $('capYVal').textContent = `${Math.round((Number(e.caption.y) || 0.8) * 100)}%`;
  $('capAlphaVal').textContent = Number(e.caption.box_alpha || 0).toFixed(2);
  $('logoWVal').textContent = `${Math.round((Number(e.logo.w) || 0.11) * 100)}%`;
  $('logoOpacityVal').textContent = `${Math.round((Number(e.logo.opacity) || 0) * 100)}%`;
  $('wmSizeVal').textContent = `${Math.round((Number(e.watermark.size) || 0.028) * H)}px`;
  $('wmOpacityVal').textContent = `${Math.round((Number(e.watermark.opacity) || 0) * 100)}%`;
  $('frameWidthVal').textContent = `${Math.max(1, Math.round((Number(e.frame.width) || 0) * Math.min(W, H)))}px`;
  $('capOutlineVal').textContent = `${Math.round((Number(e.caption.outline) || 0) * 100)}%`;
  $('capShadowVal').textContent = `${Math.round((Number(e.caption.shadow) || 0) * 100)}%`;
  $('logoBorderVal').textContent = `${Math.round((Number(e.logo.border_width) || 0) * Math.min(W, H))}px`;
}

function layoutStage() {
  const e = state.edit;
  const g = state.editGeometry;
  if (!e || !g) return;
  const stage = $('editStage');
  const wrap = $('stageWrap');
  const W = Number(g.width) || 1280;
  const H = Number(g.height) || 720;
  const availW = Math.max(240, wrap.clientWidth || 600);
  const maxH = Math.min(560, Math.max(260, window.innerHeight * 0.58));
  let sw = availW;
  let sh = sw * H / W;
  if (sh > maxH) { sh = maxH; sw = sh * W / H; }
  sw = Math.round(sw);
  sh = Math.round(sh);
  stage.style.width = `${sw}px`;
  stage.style.height = `${sh}px`;
  state.stage = { w: sw, h: sh };

  const bg = $('editBg');
  if (e.bg.mode === 'blur') {
    bg.style.display = 'block';
    stage.style.background = '#000';
    bg.style.filter = `blur(${(Number(e.bg.blur) || 0) * sh / H}px) brightness(0.84)`;
  } else {
    bg.style.display = 'none';
    stage.style.background = e.bg.mode === 'color' ? e.bg.color : '#000';
  }

  const fg = $('editFg');
  fg.style.display = state.editVideoSrc ? 'block' : 'none';
  const zoom = Number(e.canvas.zoom) || 1;
  const fitW = Number(g.fit_width) || W;
  const fitH = Number(g.fit_height) || H;
  fg.style.width = `${fitW * zoom * sw / W}px`;
  fg.style.height = `${fitH * zoom * sh / H}px`;
  fg.style.left = `${(Number(e.canvas.x) || 0.5) * 100}%`;
  fg.style.top = `${(Number(e.canvas.y) || 0.5) * 100}%`;
  fg.style.transform = `translate(-50%, -50%)${e.canvas.flip ? ' scaleX(-1)' : ''}`;

  const logoWrap = $('editLogoWrap');
  const logoImg = $('editLogo');
  if (e.logo.on) {
    logoWrap.style.display = 'block';
    const lw = (Number(e.logo.w) || 0.11) * sw;
    logoWrap.style.width = `${lw}px`;
    logoWrap.style.left = `${(Number(e.logo.x) || 0) * 100}%`;
    logoWrap.style.top = `${(Number(e.logo.y) || 0) * 100}%`;
    logoWrap.style.opacity = String(clampNum(e.logo.opacity, 0, 1, 1));
    const circle = e.logo.shape === 'circle';
    logoWrap.classList.toggle('circle', circle);
    if (circle) {
      logoWrap.style.height = `${lw}px`;
      logoImg.style.height = '100%';
      logoImg.style.objectFit = 'cover';
    } else {
      logoWrap.style.height = '';
      logoImg.style.height = '';
      logoImg.style.objectFit = '';
    }
    const bw = Number(e.logo.border_width) || 0;
    logoImg.style.boxShadow = bw > 0
      ? `inset 0 0 0 ${Math.max(1, bw * Math.min(sw, sh))}px ${e.logo.border_color || '#f5c542'}`
      : 'none';
    if (logoImg.getAttribute('src') !== `/${e.logo.src}`) logoImg.setAttribute('src', `/${e.logo.src}`);
  } else {
    logoWrap.style.display = 'none';
  }

  const wm = $('editWatermark');
  const wmText = $('editWatermarkText');
  if (e.watermark.on && e.watermark.text) {
    wm.style.display = 'block';
    wm.style.left = `${(Number(e.watermark.x) || 0) * 100}%`;
    wm.style.top = `${(Number(e.watermark.y) || 0) * 100}%`;
    wm.style.fontSize = `${(Number(e.watermark.size) || 0.028) * sh}px`;
    wm.style.color = e.watermark.color;
    wm.style.opacity = String(clampNum(e.watermark.opacity, 0, 1, 0.75));
    wm.style.fontFamily = activeFontCss();
    wmText.textContent = e.watermark.text;
  } else {
    wm.style.display = 'none';
  }

  const cap = $('editCaption');
  cap.style.top = `${(Number(e.caption.y) || 0.8) * 100}%`;
  cap.style.fontSize = `${(Number(e.caption.size) || 0.044) * sh}px`;
  cap.style.fontFamily = activeFontCss();
  cap.style.fontWeight = e.caption.bold ? '700' : '400';
  cap.style.color = e.caption.color;
  const outlineFrac = e.caption.outline != null
    ? clampNum(e.caption.outline, 0, 0.5, 0.07)
    : (e.caption.box ? 0.28 : 0.07);
  const shadowFrac = clampNum(e.caption.shadow, 0, 0.4, 0);
  const outlinePx = Math.max(0, outlineFrac * (Number(e.caption.size) || 0.044) * sh);
  const outlineColor = e.caption.outline_color || '#000000';
  if (e.caption.box) {
    const alpha = clampNum(e.caption.box_alpha, 0, 1, 0.7);
    cap.style.background = hexToRgba(e.caption.box_color, alpha);
    const pad = Math.max(2, (Number(e.caption.size) || 0.044) * sh * 0.28);
    cap.style.padding = `${pad}px ${pad * 1.6}px`;
    cap.style.webkitTextStroke = outlinePx > 0 ? `${outlinePx}px ${outlineColor}` : '0';
    cap.style.textShadow = shadowFrac > 0
      ? `${shadowFrac * 10}px ${shadowFrac * 16}px ${shadowFrac * 24}px rgba(0,0,0,.75)`
      : 'none';
  } else {
    cap.style.background = 'transparent';
    cap.style.padding = '0';
    cap.style.webkitTextStroke = outlinePx > 0 ? `${outlinePx}px ${outlineColor}` : '0';
    cap.style.textShadow = shadowFrac > 0
      ? `${shadowFrac * 10}px ${shadowFrac * 16}px ${shadowFrac * 24}px rgba(0,0,0,.75)`
      : 'none';
  }
  cap.style.display = e.caption.on ? 'block' : 'none';

  const frame = $('editFrame');
  if (e.frame.on && Number(e.frame.width) > 0) {
    frame.style.display = 'block';
    const fw = Math.max(1, Number(e.frame.width) * Math.min(sw, sh));
    frame.style.boxShadow = `inset 0 0 0 ${fw}px ${e.frame.color}`;
  } else {
    frame.style.display = 'none';
  }

  updateCaption();
  layoutBlurBoxes();
}

function activeFontCss() {
  const id = state.edit && state.edit.caption && state.edit.caption.font;
  const css = id && state.fontCss ? state.fontCss[id] : '';
  return css || (state.editFont || {}).css || '"Noto Sans Myanmar", "Padauk", sans-serif';
}

function renderBlurList() {
  const e = state.edit;
  const list = $('blurList');
  if (!e || !list) return;
  const blurs = e.blurs || [];
  if (!blurs.length) {
    list.innerHTML = '<div class="blur-empty">Blur မရှိသေးပါ — “+ Blur ထည့်” နှိပ်ပါ။</div>';
    return;
  }
  list.innerHTML = blurs.map((b, i) => `
    <div class="blur-item ${i === state.blurSel ? 'selected' : ''}" data-blur="${i}">
      <label class="check"><input type="checkbox" data-bon="${i}" ${b.on ? 'checked' : ''}> ပြ</label>
      <span class="name">#${i + 1} · ${Math.round(b.w * 100)}×${Math.round(b.h * 100)}%</span>
      <input type="range" data-bstr="${i}" min="2" max="60" step="1" value="${b.strength}" title="Blur strength">
      <button class="btn secondary small" data-bdel="${i}" type="button">ဖျက်</button>
    </div>`).join('');
  list.querySelectorAll('[data-blur]').forEach((el) => {
    el.addEventListener('click', (ev) => {
      if (ev.target.closest('input, button')) return;
      state.blurSel = Number(el.dataset.blur);
      renderBlurList();
      layoutStage();
    });
  });
  list.querySelectorAll('[data-bon]').forEach((el) => {
    el.addEventListener('change', () => updateEdit((ed) => {
      ed.blurs[Number(el.dataset.bon)].on = el.checked;
    }));
  });
  list.querySelectorAll('[data-bstr]').forEach((el) => {
    el.addEventListener('input', () => updateEdit((ed) => {
      ed.blurs[Number(el.dataset.bstr)].strength = Number(el.value);
    }));
  });
  list.querySelectorAll('[data-bdel]').forEach((el) => {
    el.addEventListener('click', () => updateEdit((ed) => {
      ed.blurs.splice(Number(el.dataset.bdel), 1);
      state.blurSel = -1;
      renderBlurList();
    }));
  });
}

function layoutBlurBoxes() {
  const e = state.edit;
  const stage = state.stage;
  const host = $('editBlurs');
  if (!e || !stage || !host) return;
  const blurs = e.blurs || [];
  host.innerHTML = '';
  blurs.forEach((b, i) => {
    const box = document.createElement('div');
    box.className = 'blur-box' + (i === state.blurSel ? ' selected' : '');
    box.dataset.blur = String(i);
    box.style.left = `${b.x * 100}%`;
    box.style.top = `${b.y * 100}%`;
    box.style.width = `${b.w * 100}%`;
    box.style.height = `${b.h * 100}%`;
    box.style.display = b.on ? 'block' : 'none';
    box.style.backdropFilter = `blur(${Math.min(18, (Number(b.strength) || 24) / 2)}px)`;
    const tag = document.createElement('span');
    tag.className = 'blur-tag';
    tag.textContent = `Blur ${i + 1}`;
    box.appendChild(tag);
    const handle = document.createElement('span');
    handle.className = 'edit-handle';
    handle.dataset.handle = 'blur';
    box.appendChild(handle);
    host.appendChild(box);
  });
}

function hexToRgba(hex, alpha) {
  const s = String(hex || '#000000').replace('#', '');
  const full = s.length === 3 ? s.split('').map((c) => c + c).join('') : s;
  const r = parseInt(full.slice(0, 2), 16) || 0;
  const g = parseInt(full.slice(2, 4), 16) || 0;
  const b = parseInt(full.slice(4, 6), 16) || 0;
  return `rgba(${r}, ${g}, ${b}, ${clampNum(alpha, 0, 1, 1)})`;
}

function captionChunksJS(text) {
  text = String(text || '').trim();
  const limit = 58;
  if (!text) return [];
  if (text.length <= limit) return [text];
  const parts = text.split(/(?<=[၊။.!?])\s*/);
  const out = [];
  let current = '';
  for (let part of parts) {
    part = part.trim();
    if (!part) continue;
    if (!current) current = part;
    else if (current.length + 1 + part.length <= limit) current += ' ' + part;
    else { out.push(current); current = part; }
  }
  if (current) out.push(current);
  const final = [];
  for (let piece of out) {
    while (piece.length > limit * 2) {
      final.push(piece.slice(0, limit * 2));
      piece = piece.slice(limit * 2);
    }
    final.push(piece);
  }
  return final;
}

function wrapCaptionJS(text, maxChars) {
  text = String(text || '').trim();
  if (!text || text.length <= maxChars) return text ? [text] : [];
  const spaced = text.includes(' ');
  const tokens = spaced ? text.split(' ') : Array.from(text);
  const sep = spaced ? ' ' : '';
  const lines = [];
  let current = '';
  for (const token of tokens) {
    const candidate = current ? current + sep + token : token;
    if (candidate.length <= maxChars) {
      current = candidate;
    } else {
      if (current) lines.push(current);
      current = token;
      while (current.length > maxChars) {
        lines.push(current.slice(0, maxChars));
        current = current.slice(maxChars);
      }
    }
  }
  if (current) lines.push(current);
  return lines;
}

function activeCaptionJS(seg, local) {
  const caps = captionChunksJS(seg.text);
  if (!caps.length) return '';
  const duration = Math.max(0.05, Number(seg.duration) || Number(seg.out_dur) || 0);
  const weight = caps.reduce((sum, c) => sum + c.length, 0) || 1;
  let pos = 0;
  for (const cap of caps) {
    const share = duration * cap.length / weight;
    const end = Math.min(duration, pos + Math.max(0.6, share));
    if (local >= pos && local < end) return cap;
    pos += share;
  }
  return '';
}

function segmentAt(t) {
  const segs = (state.editTimeline && state.editTimeline.segments) || [];
  if (!segs.length) return null;
  let lo = 0;
  let hi = segs.length - 1;
  let found = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segs[mid].out_start <= t) { found = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  return segs[found];
}

function updateCaption() {
  if (!state.edit || !state.editTimeline) return;
  const el = $('editCaptionText');
  const cap = $('editCaption');
  if (!state.edit.caption.on) { el.innerHTML = ''; cap.style.display = 'none'; return; }
  const seg = segmentAt(state.editTime);
  if (!seg) { el.innerHTML = ''; cap.style.display = 'none'; return; }
  const local = state.editTime - Number(seg.out_start || 0);
  if (local > Number(seg.duration || 0)) { el.innerHTML = ''; cap.style.display = 'none'; return; }
  const text = activeCaptionJS(seg, local);
  if (!text) { el.innerHTML = ''; cap.style.display = 'none'; return; }
  const stageH = state.stage ? state.stage.h : 300;
  const fontPx = Math.max(6, (Number(state.edit.caption.size) || 0.044) * stageH);
  const lang = (state.editTimeline && state.editTimeline.language) || 'my';
  const charW = (lang === 'zh' || lang === 'ja') ? 1.0 : 0.56;
  const maxChars = Math.max(6, Math.floor((state.stage ? state.stage.w : 500) * 0.88 / (fontPx * charW)));
  el.innerHTML = wrapCaptionJS(text, maxChars).map(esc).join('<br>');
  cap.style.display = 'block';
}

function setEditTime(t) {
  const duration = state.editTimeline ? Number(state.editTimeline.duration) || 0 : 0;
  state.editTime = clampNum(t, 0, duration, 0);
  $('editTime').value = String(state.editTime);
  $('editTimeLabel').textContent = `${hms(state.editTime)} / ${hms(duration)}`;
  updateCaption();
  seekEditVideos();
  if (state.exact) scheduleExact();
}

function seekEditVideos() {
  if (!state.editTimeline || !state.editTimeline.segments || !state.editTimeline.segments.length) return;
  const seg = segmentAt(state.editTime);
  if (!seg) return;
  const local = clampNum(state.editTime - Number(seg.out_start || 0), 0, Number(seg.out_dur) || 0.1, 0);
  const frac = Number(seg.out_dur) > 0 ? local / Number(seg.out_dur) : 0;
  const src = Number(seg.src_start || 0) + (Number(seg.src_end || 0) - Number(seg.src_start || 0)) * frac;
  if (state.seekTimer) clearTimeout(state.seekTimer);
  state.seekTimer = setTimeout(() => {
    [$('editBg'), $('editFg')].forEach((video) => {
      if (!video.getAttribute('src')) return;
      if (video.readyState < 1) return;
      if (Math.abs(video.currentTime - src) > 0.05) {
        try { video.currentTime = src; } catch { /* seek race */ }
      }
    });
  }, 80);
}

function markEditDirty() {
  if (!state.edit) return;
  state.editDirty = true;
  state.editRev += 1;
  $('editSaveState').textContent = 'မသိမ်းရသေးပါ…';
  if (state.editSaveTimer) clearTimeout(state.editSaveTimer);
  state.editSaveTimer = setTimeout(saveEdit, 800);
}

async function saveEdit() {
  if (state.editSaveTimer) { clearTimeout(state.editSaveTimer); state.editSaveTimer = null; }
  if (!state.edit || !state.editProject) return;
  if (state.editSaving) { state.editPending = true; return; }
  const pid = state.editProject;
  const rev = state.editRev;
  state.editSaving = true;
  $('editSaveState').textContent = 'သိမ်းနေသည်…';
  try {
    const data = await api(`/api/projects/${pid}/edit`, {
      method: 'PUT',
      body: JSON.stringify({ edit: state.edit }),
    });
    if (state.editProject !== pid) return;
    state.editGeometry = data.geometry;
    state.editTimeline = data.timeline || state.editTimeline;
    state.editFont = data.font || state.editFont;
    if (rev === state.editRev) {
      state.editDirty = false;
      $('editSaveState').textContent = 'သိမ်းပြီး ✓';
    } else {
      state.editPending = true;
    }
    syncLabels();
    layoutStage();
    updateRenderBox(data.render || {});
  } catch (err) {
    $('editSaveState').textContent = `မသိမ်းနိုင်ပါ: ${err.message || err}`;
    toast(`Editor မသိမ်းနိုင်ပါ: ${err.message || err}`, 8000);
  } finally {
    state.editSaving = false;
    if (state.editPending) {
      state.editPending = false;
      saveEdit();
    }
  }
}

async function flushEdit() {
  if (state.editSaveTimer) { clearTimeout(state.editSaveTimer); state.editSaveTimer = null; }
  for (let i = 0; i < 30 && (state.editSaving || state.editDirty); i++) {
    if (state.editSaving) {
      await new Promise((r) => setTimeout(r, 120));
    } else if (state.editDirty) {
      await saveEdit();
    }
  }
}

function updateRenderBox(r) {
  r = r || {};
  const status = r.status || '';
  const busy = status === 'queued' || status === 'rendering';
  const tag = $('renderTag');
  tag.textContent = renderStatusLabel(status);
  tag.className = 'status-tag' +
    (status === 'ready' ? ' ready' : status === 'error' ? ' error' : busy ? ' running' : '');
  const bar = $('renderBar');
  bar.classList.toggle('error', status === 'error');
  $('renderBarFill').style.width = `${Math.max(0, Math.min(100, Number(r.progress) || 0))}%`;
  $('renderMessage').textContent = status === 'error'
    ? `❌ ${r.error || r.message || ''}`
    : (r.message || '—');

  $('renderStale').textContent = r.stale && r.available
    ? 'Edit ပြောင်းထားတယ် — ဒီ render က ဟောင်းနေပြီ၊ ပြန်ထုတ်ပါ။'
    : '';

  const meta = [];
  if (r.available) {
    meta.push(['အရှည်', r.duration_label || '—']);
    meta.push(['Frames', r.frames ? `${r.frames} @ ${r.fps || 30}fps` : '—']);
    meta.push(['Resolution', r.width && r.height ? `${r.width}×${r.height}` : '—']);
    meta.push(['ဖိုင်အရွယ်', r.size ? bytes(r.size) : '—']);
  }
  $('renderMeta').innerHTML = meta
    .map(([k, v]) => `<div class="meta"><span>${esc(k)}</span><b>${esc(v)}</b></div>`)
    .join('');

  const video = $('outputVideo');
  const link = $('outputLink');
  if (r.available && r.url) {
    video.classList.remove('hidden');
    if (video.getAttribute('src') !== r.url) {
      video.setAttribute('src', r.url);
      video.load();
    }
    link.classList.remove('hidden');
    link.href = r.download_url || r.url;
  } else {
    video.classList.add('hidden');
    video.removeAttribute('src');
    link.classList.add('hidden');
  }

  const btn = $('renderBtn');
  btn.disabled = busy || !state.editTimeline || !state.editTimeline.ready;
  btn.textContent = busy ? 'Render နေသည်…' : (r.available ? 'Render ပြန်ထုတ်' : 'Render ထုတ်');
}

function scheduleExact() {
  if (state.exactTimer) clearTimeout(state.exactTimer);
  state.exactTimer = setTimeout(refreshExact, 350);
}

async function refreshExact() {
  if (!state.exact || !state.editProject) return;
  const url = `/api/projects/${state.editProject}/preview?t=${state.editTime.toFixed(3)}&w=960&v=${Date.now()}`;
  const img = $('exactImg');
  img.dataset.pending = url;
  const probe = new Image();
  probe.onload = () => {
    if (img.dataset.pending !== url) return;
    img.src = url;
    img.classList.remove('hidden');
  };
  probe.onerror = () => {
    if (img.dataset.pending === url) toast('Exact preview မရပါ', 6000);
  };
  probe.src = url;
}

async function showExact() {
  if (!state.editTimeline || !state.editTimeline.ready) {
    toast('Timeline မရသေးပါ');
    return;
  }
  const img = $('exactImg');
  state.exact = false;
  await flushEdit();
  state.exact = true;
  $('exactBtn').classList.add('hidden');
  $('liveBtn').classList.remove('hidden');
  $('editStageNote').textContent =
    'Exact frame — render နဲ့ တစ်ထပ်တည်း (source မှ တိုက်ရိုက်)။ ပုံကိုနှိပ်ရင် Live ပြန်ရောက်မယ်။';
  await refreshExact();
  img.classList.remove('hidden');
}

function hideExact() {
  state.exact = false;
  if (state.exactTimer) { clearTimeout(state.exactTimer); state.exactTimer = null; }
  const img = $('exactImg');
  if (img) { img.classList.add('hidden'); img.dataset.pending = ''; }
  const exactBtn = $('exactBtn');
  const liveBtn = $('liveBtn');
  if (exactBtn) exactBtn.classList.remove('hidden');
  if (liveBtn) liveBtn.classList.add('hidden');
  const note = $('editStageNote');
  if (note) note.textContent =
    'Layer ကို ဆွဲရွှေ့နိုင်၊ ထောင့်ကွက်လေးကို ဆွဲပြီး အရွယ်ညှိနိုင်။ Exact frame က render နဲ့ တစ်ထပ်တည်း still ပြမယ်။';
}

function updateEdit(mutator) {
  if (!state.edit) return;
  mutator(state.edit);
  syncLabels();
  layoutStage();
  markEditDirty();
}

function bindEditorControls() {
  $('bgMode').addEventListener('change', (ev) => updateEdit((e) => { e.bg.mode = ev.target.value; }));
  $('bgColor').addEventListener('input', (ev) => updateEdit((e) => { e.bg.color = ev.target.value; }));
  $('bgBlur').addEventListener('input', (ev) => updateEdit((e) => { e.bg.blur = Number(ev.target.value); }));
  $('zoom').addEventListener('input', (ev) => updateEdit((e) => { e.canvas.zoom = Number(ev.target.value); }));
  $('flip').addEventListener('change', (ev) => updateEdit((e) => { e.canvas.flip = ev.target.checked; }));
  $('centerResetBtn').addEventListener('click', () => updateEdit((e) => { e.canvas.x = 0.5; e.canvas.y = 0.5; }));
  $('editRatio').addEventListener('change', (ev) => {
    updateEdit((e) => { e.ratio = ev.target.value; });
    $('ratio').value = ev.target.value;
    saveSettings();
    persistSettings();
    saveEdit();
  });
  $('editResolution').addEventListener('change', (ev) => {
    updateEdit((e) => { e.resolution = ev.target.value; });
    $('resolution').value = ev.target.value;
    saveSettings();
    persistSettings();
    saveEdit();
  });
  $('editFit').addEventListener('change', (ev) => {
    updateEdit((e) => { e.canvas.fit = ev.target.value; });
    saveEdit();
  });

  $('blurAddBtn').addEventListener('click', () => updateEdit((e) => {
    if ((e.blurs || []).length >= 8) { toast('Blur အများဆုံး ၈ ကွက်'); return; }
    e.blurs = e.blurs || [];
    e.blurs.push({ on: true, x: 0.06, y: 0.06 + e.blurs.length * 0.12, w: 0.24, h: 0.1, strength: 24 });
    state.blurSel = e.blurs.length - 1;
    renderBlurList();
  }));

  $('capFont').addEventListener('change', (ev) => updateEdit((e) => { e.caption.font = ev.target.value; }));
  $('capBoldWrap').addEventListener('change', (ev) => updateEdit((e) => { e.caption.bold = ev.target.value === '1'; }));
  $('capOutlineColor').addEventListener('input', (ev) => updateEdit((e) => { e.caption.outline_color = ev.target.value; }));
  $('capOutline').addEventListener('input', (ev) => updateEdit((e) => { e.caption.outline = Number(ev.target.value); }));
  $('capShadow').addEventListener('input', (ev) => updateEdit((e) => { e.caption.shadow = Number(ev.target.value); }));

  $('capOn').addEventListener('change', (ev) => updateEdit((e) => { e.caption.on = ev.target.checked; }));
  $('capColor').addEventListener('input', (ev) => updateEdit((e) => { e.caption.color = ev.target.value; }));
  $('capBoxColor').addEventListener('input', (ev) => updateEdit((e) => { e.caption.box_color = ev.target.value; }));
  $('capSize').addEventListener('input', (ev) => updateEdit((e) => { e.caption.size = Number(ev.target.value); }));
  $('capY').addEventListener('input', (ev) => updateEdit((e) => { e.caption.y = Number(ev.target.value); }));
  $('capAlpha').addEventListener('input', (ev) => updateEdit((e) => { e.caption.box_alpha = Number(ev.target.value); }));

  $('logoOn').addEventListener('change', (ev) => updateEdit((e) => { e.logo.on = ev.target.checked; }));
  $('logoW').addEventListener('input', (ev) => updateEdit((e) => { e.logo.w = Number(ev.target.value); }));
  $('logoOpacity').addEventListener('input', (ev) => updateEdit((e) => { e.logo.opacity = Number(ev.target.value); }));
  $('logoShape').addEventListener('change', (ev) => updateEdit((e) => { e.logo.shape = ev.target.value; }));
  $('logoBorder').addEventListener('input', (ev) => updateEdit((e) => {
    e.logo.border_width = Number(ev.target.value);
    e.logo.border = Number(ev.target.value) > 0;
  }));
  $('logoBorderColor').addEventListener('input', (ev) => updateEdit((e) => { e.logo.border_color = ev.target.value; }));
  $('logoResetBtn').addEventListener('click', () => updateEdit((e) => {
    e.logo.x = 0.84; e.logo.y = 0.10; e.logo.w = 0.11; e.logo.opacity = 1;
  }));

  $('wmOn').addEventListener('change', (ev) => updateEdit((e) => { e.watermark.on = ev.target.checked; }));
  $('wmText').addEventListener('input', (ev) => updateEdit((e) => { e.watermark.text = ev.target.value; }));
  $('wmSize').addEventListener('input', (ev) => updateEdit((e) => { e.watermark.size = Number(ev.target.value); }));
  $('wmOpacity').addEventListener('input', (ev) => updateEdit((e) => { e.watermark.opacity = Number(ev.target.value); }));
  $('wmResetBtn').addEventListener('click', () => updateEdit((e) => {
    e.watermark.x = 0.03; e.watermark.y = 0.03;
  }));

  $('frameOn').addEventListener('change', (ev) => updateEdit((e) => { e.frame.on = ev.target.checked; }));
  $('frameColor').addEventListener('input', (ev) => updateEdit((e) => { e.frame.color = ev.target.value; }));
  $('frameWidth').addEventListener('input', (ev) => updateEdit((e) => { e.frame.width = Number(ev.target.value); }));

  $('editTime').addEventListener('input', (ev) => setEditTime(Number(ev.target.value)));
  $('exactBtn').addEventListener('click', showExact);
  $('liveBtn').addEventListener('click', hideExact);
  $('exactImg').addEventListener('click', hideExact);
  $('editSaveBtn').addEventListener('click', saveEdit);

  $('editResetBtn').addEventListener('click', async () => {
    if (!state.editProject) return;
    if (!confirm('Fairuse preset အတိုင်း အားလုံး ပြန်လုပ်မလား?')) return;
    try {
      const data = await api(`/api/projects/${state.editProject}/edit/reset`, { method: 'POST' });
      applyEditorData(data);
      $('editSaveState').textContent = 'Preset အတိုင်း ပြန်လုပ်ပြီး';
      toast('Preset အတိုင်း ပြန်လုပ်ပြီး', 4000);
    } catch (err) {
      toast(err.message || String(err), 8000);
    }
  });

  $('renderBtn').addEventListener('click', async () => {
    if (!state.editProject || editorBusy()) return;
    const pid = state.editProject;
    try {
      await flushEdit();
      const res = await api(`/api/projects/${pid}/render`, { method: 'POST' });
      if (res.ok) {
        toast('Render စပြီ — progress ကို ဒီကတ်မှာ ကြည့်ပါ', 4000);
        state.lastProject = { ...(state.lastProject || {}), render: { status: 'queued', progress: 0, message: 'တန်းစီထားသည်...' } };
        updateRenderBox((state.lastProject || {}).render);
        startPolling(pid);
      } else {
        toast(res.message || 'Render မစနိုင်ပါ');
      }
    } catch (err) {
      toast(err.message || String(err), 9000);
    }
  });
}

function bindStageDrag() {
  const stage = $('editStage');
  stage.addEventListener('pointerdown', (ev) => {
    if (!state.edit || !state.stage) return;
    if (state.exact) { hideExact(); return; }
    if (ev.button !== 0) return;
    const handle = ev.target.closest('[data-handle]');
    const layerEl = ev.target.closest('[data-layer]');
    const blurEl = ev.target.closest('[data-blur]');
    // The stage itself carries data-layer="video", so a blur box (a plain
    // child of the stage) must win over that ancestor.
    let layer = handle ? handle.dataset.handle
      : (blurEl ? 'blur'
        : (layerEl && layerEl !== stage ? layerEl.dataset.layer : 'video'));
    if (layer === 'logo' && !state.edit.logo.on) layer = 'video';
    if (layer === 'watermark' && !state.edit.watermark.on) layer = 'video';
    if (layer === 'caption' && !state.edit.caption.on) layer = 'video';
    const blurIndex = blurEl ? Number(blurEl.dataset.blur) : state.blurSel;
    if (blurIndex >= 0) { state.blurSel = blurIndex; renderBlurList(); }
    const rect = stage.getBoundingClientRect();
    state.drag = {
      layer,
      blurIndex,
      mode: handle ? 'resize' : 'move',
      startX: ev.clientX,
      startY: ev.clientY,
      rect,
      base: JSON.parse(JSON.stringify(state.edit)),
    };
    try { stage.setPointerCapture(ev.pointerId); } catch { /* synthetic pointer */ }
    ev.preventDefault();
  });

  stage.addEventListener('pointermove', (ev) => {
    const d = state.drag;
    if (!d || !state.edit) return;
    const dx = (ev.clientX - d.startX) / Math.max(1, d.rect.width);
    const dy = (ev.clientY - d.startY) / Math.max(1, d.rect.height);
    const e = state.edit;
    const b = d.base;
    if (d.layer === 'video') {
      e.canvas.x = clampNum(b.canvas.x + dx, 0, 1, 0.5);
      e.canvas.y = clampNum(b.canvas.y + dy, 0, 1, 0.5);
    } else if (d.layer === 'logo') {
      if (d.mode === 'resize') e.logo.w = clampNum(b.logo.w + dx, 0.02, 0.5, 0.11);
      else {
        e.logo.x = clampNum(b.logo.x + dx, 0, 1, 0.84);
        e.logo.y = clampNum(b.logo.y + dy, 0, 1, 0.1);
      }
    } else if (d.layer === 'watermark') {
      if (d.mode === 'resize') e.watermark.size = clampNum(b.watermark.size + dy * 0.08, 0.01, 0.08, 0.028);
      else {
        e.watermark.x = clampNum(b.watermark.x + dx, 0, 0.95, 0.03);
        e.watermark.y = clampNum(b.watermark.y + dy, 0, 0.95, 0.03);
      }
    } else if (d.layer === 'caption') {
      if (d.mode === 'resize') e.caption.size = clampNum(b.caption.size - dy * 0.12, 0.02, 0.08, 0.044);
      else e.caption.y = clampNum(b.caption.y + dy, 0.05, 0.95, 0.8);
    } else if (d.layer === 'blur') {
      const idx = d.blurIndex;
      const src = b.blurs && b.blurs[idx];
      if (src && e.blurs && e.blurs[idx]) {
        const box = e.blurs[idx];
        if (d.mode === 'resize') {
          box.w = clampNum(src.w + dx, 0.03, 1 - src.x, src.w);
          box.h = clampNum(src.h + dy, 0.03, 1 - src.y, src.h);
        } else {
          box.x = clampNum(src.x + dx, 0, 1 - src.w, src.x);
          box.y = clampNum(src.y + dy, 0, 1 - src.h, src.y);
        }
      }
    }
    syncLabels();
    layoutStage();
  });

  const end = (ev) => {
    if (!state.drag) return;
    state.drag = null;
    try { stage.releasePointerCapture(ev.pointerId); } catch { /* already released */ }
    markEditDirty();
  };
  stage.addEventListener('pointerup', end);
  stage.addEventListener('pointercancel', end);
}

function statusLabel(status) {
  return {
    created: 'အသစ်',
    uploading: 'တင်နေသည်',
    uploaded: 'တင်ပြီး',
    queued: 'တန်းစီ',
    probing: 'စစ်ဆေးနေသည်',
    proxying: 'Proxy',
    audio: 'အသံ',
    chunking: 'Chunk ခွဲ',
    thumbnail: 'Thumbnail',
    ready: 'အဆင်သင့်',
    error: 'အမှား',
  }[status] || status || '—';
}

function languageLabel(code) {
  const lang = (state.config && state.config.languages || []).find((l) => l.code === code);
  return lang ? `${lang.label_mm}` : (code || '—');
}

/* ---------- polling ---------- */

function startPolling(pid) {
  stopPolling();
  state.projectId = pid;
  const tick = async () => {
    state.pollTimer = null;
    try {
      const p = await api(`/api/projects/${pid}`);
      if (state.projectId !== pid) return;
      showProject(p);
      refreshRecent();
      const sc = p.script || {};
      const busyScript = SCRIPT_BUSY.has(sc.status) || TTS_BUSY.has((sc.tts || {}).status);
      const renderStatus = (p.render || {}).status;
      const busyRender = renderStatus === 'queued' || renderStatus === 'rendering';
      if ((p.status === 'ready' || p.status === 'error') && !busyScript && !busyRender) {
        return;
      }
    } catch (err) {
      console.warn('poll failed', err);
    }
    if (state.projectId === pid) state.pollTimer = setTimeout(tick, 1500);
  };
  state.pollTimer = setTimeout(tick, 600);
}

function stopPolling() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

/* ---------- recent ---------- */

async function refreshRecent() {
  try {
    const data = await api('/api/projects');
    const rows = data.projects || [];
    if (!rows.length) {
      $('recentList').innerHTML = '<div class="empty">မရှိသေးပါ</div>';
      return;
    }
    $('recentList').innerHTML = rows.map((p) => `
      <div class="project-row" data-id="${esc(p.id)}">
        <div class="proj-main">
          <b>${esc(p.name)}</b>
          <div class="meta-line">${esc(statusLabel(p.status))} · ${esc(p.duration_label || '—')} · ${esc(p.id)}</div>
        </div>
        <div class="proj-end">
          <div class="meta-line">${esc(p.target_label ? `→ ${p.target_label}` : '')}</div>
          <button class="del-btn" type="button" data-del="${esc(p.id)}" data-name="${esc(p.name)}" title="Project ဖျက်">✕ ဖျက်</button>
        </div>
      </div>`).join('');
    $('recentList').querySelectorAll('.project-row').forEach((row) => {
      row.addEventListener('click', () => openProject(row.dataset.id));
    });
    $('recentList').querySelectorAll('.del-btn').forEach((btn) => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        deleteProject(btn.dataset.del, btn.dataset.name);
      });
    });
  } catch (err) {
    console.warn('recent failed', err);
  }
}

async function deleteProject(pid, name) {
  const ok = confirm(
    `"${name}" ကို ဖျက်မှာ သေချာလား?\n\n`
    + 'ဖျက်ရင် ဒီ project ရဲ့ video၊ script၊ အသံနဲ့ render ဖိုင်အားလုံး ပျက်သွားမယ်။\n'
    + 'မဖျက်ချင်ရင် Cancel နှိပ်ပါ — project ကို ဒီအတိုင်း ဆက်ထားနိုင်ပါတယ်။'
  );
  if (!ok) return;
  try {
    await api(`/api/projects/${pid}`, { method: 'DELETE' });
  } catch (err) {
    toast(err.message || String(err), 8000);
    return;
  }
  toast('Project ဖျက်ပြီး', 3000);
  const isCurrent = state.projectId === pid;
  if (isCurrent) {
    stopPolling();
    state.projectId = null;
    state.lastProject = null;
    localStorage.removeItem(ACTIVE_KEY);
  }
  await refreshRecent();
  if (isCurrent) location.reload();
}

async function openProject(pid) {
  const token = ++state.openToken;
  try {
    stopPolling();
    const p = await api(`/api/projects/${pid}`);
    if (token !== state.openToken) return;
    state.projectId = pid;
    state.file = null;
    state.upload = null;
    localStorage.setItem(ACTIVE_KEY, pid);
    showProject(p);
    if (p.settings) applySettingsToForm(p.settings);
    const sc = p.script || {};
    const scriptBusy = SCRIPT_BUSY.has(sc.status) || TTS_BUSY.has((sc.tts || {}).status);
    const renderStatus = (p.render || {}).status;
    const renderBusy = renderStatus === 'queued' || renderStatus === 'rendering';
    if (INGESTING.has(p.status) || scriptBusy || renderBusy) startPolling(pid);
  } catch (err) {
    toast(err.message || String(err));
    if (/not found/i.test(err.message || '')) localStorage.removeItem(ACTIVE_KEY);
  }
}

/* ---------- wiring ---------- */

async function persistSettings() {
  if (!state.projectId) return;
  try {
    const data = await api(`/api/projects/${state.projectId}/settings`, {
      method: 'PUT',
      body: JSON.stringify({ settings: collectSettings() }),
    });
    if (state.lastProject && data.project) state.lastProject.settings = data.project.settings;
    // The backend copies ratio/resolution/fairuse into edit.json. Re-load the
    // editor when that changed the pending render, so the stage and labels match.
    const e = state.edit;
    if (e && state.lastProject && data.settings) {
      const before = [e.ratio, e.resolution, Number(e.fairuse)].join('|');
      const after = [data.settings.ratio, data.settings.resolution, Number(data.settings.fairuse)].join('|');
      if (before !== after) await loadEdit(state.lastProject);
    }
    syncFairuseInfo();
  } catch (err) {
    console.warn('settings save failed', err);
  }
}

function bindControls() {
  ['targetPct', 'fairuse', 'language', 'narrator', 'ratio', 'resolution', 'watermark', 'sceneSec', 'voiceRate', 'voicePitch']
    .forEach((id) => $(id).addEventListener('change', () => {
      syncVoiceLabels();
      if (id === 'fairuse') syncFairuseInfo();
      saveSettings();
      updateEstimate();
      persistSettings();
    }));
  ['voiceRate', 'voicePitch'].forEach((id) => $(id).addEventListener('input', syncVoiceLabels));

  $('voicePreviewBtn').addEventListener('click', previewVoice);
  $('presetSaveBtn').addEventListener('click', savePreset);
  $('presetApplyBtn').addEventListener('click', applyPreset);
  $('presetDeleteBtn').addEventListener('click', deletePreset);

  $('startBtn').addEventListener('click', startJob);
  $('refreshBtn').addEventListener('click', refreshRecent);

  $('scriptBtn').addEventListener('click', async () => {
    if (!state.projectId) return;
    const has = !!(state.lastProject && state.lastProject.script && state.lastProject.script.available);
    try {
      const res = await api(`/api/projects/${state.projectId}/script`, {
        method: 'POST',
        body: JSON.stringify({ force: has }),
      });
      if (res.ok) {
        toast(has ? 'Script + Voice ပြန်ထုတ်နေသည် — ပြီးရင် Setting ချိန်ပြီး Render နှိပ်ပါ' : 'Script + Voice ထုတ်နေသည် — အပိုင်းတစ်ပိုင်းချင်း', 5000);
        startPolling(state.projectId);
      } else {
        toast(res.message || 'မစနိုင်ပါ');
      }
    } catch (err) {
      toast(err.message || String(err), 8000);
    }
  });

  $('ttsBtn').addEventListener('click', async () => {
    if (!state.projectId) return;
    try {
      const res = await api(`/api/projects/${state.projectId}/tts`, { method: 'POST' });
      if (res.ok) {
        toast('Voice ထုတ်နေသည်', 3000);
        startPolling(state.projectId);
      } else {
        toast(res.message || 'မစနိုင်ပါ');
      }
    } catch (err) {
      toast(err.message || String(err), 8000);
    }
  });

  $('retryBtn').addEventListener('click', async () => {
    if (!state.projectId) return;
    try {
      const res = await api(`/api/projects/${state.projectId}/ingest`, { method: 'POST' });
      if (res.ok) {
        toast('Ingest ပြန်စပြီ');
        startPolling(state.projectId);
      } else {
        toast(res.message || 'ပြန်မစနိုင်ပါ');
      }
    } catch (err) {
      toast(err.message || String(err));
    }
  });
}

async function checkGemini() {
  const btn = $('geminiCheckBtn');
  const note = $('scriptNote');
  const old = note.textContent;
  btn.disabled = true;
  note.textContent = 'Gemini key ကို စစ်ဆေးနေသည်… (model တစ်ခုချင်း ခေါ်ကြည့်မည်)';
  try {
    const r = await api('/api/gemini/check', { method: 'POST' });
    if (r.detail || r.message) throw new Error(r.detail || r.message);
    if (r.ok) {
      if (state.config && state.config.gemini) state.config.gemini.active = r.model;
      note.textContent = `Gemini OK ✓ · ${r.model} က ဖြေနေသည် · Voice: Edge TTS။`;
    } else {
      note.textContent = `Gemini မရပါ — ${r.reason || 'unknown'}`;
    }
  } catch (err) {
    note.textContent = old || `Gemini စစ်ဆေးမှု မအောင်မြင်ပါ — ${err.message || err}`;
    toast(`Gemini စစ်ဆေးမှု မအောင်မြင်ပါ: ${err.message || err}`, 9000);
  } finally {
    btn.disabled = false;
  }
}

async function init() {
  bindFilePicker();
  bindControls();
  bindEditorControls();
  bindStageDrag();
  $('geminiCheckBtn').addEventListener('click', checkGemini);
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (state.edit) layoutStage(); }, 160);
  });
  try {
    await loadConfig();
  } catch (err) {
    toast(`Config မရပါ: ${err.message}`);
    return;
  }

  // Only restore the last project if the user has not opened one while the
  // page (and its config request) was still loading.
  const active = localStorage.getItem(ACTIVE_KEY);
  if (active && state.openToken === 0) await openProject(active);

  try { state.resume = JSON.parse(localStorage.getItem(RESUME_KEY) || 'null'); } catch { state.resume = null; }

  refreshRecent();
}

document.addEventListener('DOMContentLoaded', init);
