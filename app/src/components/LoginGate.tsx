import { useEffect, useRef, useState } from 'react';
import { Bellomberg, TOKEN_STORAGE_KEY, API_BASE } from '@/lib/api';
import './access-screen.css';

const STORAGE_KEY = 'bellomberg_unlocked_v1';
const LAUNCH_KEY = 'bellomberg_last_launch_id';
const SESSION_TTL_MS = 12 * 60 * 60 * 1000; // 12h auto-logout (fallback)

/* ===== Design handoff "schermata stellare": costanti sequenza ===== */
const PIN_LENGTH = 4;
const IMPACT_ANGLE = Math.PI * 1.44;   // punto d'impatto sul lembo (basso-sinistra visibile)
const FLY_TIME = 1.5;                  // s di volo del bolide
const EXPLO_DUR = 3.6;                 // s di esplosione
const BOOT_MS = 3000;                  // ms overlay secure boot prima dell'handoff

type Phase = 'check' | 'login' | 'ready';

const CLOCKS: ReadonlyArray<readonly [string, string]> = [
  ['NY', 'America/New_York'], ['LON', 'Europe/London'], ['MIL', 'Europe/Rome'], ['TYO', 'Asia/Tokyo'],
];
const MODULES = [
  'MEMORIA SQLITE', 'DESK & AGENTI · CAPO', 'FEED NEWS / FRED', 'QUANT GARCH / MC', 'VALUTAZIONI DCF',
];

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const i = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(i);
  }, [intervalMs]);
  return now;
}

/* ============================================================
   Motore canvas del cielo (porting fedele di bellomberg-access.html):
   backdrop pre-renderizzato (Via Lattea, nebulose, galassie), pianeta
   notturno con luci citta', satellite, lanci ambientali e sequenza
   d'impatto meteorite -> esplosione -> secure boot.
   ============================================================ */
interface AccessSkyApi { startImpact: () => void }

interface Part { x: number; y: number; vx: number; vy: number; l: number; r: number; hot: boolean }
interface Impact {
  t0: number; boom: boolean; booted: boolean; parts: Part[];
  limb?: HTMLCanvasElement; jit?: { a: number; r: number; s: number; w: number }[];
}

function AccessSky({ engineRef, onBoot }: {
  engineRef: React.MutableRefObject<AccessSkyApi | null>;
  onBoot: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const onBootRef = useRef(onBoot);
  onBootRef.current = onBoot;

  useEffect(() => {
    const c = canvasRef.current; if (!c) return;
    const ctx = c.getContext('2d'); if (!ctx) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let backdrop: HTMLCanvasElement | null = null, planet: HTMLCanvasElement | null = null;
    let planetGeo = { pr: 0, pcx: 0, pcy: 0 };
    let stars: { x: number; y: number; z: number; tw: number; sp: number; col: string }[] = [];
    let shoot: { x: number; y: number; life: number; vx: number; vy: number } | null = null;
    let rocket: { x: number; y: number; vx: number; vy: number; trail: { x: number; y: number; a: number }[]; t: number } | null = null;
    let satA = Math.PI * 1.25;
    let impact: Impact | null = null;
    let spr: { fire: HTMLCanvasElement; glow: HTMLCanvasElement; dust: HTMLCanvasElement } | null = null;
    let raf = 0, disposed = false;

    function rand(seed: number) { let s = seed; return () => (s = (s * 16807) % 2147483647) / 2147483647; }

    function buildBackdrop(W: number, H: number) {
      const b = document.createElement('canvas'); b.width = W; b.height = H; const x = b.getContext('2d')!;
      const R = rand(97531), AF = Math.min(1, (W * H) / 2200000);
      const base = x.createLinearGradient(0, 0, 0, H);
      base.addColorStop(0, '#050813'); base.addColorStop(.55, '#04060f'); base.addColorStop(1, '#020409');
      x.fillStyle = base; x.fillRect(0, 0, W, H);
      const cx = W * 0.62, cy = H * 0.38;
      x.save(); x.translate(cx, cy); x.rotate(-0.42);
      for (let i = 0; i < 220; i++) {
        const along = (R() - 0.5) * W * 1.7, off = (R() - 0.5) * H * 0.34 * (0.4 + 0.6 * Math.exp(-Math.abs(along) / (W * 0.5))), r = 30 + R() * 150;
        const g = x.createRadialGradient(along, off, 0, along, off, r);
        const col = R() < 0.3 ? '200,180,220' : (R() < 0.5 ? '150,175,235' : '175,195,240');
        g.addColorStop(0, `rgba(${col},${(0.012 + R() * 0.028).toFixed(3)})`); g.addColorStop(1, `rgba(${col},0)`);
        x.fillStyle = g; x.beginPath(); x.arc(along, off, r, 0, 7); x.fill();
      }
      for (let i = 0; i < 60; i++) {
        const along = (R() - 0.5) * W * 1.5, off = (R() - 0.5) * H * 0.10, r = 20 + R() * 90;
        const g = x.createRadialGradient(along, off, 0, along, off, r);
        g.addColorStop(0, `rgba(3,4,10,${(0.05 + R() * 0.1).toFixed(3)})`); g.addColorStop(1, 'rgba(3,4,10,0)');
        x.fillStyle = g; x.beginPath(); x.arc(along, off, r, 0, 7); x.fill();
      }
      for (let i = 0; i < Math.round(4200 * AF); i++) {
        const along = (R() - 0.5) * W * 1.8, spread = H * 0.22 * (0.35 + 0.65 * Math.exp(-Math.abs(along) / (W * 0.55)));
        const off = (R() + R() + R() - 1.5) / 1.5 * spread, a = 0.08 + R() * 0.5, s = R() < 0.92 ? 1 : 2;
        x.fillStyle = `rgba(${R() < 0.2 ? '255,225,190' : '220,230,255'},${a.toFixed(2)})`; x.fillRect(along, off, s, s);
      }
      x.restore();
      const nebula = (nx: number, ny: number, rad: number, c1: string, c2: string, n: number) => {
        for (let i = 0; i < n; i++) {
          const px = nx + (R() - 0.5) * rad * 1.6, py = ny + (R() - 0.5) * rad, r = rad * (0.12 + R() * 0.3);
          const g = x.createRadialGradient(px, py, 0, px, py, r);
          g.addColorStop(0, R() < 0.6 ? c1 : c2); g.addColorStop(1, 'rgba(0,0,0,0)');
          x.fillStyle = g; x.beginPath(); x.arc(px, py, r, 0, 7); x.fill();
        }
      };
      nebula(W * 0.16, H * 0.68, Math.min(W, H) * 0.4, 'rgba(40,90,160,0.028)', 'rgba(90,60,150,0.022)', 40);
      nebula(W * 0.86, H * 0.16, Math.min(W, H) * 0.3, 'rgba(190,120,50,0.02)', 'rgba(150,80,120,0.018)', 30);
      for (let i = 0; i < 5; i++) {
        const gx = R() * W, gy = R() * H * 0.75, size = 14 + R() * 30;
        x.save(); x.translate(gx, gy); x.rotate(R() * Math.PI); x.scale(1, 0.32 + R() * 0.2);
        const g = x.createRadialGradient(0, 0, 0, 0, 0, size);
        g.addColorStop(0, 'rgba(235,225,255,0.5)'); g.addColorStop(0.25, 'rgba(190,185,230,0.16)'); g.addColorStop(1, 'rgba(150,150,210,0)');
        x.fillStyle = g; x.beginPath(); x.arc(0, 0, size, 0, 7); x.fill(); x.restore();
      }
      const gx = W * 0.2, gy = H * 0.22, gs = Math.min(W, H) * 0.09;
      x.save(); x.translate(gx, gy); x.rotate(-0.5); x.scale(1, 0.42);
      const g = x.createRadialGradient(0, 0, 0, 0, 0, gs * 2.4);
      g.addColorStop(0, 'rgba(255,245,225,0.55)'); g.addColorStop(0.12, 'rgba(230,220,240,0.22)');
      g.addColorStop(0.5, 'rgba(170,175,225,0.07)'); g.addColorStop(1, 'rgba(140,150,210,0)');
      x.fillStyle = g; x.beginPath(); x.arc(0, 0, gs * 2.4, 0, 7); x.fill();
      for (let arm = 0; arm < 2; arm++) for (let i = 0; i < 260; i++) {
        const th = i * 0.045 + arm * Math.PI, rr = gs * 0.25 + i * gs * 0.007;
        x.fillStyle = `rgba(220,225,250,${(0.05 + R() * 0.12).toFixed(2)})`;
        x.fillRect(Math.cos(th) * rr + (R() - 0.5) * 6, Math.sin(th) * rr + (R() - 0.5) * 6, 1.5, 1.5);
      }
      x.restore();
      for (let i = 0; i < Math.round(900 * AF); i++) {
        const px = R() * W, py = R() * H, mag = R();
        const col = mag < 0.12 ? [255, 210, 160] : mag < 0.3 ? [170, 200, 255] : [235, 240, 255];
        const a = 0.15 + R() * 0.6;
        if (mag > 0.965) {
          const r = 2.2 + R() * 2, gg = x.createRadialGradient(px, py, 0, px, py, r * 5);
          gg.addColorStop(0, `rgba(${col},${Math.min(1, a + 0.35).toFixed(2)})`); gg.addColorStop(0.3, `rgba(${col},0.12)`); gg.addColorStop(1, `rgba(${col},0)`);
          x.fillStyle = gg; x.beginPath(); x.arc(px, py, r * 5, 0, 7); x.fill();
          x.strokeStyle = `rgba(${col},0.25)`; x.lineWidth = 0.8;
          x.beginPath(); x.moveTo(px - r * 6, py); x.lineTo(px + r * 6, py); x.moveTo(px, py - r * 6); x.lineTo(px, py + r * 6); x.stroke();
          x.fillStyle = `rgba(${col},0.95)`; x.beginPath(); x.arc(px, py, r * 0.8, 0, 7); x.fill();
        } else { x.fillStyle = `rgba(${col},${a.toFixed(2)})`; x.fillRect(px, py, mag > 0.85 ? 1.6 : 1, mag > 0.85 ? 1.6 : 1); }
      }
      return b;
    }

    function buildPlanet(W: number, H: number) {
      const b = document.createElement('canvas'); b.width = W; b.height = H; const x = b.getContext('2d')!;
      const R = rand(24680), pr = W * 0.85, pcx = W * 0.5, pcy = H + pr * 0.86;
      planetGeo = { pr, pcx, pcy };
      x.save(); x.beginPath(); x.arc(pcx, pcy, pr, 0, 7); x.clip();
      const surf = x.createRadialGradient(pcx, pcy - pr * 0.98, 0, pcx, pcy - pr * 0.7, pr * 0.5);
      surf.addColorStop(0, '#0a1420'); surf.addColorStop(0.4, '#050b14'); surf.addColorStop(1, '#02050a');
      x.fillStyle = surf; x.fillRect(0, 0, W, H);
      for (let i = 0; i < 90; i++) {
        const a = Math.PI * (1.15 + R() * 0.7), rr = pr * (0.965 + R() * 0.035);
        const px = pcx + Math.cos(a) * rr * (R() * 0.5 + 0.75), py = pcy + Math.sin(a) * rr, r = 20 + R() * 110;
        const g = x.createRadialGradient(px, py, 0, px, py, r); const land = R() < 0.45;
        g.addColorStop(0, land ? `rgba(18,32,30,${(0.15 + R() * 0.25).toFixed(2)})` : `rgba(10,24,44,${(0.12 + R() * 0.2).toFixed(2)})`);
        g.addColorStop(1, 'rgba(0,0,0,0)'); x.fillStyle = g; x.beginPath(); x.arc(px, py, r, 0, 7); x.fill();
      }
      for (let i = 0; i < 50; i++) {
        const a = Math.PI * (1.1 + R() * 0.8);
        const px = pcx + Math.cos(a) * pr * (0.9 + R() * 0.1), py = pcy + Math.sin(a) * pr * (0.96 + R() * 0.04), r = 15 + R() * 60;
        const g = x.createRadialGradient(px, py, 0, px, py, r);
        g.addColorStop(0, `rgba(150,175,200,${(0.02 + R() * 0.05).toFixed(3)})`); g.addColorStop(1, 'rgba(150,175,200,0)');
        x.save(); x.translate(px, py); x.scale(2.2, 0.7); x.translate(-px, -py);
        x.fillStyle = g; x.beginPath(); x.arc(px, py, r, 0, 7); x.fill(); x.restore();
      }
      for (let k = 0; k < 10; k++) {
        const a0 = Math.PI * (1.16 + R() * 0.66);
        let cxx = pcx + Math.cos(a0) * pr * (0.975 + R() * 0.02), cyy = pcy + Math.sin(a0) * pr * (0.975 + R() * 0.02);
        let dir = a0 + Math.PI / 2 + (R() - 0.5); const segs = 20 + Math.floor(R() * 50);
        for (let i = 0; i < segs; i++) {
          dir += (R() - 0.5) * 0.7; cxx += Math.cos(dir) * (3 + R() * 7); cyy += Math.sin(dir) * (2 + R() * 4);
          const br = 0.15 + R() * 0.55;
          const gg = x.createRadialGradient(cxx, cyy, 0, cxx, cyy, 6 + R() * 10);
          gg.addColorStop(0, `rgba(255,190,110,${(br * 0.22).toFixed(3)})`); gg.addColorStop(1, 'rgba(255,190,110,0)');
          x.fillStyle = gg; x.beginPath(); x.arc(cxx, cyy, 6 + R() * 10, 0, 7); x.fill();
          x.fillStyle = `rgba(255,${200 + Math.floor(R() * 40)},${130 + Math.floor(R() * 50)},${br.toFixed(2)})`;
          x.fillRect(cxx + (R() - 0.5) * 4, cyy + (R() - 0.5) * 3, 1, 1);
          if (R() < 0.3) x.fillRect(cxx + (R() - 0.5) * 8, cyy + (R() - 0.5) * 5, 1, 1);
        }
        const mg = x.createRadialGradient(cxx, cyy, 0, cxx, cyy, 14);
        mg.addColorStop(0, 'rgba(255,215,150,0.5)'); mg.addColorStop(0.4, 'rgba(255,180,100,0.15)'); mg.addColorStop(1, 'rgba(255,180,100,0)');
        x.fillStyle = mg; x.beginPath(); x.arc(cxx, cyy, 14, 0, 7); x.fill();
      }
      const sun = x.createRadialGradient(pcx + pr * 0.62, pcy - pr * 0.74, 0, pcx + pr * 0.62, pcy - pr * 0.74, pr * 0.55);
      sun.addColorStop(0, 'rgba(120,180,240,0.20)'); sun.addColorStop(0.5, 'rgba(80,140,210,0.07)'); sun.addColorStop(1, 'rgba(60,120,200,0)');
      x.fillStyle = sun; x.fillRect(0, 0, W, H);
      for (let i = 0; i < 16; i++) {
        const a = Math.PI * (1.14 + R() * 0.12);
        const px = pcx + Math.cos(a) * pr * (0.975 + R() * 0.02), py = pcy + Math.sin(a) * pr * (0.975 + R() * 0.02), r = 26 + R() * 30;
        const g = x.createRadialGradient(px, py, 0, px, py, r);
        g.addColorStop(0, `rgba(80,230,160,${(0.03 + R() * 0.05).toFixed(3)})`); g.addColorStop(1, 'rgba(80,230,160,0)');
        x.fillStyle = g; x.beginPath(); x.arc(px, py, r, 0, 7); x.fill();
      }
      x.restore();
      const lw = pr * 0.012;
      const atm = x.createRadialGradient(pcx, pcy, pr - lw * 0.5, pcx, pcy, pr + lw * 3.2);
      atm.addColorStop(0, 'rgba(255,170,90,0.10)'); atm.addColorStop(0.14, 'rgba(190,220,255,0.55)');
      atm.addColorStop(0.22, 'rgba(130,195,255,0.34)'); atm.addColorStop(0.42, 'rgba(80,140,230,0.12)'); atm.addColorStop(1, 'rgba(50,100,200,0)');
      x.fillStyle = atm; x.beginPath(); x.arc(pcx, pcy, pr + lw * 3.2, 0, 7); x.arc(pcx, pcy, pr - lw * 0.5, 0, 7, true); x.fill();
      x.strokeStyle = 'rgba(190,225,160,0.35)'; x.lineWidth = Math.max(1, W / 2600);
      x.beginPath(); x.arc(pcx, pcy, pr + lw * 1.5, Math.PI * 1.08, Math.PI * 1.92); x.stroke();
      x.strokeStyle = 'rgba(215,240,255,0.85)'; x.lineWidth = Math.max(1, W / 2200);
      x.beginPath(); x.arc(pcx, pcy, pr, Math.PI * 1.08, Math.PI * 1.92); x.stroke();
      const sunLimb = x.createRadialGradient(pcx + pr * 0.7, pcy - pr * 0.7, 0, pcx + pr * 0.7, pcy - pr * 0.7, pr * 0.6);
      sunLimb.addColorStop(0, 'rgba(255,225,180,0.20)'); sunLimb.addColorStop(0.4, 'rgba(200,220,255,0.06)'); sunLimb.addColorStop(1, 'rgba(200,220,255,0)');
      x.save(); x.beginPath(); x.arc(pcx, pcy, pr + lw * 3.2, 0, 7); x.arc(pcx, pcy, pr - lw, 0, 7, true); x.clip();
      x.fillStyle = sunLimb; x.fillRect(0, 0, W, H); x.restore();
      const halo = x.createRadialGradient(pcx, pcy, pr, pcx, pcy, pr * 1.18);
      halo.addColorStop(0, 'rgba(70,140,235,0.10)'); halo.addColorStop(1, 'rgba(50,110,210,0)');
      x.fillStyle = halo; x.beginPath(); x.arc(pcx, pcy, pr * 1.18, 0, 7); x.arc(pcx, pcy, pr, 0, 7, true); x.fill();
      return b;
    }

    function tick() {
      if (disposed) return;
      const dpr = Math.min(devicePixelRatio, 2);
      const W = Math.round(innerWidth * dpr), H = Math.round(innerHeight * dpr);
      if (c!.width !== W || c!.height !== H) {
        c!.width = W; c!.height = H;
        backdrop = buildBackdrop(W, H); planet = buildPlanet(W, H); satA = Math.PI * 1.25;
        stars = Array.from({ length: 240 }, () => ({
          x: Math.random(), y: Math.random(), z: 0.25 + Math.random() * 0.75,
          tw: Math.random() * Math.PI * 2, sp: 0.5 + Math.random(),
          col: Math.random() < 0.14 ? '255,215,170' : (Math.random() < 0.3 ? '165,200,255' : '230,238,255'),
        }));
      }
      const x = ctx!, t = performance.now() / 1000;
      x.clearRect(0, 0, W, H);
      const drift = reduced ? 0 : (t * 1.2 * dpr) % W;
      x.drawImage(backdrop!, -drift, 0); x.drawImage(backdrop!, W - drift, 0);
      for (const s of stars) {
        if (!reduced) { s.x -= 0.00003 * s.z; if (s.x < 0) { s.x = 1; s.y = Math.random(); } }
        const a = 0.25 + 0.75 * (0.5 + 0.5 * Math.sin(t * s.sp * 1.4 + s.tw)), r = s.z * 1.5 * dpr;
        x.beginPath(); x.fillStyle = `rgba(${s.col},${(a * (0.35 + s.z * 0.65)).toFixed(2)})`;
        x.arc(s.x * W, s.y * H, r, 0, 7); x.fill();
        if (s.z > 0.9 && a > 0.85) {
          x.strokeStyle = `rgba(${s.col},${((a - 0.85) * 2).toFixed(2)})`; x.lineWidth = 0.7 * dpr;
          const px = s.x * W, py = s.y * H, l = r * 4;
          x.beginPath(); x.moveTo(px - l, py); x.lineTo(px + l, py); x.moveTo(px, py - l); x.lineTo(px, py + l); x.stroke();
        }
      }
      if (!reduced) {
        if (!shoot && Math.random() < 0.0035) shoot = { x: Math.random() * 0.6 + 0.2, y: Math.random() * 0.35, life: 1, vx: 0.007 + Math.random() * 0.005, vy: 0.003 + Math.random() * 0.003 };
        if (shoot) {
          shoot.x += shoot.vx; shoot.y += shoot.vy; shoot.life -= 0.022;
          const px = shoot.x * W, py = shoot.y * H, len = 110 * dpr;
          const g = x.createLinearGradient(px, py, px - len, py - len * (shoot.vy / shoot.vx));
          g.addColorStop(0, `rgba(255,255,255,${(shoot.life * 0.9).toFixed(2)})`); g.addColorStop(1, 'rgba(255,255,255,0)');
          x.strokeStyle = g; x.lineWidth = 1.4 * dpr;
          x.beginPath(); x.moveTo(px, py); x.lineTo(px - len, py - len * (shoot.vy / shoot.vx)); x.stroke();
          if (shoot.life <= 0 || shoot.x > 1.1) shoot = null;
        }
      }
      x.globalAlpha = 0.92 + 0.08 * Math.sin(t * 0.9); x.drawImage(planet!, 0, 0); x.globalAlpha = 1;
      const geo = planetGeo;
      // satellite
      satA += 0.00042; if (satA > Math.PI * 1.88) satA = Math.PI * 1.12;
      const orbR = geo.pr * 1.075, sx = geo.pcx + Math.cos(satA) * orbR, sy = geo.pcy + Math.sin(satA) * orbR;
      x.strokeStyle = 'rgba(120,200,255,0.28)'; x.lineWidth = 1;
      x.beginPath(); x.arc(geo.pcx, geo.pcy, orbR, satA - 0.09, satA); x.stroke();
      x.setLineDash([3 * dpr, 6 * dpr]); x.strokeStyle = 'rgba(120,200,255,0.10)';
      x.beginPath(); x.arc(geo.pcx, geo.pcy, orbR, satA, satA + 0.5); x.stroke(); x.setLineDash([]);
      const sg = x.createRadialGradient(sx, sy, 0, sx, sy, 7 * dpr);
      sg.addColorStop(0, 'rgba(190,230,255,0.95)'); sg.addColorStop(1, 'rgba(120,190,255,0)');
      x.fillStyle = sg; x.beginPath(); x.arc(sx, sy, 7 * dpr, 0, 7); x.fill();
      x.strokeStyle = 'rgba(140,205,255,0.55)'; x.lineWidth = 1;
      const rs = 9 * dpr; x.strokeRect(sx - rs, sy - rs, rs * 2, rs * 2);
      x.font = `${9 * dpr}px "JetBrains Mono",monospace`; x.fillStyle = 'rgba(140,205,255,0.7)';
      x.fillText('SAT-07', sx + rs + 5 * dpr, sy + 3 * dpr);
      // lancio orbitale ambientale
      if (!rocket && !reduced && Math.random() < 0.0025) {
        const a = Math.PI * (1.3 + Math.random() * 0.25);
        rocket = {
          x: geo.pcx + Math.cos(a) * geo.pr * 0.995, y: geo.pcy + Math.sin(a) * geo.pr * 0.995,
          vx: Math.cos(a) * 0.55 * dpr + 0.28 * dpr, vy: Math.sin(a) * 0.55 * dpr, trail: [], t: 0,
        };
      }
      if (rocket) {
        const rk = rocket; rk.t += 1 / 60; rk.vx *= 1.009; rk.vy *= 1.009; rk.x += rk.vx; rk.y += rk.vy;
        rk.trail.push({ x: rk.x, y: rk.y, a: 0.85 }); if (rk.trail.length > 110) rk.trail.shift();
        x.lineCap = 'round';
        for (let i = 2; i < rk.trail.length; i += 2) {
          const p = rk.trail[i], q = rk.trail[i - 2]; p.a *= 0.993;
          x.strokeStyle = `rgba(150,200,255,${(p.a * (i / rk.trail.length) * 0.5).toFixed(3)})`;
          x.lineWidth = (i / rk.trail.length) * 2.6 * dpr + 0.3;
          x.beginPath(); x.moveTo(q.x, q.y); x.lineTo(p.x, p.y); x.stroke();
        }
        const fl = 0.85 + 0.15 * Math.sin(t * 47) * Math.sin(t * 13);
        const g = x.createRadialGradient(rk.x, rk.y, 0, rk.x, rk.y, 14 * dpr * fl);
        g.addColorStop(0, 'rgba(255,250,240,0.95)'); g.addColorStop(0.15, 'rgba(255,215,150,0.55)');
        g.addColorStop(0.5, 'rgba(160,190,255,0.12)'); g.addColorStop(1, 'rgba(140,180,255,0)');
        x.fillStyle = g; x.beginPath(); x.arc(rk.x, rk.y, 14 * dpr * fl, 0, 7); x.fill();
        x.fillStyle = 'rgba(255,255,255,0.98)'; x.beginPath(); x.arc(rk.x, rk.y, 1.3 * dpr, 0, 7); x.fill();
        const rr2 = 13 * dpr, k = 4.5 * dpr;
        x.strokeStyle = 'rgba(245,166,35,0.7)'; x.lineWidth = Math.max(1, dpr * 0.7);
        x.beginPath();
        x.moveTo(rk.x - rr2 + k, rk.y - rr2); x.lineTo(rk.x - rr2, rk.y - rr2); x.lineTo(rk.x - rr2, rk.y - rr2 + k);
        x.moveTo(rk.x + rr2 - k, rk.y - rr2); x.lineTo(rk.x + rr2, rk.y - rr2); x.lineTo(rk.x + rr2, rk.y - rr2 + k);
        x.moveTo(rk.x - rr2 + k, rk.y + rr2); x.lineTo(rk.x - rr2, rk.y + rr2); x.lineTo(rk.x - rr2, rk.y + rr2 - k);
        x.moveTo(rk.x + rr2 - k, rk.y + rr2); x.lineTo(rk.x + rr2, rk.y + rr2); x.lineTo(rk.x + rr2, rk.y + rr2 - k);
        x.stroke();
        x.strokeStyle = 'rgba(245,166,35,0.35)';
        x.beginPath(); x.moveTo(rk.x + rr2, rk.y - rr2); x.lineTo(rk.x + rr2 + 22 * dpr, rk.y - rr2 - 12 * dpr); x.lineTo(rk.x + rr2 + 30 * dpr, rk.y - rr2 - 12 * dpr); x.stroke();
        const alt = Math.round(rk.t * 11 + 8), mm = String(Math.floor(rk.t / 60)).padStart(2, '0'), ss = String(Math.floor(rk.t % 60)).padStart(2, '0');
        x.font = `${8.5 * dpr}px "JetBrains Mono",monospace`;
        x.fillStyle = 'rgba(245,166,35,0.85)'; x.fillText('BLM-1 ASCENT', rk.x + rr2 + 34 * dpr, rk.y - rr2 - 9 * dpr);
        x.fillStyle = 'rgba(150,190,240,0.65)'; x.fillText(`T+${mm}:${ss}  ALT ${alt} KM`, rk.x + rr2 + 34 * dpr, rk.y - rr2 + 2 * dpr);
        if (rk.x < -100 || rk.x > W + 100 || rk.y < -100) rocket = null;
      }
      // sequenza impatto
      if (impact) {
        const im = impact, e = (performance.now() - im.t0) / 1000, aT = IMPACT_ANGLE;
        const ipx = geo.pcx + Math.cos(aT) * geo.pr, ipy = geo.pcy + Math.sin(aT) * geo.pr;
        const sx0 = W * 0.88, sy0 = -H * 0.08;
        if (e < FLY_TIME) {
          const p = (e / FLY_TIME) ** 1.8;
          const mx = sx0 + (ipx - sx0) * p, my = sy0 + (ipy - sy0) * p;
          const tx = sx0 + (ipx - sx0) * Math.max(0, p - 0.22), ty = sy0 + (ipy - sy0) * Math.max(0, p - 0.22);
          const hot = p > 0.55;
          const tg = x.createLinearGradient(tx, ty, mx, my);
          tg.addColorStop(0, 'rgba(255,140,60,0)');
          tg.addColorStop(0.6, hot ? 'rgba(255,150,60,0.5)' : 'rgba(200,215,255,0.35)');
          tg.addColorStop(1, hot ? 'rgba(255,230,180,0.95)' : 'rgba(240,245,255,0.9)');
          x.strokeStyle = tg; x.lineWidth = (hot ? 3.4 : 2) * dpr; x.lineCap = 'round';
          x.beginPath(); x.moveTo(tx, ty); x.lineTo(mx, my); x.stroke();
          if (hot) for (let i = 0; i < 3; i++) {
            const fq = Math.random() * 0.2;
            x.fillStyle = `rgba(255,${160 + Math.random() * 60 | 0},70,${(0.5 * Math.random()).toFixed(2)})`;
            x.beginPath(); x.arc(mx - (mx - tx) * fq + (Math.random() - .5) * 8 * dpr, my - (my - ty) * fq + (Math.random() - .5) * 8 * dpr, (0.6 + Math.random()) * dpr, 0, 7); x.fill();
          }
          const hg = x.createRadialGradient(mx, my, 0, mx, my, 16 * dpr);
          hg.addColorStop(0, 'rgba(255,250,235,0.95)');
          hg.addColorStop(0.3, hot ? 'rgba(255,180,90,0.5)' : 'rgba(190,210,255,0.4)');
          hg.addColorStop(1, 'rgba(255,160,70,0)');
          x.fillStyle = hg; x.beginPath(); x.arc(mx, my, 16 * dpr, 0, 7); x.fill();
          x.strokeStyle = 'rgba(255,90,60,0.6)'; x.lineWidth = 1;
          const rd = 12 * dpr;
          x.beginPath(); x.moveTo(mx - rd, my); x.lineTo(mx - rd * 1.8, my); x.moveTo(mx + rd, my); x.lineTo(mx + rd * 1.8, my);
          x.moveTo(mx, my - rd); x.lineTo(mx, my - rd * 1.8); x.moveTo(mx, my + rd); x.lineTo(mx, my + rd * 1.8); x.stroke();
          x.font = `${8.5 * dpr}px "JetBrains Mono",monospace`; x.fillStyle = 'rgba(255,110,70,0.9)';
          x.fillText('BOLIDE 2026-OB · TRAIETTORIA CONFERMATA', mx + rd * 2.2, my - 3 * dpr);
        } else {
          const te = e - FLY_TIME;
          if (!im.boom) {
            im.boom = true;
            for (let i = 0; i < 200; i++) {
              const spread = aT + (Math.random() - 0.5) * 2.8, sp2 = (1 + Math.random() * 7.5) * dpr;
              im.parts.push({ x: ipx, y: ipy, vx: Math.cos(spread) * sp2, vy: Math.sin(spread) * sp2, l: 1, r: (0.5 + Math.random() * 1.8) * dpr, hot: Math.random() < 0.7 });
            }
            const mkSpr = (stops: [number, string][]) => {
              const s = document.createElement('canvas'); s.width = s.height = 256; const sx2 = s.getContext('2d')!;
              const g3 = sx2.createRadialGradient(128, 128, 0, 128, 128, 128);
              for (const [o, col] of stops) g3.addColorStop(o, col); sx2.fillStyle = g3; sx2.fillRect(0, 0, 256, 256); return s;
            };
            if (!spr) spr = {
              fire: mkSpr([[0, 'rgba(255,252,242,1)'], [0.18, 'rgba(255,220,140,0.85)'], [0.45, 'rgba(255,130,45,0.45)'], [0.75, 'rgba(150,50,20,0.14)'], [1, 'rgba(100,35,15,0)']]),
              glow: mkSpr([[0, 'rgba(255,248,235,1)'], [0.4, 'rgba(255,225,180,0.35)'], [1, 'rgba(255,210,160,0)']]),
              dust: mkSpr([[0, 'rgba(120,92,72,0.55)'], [0.55, 'rgba(85,66,58,0.28)'], [1, 'rgba(70,55,50,0)']]),
            };
            im.jit = Array.from({ length: 8 }, () => ({ a: Math.random() * 6.28, r: 0.3 + Math.random() * 0.5, s: 0.5 + Math.random() * 0.55, w: 1.5 + Math.random() * 2.5 }));
            const lc = document.createElement('canvas'); lc.width = W; lc.height = H; const lx = lc.getContext('2d')!;
            lx.beginPath(); lx.arc(geo.pcx, geo.pcy, geo.pr, 0, 7); lx.clip();
            const li = lx.createRadialGradient(ipx, ipy, 0, ipx, ipy, geo.pr * 1.4);
            li.addColorStop(0, 'rgba(255,170,90,0.55)'); li.addColorStop(1, 'rgba(255,150,80,0)');
            lx.fillStyle = li; lx.fillRect(0, 0, W, H); im.limb = lc;
          }
          if (te < 1.1 && !reduced) {
            const s = Math.exp(-te * 3.2) * 26, z = 1 + Math.exp(-te * 2.5) * 0.025;
            c!.style.transform = `translate(${(Math.random() - .5) * s}px,${(Math.random() - .5) * s}px) scale(${z.toFixed(4)})`;
          } else c!.style.transform = '';
          if (te < EXPLO_DUR) {
            const sp = spr!;
            const fa = Math.max(0, 1 - te / EXPLO_DUR);
            const drawS = (sp2: HTMLCanvasElement, px: number, py: number, r: number, a: number) => {
              if (a <= 0.005) return; x.globalAlpha = Math.min(1, a); x.drawImage(sp2, px - r, py - r, r * 2, r * 2);
            };
            x.globalAlpha = fa * (0.55 + 0.1 * Math.sin(t * 30)); x.drawImage(im.limb!, 0, 0); x.globalAlpha = 1;
            x.globalCompositeOperation = 'lighter';
            const baseR = (30 + Math.sqrt(te) * 300) * dpr;
            for (const j of im.jit!) {
              const jr = baseR * j.s * (0.8 + 0.2 * Math.sin(t * j.w + j.a));
              const ox = Math.cos(j.a + t * 0.6) * baseR * 0.3 * j.r;
              const oy = Math.sin(j.a * 1.7 + t * 0.5) * baseR * 0.22 * j.r - te * 26 * dpr;
              drawS(sp.fire, ipx + ox, ipy + oy, jr, fa * 0.5);
            }
            if (te < 0.5) {
              const ca = 1 - te / 0.5;
              drawS(sp.glow, ipx, ipy, baseR * 1.3, ca * 0.9);
              x.globalAlpha = ca * 0.55;
              const sg2 = x.createLinearGradient(ipx - baseR * 3, ipy, ipx + baseR * 3, ipy);
              sg2.addColorStop(0, 'rgba(255,240,220,0)'); sg2.addColorStop(0.5, 'rgba(255,248,238,0.9)'); sg2.addColorStop(1, 'rgba(255,240,220,0)');
              x.fillStyle = sg2; x.fillRect(ipx - baseR * 3, ipy - 1.2 * dpr, baseR * 6, 2.4 * dpr);
              x.globalAlpha = 1;
            }
            if (te > 0.55 && te < 1.0) drawS(sp.glow, ipx + baseR * 0.5, ipy - baseR * 0.3, baseR * 0.35, Math.sin((te - 0.55) / 0.45 * Math.PI) * 0.7);
            if (te > 0.9 && te < 1.45) drawS(sp.glow, ipx - baseR * 0.45, ipy - baseR * 0.15, baseR * 0.3, Math.sin((te - 0.9) / 0.55 * Math.PI) * 0.6);
            const wa = te * 0.55;
            for (const dir of [-1, 1]) {
              const a0 = aT + dir * wa;
              drawS(sp.glow, geo.pcx + Math.cos(a0) * geo.pr, geo.pcy + Math.sin(a0) * geo.pr, geo.pr * 0.05, fa * 0.5);
            }
            x.globalCompositeOperation = 'source-over';
            const dr2 = (12 + te * 130) * dpr, da = Math.min(1, te * 1.4) * fa;
            drawS(sp.dust, ipx, ipy - dr2 * 0.35, dr2, da * 0.85);
            drawS(sp.dust, ipx - dr2 * 0.4, ipy - dr2 * 0.15, dr2 * 0.6, da * 0.6);
            drawS(sp.dust, ipx + dr2 * 0.45, ipy - dr2 * 0.2, dr2 * 0.55, da * 0.6);
            x.globalAlpha = 1;
            const rr3 = (te * 560 + 24) * dpr, ra = fa * 0.6;
            x.strokeStyle = `rgba(255,215,165,${ra.toFixed(2)})`; x.lineWidth = Math.max(1, (3 - te) * dpr);
            x.beginPath(); x.arc(ipx, ipy, rr3, 0, 7); x.stroke();
            x.strokeStyle = `rgba(255,190,140,${(ra * 0.5).toFixed(2)})`; x.lineWidth = 1.2 * dpr;
            x.beginPath(); x.arc(ipx, ipy, rr3 * 0.62, 0, 7); x.stroke();
            x.save(); x.translate(ipx, ipy); x.rotate(aT + Math.PI / 2); x.scale(1, 0.22);
            x.strokeStyle = `rgba(255,175,110,${(fa * 0.45).toFixed(2)})`; x.lineWidth = 2 * dpr;
            x.beginPath(); x.arc(0, 0, (te * 420 + 16) * dpr, 0, 7); x.stroke(); x.restore();
          }
          for (const pt of im.parts) {
            const gvx = geo.pcx - pt.x, gvy = geo.pcy - pt.y, gd = Math.hypot(gvx, gvy);
            pt.vx += (gvx / gd) * 0.05 * dpr; pt.vy += (gvy / gd) * 0.05 * dpr;
            pt.x += pt.vx; pt.y += pt.vy; pt.l *= 0.988;
            if (pt.l > 0.03) {
              const pr2 = pt.r * (0.5 + pt.l * 0.5);
              x.fillStyle = pt.hot ? `rgba(255,${140 + pt.l * 100 | 0},60,${pt.l.toFixed(2)})` : `rgba(120,110,100,${(pt.l * 0.6).toFixed(2)})`;
              x.fillRect(pt.x - pr2, pt.y - pr2, pr2 * 2, pr2 * 2);
              if (pt.hot && pt.l > 0.5) { x.fillStyle = `rgba(255,220,160,${((pt.l - 0.5) * 0.5).toFixed(2)})`; x.fillRect(pt.x - pt.vx * 2, pt.y - pt.vy * 2, pr2, pr2); }
            }
          }
          if (te < 0.45) { x.fillStyle = `rgba(255,244,225,${(0.92 * (1 - te / 0.45)).toFixed(2)})`; x.fillRect(0, 0, W, H); }
          if (te >= 3.4 && te < 8) {
            const ca = Math.max(0, 0.4 * (1 - (te - 3.4) / 4.6));
            const cg = x.createRadialGradient(ipx, ipy, 0, ipx, ipy, 50 * dpr);
            cg.addColorStop(0, `rgba(255,150,70,${ca.toFixed(2)})`); cg.addColorStop(1, 'rgba(255,120,50,0)');
            x.fillStyle = cg; x.beginPath(); x.arc(ipx, ipy, 50 * dpr, 0, 7); x.fill();
          }
          if (te > 2.9 && !im.booted) { im.booted = true; onBootRef.current(); }
          if (te > 8) { impact = null; c!.style.transform = ''; }
        }
      }
      raf = requestAnimationFrame(tick);
    }

    engineRef.current = {
      startImpact() { if (!impact) impact = { t0: performance.now(), boom: false, booted: false, parts: [] }; },
    };
    raf = requestAnimationFrame(tick);
    return () => {
      disposed = true; cancelAnimationFrame(raf);
      c.style.transform = ''; engineRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <canvas ref={canvasRef} className="sky" aria-hidden="true" />;
}

/* ============================================================
   LoginGate — stessa macchina a stati e stessa auth di prima
   (authLogin + token X-BB-Token, TTL 12h, launch-id Electron);
   cambia SOLO il vestito: schermata stellare + sequenza meteorite.
   ============================================================ */
export default function LoginGate({ children }: { children: React.ReactNode }) {
  const [phase, setPhase] = useState<Phase>('check');
  const [pin, setPin] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [granted, setGranted] = useState(false);
  const [boot, setBoot] = useState(false);
  const [notice, setNotice] = useState<{ text: string; tone: 'err' | 'hint' } | null>(null);
  const [defaultPin, setDefaultPin] = useState(false);
  const [shakeKey, setShakeKey] = useState(0);
  const engineRef = useRef<AccessSkyApi | null>(null);
  const verifyingRef = useRef(false);
  const grantedRef = useRef(false);
  const now = useNow();

  // On mount: sessione ancora valida? (entro TTL E stesso launch di Electron)
  useEffect(() => {
    const currentLaunchId = (window as any).bellomberg?.launchId || '';
    const lastLaunchId = localStorage.getItem(LAUNCH_KEY) || '';
    // Se il launch ID e' cambiato (= rilancio di Electron) invalida la sessione
    if (currentLaunchId && currentLaunchId !== lastLaunchId) {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        try {
          const { ts } = JSON.parse(raw);
          if (Date.now() - ts < SESSION_TTL_MS) {
            setPhase('ready');
            return;
          }
        } catch {}
        localStorage.removeItem(STORAGE_KEY);
      }
    }
    // Probe stato backend (avvisa se PIN default)
    Bellomberg.authStatus()
      .then(s => setDefaultPin(s.default_pin))
      .catch(() => {});
    setPhase('login');
  }, []);

  const verify = async (pinValue: string) => {
    if (verifyingRef.current || grantedRef.current) return;
    verifyingRef.current = true; setVerifying(true); setNotice(null);
    try {
      const r = await Bellomberg.authLogin(pinValue);
      // Hardening #32: salva il token di sessione (campo nuovo, retrocompatibile)
      try { if (r?.token) localStorage.setItem(TOKEN_STORAGE_KEY, r.token); } catch {}
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ ts: Date.now() }));
      const currentLaunchId = (window as any).bellomberg?.launchId || '';
      if (currentLaunchId) localStorage.setItem(LAUNCH_KEY, currentLaunchId);
      grantedRef.current = true; setGranted(true);
      // PIN accettato -> dopo 700ms parte il bolide; il boot arriva dal canvas
      setTimeout(() => engineRef.current?.startImpact(), 700);
    } catch (err: any) {
      const msg = err?.response?.status === 401
        ? 'PIN ERRATO · ACCESSO NEGATO'
        : err?.response?.status === 429
          ? (err?.response?.data?.detail || 'TROPPI TENTATIVI PIN · RIPROVA TRA QUALCHE MINUTO')
          : (err?.message || 'ERRORE CONNESSIONE BACKEND');
      setNotice({ text: String(msg).toUpperCase(), tone: 'err' });
      setShakeKey(k => k + 1);
      setPin('');
    } finally {
      verifyingRef.current = false; setVerifying(false);
    }
  };

  // Tastiera: 0-9 riempie i pallini, Backspace cancella (come da mockup)
  useEffect(() => {
    if (phase !== 'login') return;
    const onKey = (e: KeyboardEvent) => {
      if (grantedRef.current || verifyingRef.current) return;
      if (/^[0-9]$/.test(e.key)) {
        setNotice(null);
        setPin(p => (p.length < PIN_LENGTH ? p + e.key : p));
      } else if (e.key === 'Backspace') {
        setNotice(null);
        setPin(p => p.slice(0, -1));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [phase]);

  // Alla 4a cifra: verifica reale via backend (al posto del grant mock del handoff)
  useEffect(() => {
    if (phase !== 'login' || granted || verifying || pin.length !== PIN_LENGTH) return;
    const t = setTimeout(() => verify(pin), 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pin, phase, granted, verifying]);

  const handleBoot = () => {
    setBoot(true);
    // Fine secure boot = handoff all'app (equivalente dell'evento bellomberg:unlock)
    setTimeout(() => setPhase('ready'), BOOT_MS);
  };

  const onAuthClick = () => {
    if (grantedRef.current || verifyingRef.current) return;
    if (pin.length === PIN_LENGTH) verify(pin);
    else setNotice({ text: 'DIGITA LE 4 CIFRE DEL PIN', tone: 'hint' });
  };

  if (phase === 'check') return <div className="stl-stage" />;
  if (phase === 'ready') return <>{children}</>;

  const dateStr = now.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase();
  const altVal = 408 + Math.round(6 * Math.sin(now.getTime() / 9000));
  const hintText = notice ? notice.text
    : granted ? 'PIN ACCETTATO · CANALE APERTO'
    : verifying ? 'VERIFICA PIN IN CORSO'
    : pin.length > 0 ? '•'.repeat(pin.length)
    : 'DIGITA IL PIN';

  return (
    <div className="stl-stage">
      <AccessSky engineRef={engineRef} onBoot={handleBoot} />
      <div className="scan"><div /></div>
      <div className="vig" />
      <div className="corner c-tl" /><div className="corner c-tr" /><div className="corner c-bl" /><div className="corner c-br" />

      <div className="topbar">
        <div>BELLOMBERG · TERMINAL 05 · <b>{dateStr}</b></div>
        <div className="clocks">
          {CLOCKS.map(([l, tz]) => (
            <span key={l}>{l} <b>{now.toLocaleTimeString('en-GB', { hour12: false, timeZone: tz, hour: '2-digit', minute: '2-digit' })}</b></span>
          ))}
        </div>
      </div>

      <div className={'center' + (boot ? ' stl-hidden' : '')}>
        <div className="emblem"><div className="r1" /><div className="r2" /><div className="r3" /><div className="core"><div /></div></div>
        <div className="title">BELLOMBERG</div>
        <div className="sub1">PRIVATE INTELLIGENCE TERMINAL</div>
        <div className="sub2">V0.9 OBSIDIAN</div>
        <div className="auth">
          <div className="authlabel"><span className="lock" />PIN AUTHENTICATION</div>
          <div key={shakeKey} className={'dots' + (notice?.tone === 'err' ? ' shake' : '')}>
            {Array.from({ length: PIN_LENGTH }, (_, i) => (
              <div key={i} className={'dot' + (i < (granted ? PIN_LENGTH : pin.length) ? ' on' : '')} />
            ))}
          </div>
          <div className="pinline" />
          <div className={'pinhint' + (notice?.tone === 'err' ? ' err' : '')}>
            <span>{hintText}</span><span className="cr">▌</span>
          </div>
          <div className={'authbtn' + (verifying || granted ? ' busy' : '')} onClick={onAuthClick}>
            {granted ? '◈ ACCESS GRANTED' : verifying ? '◌ AUTHENTICATING…' : '◌ AUTHORIZE ACCESS'}
          </div>
          <div className="operator">OPERATORE · ACCESSO RISERVATO</div>
          {defaultPin && (
            <div className="warnpin">⚠ DEFAULT PIN ATTIVO · SETTA BELLOMBERG_PIN NEL FILE .ENV (DEFAULT: 1234)</div>
          )}
        </div>
      </div>

      <div className={'bootlist' + (boot ? ' stl-hidden' : '')}>
        {MODULES.map((m, i) => (
          <div key={m} className="row" style={{ animationDelay: (0.3 + i * 0.25) + 's' }}>
            <span>{m}</span><span className="led" />
          </div>
        ))}
        <div className={'row' + (granted ? '' : ' wait')} style={{ animationDelay: '1.55s' }}>
          <span>{granted ? 'AUTORIZZAZIONE CONCESSA' : 'IN ATTESA DI AUTORIZZAZIONE'}</span><span className="led" />
        </div>
      </div>

      <div className={'telemetry' + (boot ? ' stl-hidden' : '')}>
        <span>ALT <b>{altVal}</b> KM</span><span>VEL <b>7.66</b> KM/S</span>
        <span>ORBIT <b>LEO-2</b></span><span>LINK <b className="g">SAT-07 ▲</b></span>
      </div>

      <div className={'ruler' + (boot ? ' stl-hidden' : '')}>
        <div className="t"><div className="l" style={{ width: 18 }} /><span>400</span></div>
        <div className="t"><div className="l" style={{ width: 10 }} /><span>300</span></div>
        <div className="t"><div className="l" style={{ width: 18 }} /><span>200</span></div>
        <div className="t"><div className="l" style={{ width: 10 }} /><span>100</span></div>
        <div className="t"><div className="l" style={{ width: 18 }} /><span>KM 0</span></div>
      </div>

      <div className="bottombar">
        <div className="live"><span className="led" /> BACKEND LOCALE · {API_BASE}</div>
        <div>ORBITA STABILE · SESSION TTL 12H</div>
      </div>

      <div className={'bootseq' + (boot ? ' show' : '')}>
        {boot && (
          <div className="box">
            <div className="hd">BELLOMBERG SECURE BOOT</div>
            <div className="ln" style={{ animation: 'stlBootline .4s .3s ease both' }}><span>HANDSHAKE CANALE SICURO</span><span className="ok">OK</span></div>
            <div className="ln" style={{ animation: 'stlBootline .4s .8s ease both' }}><span>PIN OPERATORE</span><span className="ok">VERIFICATO</span></div>
            <div className="ln" style={{ animation: 'stlBootline .4s 1.3s ease both' }}><span>IMPATTO 2026-OB · 4.2 KT</span><span className="ok">CONFERMATO</span></div>
            <div className="ln" style={{ animation: 'stlBootline .4s 1.8s ease both' }}><span>SESSIONE BACKEND</span><span className="am">AUTORIZZATA</span></div>
            <div className="ln" style={{ animation: 'stlBootline .4s 2.3s ease both' }}><span>AVVIO TERMINALE</span><span className="cr">▌</span></div>
            <div className="bar"><div /></div>
          </div>
        )}
      </div>
    </div>
  );
}
