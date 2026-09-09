/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // ===== BELLOMBERG v0.9 OBSIDIAN - institutional futurism =====
        // Background tiers (obsidian blue-black hierarchy)
        bg: '#050608',           // canvas
        'bg-elev': '#090C14',    // elevated surface
        panel: '#0C111E',        // panel base
        'panel-hi': '#121A2C',   // hover / focused
        'panel-lo': '#04060B',   // recessed

        // Borders (hairline, blue-steel)
        border: '#1A2440',
        'border-bright': '#2A3760',
        'border-glow': '#3D4F8A',

        // Text
        text: '#ECF1FA',
        // Scala dei grigi a TRE gradini (decisione PM 26/07 sui PNG di
        // mockup_audit_ui/palette/SCALA_GRIGI.png, opzione C): il colore dice di
        // che TIPO e' il dato, la gerarchia la portano peso e spaziatura. Nessun
        // gradino sotto 4,87:1 su nessuno dei fondi veri dell'app — misurato.
        // 'text-dim' e 'muted' sono ora lo STESSO gradino (etichetta): la scala a
        // quattro esisteva solo perche' due gradini erano illeggibili (2,05 e 2,21:1).
        'text-dim': '#8D9FC4',   // etichetta — 7,2:1 sul pannello
        muted: '#8D9FC4',        // etichetta (era #66738E, 4,01:1)
        faint: '#73829F',        // nota      (era #3D4763, 2,08:1)

        // === Signature amber (champagne, meno acido del v2) ===
        amber: '#FFA51E',
        'amber-bright': '#FFC555',
        'amber-deep': '#B97A00',

        // Gold (secondary accent)
        gold: '#D4AF37',
        'gold-light': '#EED27E',
        'gold-deep': '#8F731F',

        // Cyan (HUD, dati live) - tonificato
        cyan: '#29D3F2',
        'cyan-bright': '#74E6FF',
        'cyan-deep': '#0B5E73',

        // P/L semantic - premium, non neon
        emerald: '#21E0A0',
        'emerald-deep': '#0D8059',
        crimson: '#FF3D60',
        'crimson-deep': '#A01838',

        // Other accents
        violet: '#9B7BFF',
        magenta: '#FF5EC4',
        lime: '#CAFF4D',
      },
      fontFamily: {
        sans: ['Inter', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"IBM Plex Mono"', '"Fira Code"', 'Consolas', 'monospace'],
      },
      fontSize: {
        '2xs': '0.625rem',  // 10px
        '3xs': '0.5rem',    // 8px
      },
      boxShadow: {
        'glow-amber': '0 0 8px rgba(255, 165, 30, 0.22), 0 0 24px rgba(255, 165, 30, 0.08)',
        'glow-cyan':  '0 0 8px rgba(41, 211, 242, 0.22), 0 0 24px rgba(41, 211, 242, 0.08)',
        'glow-gold':  '0 0 8px rgba(212, 175, 55, 0.22), 0 0 24px rgba(212, 175, 55, 0.08)',
        'glow-emerald': '0 0 8px rgba(33, 224, 160, 0.22), 0 0 24px rgba(33, 224, 160, 0.07)',
        'glow-crimson': '0 0 8px rgba(255, 61, 96, 0.22), 0 0 24px rgba(255, 61, 96, 0.07)',
        'inset-line': 'inset 0 1px 0 0 rgba(255,255,255,0.04)',
        'depth': '0 12px 40px rgba(0,0,0,0.45)',
      },
      backgroundImage: {
        'grid-hud': 'linear-gradient(rgba(61,79,138,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(61,79,138,0.06) 1px, transparent 1px)',
        'gradient-fade-amber': 'linear-gradient(180deg, rgba(255,165,30,0.05) 0%, transparent 100%)',
        'gradient-fade-cyan': 'linear-gradient(180deg, rgba(41,211,242,0.05) 0%, transparent 100%)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'scan': 'scan 8s linear infinite',
        'blink': 'blink 1.2s steps(2) infinite',
        'glow-pulse': 'glow-pulse 2s ease-in-out infinite',
        'fadeIn': 'fadeIn 0.3s ease-out',
        'slideUp': 'slideUp 0.32s cubic-bezier(0.22, 1, 0.36, 1)',
        'rise': 'slideUp 0.5s cubic-bezier(0.22, 1, 0.36, 1) both',
        'ticker': 'ticker 80s linear infinite',
      },
      keyframes: {
        scan: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100%)' },
        },
        blink: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.2' },
        },
        'glow-pulse': {
          '0%, 100%': { filter: 'drop-shadow(0 0 4px currentColor)' },
          '50%': { filter: 'drop-shadow(0 0 12px currentColor)' },
        },
        fadeIn: {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        slideUp: {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        ticker: {
          '0%': { transform: 'translateX(0%)' },
          '100%': { transform: 'translateX(-50%)' },
        },
      },
    },
  },
  plugins: [],
};
