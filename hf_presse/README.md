# HF-Presse — synthetischer Benchmark für tabellarische Foundation-Modelle

Fiktives, aber physikalisch durchgerechnetes Szenario zum Testen von TabFM und
vergleichbaren Modellen: eine **Hochfrequenz-Furnierpresse**, deren
NH-Sicherungen nach im Mittel **etwa 40 Presszyklen** durchbrennen.

Der Datensatz ist bewusst klein (1165 Zeilen, 30 Merkmale) — also genau der
Bereich, in dem tabellarische Foundation-Modelle ihren Vorteil gegenüber
klassischen Verfahren ausspielen sollen.

---

## 1. Die Anlage

Holzverleimung mit Hochfrequenz. Ein Röhrengenerator (Trioden-Oszillator) heizt
die Leimfuge dielektrisch auf — die Wärme entsteht direkt in der Fuge, nicht
durch Wärmeleitung von außen.

```
400 V 3~ ──▶ NH-Sicherungen ──▶ Anodentrafo ──▶ Gleichrichter ──▶ Siebkondensatoren
             (100 A, gR)                                              │
                                                                      ▼
                                                          7000 V DC ──▶ Senderöhre
```

| Größe | Wert |
|---|---|
| Anodenspannung | 7000 V DC, geregelt (nur Netzschwankung sichtbar, ±1 %) |
| Anodenstrom | 6,70 … 7,20 A, je nach Charge |
| Anodenleistung | ≈ 47 … 50 kW |
| Primärstrom | ≈ 82 … 88 A → daher 100-A-Sicherungen |
| Zyklusdauer | 45 … 75 min |
| Zuschaltungen des Generators | 2 … 3 je Zyklus (Vor- und Nachheizung) |

Der Anodenstrom ist keine freie Zufallsgröße: er folgt der **Holzfeuchte**, die
den dielektrischen Verlustfaktor und damit die Leistungsaufnahme bestimmt.
Feuchtes Holz zieht mehr Strom.

## 2. Das Fehlerbild

Jede Zuschaltung lädt die Siebkondensatorbank über den Anodentrafo. Der dabei
fließende Einschaltstromstoß (≈ 650 … 950 A für 10 … 30 ms) belastet die
Sicherungen mit einem Schmelzintegral I²·t. Ein einzelner Stoß ist harmlos — er
liegt weit unter dem Schmelzintegral von 25 000 A²s. Aber die **Einschnürungen
des Schmelzleiters ermüden kumulativ**.

Nach ≈ 95 Zuschaltungen, also **etwa 40 Zyklen**, lassen sie los.

Zwei Dinge machen den Verlauf nichtlinear:

- Die **Siebkondensatoren altern mit**. Ihr ESR steigt, der Einschaltstrom-Peak
  wächst über die Standzeit → die Schädigung beschleunigt sich zum Ende hin.
- Der **Übergangswiderstand der geschädigten Sicherung steigt**. Sie wird
  wärmer, was die Ermüdung weiter treibt.

Standzeit in der Grundgesamtheit (400 simulierte Anlagen):
**Mittel 39,8 · Median 40 · SD 9,5 Zyklen**, 5.–95. Perzentil 25 … 56.
Im ausgelieferten 30-Anlagen-Datensatz: Mittel 40,2 · Median 42 · Spanne 23 … 55.

Die Streuung hat zwei Quellen: die **Chargenqualität der Sicherungen** und die
**Vorschädigung der Kondensatorbank** der jeweiligen Anlage. Dazu kommt der
Produktmix — dicke Chargen und PUR-Leim brauchen häufiger die dritte
Zuschaltung, was die Schädigung pro Zyklus um die Hälfte erhöht.

## 3. Warum die Aufgabe nicht trivial ist

Das eigentliche Zustandssignal ist die **Übertemperatur am Sicherungshalter**.
Sie wird von drei Dingen überlagert:

1. **Hallentemperatur** — Jahres- und Tagesgang, ±8 K.
2. **Last** — eine feuchte, dicke Charge erwärmt die Sicherung auch ohne jede
   Schädigung.
3. **Montageoffset des Fühlers** — der Fühler ist angeschraubt oder aufgeklebt;
   Anpressdruck und Montageort streuen von Anlage zu Anlage mit **SD 4,3 K**.

Punkt 3 ist der entscheidende: **die Montagestreuung zwischen den Anlagen ist
größer als der Signalzuwachs in den letzten Zyklen vor dem Ausfall.** Ein fester
Schwellwert („über 38 °C wechseln") trägt deshalb nicht über Anlagengrenzen
hinweg. Das Modell muss Last und Umgebung herausrechnen und den Verlauf
*relativ* zur jeweiligen Anlage lesen.

Verstärkt wird das durch den **Gruppen-Split**: eine Anlage liegt vollständig in
`train` oder `test`. Im Test sieht das Modell also ausschließlich Anlagen, deren
Sensoroffset es nie gesehen hat.

**Ablenkungen.** `u_ripple_v` und `i_inrush_peak_a_max` steigen sichtbar über die
Standzeit an — sie messen aber die Alterung der *Kondensatorbank*, nicht den
Zustand der *Sicherung*. Weil die Auslegung des Siebkreises von Anlage zu Anlage
streut, taugen sie anlagenübergreifend nur schwach als Altersproxy (AUC 0,74
bzw. 0,71 gegenüber 0,90 für das echte Zustandssignal). `press_hours_total`
(Betriebsstundenzähler *gesamt*, nicht seit Sicherungswechsel, AUC 0,55),
`hydraulic_pressure_bar` (0,53), `glue_batch_id` und `wood_species` sind ohne
verwertbaren Bezug zum Fehlerbild.

---

## 4. Dateien

```
hf_presse/
  simulate.py       Simulator (nur Standardbibliothek)
  baseline.py       Referenz-Baseline (nur Standardbibliothek)
  run_tabfm.py      Runner für Googles TabFM (braucht tabfm + torch)
  README.md         dieses Dokument
data/
  cycles.csv        1165 Zeilen — eine je Presszyklus  ◀ Haupttabelle
  activations.csv   2774 Zeilen — eine je Generator-Zuschaltung
  presses.csv         30 Zeilen — Stammdaten inkl. latenter Parameter
```

Neu erzeugen (deterministisch, keine Abhängigkeiten):

```bash
cd hf_presse
python3 simulate.py --out ../data --presses 30 --seed 20260807
```

## 5. Schema von `cycles.csv`

**Kontext**

| Spalte | Bedeutung |
|---|---|
| `press_id` | Anlage P01 … P30 (Gruppenschlüssel für den Split) |
| `press_model` | HFP-2400 / HFP-3200 |
| `cycle_index` | Zyklus seit dem letzten Sicherungswechsel, 1-basiert |
| `ts_start` | Startzeitstempel |
| `shift` | F / S / N |

**Charge und Prozess**

| Spalte | Bedeutung |
|---|---|
| `glue_type` | PVAc_D3 / PVAc_D4 / PUR |
| `wood_species` | Buche / Eiche / Fichte / Esche — ohne Bezug zum Fehler |
| `wood_moisture_pct` | Holzfeuchte, treibt die Stromaufnahme |
| `charge_thickness_mm` | Aufbaudicke |
| `charge_area_m2` | Pressfläche |
| `cycle_duration_min` | 45 … 75 |
| `n_activations` | 2 oder 3 |
| `t_hf_total_min` | gesamte HF-Zeit im Zyklus |

**Elektrik**

| Spalte | Bedeutung |
|---|---|
| `u_anode_v_mean`, `u_anode_v_std` | Anodenspannung, ≈ 7000 V |
| `i_anode_a_mean`, `i_anode_a_max` | Anodenstrom |
| `p_anode_kw`, `i_prim_rms_a`, `cos_phi` | Leistung und Primärseite |
| `u_ripple_v` | DC-Welligkeit — **Ablenkung** (Kondensatoralterung) |
| `i_inrush_peak_a_max` | Einschaltstrom-Peak — **Ablenkung** |
| `t_inrush_ms_mean` | Dauer des Stromstoßes |
| `i2t_cycle_a2s` | Schmelzintegral dieses Zyklus |
| `i2t_cum_a2s` | kumuliert seit Sicherungswechsel — **stärkster Zähler** |

**Thermik — hier steckt das Zustandssignal**

| Spalte | Bedeutung |
|---|---|
| `t_hall_c` | Hallentemperatur |
| `t_cabinet_c` | Schaltschrank, gemessen (eigenes Rauschen) |
| `t_fuse_holder_c` | Sicherungshalter, gemessen (Rauschen + Montageoffset) |
| `dt_fuse_cabinet_k` | Differenz der beiden Messwerte |
| `r_fuse_est_mohm` | geschätzter Sicherungswiderstand — direkt, aber verrauscht |

**Störgrößen**: `press_hours_total`, `hydraulic_pressure_bar`, `glue_batch_id`

**Zielgrößen**

| Spalte | Bedeutung |
|---|---|
| `fuse_blown` | 1 im Ausfallzyklus |
| `blow_within_5` | 1, wenn `rul_cycles ≤ 4` — **Aufgabe A** |
| `rul_cycles` | Restlebensdauer in Zyklen — **Aufgabe B** |
| `damage_true` | latente Schädigung 0…1 — **nur Diagnose, niemals Merkmal** |
| `censored` | 1 = präventiv gewechselt, kein Ausfall beobachtet |
| `split` | train / test (nach Anlage) |

> **Rechtszensierte Verläufe**: 4 Anlagen wurden präventiv gewechselt, bevor die
> Sicherungen durchbrannten. Ihre Zielspalten sind leer. Für beide Aufgaben
> ausschließen — oder als Zensierung modellieren, wenn ein
> Überlebenszeit-Ansatz getestet werden soll.

**Kein Leck im Ausfallzyklus**: Der Zyklus wird immer vollständig gefahren, die
Sicherung lässt bei der letzten Zuschaltung los. `n_activations`,
`t_hf_total_min` und `i2t_cycle_a2s` sind deshalb auch in der Ausfallzeile
unverkürzt — eine mitten im Zyklus abgebrochene Zeile wäre sonst allein an ihrer
Verkürzung als Ausfall erkennbar gewesen.

## 6. Aufgaben und Referenzwerte

Split nach Anlage: 21 Anlagen train, 9 test. Für Aufgabe A: 663 Trainingszeilen
(85 positiv), 383 Testzeilen (45 positiv), Positivrate ≈ 12 %.

### Aufgabe A — `blow_within_5`, Klassifikation, Metrik ROC-AUC

| Verfahren | standard | `--hard` |
|---|---|---|
| Zufall | 0,500 | 0,500 |
| bestes Einzelmerkmal, Schwelle | **0,980** (`i2t_cum_a2s`) | **0,904** (`dt_fuse_cabinet_k`) |
| zweitbestes | 0,914 (`cycle_index`) | 0,792 (`t_fuse_holder_c`) |
| drittbestes | 0,904 (`dt_fuse_cabinet_k`) | 0,773 (`r_fuse_est_mohm`) |
| logistische Regression, alle Merkmale | 0,937 | 0,904 |

**Das ist der interessante Teil**: Im Standardmodus schlägt das kumulierte
Schmelzintegral als *einzelnes* Merkmal (0,980) das volle lineare Modell (0,937)
deutlich. Im `--hard`-Modus kommt die logistische Regression über das beste
Einzelmerkmal überhaupt nicht hinaus (beide 0,904). Ein lineares Modell kann den
Zusammenhang also nicht ausnutzen — die wahre Schädigungskurve ist proportional
zu D^1,9 mit anlagenspezifischer Verstärkung. **Hier liegt der Spielraum, den
ein stärkeres Modell heben muss.**

### Aufgabe B — `rul_cycles`, Regression, Metrik MAE

| Verfahren | standard | `--hard` |
|---|---|---|
| Mittelwert-Vorhersage | 11,31 | 11,31 |
| Ridge-Regression, alle Merkmale | **5,17** | **6,37** |

Zur Einordnung: die Streuung der Zielgröße im Test beträgt SD 13,35 Zyklen.

Baseline reproduzieren:

```bash
python3 baseline.py --data ../data/cycles.csv
python3 baseline.py --data ../data/cycles.csv --hard
```

### TabFM dagegen laufen lassen

```bash
pip install "tabfm[pytorch]"
python3 run_tabfm.py            # Standardmodus
python3 run_tabfm.py --hard     # ohne Zählerstände
```

`run_tabfm.py` gibt die TabFM-Ergebnisse direkt neben den Referenzwerten aus und
benutzt für AUC und MAE denselben Code wie `baseline.py`, damit die Zahlen
vergleichbar sind. Die Gewichte (`google/tabfm-1.0.0-pytorch`) lädt TabFM beim
ersten Lauf von Hugging Face. Ist Hugging Face gesperrt, die Gewichte einmal
anderswo holen und den Ordner übergeben:

```bash
huggingface-cli download google/tabfm-1.0.0-pytorch --local-dir tabfm-w
python3 run_tabfm.py --checkpoint tabfm-w
```

Auf CPU ist die Vorgabe-Ensemblegröße von 32 langsam; `run_tabfm.py` nutzt
deshalb 8 und lässt sich mit `--n-estimators 32 --device cuda` hochdrehen.

### Der `--hard`-Modus

Entfernt `cycle_index`, `i2t_cum_a2s` und `press_hours_total` — also alle
Zählerstände. Damit lässt sich trennen, ob ein Modell den **Zustand** aus den
Messwerten liest oder nur **Zyklen zählt**. Beides ist in der Praxis legitim,
aber nur Ersteres trägt, wenn ein Zähler nach einem Steuerungstausch fehlt oder
falsch zurückgesetzt wurde.

## 7. Für den TabFM-Test

Empfohlene Vorgehensweise:

1. **Merkmale**: alle Spalten außer `damage_true`, `fuse_blown`,
   `blow_within_5`, `rul_cycles`, `censored`, `split`, `press_id`, `ts_start`,
   `glue_batch_id`. Das sind die 30 Merkmale, die auch die Baseline verwendet.
2. **Split**: die mitgelieferte `split`-Spalte verwenden — nicht neu ziehen. Ein
   zufälliger Zeilen-Split würde Verläufe derselben Anlage über die Grenze
   auslaufen lassen und die Ergebnisse deutlich zu optimistisch machen.
3. **Zensierte Zeilen** (`censored = 1`) für beide Aufgaben ausschließen.
4. Gegen die Tabellen in Abschnitt 6 vergleichen — **beide Modi**, sonst bleibt
   unklar, ob das Modell zählt oder misst.

Interessante Zusatzfragen, für die der Datensatz ausgelegt ist:

- Schlägt das Modell im Standardmodus die 0,980 des besten Einzelmerkmals? Das
  ist die eigentliche Messlatte, nicht die 0,937 der linearen Baseline.
- Erkennt es, dass `u_ripple_v` und `i_inrush_peak_a_max` trotz klarem Trend über
  die Standzeit **keine** Zustandssignale der Sicherung sind?
- Nutzt es `t_cabinet_c`, um den Umgebungseinfluss aus `t_fuse_holder_c`
  herauszurechnen — oder verlässt es sich auf den Absolutwert und bricht auf den
  unbekannten Anlagen des Testsplits ein?
- Skaliert die Leistung mit der Zahl der Trainingsanlagen? `--presses` erhöhen
  und die Kurve aufnehmen.

## 8. Kalibrierung

Die Konstante `DAMAGE_K = 0.1200` in `simulate.py` stellt die mittlere Standzeit
ein; sie wurde über 400 simulierte Anlagen auf Mittel 39,8 / Median 40 Zyklen
kalibriert. Kleinerer Wert bedeutet längere Standzeit. Die Streuung der Standzeit
(SD 9,5) kommt aus `fuse_quality` (SD 0,155) und `cap_bank_age` (0 … 1,25);
die Schwierigkeit des Zustandssignals steuern `t_sensor_offset` (SD 4,3 K) und
der Faktor 12,0 vor dem Schädigungsterm der Übertemperatur.

Alle Zufallszahlen laufen über eine einzige gesäte `random.Random`-Instanz; bei
gleicher Saat und gleicher Anlagenzahl ist der Datensatz bitgleich reproduzierbar.
