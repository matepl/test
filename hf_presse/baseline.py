#!/usr/bin/env python3
"""
Referenz-Baseline fuer den HF-Pressen-Datensatz -- ohne externe Abhaengigkeiten.

Liefert Vergleichszahlen, gegen die ein tabellarisches Foundation-Modell
(TabFM o. ae.) antreten kann:

  Aufgabe A (Klassifikation) : blow_within_5  -- brennen die Sicherungen
                               innerhalb der naechsten 5 Zyklen durch?
                               Metrik: ROC-AUC
  Aufgabe B (Regression)     : rul_cycles     -- Restlebensdauer in Zyklen
                               Metrik: MAE

Die Aufteilung ist ein Gruppen-Split: eine Anlage liegt vollstaendig in train
oder test. Damit kann kein Verlauf ueber den Split hinweg auslaufen.

    python3 baseline.py --data ../data/cycles.csv
    python3 baseline.py --data ../data/cycles.csv --hard
"""

from __future__ import annotations

import argparse
import csv
import math

# Spalten, die niemals ins Modell duerfen: Zielgroessen, latente Wahrheit,
# Schluessel und Freitext.
LEAK_COLS = {
    "fuse_blown", "blow_within_5", "rul_cycles", "damage_true",
    "censored", "split", "press_id", "ts_start", "glue_batch_id",
}

# Zaehlerstaende seit Sicherungswechsel. Physikalisch verfuegbar, aber sehr
# stark -- im --hard-Modus entfernt, um die Zustandssignale zu pruefen.
COUNTER_COLS = {"cycle_index", "i2t_cum_a2s", "press_hours_total"}

CATEGORICAL = {"press_model", "shift", "glue_type", "wood_species"}


# --------------------------------------------------------------------------
# Daten
# --------------------------------------------------------------------------

def load(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_matrix(rows: list[dict], feat_cols: list[str]):
    """Baut die Designmatrix: One-Hot fuer Kategorien, roh fuer Numerik."""
    levels = {
        c: sorted({r[c] for r in rows}) for c in feat_cols if c in CATEGORICAL
    }
    names: list[str] = []
    for c in feat_cols:
        if c in CATEGORICAL:
            names.extend(f"{c}={lv}" for lv in levels[c][1:])   # Referenzkategorie weg
        else:
            names.append(c)

    X = []
    for r in rows:
        row = []
        for c in feat_cols:
            if c in CATEGORICAL:
                row.extend(1.0 if r[c] == lv else 0.0 for lv in levels[c][1:])
            else:
                row.append(float(r[c]))
        X.append(row)
    return X, names


def standardize(X_tr: list[list[float]], X_te: list[list[float]]):
    p = len(X_tr[0])
    mu = [sum(r[j] for r in X_tr) / len(X_tr) for j in range(p)]
    sd = []
    for j in range(p):
        v = sum((r[j] - mu[j]) ** 2 for r in X_tr) / max(len(X_tr) - 1, 1)
        sd.append(math.sqrt(v) if v > 1e-12 else 1.0)
    norm = lambda X: [[(r[j] - mu[j]) / sd[j] for j in range(p)] for r in X]
    return norm(X_tr), norm(X_te)


# --------------------------------------------------------------------------
# Modelle
# --------------------------------------------------------------------------

def logistic_fit(X, y, lr=0.35, epochs=3000, l2=1e-3):
    n, p = len(X), len(X[0])
    w = [0.0] * p
    b = 0.0
    for _ in range(epochs):
        gw = [0.0] * p
        gb = 0.0
        for xi, yi in zip(X, y):
            z = b + sum(w[j] * xi[j] for j in range(p))
            e = 1.0 / (1.0 + math.exp(-max(min(z, 35.0), -35.0))) - yi
            gb += e
            for j in range(p):
                gw[j] += e * xi[j]
        b -= lr * gb / n
        for j in range(p):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
    return w, b


def logistic_predict(X, w, b):
    out = []
    for xi in X:
        z = b + sum(w[j] * xi[j] for j in range(len(w)))
        out.append(1.0 / (1.0 + math.exp(-max(min(z, 35.0), -35.0))))
    return out


def solve(A: list[list[float]], rhs: list[float]) -> list[float]:
    """Gauss-Elimination mit Spaltenpivotisierung."""
    n = len(A)
    M = [row[:] + [rhs[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        if abs(M[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r == c:
                continue
            f = M[r][c] / M[c][c]
            for k in range(c, n + 1):
                M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(n)]


def ridge_fit(X, y, l2=1.0):
    n, p = len(X), len(X[0])
    Xa = [[1.0] + r for r in X]
    q = p + 1
    A = [[sum(Xa[i][a] * Xa[i][b] for i in range(n)) for b in range(q)] for a in range(q)]
    for j in range(1, q):
        A[j][j] += l2 * n
    rhs = [sum(Xa[i][a] * y[i] for i in range(n)) for a in range(q)]
    return solve(A, rhs)


def ridge_predict(X, coef):
    return [coef[0] + sum(coef[j + 1] * r[j] for j in range(len(r))) for r in X]


# --------------------------------------------------------------------------
# Metriken
# --------------------------------------------------------------------------

def roc_auc(y: list[int], s: list[float]) -> float:
    """Mann-Whitney-U mit Behandlung von Bindungen."""
    pairs = sorted(zip(s, y))
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    npos = sum(y)
    nneg = len(y) - npos
    if npos == 0 or nneg == 0:
        return float("nan")
    rsum = sum(rk for rk, (_, yy) in zip(ranks, pairs) if yy == 1)
    return (rsum - npos * (npos + 1) / 2.0) / (npos * nneg)


def mae(y, p):
    return sum(abs(a - b) for a, b in zip(y, p)) / len(y)


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Baseline HF-Presse")
    ap.add_argument("--data", default="../data/cycles.csv")
    ap.add_argument("--hard", action="store_true",
                    help="Zaehlerstaende entfernen (cycle_index, i2t_cum, Betriebsstunden)")
    args = ap.parse_args()

    rows = load(args.data)
    drop = set(LEAK_COLS) | (COUNTER_COLS if args.hard else set())
    feat_cols = [c for c in rows[0].keys() if c not in drop]

    print(f"Datensatz : {args.data}")
    print(f"Modus     : {'hard (ohne Zaehlerstaende)' if args.hard else 'standard'}")
    print(f"Merkmale  : {len(feat_cols)}  -> {', '.join(feat_cols)}\n")

    # ---------- Aufgabe A: blow_within_5 ----------
    lab = [r for r in rows if r["blow_within_5"] != ""]     # zensierte raus
    tr = [r for r in lab if r["split"] == "train"]
    te = [r for r in lab if r["split"] == "test"]

    Xtr_raw, _ = build_matrix(tr, feat_cols)
    Xte_raw, _ = build_matrix(te, feat_cols)
    Xtr, Xte = standardize(Xtr_raw, Xte_raw)
    ytr = [int(r["blow_within_5"]) for r in tr]
    yte = [int(r["blow_within_5"]) for r in te]

    w, b = logistic_fit(Xtr, ytr)
    auc = roc_auc(yte, logistic_predict(Xte, w, b))

    print("Aufgabe A -- blow_within_5 (Klassifikation)")
    print(f"  train {len(tr):4d} Zeilen ({sum(ytr)} positiv)   "
          f"test {len(te):4d} Zeilen ({sum(yte)} positiv)")
    print(f"  Zufall                      AUC = 0.500")
    print(f"  logistische Regression      AUC = {auc:.3f}")

    # Staerkste Einzelmerkmale: zeigt, wie viel eine simple Schwelle schon holt.
    # Richtungskorrigiert, weil manche Merkmale invers wirken (z. B. cos_phi).
    singles = []
    for c in feat_cols:
        if c in CATEGORICAL:
            continue
        a = roc_auc(yte, [float(r[c]) for r in te])
        singles.append((max(a, 1.0 - a), c))
    singles.sort(reverse=True)
    print("  beste Einzelmerkmale (Schwelle):")
    for a, c in singles[:3]:
        print(f"    {c:24s}  AUC = {a:.3f}")
    print()

    # ---------- Aufgabe B: rul_cycles ----------
    ytr_r = [float(r["rul_cycles"]) for r in tr]
    yte_r = [float(r["rul_cycles"]) for r in te]
    coef = ridge_fit(Xtr, ytr_r, l2=1e-3)
    pred = [max(0.0, v) for v in ridge_predict(Xte, coef)]
    mean_tr = sum(ytr_r) / len(ytr_r)

    print("Aufgabe B -- rul_cycles (Regression, Restlebensdauer)")
    print(f"  Mittelwert-Vorhersage       MAE = {mae(yte_r, [mean_tr] * len(yte_r)):.2f} Zyklen")
    print(f"  Ridge-Regression            MAE = {mae(yte_r, pred):.2f} Zyklen")
    print(f"  Streuung Ziel (test)        SD  = "
          f"{math.sqrt(sum((v - sum(yte_r) / len(yte_r)) ** 2 for v in yte_r) / len(yte_r)):.2f}")


if __name__ == "__main__":
    main()
