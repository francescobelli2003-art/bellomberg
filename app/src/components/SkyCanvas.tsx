/* ============================================================
   F4 — IL CIELO DELLA PLANCIA (Opus 5, 26/07)

   Fondo del solo pannello-strumento. Il PM ha indicato la schermata
   d'accesso (LoginGate/AccessSky) come livello di riferimento; qui il
   cielo NON e' il soggetto ma il fondo di uno strumento pieno di numeri,
   quindi tutte le densita' e le opacita' sono ridotte di un fattore
   dichiarato (DIM) e la banda della Via Lattea sta in alto a destra,
   fuori dal centro ottico dove vive il quadrante.

   Seed FISSO: due montaggi della pagina danno lo stesso cielo. Un fondo
   che cambia a ogni render renderebbe impossibile confrontare due
   screenshot di collaudo.

   Rispetta prefers-reduced-motion: niente deriva, niente meteore.
   ============================================================ */
import { useEffect, useRef } from 'react';

/** quanto il cielo puo' farsi vedere sotto i numeri (1 = come la login) */
const DIM = 0.62;

function seeded(seed: number) { let s = seed; return () => (s = (s * 16807) % 2147483647) / 2147483647; }

function buildBackdrop(W: number, H: number): HTMLCanvasElement {
  const b = document.createElement('canvas');
  b.width = W; b.height = H;
  const x = b.getContext('2d')!;
  const R = seeded(97531);
  const AF = Math.min(1, (W * H) / 2200000) * DIM;

  const base = x.createLinearGradient(0, 0, 0, H);
  base.addColorStop(0, '#050813'); base.addColorStop(0.55, '#04060f'); base.addColorStop(1, '#020409');
  x.fillStyle = base; x.fillRect(0, 0, W, H);

  /* banda di Via Lattea, tenuta alta e a destra */
  x.save(); x.translate(W * 0.74, H * 0.2); x.rotate(-0.42);
  for (let i = 0; i < 180; i++) {
    const along = (R() - 0.5) * W * 1.7;
    const off = (R() - 0.5) * H * 0.34 * (0.4 + 0.6 * Math.exp(-Math.abs(along) / (W * 0.5)));
    const r = 30 + R() * 150;
    const g = x.createRadialGradient(along, off, 0, along, off, r);
    const col = R() < 0.3 ? '200,180,220' : R() < 0.5 ? '150,175,235' : '175,195,240';
    g.addColorStop(0, `rgba(${col},${(0.012 + R() * 0.028) * DIM})`);
    g.addColorStop(1, `rgba(${col},0)`);
    x.fillStyle = g; x.beginPath(); x.arc(along, off, r, 0, 7); x.fill();
  }
  for (let i = 0; i < Math.round(3600 * AF); i++) {
    const along = (R() - 0.5) * W * 1.8;
    const spread = H * 0.22 * (0.35 + 0.65 * Math.exp(-Math.abs(along) / (W * 0.55)));
    const off = ((R() + R() + R() - 1.5) / 1.5) * spread;
    x.fillStyle = `rgba(${R() < 0.2 ? '255,225,190' : '220,230,255'},${((0.08 + R() * 0.5) * DIM).toFixed(2)})`;
    x.fillRect(along, off, R() < 0.92 ? 1 : 2, R() < 0.92 ? 1 : 2);
  }
  x.restore();

  /* nebulose d'ambiente, agli angoli lontani dal quadrante */
  const nebula = (nx: number, ny: number, rad: number, c1: string, c2: string, n: number) => {
    for (let i = 0; i < n; i++) {
      const px = nx + (R() - 0.5) * rad * 1.6, py = ny + (R() - 0.5) * rad;
      const r = rad * (0.12 + R() * 0.3);
      const g = x.createRadialGradient(px, py, 0, px, py, r);
      g.addColorStop(0, R() < 0.6 ? c1 : c2); g.addColorStop(1, 'rgba(0,0,0,0)');
      x.fillStyle = g; x.beginPath(); x.arc(px, py, r, 0, 7); x.fill();
    }
  };
  nebula(W * 0.1, H * 0.8, Math.min(W, H) * 0.42, `rgba(40,90,160,${0.028 * DIM})`, `rgba(90,60,150,${0.022 * DIM})`, 34);
  nebula(W * 0.93, H * 0.1, Math.min(W, H) * 0.3, `rgba(190,120,50,${0.02 * DIM})`, `rgba(150,80,120,${0.018 * DIM})`, 24);

  /* stelle di campo, con spike di diffrazione sulle piu' brillanti */
  for (let i = 0; i < Math.round(760 * AF); i++) {
    const px = R() * W, py = R() * H, mag = R();
    const col = mag < 0.12 ? '255,210,160' : mag < 0.3 ? '170,200,255' : '235,240,255';
    const a = (0.15 + R() * 0.6) * DIM;
    if (mag > 0.968) {
      const r = 2.2 + R() * 2;
      const g = x.createRadialGradient(px, py, 0, px, py, r * 5);
      g.addColorStop(0, `rgba(${col},${Math.min(1, a + 0.35).toFixed(2)})`);
      g.addColorStop(0.3, `rgba(${col},${0.12 * DIM})`);
      g.addColorStop(1, `rgba(${col},0)`);
      x.fillStyle = g; x.beginPath(); x.arc(px, py, r * 5, 0, 7); x.fill();
      x.strokeStyle = `rgba(${col},${0.25 * DIM})`; x.lineWidth = 0.8;
      x.beginPath();
      x.moveTo(px - r * 6, py); x.lineTo(px + r * 6, py);
      x.moveTo(px, py - r * 6); x.lineTo(px, py + r * 6); x.stroke();
      x.fillStyle = `rgba(${col},${0.95 * DIM})`; x.beginPath(); x.arc(px, py, r * 0.8, 0, 7); x.fill();
    } else {
      x.fillStyle = `rgba(${col},${a.toFixed(2)})`;
      x.fillRect(px, py, mag > 0.85 ? 1.6 : 1, mag > 0.85 ? 1.6 : 1);
    }
  }
  return b;
}

export default function SkyCanvas() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = ref.current; if (!c) return;
    const parent = c.parentElement; if (!parent) return;
    const ctx = c.getContext('2d'); if (!ctx) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let backdrop: HTMLCanvasElement | null = null;
    let stars: { x: number; y: number; z: number; tw: number; sp: number; col: string }[] = [];
    let raf = 0, dead = false, lastW = 0, lastH = 0;

    const tick = () => {
      if (dead) return;
      const dpr = Math.min(devicePixelRatio, 2);
      const box = parent.getBoundingClientRect();
      const W = Math.max(1, Math.round(box.width * dpr));
      const H = Math.max(1, Math.round(box.height * dpr));
      if (W !== lastW || H !== lastH) {
        lastW = W; lastH = H;
        c.width = W; c.height = H;
        c.style.width = box.width + 'px'; c.style.height = box.height + 'px';
        backdrop = buildBackdrop(W, H);
        const SR = seeded(13579);
        stars = Array.from({ length: Math.round(140 * DIM) }, () => ({
          x: SR(), y: SR(), z: 0.25 + SR() * 0.75, tw: SR() * Math.PI * 2, sp: 0.5 + SR(),
          col: SR() < 0.14 ? '255,215,170' : SR() < 0.3 ? '165,200,255' : '230,238,255',
        }));
      }
      const t = performance.now() / 1000;
      ctx.clearRect(0, 0, W, H);
      /* deriva lentissima: a schermo si percepisce, fra due scatti no */
      const drift = reduced ? 0 : (t * 0.35 * dpr) % W;
      if (backdrop) { ctx.drawImage(backdrop, -drift, 0); ctx.drawImage(backdrop, W - drift, 0); }
      for (const s of stars) {
        const a = (0.25 + 0.75 * (0.5 + 0.5 * Math.sin(t * s.sp * 1.4 + s.tw))) * DIM;
        const r = s.z * 1.5 * dpr;
        ctx.beginPath();
        ctx.fillStyle = `rgba(${s.col},${(a * (0.35 + s.z * 0.65)).toFixed(2)})`;
        ctx.arc(s.x * W, s.y * H, r, 0, 7); ctx.fill();
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => { dead = true; cancelAnimationFrame(raf); };
  }, []);

  return <canvas ref={ref} aria-hidden="true" />;
}
