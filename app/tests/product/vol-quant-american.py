"""Fixture INDIPENDENTE per la de-americanizzazione (10/10/2026, Opus 5.5).

Quote AMERICANE da un albero CRR a 2000 passi in numpy (scritto qui, non la libreria), S = 100,
sigma 30%, strike passo 1 attorno al forward vero, bid/ask = prezzo -/+ 0,02, IV del contratto =
0,30 (quella vera). Casi del revisore: (r, q) in {(4,5%, 0), (4,5%, 1,5%), (5%, 3%)} x T in
{0,1; 0,5; 1; 2}. Scrive tests/product/vol-quant-american.json.
    python app/tests/product/vol-quant-american.py
"""
import json
import math
import os

import numpy as np


def amer(typ, S, K, T, r, q, s, N=2000):
    dt = T / N; u = math.exp(s * math.sqrt(dt)); d = 1 / u
    p = (math.exp((r - q) * dt) - d) / (u - d); disc = math.exp(-r * dt)
    j = np.arange(N + 1); ST = S * u ** (N - j) * d ** j
    V = np.maximum(ST - K, 0) if typ == 'call' else np.maximum(K - ST, 0)
    for n in range(N - 1, -1, -1):
        ST = S * u ** (n - np.arange(n + 1)) * d ** np.arange(n + 1)
        V = disc * (p * V[:-1] + (1 - p) * V[1:])
        V = np.maximum(V, np.maximum(ST - K, 0) if typ == 'call' else np.maximum(K - ST, 0))
    return float(V[0])


cases = []
for r, q in ((0.045, 0.0), (0.045, 0.015), (0.05, 0.03)):
    for T in (0.1, 0.5, 1.0, 2.0):
        S = 100.0; F = S * math.exp((r - q) * T); cs = []
        for K in range(int(F) - 6, int(F) + 7):
            for typ in ('call', 'put'):
                px = amer(typ, S, K, T, r, q, 0.3)
                cs.append({'type': typ, 'strike': K, 'bid': px - 0.02, 'ask': px + 0.02, 'oi': 10, 'volume': 0, 'iv': 0.3})
        cases.append({'r': r, 'q': q, 'T': T, 'S': S, 'F': F, 'contracts': cs})
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, 'vol-quant-american.json'), 'w', encoding='utf-8', newline='\n') as f:
    json.dump(cases, f)
print(len(cases), 'cases')
