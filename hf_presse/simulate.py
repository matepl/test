#!/usr/bin/env python3
"""
Simulator einer HF-Furnierpresse (Hochfrequenz-Presse, Roehrengenerator).

Fiktives, aber physikalisch plausibles Szenario fuer Benchmarks von tabellarischen
Foundation-Modellen (TabFM & Co.).

Anlage
------
Holzverleimung mit Hochfrequenz. Ein Roehrengenerator (Trioden-Oszillator) heizt
die Leimfuge dielektrisch auf. Leistungspfad:

    400 V 3~  ->  NH-Sicherungen (100 A gR)  ->  Anodentrafo  ->  Gleichrichter
              ->  Siebkondensatorbank  ->  7000 V DC  ->  Anode der Senderoehre

Betrieb
-------
* Anodenspannung konstant ~7000 V (geregelt, nur Netzschwankung sichtbar).
* Anodenstrom 6,7 A .. 7,2 A, variiert von Zyklus zu Zyklus mit der Charge
  (v. a. Holzfeuchte -> dielektrischer Verlustfaktor).
* Zyklusdauer 45 .. 75 min (Beschicken, Pressen, HF-Phasen, Entnahme).
* Der Generator wird pro Zyklus 2 .. 3 mal zugeschaltet (Vor- und Nachheizung).

Fehlerbild
----------
Jede Zuschaltung laedt die Siebkondensatorbank ueber den Anodentrafo. Der dabei
fliessende Einschaltstromstoss belastet die NH-Sicherungen mit einem Schmelz-
integral I^2*t. Die Sicherungen ueberleben den einzelnen Stoss problemlos, aber
die Einschnuerungen des Schmelzleiters ermueden kumulativ. Nach im Mittel etwa
40 Zyklen (~100 Zuschaltungen) brennen sie durch.

Weil die Siebkondensatoren und der Daempfungswiderstand mitaltern, steigt der
Einschaltstrom-Peak ueber die Standzeit langsam an -> die Schaedigung
beschleunigt sich zum Ende hin.

Beobachtbare Vorlaeufer (das, was ein Modell finden soll)
---------------------------------------------------------
* t_fuse_holder_c  Temperatur am Sicherungshalter: steigt ueberproportional mit
                   der Schaedigung (Uebergangswiderstand). Staerkstes Signal --
                   aber ueberlagert von Hallentemperatur, Last und einem
                   anlagenspezifischen Montageoffset des Fuehlers.
* dt_fuse_cabinet_k  Uebertemperatur gegen Schaltschrank. Entkoppelt vom
                   Umgebungseinfluss, erbt aber das Rauschen beider Fuehler.
* r_fuse_est_mohm  aus dem Spannungsabfall geschaetzter Sicherungswiderstand;
                   physikalisch direkt, messtechnisch aber sehr verrauscht.
* i_inrush_peak_a_max, u_ripple_v  altern mit der Kondensatorbank, korrelieren
                   also mit dem Alter, nicht mit der Sicherungsschaedigung
                   (Stoergroesse / Ablenkung).

Der Datensatz ist bewusst so ausgelegt, dass eine einzelne Schwelle nicht
reicht: der Montageoffset des Temperaturfuehlers streut zwischen den Anlagen
staerker, als das Schaedigungssignal in den letzten Zyklen zunimmt. Erst wenn
Last (p_anode_kw, i_prim_rms_a) und Umgebung (t_cabinet_c) herausgerechnet
werden, wird das Signal sauber. Die Aufteilung train/test erfolgt nach Anlage,
das Modell sieht im Test also ausschliesslich unbekannte Offsets.

Verwendung
----------
    python3 simulate.py --out ../data --presses 30 --seed 20260807
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import os
import random
import statistics

# --------------------------------------------------------------------------
# Anlagenauslegung (feste Konstanten)
# --------------------------------------------------------------------------

U_ANODE_NOM_V = 7000.0      # geregelte Anodenspannung
U_GRID_NOM_V = 400.0        # Netzspannung, 3~
ETA_GEN = 0.90              # Wirkungsgrad Trafo + Gleichrichter
I2T_FUSE_A2S = 25_000.0     # Schmelzintegral der 100-A-gR-Sicherung
I_INRUSH_0_A = 640.0        # Einschaltstrom-Peak bei neuer Kondensatorbank
R_FUSE_COLD_MOHM = 0.62     # Kaltwiderstand des Schmelzleiters

# Kalibrierung des Schaedigungsmodells -> mittlere Standzeit ~40 Zyklen.
DAMAGE_K = 0.1200
DAMAGE_EXP = 1.35

GLUE_TYPES = ("PVAc_D3", "PVAc_D4", "PUR")
WOOD_SPECIES = ("Buche", "Eiche", "Fichte", "Esche")
PRESS_MODELS = ("HFP-2400", "HFP-3200")
SHIFTS = ("F", "S", "N")


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def shift_of(hour: int) -> str:
    """Dreischichtbetrieb: Frueh 06-14, Spaet 14-22, Nacht 22-06."""
    if 6 <= hour < 14:
        return "F"
    if 14 <= hour < 22:
        return "S"
    return "N"


def hall_temperature(ts: dt.datetime, rng: random.Random) -> float:
    """Hallentemperatur: Jahresgang + Tagesgang + Rauschen."""
    doy = ts.timetuple().tm_yday
    seasonal = 7.5 * math.sin(2 * math.pi * (doy - 105) / 365.0)
    daily = 2.4 * math.sin(2 * math.pi * (ts.hour - 9) / 24.0)
    return 19.5 + seasonal + daily + rng.gauss(0.0, 0.9)


# --------------------------------------------------------------------------
# Simulation einer einzelnen Presse (ein Sicherungswechsel-Intervall)
# --------------------------------------------------------------------------

def simulate_press(press_no: int, rng: random.Random, censored: bool):
    """Simuliert eine Presse von frischen Sicherungen bis zum Durchbrennen.

    Gibt (stammdaten, zyklus_zeilen, zuschaltungs_zeilen) zurueck.
    """
    press_id = f"P{press_no:02d}"
    model = PRESS_MODELS[press_no % len(PRESS_MODELS)]

    # Anlagen- und Bauteilstreuung -- konstant ueber die Standzeit.
    fuse_quality = clamp(rng.gauss(1.0, 0.155), 0.62, 1.45)   # Chargenstreuung NH-Sicherung
    p3_bias = rng.gauss(0.0, 0.19)                            # Produktmix der Anlage
    press_i_offset = rng.gauss(0.0, 0.055)                    # Roehrenzustand
    cap_bank_age = rng.uniform(0.0, 1.25)                     # Vorschaedigung Siebkreis
    cabinet_rise_k = rng.gauss(11.5, 1.3)                     # Schaltschrankerwaermung
    grid_bias = rng.gauss(0.0, 0.004)                         # Netzhaerte am Standort
    hours_total_0 = rng.uniform(4_000, 41_000)                # Betriebsstundenzaehler

    # Messtechnik. Der Temperaturfuehler am Sicherungshalter ist ein aufgeklebtes
    # bzw. angeschraubtes Element -- Anpressdruck und Montageort streuen von
    # Anlage zu Anlage erheblich. Das verschiebt den Absolutwert, ohne dass sich
    # der Zustand der Sicherung aendert. Ein anlagenuebergreifend fester
    # Schwellwert traegt deshalb nicht.
    t_sensor_offset = rng.gauss(0.0, 4.3)                     # Montagestreuung [K]
    t_sensor_noise = rng.uniform(1.3, 2.6)                    # Rauschband [K]
    t_cab_noise = rng.uniform(0.6, 1.1)
    damage_gain = clamp(rng.gauss(1.0, 0.22), 0.5, 1.6)       # thermische Auspraegung
    r_meas_noise = rng.uniform(0.10, 0.19)                    # Widerstandsmessung [mOhm]
    r_offset = rng.gauss(0.0, 0.07)                           # Klemmenuebergang
    ripple_offset = rng.gauss(0.0, 4.5)                       # Streuung Siebkreis-Auslegung

    # Praeventiver Wechsel bei zensierten Verlaeufen.
    censor_at = rng.randint(22, 34) if censored else None

    ts = dt.datetime(2025, 1, 6, 6, 0) + dt.timedelta(
        days=rng.randint(0, 300), hours=rng.randint(0, 23)
    )

    damage = 0.0
    hours_total = hours_total_0
    i2t_cum = 0.0
    cycle_rows: list[dict] = []
    act_rows: list[dict] = []
    cycle_index = 0
    blown = False

    while True:
        cycle_index += 1

        # ---------------- Charge / Prozessgroessen ----------------
        glue = rng.choice(GLUE_TYPES)
        species = rng.choice(WOOD_SPECIES)
        moisture = clamp(rng.gauss(10.6, 1.5), 7.8, 14.0)
        thickness = rng.choice((18.0, 22.0, 25.0, 30.0, 38.0))
        area = clamp(rng.gauss(2.30, 0.34), 1.55, 3.10)

        # Dicke Chargen brauchen eine dritte Zuschaltung (Nachheizung).
        p3 = (0.18 + 0.020 * (thickness - 18.0)
              + (0.10 if glue == "PUR" else 0.0) + p3_bias)
        n_act = 3 if rng.random() < clamp(p3, 0.0, 0.92) else 2

        # ---------------- Elektrik ----------------
        # Anodenstrom: Feuchte treibt den Verlustfaktor, damit die Stromaufnahme.
        i_anode = (
            6.72
            + 0.055 * (moisture - 8.0)
            + 0.048 * (area - 2.25)
            + (0.02 if glue == "PUR" else 0.0)
            + press_i_offset
            + rng.gauss(0.0, 0.030)
        )
        i_anode = clamp(i_anode, 6.70, 7.20)

        grid_factor = 1.0 + grid_bias + rng.gauss(0.0, 0.0055)
        u_anode = U_ANODE_NOM_V * (1.0 + grid_bias * 0.6) + rng.gauss(0.0, 14.0)
        u_anode_std = abs(rng.gauss(9.5, 2.2))
        p_anode_kw = u_anode * i_anode / 1000.0

        cos_phi = clamp(0.923 - 0.00042 * cycle_index + rng.gauss(0.0, 0.006), 0.86, 0.96)
        i_prim = p_anode_kw * 1000.0 / (math.sqrt(3) * U_GRID_NOM_V * ETA_GEN * cos_phi)

        # Siebkondensatoren altern -> Welligkeit und Einschaltstrom steigen.
        cap_age = cap_bank_age + 0.0125 * cycle_index
        u_ripple = 41.0 * (1.0 + 0.62 * cap_age) + ripple_offset + rng.gauss(0.0, 3.4)

        # ---------------- Temperaturen ----------------
        t_hall = hall_temperature(ts, rng)
        t_cabinet = t_hall + cabinet_rise_k + 0.085 * (p_anode_kw - 48.0) + rng.gauss(0.0, 0.7)

        # ---------------- Zuschaltungen des Generators ----------------
        t_hf_total = 0.0
        i2t_cycle = 0.0
        inrush_peaks: list[float] = []
        t_inrush_list: list[float] = []
        blow_act = None

        for k in range(1, n_act + 1):
            # Einschaltstromstoss beim Laden der Siebkondensatorbank.
            i_peak = (
                I_INRUSH_0_A
                * (1.0 + 0.30 * cap_age)
                * grid_factor
                * (1.0 + rng.gauss(0.0, 0.055))
            )
            # Erste Zuschaltung im Zyklus trifft eine kalte, voll entladene Bank.
            if k == 1:
                i_peak *= 1.085
            t_inrush_ms = clamp(rng.gauss(18.0, 2.6), 10.5, 31.0)

            # Dreieckfoermiger Abklingverlauf -> I^2*t = I_peak^2/3 * t
            i2t_act = (i_peak ** 2) / 3.0 * (t_inrush_ms / 1000.0)

            # Kumulative Ermuedung der Schmelzleiter-Einschnuerungen.
            d_damage = (
                DAMAGE_K
                * (i2t_act / I2T_FUSE_A2S) ** DAMAGE_EXP
                / fuse_quality
                * (1.0 + rng.gauss(0.0, 0.10))
            )
            damage += max(d_damage, 0.0)

            hf_min = clamp(
                (4.2 + 0.135 * thickness + (1.6 if glue == "PUR" else 0.0)) / n_act * 1.7
                + rng.gauss(0.0, 0.8),
                2.5, 16.0,
            )
            t_hf_total += hf_min
            i2t_cycle += i2t_act
            i2t_cum += i2t_act
            inrush_peaks.append(i_peak)
            t_inrush_list.append(t_inrush_ms)

            act_rows.append({
                "press_id": press_id,
                "cycle_index": cycle_index,
                "activation_no": k,
                "i_inrush_peak_a": round(i_peak, 1),
                "t_inrush_ms": round(t_inrush_ms, 2),
                "i2t_activation_a2s": round(i2t_act, 1),
                "t_hf_min": round(hf_min, 2),
                "i_anode_a": round(i_anode + rng.gauss(0.0, 0.012), 3),
                "u_anode_v": round(u_anode + rng.gauss(0.0, 11.0), 1),
                "damage_true": round(min(damage, 1.0), 5),
                "fuse_blown": 0,
            })

        # Der Zyklus wird immer vollstaendig gefahren. Erreicht die Schaedigung
        # dabei die Schwelle, laesst der Schmelzleiter bei der letzten
        # Zuschaltung des Zyklus los. Damit bleiben alle Zyklusaggregate
        # (n_activations, t_hf_total_min, i2t_cycle_a2s) unverkuerzt -- eine
        # mitten im Zyklus abgebrochene Zeile waere sonst allein an ihrer
        # Verkuerzung als Ausfall erkennbar.
        if damage >= 1.0:
            blow_act = n_act
            act_rows[-1]["fuse_blown"] = 1

        # ---------------- Zyklusdauer ----------------
        duration = (
            t_hf_total
            + 14.0 + 0.62 * thickness          # Presszeit / Abbindezeit
            + 9.5 + 3.4 * area                  # Beschicken und Entnehmen
            + rng.gauss(0.0, 3.2)
        )
        duration = clamp(duration, 45.0, 75.0)

        # ---------------- Messwerte am Sicherungshalter ----------------
        d_eff = min(damage, 1.0)
        # Wahre Uebertemperatur: Lastanteil + ueberproportionaler Schaedigungsanteil.
        dt_latent = (
            5.4 * (p_anode_kw / 48.5)
            + 2.1 * (i_prim / 86.0)
            + 12.0 * damage_gain * d_eff ** 1.9
        )
        # Was die Anlage tatsaechlich meldet: zwei getrennte Fuehler, jeder mit
        # eigenem Rauschen; der Halterfuehler zusaetzlich mit Montageoffset.
        t_cabinet_meas = t_cabinet + rng.gauss(0.0, t_cab_noise)
        t_fuse_holder = (
            t_cabinet + dt_latent + t_sensor_offset + rng.gauss(0.0, t_sensor_noise)
        )
        # Die Uebertemperatur wird aus den beiden Messwerten gebildet, erbt also
        # beide Rauschanteile und den Offset.
        dt_fuse = t_fuse_holder - t_cabinet_meas
        r_fuse = (
            R_FUSE_COLD_MOHM * (1.0 + 0.55 * d_eff) + r_offset
            + rng.gauss(0.0, r_meas_noise)
        )

        hours_total += duration / 60.0
        ts_start = ts

        cycle_rows.append({
            # --- Kontext ---
            "press_id": press_id,
            "press_model": model,
            "cycle_index": cycle_index,
            "ts_start": ts_start.strftime("%Y-%m-%d %H:%M"),
            "shift": shift_of(ts_start.hour),
            # --- Charge / Prozess ---
            "glue_type": glue,
            "wood_species": species,
            "wood_moisture_pct": round(moisture, 2),
            "charge_thickness_mm": thickness,
            "charge_area_m2": round(area, 3),
            "cycle_duration_min": round(duration, 1),
            "n_activations": n_act,
            "t_hf_total_min": round(t_hf_total, 2),
            # --- Elektrik ---
            "u_anode_v_mean": round(u_anode, 1),
            "u_anode_v_std": round(u_anode_std, 2),
            "i_anode_a_mean": round(i_anode, 3),
            "i_anode_a_max": round(i_anode + abs(rng.gauss(0.028, 0.012)), 3),
            "p_anode_kw": round(p_anode_kw, 2),
            "i_prim_rms_a": round(i_prim, 2),
            "cos_phi": round(cos_phi, 4),
            "u_ripple_v": round(u_ripple, 2),
            "i_inrush_peak_a_max": round(max(inrush_peaks), 1),
            "t_inrush_ms_mean": round(statistics.fmean(t_inrush_list), 2),
            "i2t_cycle_a2s": round(i2t_cycle, 1),
            "i2t_cum_a2s": round(i2t_cum, 1),
            # --- Thermik ---
            "t_hall_c": round(t_hall, 2),
            "t_cabinet_c": round(t_cabinet_meas, 2),
            "t_fuse_holder_c": round(t_fuse_holder, 2),
            "dt_fuse_cabinet_k": round(dt_fuse, 2),
            "r_fuse_est_mohm": round(r_fuse, 4),
            # --- Stoergroessen ohne Bezug zum Fehlerbild ---
            "press_hours_total": round(hours_total, 1),
            "hydraulic_pressure_bar": round(rng.gauss(178.0, 6.5), 1),
            "glue_batch_id": f"B{rng.randint(1000, 9999)}",
            # --- Zielgroessen ---
            "fuse_blown": 1 if blow_act else 0,
            "damage_true": round(d_eff, 5),
        })

        # Naechster Zyklus: Ruestzeit zwischen den Chargen.
        ts = ts + dt.timedelta(minutes=duration + rng.uniform(8.0, 22.0))

        if blow_act:
            blown = True
            break
        if censor_at is not None and cycle_index >= censor_at:
            break
        if cycle_index >= 90:      # Reissleine, wird praktisch nie erreicht
            break

    # ---------------- Abgeleitete Zielgroessen ----------------
    n = len(cycle_rows)
    for i, row in enumerate(cycle_rows):
        if blown:
            rul = n - 1 - i
            row["rul_cycles"] = rul
            row["blow_within_5"] = 1 if rul <= 4 else 0
        else:
            row["rul_cycles"] = ""      # rechtszensiert -- unbekannt
            row["blow_within_5"] = ""
        row["censored"] = 0 if blown else 1

    master = {
        "press_id": press_id,
        "press_model": model,
        "n_cycles": n,
        "censored": 0 if blown else 1,
        "fuse_quality": round(fuse_quality, 4),
        "cap_bank_age_0": round(cap_bank_age, 4),
        "cabinet_rise_k": round(cabinet_rise_k, 3),
        "hours_total_start": round(hours_total_0, 1),
    }
    return master, cycle_rows, act_rows


# --------------------------------------------------------------------------
# Datensatz erzeugen
# --------------------------------------------------------------------------

def build(n_presses: int, seed: int, censored_n: int):
    rng = random.Random(seed)
    censored_ids = set(rng.sample(range(1, n_presses + 1), k=min(censored_n, n_presses)))

    masters, cycles, acts = [], [], []
    for p in range(1, n_presses + 1):
        m, c, a = simulate_press(p, rng, censored=p in censored_ids)
        masters.append(m)
        cycles.extend(c)
        acts.extend(a)

    # Gruppen-Split: eine Presse liegt komplett in train oder test.
    ids = sorted({m["press_id"] for m in masters})
    rng.shuffle(ids)
    n_test = max(1, round(0.30 * len(ids)))
    test_ids = set(ids[:n_test])
    for m in masters:
        m["split"] = "test" if m["press_id"] in test_ids else "train"
    for row in cycles:
        row["split"] = "test" if row["press_id"] in test_ids else "train"
    for row in acts:
        row["split"] = "test" if row["press_id"] in test_ids else "train"

    return masters, cycles, acts


def write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def report(masters, cycles, acts) -> None:
    lens = [m["n_cycles"] for m in masters if not m["censored"]]
    acts_per_cycle = [r["n_activations"] for r in cycles]
    i_means = [r["i_anode_a_mean"] for r in cycles]
    durs = [r["cycle_duration_min"] for r in cycles]
    pos = [r["blow_within_5"] for r in cycles if r["blow_within_5"] != ""]

    print(f"Pressen gesamt        : {len(masters)}  "
          f"({sum(1 for m in masters if m['censored'])} rechtszensiert)")
    print(f"Zyklen gesamt         : {len(cycles)}")
    print(f"Zuschaltungen gesamt  : {len(acts)}")
    print(f"Standzeit bis Ausfall : {min(lens)} .. {max(lens)} Zyklen, "
          f"Mittel {statistics.fmean(lens):.1f}, Median {statistics.median(lens):.0f}")
    print(f"Zuschaltungen/Zyklus  : Mittel {statistics.fmean(acts_per_cycle):.2f} "
          f"(2er: {acts_per_cycle.count(2)}, 3er: {acts_per_cycle.count(3)})")
    print(f"Anodenstrom           : {min(i_means):.2f} .. {max(i_means):.2f} A, "
          f"Mittel {statistics.fmean(i_means):.3f} A")
    print(f"Zyklusdauer           : {min(durs):.0f} .. {max(durs):.0f} min, "
          f"Mittel {statistics.fmean(durs):.1f} min")
    print(f"Positivrate blow_within_5 : {sum(pos) / len(pos):.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="HF-Presse Simulator")
    ap.add_argument("--out", default="../data", help="Zielverzeichnis fuer die CSVs")
    ap.add_argument("--presses", type=int, default=30, help="Anzahl simulierter Anlagen")
    ap.add_argument("--seed", type=int, default=20260807, help="Zufallssaat")
    ap.add_argument("--censored", type=int, default=4,
                    help="Anlagen mit praeventivem Sicherungswechsel (rechtszensiert)")
    args = ap.parse_args()

    masters, cycles, acts = build(args.presses, args.seed, args.censored)

    os.makedirs(args.out, exist_ok=True)
    write_csv(os.path.join(args.out, "presses.csv"), masters)
    write_csv(os.path.join(args.out, "cycles.csv"), cycles)
    write_csv(os.path.join(args.out, "activations.csv"), acts)

    report(masters, cycles, acts)
    print(f"\nGeschrieben nach: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
