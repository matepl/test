# 7-Zip Passwort-Recovery (für eigene Dateien)

Ein interaktiver Orchestrator, der das vergessene Passwort einer **dir gehörenden**
7-Zip-Datei wiederherstellt. Du beantwortest **einmal** ein paar Fragen, danach
läuft das Skript **stundenlang selbstständig** durch eine eskalierende Strategie –
von schnellen, gezielten Versuchen bis zu breiter, teurer Brute-Force. Du musst am
Algorithmus **nichts** anpassen.

> ⚠️ **Nur für eigene Dateien / autorisierte Fälle.** Passwort-Recovery an fremden
> Daten ist illegal. Dieses Werkzeug ist für genau deinen Fall gedacht: deine private
> Datei, dein Passwort, das du vergessen hast.

---

## Warum deine bisherigen Versuche scheiterten

7-Zip leitet den AES-256-Schlüssel über **SHA-256 mit 2^19 (≈ 524.288) Iterationen**
ab. Das macht **jeden einzelnen Passwortversuch teuer**. Selbst auf einer guten GPU
sind das nur einige tausend bis zehntausend Versuche pro Sekunde. Konsequenz:

- **Reine Brute-Force ist chancenlos**, sobald das Passwort länger als ~7–8 Zeichen ist.
- Der Gewinn liegt fast immer in einer **gezielten, personalisierten** Strategie:
  deine typischen Wörter + Regeln + Muster – in der richtigen Reihenfolge (billig zuerst).
- „Viele Versuche mit verändertem Mechanismus“ von Hand ist genau das, was dieses
  Skript **automatisiert und in sinnvoller Reihenfolge** erledigt.

Die **Dateigröße (2 GB) spielt fast keine Rolle** für die Geschwindigkeit: Der
Flaschenhals ist die Schlüsselableitung, nicht die Datenmenge. Wichtig ist nur die
**Hash-Extraktion** – dafür nutzen wir bevorzugt `7z2hashcat`, das große Archive
kompakt und prüfbar hält.

---

## Voraussetzungen (in deiner Umgebung installieren)

```bash
# Debian/Ubuntu/Kali
sudo apt update && sudo apt install -y hashcat john p7zip-full perl

# 7z2hashcat (empfohlen für die Hash-Extraktion großer Dateien)
git clone https://github.com/philsmd/7z2hashcat
# 7z2hashcat/7z2hashcat.pl in den PATH legen oder Pfad merken

# GPU dringend empfohlen – prüfen ob hashcat die GPU sieht:
hashcat -I
```

Optional, aber sehr wirksam – große Wortlisten & Regeln (SecLists / rockyou /
OneRuleToRuleThemAll). Das Skript findet sie automatisch unter
`/usr/share/wordlists`, `/usr/share/hashcat/rules` usw., oder du gibst den Pfad
in der Abfrage an.

---

## Benutzung

```bash
cd 7z-recover

# 0) (Empfohlen) Toolchain-Selbsttest: erstellt ein Mini-7z mit bekanntem
#    Passwort und knackt es – beweist, dass hashcat mode 11600 bei dir wirklich läuft.
python3 recover.py selftest

# 1) Alles in einem: Hash extrahieren -> Fragen -> stundenlang angreifen
python3 recover.py run --archive /pfad/zu/deiner_datei.7z

# Nützliche Einzelschritte:
python3 recover.py add wort1 wort2 "zwei woerter"  # Wörter ergänzen (nur diese werden getestet)
python3 recover.py words                 # Fortschritt: was wurde schon geprüft?
python3 recover.py extract -f datei.7z   # nur den Hash extrahieren
python3 recover.py intake                # nur die Fragen (neu) beantworten
python3 recover.py status                # steht das Passwort schon?
python3 recover.py reset                 # Config/State/Ledger/Hash zurücksetzen
```

**Abbrechen ist sicher:** `Strg-C` speichert den Fortschritt. Ein erneutes
`python3 recover.py run` **setzt automatisch fort** (Ledger + hashcat-Sessions +
Potfile). Erschöpfte Stufen werden nicht erneut gestartet.

Ergebnis: Das Passwort wird angezeigt **und** in `work/PASSWORT_GEFUNDEN.txt`
gespeichert.

---

## Erweiterbares Wörterbuch – ohne doppelte Arbeit

Das ist der Kern: Du kannst dein Wörterbuch **jederzeit ergänzen**, und beim
nächsten Lauf testet das Skript **nur die neuen Wörter und die neuen
Kombinationen** – bereits geprüfte Arbeit wird nie wiederholt.

**Wörter ergänzen** – zwei Wege, beide gleichwertig:

```bash
# a) bequem per Befehl (dedupliziert automatisch)
python3 recover.py add mausi 1234 "roter drache"

# b) oder Dateien direkt bearbeiten / neue anlegen:
#    words/dictionary.txt      <- ein Wort pro Zeile
#    words/beliebiger_name.txt <- jede weitere .txt wird automatisch eingelesen
#    words/numbers.txt         <- Zahlen/Jahre
```

**Sehen, was schon geprüft wurde:**

```bash
python3 recover.py words
```

Beispielausgabe (die Zahlen bedeuten *verarbeitet / aktuell vorhanden / NEU offen*):

```
  Fortschritt je Stufe (verarbeitet / aktuell / NEU offen):
    [fertig] P1: Persoenliche Woerter (roh)              120 /   120 / 0
    [neu!]   P1: Persoenliche Woerter + Basisregeln       96 /   120 / 24
    [fertig] P3: Wort + Wort (Kombinator)             L 60/60  R 60/60
```

Zusätzlich schreibt das Skript ein chronologisches Protokoll nach
`work/tested.log` (Zeitstempel, Stufe, wie viele neue Kandidaten getestet wurden).

### Wie das technisch funktioniert (kurz)

Für jede wörterbasierte Angriffsstufe merkt sich ein **Ledger**
(`work/ledger.json`), welche Kandidaten dort schon durchliefen. Beim Lauf gilt:

- **Straight-/Regel-/Hybrid-Stufen:** getestet wird nur
  `aktuelle Kandidaten − bereits geprüfte` → nur die neuen Wörter.
- **Kombinator-Stufen (Wort×Wort, Wort×Zahl):** bei neuen Wörtern werden gezielt
  nur die neuen Paare gefahren: `(neu_links × alle_rechts) ∪ (alt_links ×
  neu_rechts)`. Die bereits geprüften `alt×alt`-Paare werden übersprungen – ohne
  Lücke und ohne Wiederholung.
- Änderst du die *Regel* einer Stufe (anderer Regelsatz), bekommt sie einen neuen
  Ledger-Eintrag und läuft für diese neue Regel komplett – die alte Arbeit bleibt
  aber getrennt gespeichert und wird nicht doppelt gemacht.

> Hinweis: Große externe Wortlisten (rockyou) sowie Masken- und Brute-Force-Stufen
> sind nicht wort-basiert; ihr Fortschritt wird über hashcat-Sessions/`--restore`
> und die Erschöpft-Markierung fortgesetzt.

---

## Was das Skript dich fragt (einmalig)

Je besser diese Angaben, desto höher die Trefferchance – denk daran, **wie du
früher Passwörter gebildet hast**:

- Namen (du, Familie, Partner, Kinder, Haustiere), Hobbys, Orte, Bands, Vereine
- Wichtige Jahre/Zahlen und Daten (Geburtsjahre, PLZ, `ddmmyyyy` …)
- Typische Sonderzeichen/Endungen (`! ? . @ # $ _ - *` …)
- Vermutete Mindest-/Maximallänge
- Gewohnheiten: Großbuchstabe am Anfang? Ziffern am Ende? Leetspeak (`a→@`)?
- Grobe Struktur (Wort+Zahlen, Wort+Sonderzeichen+Zahlen, zwei Wörter, PIN …)
- Optional: Pfad zu großer Wortliste, gewünschte Gesamtlaufzeit (z. B. 8 h)

Alle Antworten landen in `work/config.json` und werden bei Folgeläufen
wiederverwendet.

---

## Die automatische Angriffs-Strategie (billig → teuer)

Das Skript arbeitet die Stufen in dieser Reihenfolge ab und **eskaliert von selbst**.
Bei einem Treffer stoppt es sofort; bei Zeitablauf einer Stufe macht es später dort weiter.

| Phase | Angriff | Idee |
|------|---------|------|
| **P1** | Persönliche Wörter roh + Basisregeln | Schnelle, wahrscheinlichste Treffer |
| **P2** | Persönliche Wörter + `best64` / große Regel | Typische Mutationen (Groß/Klein, Anhänge, Leet) |
| **P3** | Hybrid `Wort+Ziffern`, `Ziffern+Wort`, `Wort+Sonderzeichen`; Kombinator `Wort+Wort`, `Wort+Jahr` | Deine typischen Baumuster |
| **P4** | Große Wortliste (rockyou) roh / `+best64` / `+große Regel` | Breite Abdeckung gängiger Passwörter |
| **P5** | Gezielte Masken aus deiner Struktur/Länge | Passgenaue Brute-Force nach Muster |
| **P6** | Begrenzte Brute-Force (Länge/Charset gedeckelt) | Letzte Instanz, resümierbar |

Warum diese Reihenfolge? **Erwartungswert pro Sekunde.** Ein passendes persönliches
Wort mit Regel wird in Minuten gefunden; blinde Brute-Force über 10 Zeichen würde
Jahre dauern. Deshalb kommt das Billige und Wahrscheinliche zuerst.

### Selbst erweitern – ohne Code zu ändern
- **Bessere Wörter:** `python3 recover.py add <wort> ...` oder `words/*.txt`
  bearbeiten → beim nächsten Lauf werden **nur die neuen** getestet.
- **Eigene Regeln:** Datei `work/builtin.rule` ergänzen (hashcat-Regelsyntax).
- **Eigene Masken:** Zeilen an `work/masks.hcmask` anhängen (z. B. `?u?l?l?l?l?d?d?d?d`).

Diese Dateien werden beim nächsten `run` automatisch mitbenutzt.

---

## Technische Details

- **hashcat mode:** `11600` (7-Zip).
- **Hash-Extraktion:** bevorzugt `7z2hashcat.pl` (kompakt & prüfbar, ideal für große
  Dateien), Fallback `7z2john.pl`. Der Datei-Präfix wird automatisch entfernt.
- **Trefferprüfung:** autoritativ über das Potfile (`hashcat --show`), nicht nur über
  Exit-Codes.
- **Fortsetzen:** teure Stufen laufen mit `--session`/`--restore`; erschöpfte Stufen
  werden in `work/state.json` markiert und nicht erneut gestartet.
- **Optimierter Kernel (`-O`):** standardmäßig an (schneller; Passwortlänge i. d. R.
  bis ~31 Zeichen). In der Abfrage abschaltbar, falls du längere Passwörter vermutest.

### Tipp bei sehr großem Hash / langsamer Prüfung
Falls dein Archiv **mit Header-Verschlüsselung** (`-mhe=on`) erstellt wurde, greift
`7z2hashcat` den kleinen, verschlüsselten Header an – das ist schnell. Falls die
Prüfung pro Kandidat langsam wirkt, stelle sicher, dass du `7z2hashcat` (nicht
`7z2john`) benutzt; es wählt automatisch den kleinsten prüfbaren Datenstrom.

---

## Realistische Erwartung

- **Gut:** Passwort war ein persönliches Wort ± Zahlen/Regeln/Muster → oft Minuten
  bis wenige Stunden.
- **Schwierig:** langes, zufälliges Passwort ohne erinnerbares Muster → evtl. nicht
  in vertretbarer Zeit. Dann hilft nur: mehr/bessere Hinweise in `intake` und mehr
  Laufzeit.

Je mehr du dich an **Bausteine** erinnerst (auch nur ein Wortstamm oder die grobe
Länge), desto dramatischer steigt die Chance.
