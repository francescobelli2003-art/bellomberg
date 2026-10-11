"""Oracolo INDIPENDENTE per tests/product/vol-quant.mts (10/10/2026, Opus 5.5).

scipy.stats.norm + scipy.optimize.brentq + numpy.interp: nessuna riga di vol-quant.ts.
I numeri stampati sono copiati come letterali nel test; rilancia con
    python app/tests/product/vol-quant-oracle.py
se cambi un caso, e ricopia l'output.
"""
import json
import math

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

out = {}

# 1. delta BSM spot e delta forward non scontato
def bs_delta(typ, S, K, T, s, r, q):
    d1 = (math.log(S / K) + (r - q + s * s / 2) * T) / (s * math.sqrt(T))
    return math.exp(-q * T) * (norm.cdf(d1) if typ == "call" else norm.cdf(d1) - 1)

def fwd_delta(typ, F, K, T, s):
    d1 = (math.log(F / K) + s * s * T / 2) / (s * math.sqrt(T))
    return norm.cdf(d1) if typ == "call" else norm.cdf(d1) - 1

out["bs"] = [bs_delta(*c) for c in [
    ("call", 100, 110, 0.5, 0.3, 0.04, 0.01), ("put", 100, 110, 0.5, 0.3, 0.04, 0.01),
    ("call", 50, 30, 2.0, 0.6, 0.0, 0.03), ("put", 250, 200, 0.05, 0.15, 0.05, 0.0)]]
out["fwd"] = [fwd_delta(*c) for c in [("call", 100, 100, 1.0, 0.2), ("put", 100, 90, 0.25, 0.35), ("call", 4000, 4400, 0.1, 0.12)]]
out["cdf"] = [float(norm.cdf(x)) for x in (-8.5, -3.2, -0.7, 0.0, 1.3, 6.0)]

# 2. Black-76 non scontato
def b76(typ, F, K, T, s):
    d1 = (math.log(F / K) + s * s * T / 2) / (s * math.sqrt(T)); d2 = d1 - s * math.sqrt(T)
    return F * norm.cdf(d1) - K * norm.cdf(d2) if typ == "call" else K * norm.cdf(-d2) - F * norm.cdf(-d1)
out["b76"] = [b76("call", 100, 95, 0.4, 0.25), b76("put", 100, 95, 0.4, 0.25)]

# 3. strike per delta su smile SVI analitico (sigma(k) passata come funzione)
T_SVI = 0.3
def svi_w(k, a=0.02, b=0.12, rho=-0.5, m=0.02, sig=0.15):
    return a + b * (rho * (k - m) + math.sqrt((k - m) ** 2 + sig * sig))
def svi_sigma(k):
    return math.sqrt(svi_w(k) / T_SVI)
F0 = 150.0
rt = {}
for target, typ in [(0.25, "call"), (0.10, "call"), (-0.25, "put"), (-0.10, "put"), (0.5, "call")]:
    f = lambda k: fwd_delta(typ, F0, F0 * math.exp(k), T_SVI, svi_sigma(k)) - target
    k = brentq(f, -2, 2, xtol=1e-15)
    rt[f"{typ}{target}"] = {"k": k, "strike": F0 * math.exp(k), "iv": svi_sigma(k)}
out["strike_for_delta"] = rt

# 4. griglia delta su smile campionato: interpolazione lineare in varianza totale su k
F1, T1 = 200.0, 0.5
ks = np.round(np.arange(-0.6, 0.41, 0.05), 10)
strikes = [F1 * math.exp(k) for k in ks]
ivs = [math.sqrt(svi_w(k) / T1) for k in ks]
w_nodes = np.array([v * v * T1 for v in ivs])
def sig_lin(k):
    return math.sqrt(float(np.interp(k, ks, w_nodes)) / T1)
grid = {}
for key, target, typ in [("10P", -0.10, "put"), ("25P", -0.25, "put"), ("25C", 0.25, "call"), ("10C", 0.10, "call")]:
    f = lambda k: fwd_delta(typ, F1, F1 * math.exp(k), T1, sig_lin(k)) - target
    k = brentq(f, ks[0], ks[-1], xtol=1e-15)
    grid[key] = {"k": k, "iv": sig_lin(k)}
grid["ATM"] = {"k": 0.0, "iv": sig_lin(0.0)}
out["grid"] = {"strikes": strikes, "ivs": ivs, "cells": grid}
out["skew"] = {"rr25": grid["25C"]["iv"] - grid["25P"]["iv"],
               "bf25": (grid["25C"]["iv"] + grid["25P"]["iv"]) / 2 - grid["ATM"]["iv"],
               "rr10": grid["10C"]["iv"] - grid["10P"]["iv"],
               "bf10": (grid["10C"]["iv"] + grid["10P"]["iv"]) / 2 - grid["ATM"]["iv"]}
out["skew"]["norm25"] = out["skew"]["rr25"] / grid["ATM"]["iv"]

# 5. forward da parità: catena Black-76 SCONTATA con forward noto, spread simmetrici
FP, rP, TP = 104.3, 0.045, 0.75
df = math.exp(-rP * TP)
chain = []
for K in [90, 95, 100, 105, 110, 115]:
    c = df * b76("call", FP, K, TP, 0.28); p = df * b76("put", FP, K, TP, 0.28)
    chain.append({"K": K, "c": c, "p": p})
out["parity"] = {"F": FP, "r": rP, "T": TP, "chain": chain}

# 6. vol forward
out["fwdvol"] = math.sqrt((0.27 ** 2 * 0.5 - 0.25 ** 2 * 0.25) / 0.25)

# 7. diff a tenor costante (varianza totale lineare in T)
def iv_tenor(pts, T):
    Ts = [p[0] for p in pts]; ws = [p[1] ** 2 * p[0] for p in pts]
    return math.sqrt(float(np.interp(T, Ts, ws)) / T)
out["diff45"] = iv_tenor([(20 / 365, 0.30), (50 / 365, 0.28)], 45 / 365) - iv_tenor([(25 / 365, 0.26), (80 / 365, 0.27)], 45 / 365)

# 8. butterfly: smile a gobba (T = 0,1) — triplette ai nodi e Durrleman continuo
def b76n(k, T, s):
    d1 = (-k + s * s * T / 2) / (s * math.sqrt(T)); d2 = d1 - s * math.sqrt(T)
    return norm.cdf(d1) - math.exp(k) * norm.cdf(d2), norm.pdf(d1) * math.sqrt(T)
hump = lambda k: 0.20 + 0.25 * math.exp(-(k / 0.06) ** 2)
hk = [-0.2 + 0.025 * i for i in range(17)]
hc = [b76n(k, 0.1, hump(k)) for k in hk]; hK = [math.exp(k) for k in hk]
flags = []
for i in range(1, len(hk) - 1):
    l1 = (hK[i + 1] - hK[i]) / (hK[i + 1] - hK[i - 1]); l3 = (hK[i] - hK[i - 1]) / (hK[i + 1] - hK[i - 1])
    B = l1 * hc[i - 1][0] - hc[i][0] + l3 * hc[i + 1][0]
    # v2: rumore per nodo ε_i = 0,005·(1 + 1,5·|k_i|/(σ_i√T)) (regola dichiarata in arbitrageChecks)
    e = [0.005 * (1 + 1.5 * abs(hk[j]) / (hump(hk[j]) * math.sqrt(0.1))) for j in (i - 1, i, i + 1)]
    tol = l1 * hc[i - 1][1] * e[0] + hc[i][1] * e[1] + l3 * hc[i + 1][1] * e[2]
    if B < -tol:
        flags.append({"k": hk[i], "B": B})
kk = np.linspace(-0.2, 0.2, 4001); ww = (0.20 + 0.25 * np.exp(-(kk / 0.06) ** 2)) ** 2 * 0.1
g1 = np.gradient(ww, kk); g2 = np.gradient(g1, kk)
g = (1 - kk * g1 / (2 * ww)) ** 2 - g1 ** 2 / 4 * (1 / ww + 0.25) + g2 / 2
out["hump"] = {"flags": flags, "durrleman_negative": [float(kk[g < 0].min()), float(kk[g < 0].max())]}
kk = np.linspace(-0.3, 0.3, 601); ww = (0.30 - 1.5 * kk ** 2) ** 2 * 0.5
g1 = np.gradient(ww, kk); g2 = np.gradient(g1, kk)
out["mild_concave_min_g"] = float(((1 - kk * g1 / (2 * ww)) ** 2 - g1 ** 2 / 4 * (1 / ww + 0.25) + g2 / 2).min())

# 9. densita' dell'interpolante: w lineare fra due nodi (w'' = 0), Durrleman ai 4 punti interni
dk = [-0.1, 0.0]; dw = [0.2, 0.01]; sl = (dw[1] - dw[0]) / (dk[1] - dk[0])
gs = []
for j in range(1, 5):
    k = dk[0] + (dk[1] - dk[0]) * j / 5; w = dw[0] + sl * (k - dk[0])
    gs.append((1 - k * sl / (2 * w)) ** 2 - sl * sl / 4 * (1 / w + 0.25))
out["density_min_g"] = min(gs)

# 10. v2: banda lognormale del cono
out["cone_log"] = [100 * math.exp(-1.3), 100 * math.exp(1.3), 1 - math.exp(-1.3), math.exp(1.3) - 1]

# 11. v2: massa puntuale di un gomito concavo di w lineare a tratti (differenze finite sul prezzo)
def kink(w0, T=0.5, eps=0.005):
    ks_ = np.array([-0.2, -0.1, 0.0, 0.1, 0.2]); ws = np.array([0.035, 0.026, w0, 0.026, 0.035])
    def c_of(K):
        k = math.log(K); w = float(np.interp(k, ks_, ws)); s = math.sqrt(w / T)
        d1 = (-k + w / 2) / math.sqrt(w); return norm.cdf(d1) - K * norm.cdf(d1 - math.sqrt(w))
    h = 1e-8
    right = (c_of(1 + 2 * h) - c_of(1 + h)) / h; left = (c_of(1 - h) - c_of(1 - 2 * h)) / h
    sig = np.sqrt(ws / T); up = (sig + eps) ** 2 * T; dn = max(sig[2] - eps, 0) ** 2 * T
    fav = (up[3] - dn) / 0.1 - (dn - up[1]) / 0.1
    return {"mass": right - left, "dw_fav": float(fav)}
out["kink028"] = kink(0.028); out["kink032"] = kink(0.032)

# 12. v2: tasso continuo da DGS3MO (bond-equivalent, percento)
out["rate_dgs3mo_4_37"] = 2 * math.log1p(0.0437 / 2)

# 13. v2: due radici a 9e-6 l'una dall'altra (dentro un passo da 1e-3) + una terza a sinistra
def dc_vec(k):
    s = 0.25 + 0.05 * np.exp(-((k - 0.1) / 0.002) ** 2)
    return norm.cdf(-k / (s * np.sqrt(0.25)) + s * np.sqrt(0.25) / 2)
kk_ = np.linspace(0.0995, 0.11, 1000001); dd_ = dc_vec(kk_)
tgt = float(dd_.max()) - 2e-7
allk = np.linspace(-1, 1, 20000001); sgn = np.sign(dc_vec(allk) - tgt)
out["close_roots"] = {"target": tgt, "roots": [float(allk[x]) for x in np.where(sgn[:-1] != sgn[1:])[0]]}

# 14. v2: salto alla giunzione put/call (put 0,32 sotto F = 100, call 0,22 sopra), T = 0,25
jk = [90, 95, 100, 105, 110]; jiv = [0.32, 0.32, 0.22, 0.22, 0.22]; JT = 0.25
jc = [b76n(math.log(K / 100), JT, v) for K, v in zip(jk, jiv)]; jK = [K / 100 for K in jk]
jflags = []
for i in range(1, 4):
    l1 = (jK[i + 1] - jK[i]) / (jK[i + 1] - jK[i - 1]); l3 = (jK[i] - jK[i - 1]) / (jK[i + 1] - jK[i - 1])
    B = l1 * jc[i - 1][0] - jc[i][0] + l3 * jc[i + 1][0]
    e = [0.005 * (1 + 1.5 * abs(math.log(jk[j] / 100)) / (jiv[j] * math.sqrt(JT))) for j in (i - 1, i, i + 1)]
    tol = l1 * jc[i - 1][1] * e[0] + jc[i][1] * e[1] + l3 * jc[i + 1][1] * e[2]
    if B < -tol:
        jflags.append({"strikes": jk[i - 1:i + 2], "B": B})
out["junction"] = jflags

# 15. v2: verticale sul modello (pendenza della call normalizzata < -1), T = 0,25
vk = [-0.1, -0.05, 0.0]; vs = [0.25, 1.5, 0.1]
vc = [b76n(k, 0.25, v) for k, v in zip(vk, vs)]
vslope = (vc[2][0] - vc[1][0]) / (math.exp(vk[2]) - math.exp(vk[1]))
out["vertical_excess"] = -1 - vslope

print(json.dumps(out, indent=1))
