#!/usr/bin/env python3
"""
Laesst Googles TabFM auf dem HF-Pressen-Datensatz laufen und stellt das
Ergebnis der Referenz-Baseline gegenueber.

Die Metriken werden bewusst aus baseline.py importiert -- identischer Code fuer
AUC und MAE, damit die Zahlen direkt vergleichbar sind.

Voraussetzungen
---------------
    pip install "tabfm[pytorch]"        # zieht torch, sklearn, pandas, hf-hub

Die Gewichte (google/tabfm-1.0.0-pytorch, ~Hugging Face) werden beim ersten
Lauf heruntergeladen. Ist Hugging Face nicht erreichbar, die Gewichte einmal
anderswo holen und den Ordner mit --checkpoint uebergeben:

    huggingface-cli download google/tabfm-1.0.0-pytorch --local-dir tabfm-w
    python3 run_tabfm.py --checkpoint tabfm-w

Verwendung
----------
    python3 run_tabfm.py                          # Standardmodus
    python3 run_tabfm.py --hard                   # ohne Zaehlerstaende
    python3 run_tabfm.py --device cuda            # auf GPU
"""

from __future__ import annotations

import argparse
import csv
import sys

import baseline as bl     # LEAK_COLS, COUNTER_COLS, CATEGORICAL, roc_auc, mae


# Referenzwerte aus baseline.py, zum direkten Vergleich in der Ausgabe.
REFERENCE = {
    "standard": {"auc_lin": 0.937, "auc_best1": 0.980, "best1": "i2t_cum_a2s",
                 "mae_ridge": 5.17, "mae_mean": 11.31},
    "hard":     {"auc_lin": 0.904, "auc_best1": 0.904, "best1": "dt_fuse_cabinet_k",
                 "mae_ridge": 6.37, "mae_mean": 11.31},
}


def load_frames(path: str, feat_cols: list[str]):
    """Baut die Trainings- und Testrahmen. Kategorien bleiben als Text stehen --
    TabFM bringt seine eigene Vorverarbeitung mit."""
    import pandas as pd

    rows = [r for r in csv.DictReader(open(path, encoding="utf-8"))
            if r["blow_within_5"] != ""]          # rechtszensierte Anlagen raus

    def frame(subset):
        data = {}
        for c in feat_cols:
            if c in bl.CATEGORICAL:
                data[c] = [r[c] for r in subset]
            else:
                data[c] = [float(r[c]) for r in subset]
        return pd.DataFrame(data)

    tr = [r for r in rows if r["split"] == "train"]
    te = [r for r in rows if r["split"] == "test"]
    return (
        frame(tr), frame(te),
        [int(r["blow_within_5"]) for r in tr], [int(r["blow_within_5"]) for r in te],
        [float(r["rul_cycles"]) for r in tr], [float(r["rul_cycles"]) for r in te],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="TabFM auf dem HF-Pressen-Datensatz")
    ap.add_argument("--data", default="../data/cycles.csv")
    ap.add_argument("--hard", action="store_true",
                    help="Zaehlerstaende entfernen (cycle_index, i2t_cum, Betriebsstunden)")
    ap.add_argument("--checkpoint", default=None,
                    help="lokaler Gewichte-Ordner statt Download von Hugging Face")
    ap.add_argument("--device", default="cpu", help="cpu oder cuda")
    ap.add_argument("--n-estimators", type=int, default=8,
                    help="Ensemblegroesse; 32 ist der Vorgabewert von TabFM, "
                         "auf CPU aber langsam")
    args = ap.parse_args()

    try:
        from tabfm import TabFMClassifier, TabFMRegressor
        from tabfm.src.pytorch import tabfm_v1_0_0
    except ImportError as exc:
        sys.exit(f"TabFM nicht verfuegbar ({exc}).\n"
                 f'Installieren mit:  pip install "tabfm[pytorch]"')

    drop = set(bl.LEAK_COLS) | (bl.COUNTER_COLS if args.hard else set())
    header = next(csv.reader(open(args.data, encoding="utf-8")))
    feat_cols = [c for c in header if c not in drop]

    Xtr, Xte, ytr_c, yte_c, ytr_r, yte_r = load_frames(args.data, feat_cols)
    mode = "hard" if args.hard else "standard"
    ref = REFERENCE[mode]

    print(f"Datensatz : {args.data}")
    print(f"Modus     : {mode}   Merkmale: {len(feat_cols)}")
    print(f"train {len(Xtr)} Zeilen / test {len(Xte)} Zeilen, "
          f"Ensemble {args.n_estimators}, Geraet {args.device}\n")

    # ---------- Aufgabe A ----------
    print("Aufgabe A -- blow_within_5 (ROC-AUC, hoeher ist besser)")
    model = tabfm_v1_0_0.load("classification",
                              checkpoint_path=args.checkpoint, device=args.device)
    clf = TabFMClassifier(model, n_estimators=args.n_estimators, random_state=0)
    clf.fit(Xtr, ytr_c)
    proba = [float(p[1]) for p in clf.predict_proba(Xte)]
    auc = bl.roc_auc(yte_c, proba)

    print(f"  Zufall                       {0.500:.3f}")
    print(f"  bestes Einzelmerkmal         {ref['auc_best1']:.3f}  ({ref['best1']})")
    print(f"  logistische Regression       {ref['auc_lin']:.3f}")
    print(f"  TabFM                        {auc:.3f}   "
          f"{'BESSER' if auc > ref['auc_best1'] else 'schlechter'} als die Messlatte\n")

    # ---------- Aufgabe B ----------
    print("Aufgabe B -- rul_cycles (MAE in Zyklen, niedriger ist besser)")
    model_r = tabfm_v1_0_0.load("regression",
                                checkpoint_path=args.checkpoint, device=args.device)
    reg = TabFMRegressor(model_r, n_estimators=args.n_estimators, random_state=0)
    reg.fit(Xtr, ytr_r)
    pred = [max(0.0, float(v)) for v in reg.predict(Xte)]
    m = bl.mae(yte_r, pred)

    print(f"  Mittelwert-Vorhersage        {ref['mae_mean']:.2f}")
    print(f"  Ridge-Regression             {ref['mae_ridge']:.2f}")
    print(f"  TabFM                        {m:.2f}   "
          f"{'BESSER' if m < ref['mae_ridge'] else 'schlechter'} als Ridge")


if __name__ == "__main__":
    main()
