(() => {
  'use strict';
  const $ = (sel, root = document) => root.querySelector(sel);
  const csrf = $('meta[name="csrf"]').content;

  /* ---------- Notifications et presse-papiers ---------- */
  const toastEl = $('#toast');
  let toastTimer;
  function toast(msg, kind = 'ok') {
    toastEl.textContent = msg;
    toastEl.className = 'toast show ' + kind;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.className = 'toast'; }, 3500);
  }

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.className = 'offscreen';
      document.body.append(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch { /* ignoré */ }
      ta.remove();
      return ok;
    }
  }

  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-copy]');
    if (btn) {
      toast(await copy(btn.dataset.copy) ? 'Lien copié.' : 'Copie impossible : sélectionne le lien à la main.');
    }
    if (e.target.id === 'copy-all') {
      const lines = [...document.querySelectorAll('.plink')].map(r => `${r.dataset.name} : ${r.dataset.url}`);
      toast(await copy(lines.join('\n')) ? 'Tous les liens sont copiés.' : 'Copie impossible.');
    }
  });

  document.addEventListener('focusin', (e) => {
    if (e.target.classList.contains('link-field')) e.target.select();
  });

  document.addEventListener('submit', (e) => {
    const msg = e.target.dataset.confirm;
    if (!e.defaultPrevented && msg && !confirm(msg)) e.preventDefault();
  });

  /* ---------- Grille des notes ---------- */
  const table = $('#matrix');
  if (!table) return;

  const data = JSON.parse($('#data').textContent);
  const P = data.participants;
  const N = data.notes;                 // { min, max, default, forced }
  const DEFAULT = N.default;
  const FORCED = N.forced;
  const locked = data.locked;
  const state = new Map(Object.entries(data.state));
  const cells = new Map();
  let brush = 0;
  let painting = false;
  let dirty = false;

  const level = (g, r) => state.get(`${g}-${r}`) ?? DEFAULT;
  const shown = (n) => String(n);
  const words = (n) => (n === FORCED ? '10 sur 10, certain (100 %)' : n === 0 ? 'interdit' : `${n} sur ${N.max}`);
  const mirror = $('#mirror');
  const avoid = $('#avoid');
  const note = $('#matrix-note');

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  // Pinceaux : 0 à 10
  const brushBox = $('#brushes');
  const values = Array.from({ length: N.max + 1 }, (_, i) => i);
  const brushBtns = values.map((v) => {
    const b = el('button', `brush n${v}`, shown(v));
    b.type = 'button';
    b.title = v === FORCED ? 'Note 10 : certain (100 %)' : `Note ${v}`;
    b.setAttribute('aria-label', b.title);
    b.addEventListener('click', () => selectBrush(v));
    brushBox.append(b);
    return b;
  });
  function selectBrush(v) {
    brush = v;
    brushBtns.forEach((b, i) => b.setAttribute('aria-pressed', String(values[i] === v)));
  }
  selectBrush(0);

  // Tableau
  const head = table.createTHead().insertRow();
  head.append(el('td'));
  for (const p of P) {
    const th = el('th');
    th.scope = 'col';
    const b = el('button', 'hd hd-col', p.name);
    b.type = 'button';
    b.dataset.r = p.id;
    b.title = `Peindre toute la colonne de ${p.name}`;
    b.disabled = locked;
    th.append(b);
    head.append(th);
  }
  const body = table.createTBody();
  for (const g of P) {
    const tr = body.insertRow();
    const th = el('th');
    th.scope = 'row';
    const hb = el('button', 'hd hd-row', g.name);
    hb.type = 'button';
    hb.dataset.g = g.id;
    hb.title = `Peindre toute la ligne de ${g.name}`;
    hb.disabled = locked;
    th.append(hb);
    tr.append(th);
    for (const r of P) {
      const td = tr.insertCell();
      if (g.id === r.id) { td.className = 'diag'; td.append(el('span', 'sr-only', 'Même personne')); continue; }
      const b = el('button', 'cell');
      b.type = 'button';
      b.dataset.g = g.id;
      b.dataset.r = r.id;
      b.disabled = locked;
      const sym = el('span', 'sym');
      const pct = el('span', 'pct');
      b.append(sym, pct);
      td.append(b);
      cells.set(`${g.id}-${r.id}`, { b, sym, pct, g, r, base: '' });
      refresh(g.id, r.id);
    }
  }

  function refresh(g, r) {
    const c = cells.get(`${g}-${r}`);
    if (!c) return;
    const l = level(g, r);
    c.b.className = `cell n${l}`;
    c.sym.textContent = shown(l);
    c.base = `${c.g.name} → ${c.r.name} : ${words(l)}`;
    c.b.setAttribute('aria-label', `${c.g.name} offre à ${c.r.name} : ${words(l)}`);
    c.b.title = c.base;
  }

  function setLevel(g, r, l) {
    if (g === r) return;
    if (l === DEFAULT) state.delete(`${g}-${r}`); else state.set(`${g}-${r}`, l);
    refresh(g, r);
  }

  // Pose une note ; 10 = certain : une seule case à 10 par ligne et par colonne
  function assign(g, r, v) {
    if (g === r) return;
    if (v === FORCED) {
      for (const p of P) {
        if (p.id !== r && level(g, p.id) === FORCED) setLevel(g, p.id, DEFAULT);
        if (p.id !== g && level(p.id, r) === FORCED) setLevel(p.id, r, DEFAULT);
      }
    }
    setLevel(g, r, v);
  }

  // Grise les cases rendues impossibles par une paire à 10
  function refreshMasks() {
    const forced = [];
    for (const [k, v] of state) if (v === FORCED) forced.push(k.split('-').map(Number));
    cells.forEach((c, key) => {
      const [g, r] = key.split('-').map(Number);
      const masked = level(g, r) !== FORCED && forced.some(([a, b]) => g === a || r === b);
      c.b.classList.toggle('masked', masked);
      c.b.title = masked ? `${c.base} (écarté par une paire à 10)` : c.base;
    });
  }
  refreshMasks();

  function markDirty() {
    refreshMasks();
    if (!dirty) { dirty = true; note.textContent = 'Modifications non enregistrées.'; }
    if (table.classList.contains('with-pct')) {
      table.classList.remove('with-pct');
      cells.forEach(c => { c.pct.textContent = ''; });
    }
  }

  function applyCell(g, r) {
    assign(g, r, brush);
    if (mirror.checked && brush !== FORCED) assign(r, g, brush);
    markDirty();
  }

  table.addEventListener('mousedown', (e) => {
    const c = e.target.closest('.cell');
    if (!c || e.button !== 0) return;
    e.preventDefault();
    painting = true;
    applyCell(+c.dataset.g, +c.dataset.r);
  });
  table.addEventListener('mouseover', (e) => {
    if (!painting || brush === FORCED) return;
    const c = e.target.closest('.cell');
    if (c) applyCell(+c.dataset.g, +c.dataset.r);
  });
  document.addEventListener('mouseup', () => { painting = false; });

  table.addEventListener('click', (e) => {
    const c = e.target.closest('.cell');
    if (c && e.detail === 0) { applyCell(+c.dataset.g, +c.dataset.r); return; } // clavier
    const h = e.target.closest('.hd');
    if (!h) return;
    if (brush === FORCED) { toast('La note 10 se pose case par case.', 'error'); return; }
    for (const p of P) {
      if (h.dataset.g) setLevel(+h.dataset.g, p.id, brush);
      else setLevel(p.id, +h.dataset.r, brush);
    }
    markDirty();
  });

  avoid.addEventListener('change', markDirty);
  $('#repeat-years').addEventListener('change', markDirty);

  /* ---------- Réglage d'une paire : « Alice → Emma 10, Emma → Alice 5 » ---------- */
  const pe = {
    form: $('#pair-editor'), a: $('#pe-a'), b: $('#pe-b'),
    ab: $('#pe-ab'), ba: $('#pe-ba'),
  };
  for (const p of P) {
    pe.a.add(new Option(p.name, p.id));
    pe.b.add(new Option(p.name, p.id));
  }
  pe.b.selectedIndex = 1;
  [pe.a, pe.b, pe.ab, pe.ba, pe.form.querySelector('button')].forEach(x => { x.disabled = locked; });

  function loadPair() {
    const a = +pe.a.value, b = +pe.b.value;
    if (a === b) return;
    const v = level(a, b);
    const w = level(b, a);
    pe.ab.value = v;
    pe.ba.value = w;
  }
  pe.a.addEventListener('change', loadPair);
  pe.b.addEventListener('change', loadPair);
  loadPair();

  const readNote = (input) => {
    if (input.value.trim() === '') return null;
    const n = Number(input.value);
    return Number.isInteger(n) && n >= N.min && n <= N.max ? n : NaN;
  };

  pe.form.addEventListener('submit', (e) => {
    e.preventDefault();
    const a = +pe.a.value, b = +pe.b.value;
    if (a === b) { toast('Choisis deux personnes différentes.', 'error'); return; }
    const ab = readNote(pe.ab);
    const ba = readNote(pe.ba);
    if (ab === null || Number.isNaN(ab) || Number.isNaN(ba)) {
      toast(`Les notes sont des entiers de ${N.min} à ${N.max}.`, 'error');
      return;
    }
    assign(a, b, ab);
    if (ba !== null) assign(b, a, ba);
    markDirty();
    const nm = (id) => P.find(p => p.id === id).name;
    toast(`${nm(a)} → ${nm(b)} : ${words(ab)}` + (ba !== null ? `. ${nm(b)} → ${nm(a)} : ${words(ba)}.` : '.'));
  });

  /* ---------- Enregistrer et simuler ---------- */
  const repeat = $('#repeat-years');
  const payload = () => ({ levels: Object.fromEntries(state), avoid_swaps: avoid.checked, repeat_years: Number(repeat.value) });

  async function post(url, body) {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify(body),
      });
      return await res.json();
    } catch {
      return { ok: false, message: 'Le serveur ne répond pas. Recharge la page.' };
    }
  }

  $('#save').addEventListener('click', async () => {
    const res = await post('/admin/weights', payload());
    if (res.ok) { dirty = false; note.textContent = ''; toast('Réglages enregistrés.'); }
    else toast(res.message, 'error');
  });

  $('#simulate').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    note.textContent = 'Simulation en cours…';
    const res = await post('/admin/simulate', payload());
    btn.disabled = false;
    if (!res.ok) { note.textContent = dirty ? 'Modifications non enregistrées.' : ''; toast(res.message, 'error'); return; }
    cells.forEach((c, key) => { c.pct.textContent = `${res.pct[key] ?? 0} %`; });
    table.classList.add('with-pct');
    note.textContent = `Probabilité de chaque paire, estimée sur ${res.runs} tirages.` +
      (res.softened ? ' Impossible d\'éviter toutes les paires des années passées : certaines sont seulement très improbables.' : '') +
      (dirty ? ' Modifications non enregistrées.' : '');
  });

  const drawForm = $('#draw-form');
  if (drawForm) {
    drawForm.addEventListener('submit', (e) => {
      if (dirty) {
        e.preventDefault();
        toast('Enregistre d\'abord tes réglages.', 'error');
      }
    });
  }
  window.addEventListener('beforeunload', (e) => { if (dirty) e.preventDefault(); });
})();
