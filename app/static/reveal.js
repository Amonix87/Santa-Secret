(() => {
  'use strict';
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const canvas = document.getElementById('fx');
  const ctx = canvas.getContext('2d');
  const rnd = (a, b) => a + Math.random() * (b - a);
  let W = 0;
  let H = 0;

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resize();
  window.addEventListener('resize', resize);

  /* ---------- Neige et confettis (un seul canvas) ---------- */
  const flakes = Array.from({ length: Math.round(Math.min(90, W / 8)) }, () => ({
    x: rnd(0, W), y: rnd(0, H), r: rnd(1, 3), v: rnd(0.25, 0.9), s: rnd(0, 6.28), a: rnd(0.35, 0.85),
  }));
  const COLORS = ['#C8323F', '#DDBB70', '#FFFFFF', '#3E8F6E', '#8FC1D8'];
  const LIFE = 170;
  let bits = [];
  let raf = 0;

  function burst() {
    const gift = document.querySelector('.gift');
    const box = gift ? gift.getBoundingClientRect() : { left: W / 2, top: H / 2, width: 0 };
    const ox = box.left + box.width / 2;
    const oy = box.top + 30;
    for (let i = 0; i < 140; i++) {
      const ang = rnd(-Math.PI * 0.95, -Math.PI * 0.05);
      const sp = rnd(4, 13);
      bits.push({
        x: ox, y: oy, vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp,
        w: rnd(5, 10), h: rnd(3, 6), rot: rnd(0, 6.28), vr: rnd(-0.25, 0.25),
        c: COLORS[i % COLORS.length], life: 0,
      });
    }
    start();
  }

  function frame() {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#fff';
    for (const f of flakes) {
      f.y += f.v;
      f.s += 0.01;
      f.x += Math.sin(f.s) * 0.3;
      if (f.y > H + 4) { f.y = -4; f.x = rnd(0, W); }
      ctx.globalAlpha = f.a;
      ctx.beginPath();
      ctx.arc(f.x, f.y, f.r, 0, 6.283);
      ctx.fill();
    }
    for (const p of bits) {
      p.vy += 0.32;
      p.vx *= 0.992;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      p.life += 1;
      ctx.globalAlpha = Math.max(0, 1 - p.life / LIFE);
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.c;
      ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      ctx.restore();
    }
    bits = bits.filter(p => p.life < LIFE && p.y < H + 20);
    ctx.globalAlpha = 1;
    raf = requestAnimationFrame(frame);
  }
  function start() { if (!raf && !reduce) raf = requestAnimationFrame(frame); }

  start();
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { cancelAnimationFrame(raf); raf = 0; } else start();
  });

  // Page déjà « ouverte » (envoi classique du formulaire) : confettis au chargement
  if (document.querySelector('.gift.open')) setTimeout(burst, 500);

  /* ---------- Ouverture du cadeau sans recharger la page ---------- */
  const form = document.getElementById('open-form');
  if (!form) return;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    form.querySelector('button').disabled = true;
    const gift = document.querySelector('.gift');
    gift.classList.add('opening');
    const [html] = await Promise.all([
      fetch(window.location.href, { method: 'POST', credentials: 'same-origin' })
        .then(r => (r.ok ? r.text() : null)).catch(() => null),
      new Promise(resolve => setTimeout(resolve, reduce ? 0 : 750)),
    ]);
    const next = html && new DOMParser().parseFromString(html, 'text/html').getElementById('swap');
    if (!next) { form.submit(); return; } // repli : envoi classique
    document.getElementById('swap').innerHTML = next.innerHTML;
    burst();
  });
})();
