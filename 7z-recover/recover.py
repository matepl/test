#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recover.py - Interaktiver 7-Zip Passwort-Recovery-Orchestrator
==============================================================

Fuer die Wiederherstellung eines vergessenen Passworts einer EIGENEN 7-Zip-Datei.

Ablauf:
  1. Tool-Check (hashcat, 7z2hashcat/7z2john, perl)
  2. Hash-Extraktion aus dem Archiv (einmalig, gecacht)
  3. Interaktive Abfrage von persoenlichem Kontext (einmalig, gespeichert)
  4. Automatischer, eskalierender Angriffsplan der stundenlang laeuft:
       schnelle, gezielte Angriffe zuerst -> immer breitere/teurere zuletzt.
  5. Ergebnis: das gefundene Passwort wird angezeigt und gespeichert.

ERWEITERBARES WOERTERBUCH & KEINE DOPPELTE ARBEIT
-------------------------------------------------
Woerter liegen im Ordner  words/  (eine oder mehrere .txt-Dateien, editierbar).
Du kannst jederzeit Woerter ergaenzen:
    python3 recover.py add wort1 wort2 "zwei woerter"
    (oder einfach words/dictionary.txt bzw. eigene words/*.txt-Dateien bearbeiten)

Beim naechsten Lauf testet das Skript NUR die neu hinzugekommenen Woerter und
die neuen Kombinationen - bereits geprueftes wird nicht wiederholt. Was schon
getestet wurde, siehst du mit:
    python3 recover.py words       # Fortschritt pro Angriffsstufe
    work/tested.log                # chronologisches Protokoll

Du musst am Algorithmus NICHTS anpassen. Abbrechen (Strg-C) und spaeter erneut
starten setzt automatisch fort (Ledger + Sessions + Potfile).

Rechtlicher Hinweis: Nur fuer Dateien verwenden, die dir gehoeren / fuer die du
autorisiert bist. Passwort-Recovery an fremden Daten ist illegal.
"""

import argparse
import datetime
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------
# Pfade / Konstanten
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"                 # Alle Laufzeit-Dateien landen hier
WORDS_DIR = HERE / "words"           # Erweiterbares Woerterbuch (deine *.txt)
CONFIG_FILE = WORK / "config.json"   # Deine Antworten (persoenlicher Kontext)
STATE_FILE = WORK / "state.json"     # Fortschritt der NICHT-Wort-Angriffe
LEDGER_FILE = WORK / "ledger.json"   # Welche Woerter/Paare je Stufe geprueft wurden
TESTED_LOG = WORK / "tested.log"     # Menschenlesbares Pruef-Protokoll
HASH_FILE = WORK / "hash.txt"        # Extrahierter 7z-Hash (hashcat-Format)
POTFILE = WORK / "cracked.pot"       # Gefundene Passwoerter (hashcat potfile)
RESULT_FILE = WORK / "PASSWORT_GEFUNDEN.txt"
DICT_FILE = WORDS_DIR / "dictionary.txt"   # Standard-Woerterbuch (editierbar)
NUMBERS_FILE = WORDS_DIR / "numbers.txt"   # Zahlen/Jahre (editierbar)

HASH_MODE = "11600"                  # hashcat mode fuer 7-Zip

# Verzeichnisse mit Wortlisten/Regeln, die typischerweise vorhanden sind.
COMMON_WORDLIST_DIRS = [
    "/usr/share/wordlists",
    "/usr/share/seclists/Passwords",
    "/usr/share/hashcat/wordlists",
    str(Path.home() / "wordlists"),
]
COMMON_RULE_DIRS = [
    "/usr/share/hashcat/rules",
    "/usr/local/share/hashcat/rules",
    str(Path.home() / "hashcat" / "rules"),
]

# --------------------------------------------------------------------------
# Kleine Helfer
# --------------------------------------------------------------------------

def c(text, color):
    codes = {"g": "32", "y": "33", "r": "31", "b": "34", "c": "36", "bold": "1"}
    if not sys.stdout.isatty():
        return text
    return f"\033[{codes.get(color,'0')}m{text}\033[0m"

def info(msg):  print(c("[i] ", "c") + msg)
def ok(msg):    print(c("[+] ", "g") + msg)
def warn(msg):  print(c("[!] ", "y") + msg)
def err(msg):   print(c("[x] ", "r") + msg)
def head(msg):
    print()
    print(c("=" * 70, "b"))
    print(c("  " + msg, "bold"))
    print(c("=" * 70, "b"))

def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    try:
        val = input(c("  ? ", "y") + prompt + suffix + ": ").strip()
    except EOFError:
        val = ""
    return val if val else default

def ask_yesno(prompt, default=False):
    d = "j" if default else "n"
    val = ask(prompt + " (j/n)", d).lower()
    return val.startswith("j") or val.startswith("y")

def ask_list(prompt):
    """Komma- oder Leerzeichen-getrennte Liste einlesen."""
    raw = ask(prompt + " (durch Komma trennen)")
    if not raw:
        return []
    parts = re.split(r"[,\n]", raw)
    return [p.strip() for p in parts if p.strip()]

def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None

def find_first(paths):
    for p in paths:
        if p and Path(p).exists():
            return str(p)
    return None

def akey(*parts):
    """Stabiler, kurzer Schluessel fuer eine Angriffsstufe."""
    return hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]

# --------------------------------------------------------------------------
# Woerterbuch: sammeln & Varianten (Basis fuer die Delta-Logik)
# --------------------------------------------------------------------------

def collect_base_words(cfg):
    """Alle Basiswoerter = Config-Woerter + alle words/*.txt (ausser numbers.txt)."""
    words = set(w.strip() for w in cfg.get("words", []) if w.strip())
    if WORDS_DIR.exists():
        for f in sorted(WORDS_DIR.glob("*.txt")):
            if f.name == NUMBERS_FILE.name:
                continue
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    words.add(line)
    return words

def collect_numbers(cfg):
    nums = set(str(n).strip() for n in cfg.get("numbers", []) if str(n).strip())
    if NUMBERS_FILE.exists():
        for line in NUMBERS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                nums.add(line)
    return nums

def variant_lines(words):
    """Voller Varianten-Satz je Wort (fuer Straight-/Regel-/Hybrid-Angriffe)."""
    out = set()
    for w in words:
        if not w:
            continue
        out.add(w)
        out.add(w.lower())
        out.add(w.capitalize())
        out.add(w.upper())
    return out

def combo_lines(words):
    """Kompakter Varianten-Satz fuer Kombinator-Angriffe (begrenzt die Explosion)."""
    out = set()
    for w in words:
        if not w:
            continue
        out.add(w.lower())
        out.add(w.capitalize())
    return out

def write_lines(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

# --------------------------------------------------------------------------
# Ledger (welche Kandidaten/Paare je Stufe bereits geprueft wurden)
# --------------------------------------------------------------------------

def load_ledger():
    led = load_json(LEDGER_FILE, {})
    led.setdefault("attacks", {})   # key -> {"name":.., "words":[...]}
    led.setdefault("combi", {})     # key -> {"name":.., "left":[...], "right":[...]}
    return led

def save_ledger(led):
    save_json(LEDGER_FILE, led)

def log_tested(name, new_count, total):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"{ts}  {name}  +{new_count} neue Kandidaten getestet (verarbeitet gesamt: {total})\n"
    try:
        with open(TESTED_LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass

# --------------------------------------------------------------------------
# Tool-Erkennung
# --------------------------------------------------------------------------

def detect_tools():
    return {
        "hashcat": which("hashcat"),
        "perl": which("perl"),
        "7z2hashcat": which("7z2hashcat.pl", "7z2hashcat"),
        "7z2john": which("7z2john.pl", "7z2john"),
        "john": which("john"),
    }

def print_install_hint():
    warn("Ein oder mehrere Tools fehlen. Installationshinweise:")
    print("""
  Debian/Ubuntu/Kali:
      sudo apt update
      sudo apt install -y hashcat john p7zip-full perl

  Arch:
      sudo pacman -S hashcat john p7zip perl

  Fedora:
      sudo dnf install hashcat john p7zip perl

  7z2hashcat (empfohlen fuer die Hash-Extraktion, v.a. bei grossen Dateien):
      git clone https://github.com/philsmd/7z2hashcat
      # danach 7z2hashcat/7z2hashcat.pl im PATH oder Pfad angeben

  GPU-Treiber fuer hashcat (stark empfohlen, sonst sehr langsam):
      NVIDIA: CUDA-Toolkit  |  AMD: ROCm/OpenCL  |  pruefen mit:  hashcat -I
""")

# --------------------------------------------------------------------------
# Schritt 1: Hash-Extraktion
# --------------------------------------------------------------------------

def extract_hash(archive, tools):
    if HASH_FILE.exists() and HASH_FILE.stat().st_size > 0:
        ok(f"Hash bereits extrahiert: {HASH_FILE}")
        return True

    archive = Path(archive).expanduser()
    if not archive.exists():
        err(f"Archiv nicht gefunden: {archive}")
        return False

    WORK.mkdir(parents=True, exist_ok=True)
    raw = ""

    if tools.get("7z2hashcat") and tools.get("perl"):
        info("Extrahiere Hash mit 7z2hashcat ...")
        try:
            r = subprocess.run([tools["perl"], tools["7z2hashcat"], str(archive)],
                               capture_output=True, text=True, timeout=3600)
            raw = r.stdout.strip()
        except Exception as e:
            warn(f"7z2hashcat fehlgeschlagen: {e}")

    if not raw and tools.get("7z2john") and tools.get("perl"):
        info("Extrahiere Hash mit 7z2john ...")
        try:
            r = subprocess.run([tools["perl"], tools["7z2john"], str(archive)],
                               capture_output=True, text=True, timeout=3600)
            raw = r.stdout.strip()
        except Exception as e:
            warn(f"7z2john fehlgeschlagen: {e}")

    if not raw:
        err("Konnte keinen Hash extrahieren. Ist 7z2hashcat oder 7z2john installiert?")
        return False

    lines = []
    for line in raw.splitlines():
        idx = line.find("$7z$")
        if idx >= 0:
            lines.append(line[idx:].strip())
    if not lines:
        err("Ausgabe enthaelt kein $7z$-Hash. Archiv ggf. nicht passwortgeschuetzt?")
        return False

    HASH_FILE.write_text(lines[0] + "\n", encoding="utf-8")
    ok(f"Hash gespeichert: {HASH_FILE}  ({len(lines[0])} Zeichen)")
    if len(lines[0]) > 200000:
        warn("Der Hash ist sehr gross. Verifikation pro Kandidat kann langsamer sein.")
        warn("Tipp: 7z2hashcat (statt 7z2john) haelt ihn i.d.R. kompakt.")
    return True

# --------------------------------------------------------------------------
# Schritt 2: Interaktive Abfrage
# --------------------------------------------------------------------------

def run_intake():
    head("Persoenlicher Kontext - einmalig beantworten")
    print("""  Je besser diese Angaben, desto hoeher die Erfolgschance. Alles optional -
  einfach Enter druecken zum Ueberspringen. Denk an das Passwort so, wie DU
  frueher Passwoerter gebildet hast (Namen, Jahre, Lieblingswoerter, Muster).
  (Woerter kannst du spaeter jederzeit mit 'add' ergaenzen.)
""")
    cfg = load_json(CONFIG_FILE, {})

    words = []
    words += ask_list("Vornamen/Nachnamen (du, Familie, Partner, Kinder, Haustiere)")
    words += ask_list("Wichtige Woerter (Hobbys, Orte, Bands, Vereine, Firmen, Spitznamen)")
    words += ask_list("Weitere moegliche Basiswoerter")
    cfg["words"] = sorted(set(list(cfg.get("words", [])) + [w for w in words if w]))

    numbers = ask_list("Wichtige Jahre/Zahlen (Geburtsjahre, PLZ, Lieblingszahlen)")
    dates = ask_list("Wichtige Daten (z.B. 1985, 0304, 03041985, ddmmyyyy)")
    cfg["numbers"] = sorted(set(list(cfg.get("numbers", [])) + numbers + dates))

    default_syms = cfg.get("symbols", "!?.@#$_-*+1")
    cfg["symbols"] = ask("Welche Sonderzeichen/Endungen nutzt du typischerweise", default_syms)

    cfg["min_len"] = int(ask("Vermutete MINIMALE Passwortlaenge", str(cfg.get("min_len", 6))) or "6")
    cfg["max_len"] = int(ask("Vermutete MAXIMALE Passwortlaenge", str(cfg.get("max_len", 12))) or "12")

    cfg["cap_first"] = ask_yesno("Faengt dein Passwort oft mit einem Grossbuchstaben an?", cfg.get("cap_first", True))
    cfg["has_upper"] = ask_yesno("Nutzt du (auch mitten drin) Grossbuchstaben?", cfg.get("has_upper", True))
    cfg["has_digits"] = ask_yesno("Enthaelt es meistens Ziffern?", cfg.get("has_digits", True))
    cfg["digits_at_end"] = ask_yesno("Stehen Ziffern meist am ENDE?", cfg.get("digits_at_end", True))
    cfg["has_symbols"] = ask_yesno("Enthaelt es Sonderzeichen?", cfg.get("has_symbols", True))

    print("""
  Typische Struktur deines Passworts?
    1) Wort + Ziffern am Ende         (z.B. Berlin2010, hund123)
    2) Wort + Sonderzeichen + Ziffern (z.B. Berlin!2010)
    3) Zwei Woerter kombiniert        (z.B. redhouse, BierGarten)
    4) Nur Ziffern (PIN-artig)        (z.B. 04031985)
    5) Unbekannt / gemischt
""")
    cfg["structure"] = ask("Auswahl 1-5", cfg.get("structure", "5"))
    cfg["leet"] = ask_yesno("Ersetzt du Buchstaben durch Zeichen (a->@, e->3, o->0, s->$)?", cfg.get("leet", False))

    guessed_wl = find_first([str(Path(d) / "rockyou.txt") for d in COMMON_WORDLIST_DIRS])
    cfg["big_wordlist"] = ask("Pfad zu grosser Wortliste (z.B. rockyou.txt), leer=auto",
                              cfg.get("big_wordlist", guessed_wl or ""))
    cfg["total_hours"] = float(ask("Gesamtlaufzeit in Stunden bevor gestoppt wird",
                                    str(cfg.get("total_hours", 8))) or "8")
    cfg["use_optimized"] = ask_yesno("Optimierten Kernel nutzen (-O, schneller, Laenge<=~31)?",
                                     cfg.get("use_optimized", True))
    cfg["workload"] = ask("hashcat Workload-Profil 1..4 (3=Desktop, 4=nur dediziert)",
                          str(cfg.get("workload", "3")))

    save_json(CONFIG_FILE, cfg)
    ok(f"Konfiguration gespeichert: {CONFIG_FILE}")
    return cfg

# --------------------------------------------------------------------------
# Schritt 3: Eingaben vorbereiten (Woerterbuch, Zahlen, Regeln, Masken)
# --------------------------------------------------------------------------

def prepare_inputs(cfg):
    WORK.mkdir(parents=True, exist_ok=True)
    WORDS_DIR.mkdir(parents=True, exist_ok=True)

    # Standard-Woerterbuch aus Config-Woertern anlegen (nur beim ersten Mal),
    # damit du es sehen und editieren kannst.
    if not DICT_FILE.exists():
        seed = sorted(set(cfg.get("words", [])))
        header = ("# Dein Woerterbuch - ein Wort pro Zeile. Jederzeit ergaenzbar.\n"
                  "# Neue Woerter werden beim naechsten Lauf automatisch (nur sie) getestet.\n"
                  "# Weitere Dateien words/*.txt werden ebenfalls eingelesen.\n")
        DICT_FILE.write_text(header + "\n".join(seed) + ("\n" if seed else ""), encoding="utf-8")

    if not NUMBERS_FILE.exists():
        nums = set(cfg.get("numbers", []))
        for y in range(1970, 2027):
            nums.add(str(y))
        header = "# Zahlen/Jahre - ein Eintrag pro Zeile. Jederzeit ergaenzbar.\n"
        NUMBERS_FILE.write_text(header + "\n".join(sorted(nums)) + "\n", encoding="utf-8")

    write_builtin_rules(cfg)

    masks = build_masks(cfg)
    write_lines(WORK / "masks.hcmask", masks)
    ok(f"Eingaben vorbereitet. Woerterbuch: {DICT_FILE}")

def write_builtin_rules(cfg):
    rules = [
        ":", "c", "C", "u", "l", "t",
        "$1", "$2", "$3", "$!", "$.", "$@", "$#",
        "$1$2$3", "$1$2$3$4", "$0$1", "$1$2", "$2$0$1$1",
        "$!$!", "$1$!", "^!",
        "so0", "se3", "sa@", "si1", "ss$",
        "c $1", "c $1$2$3", "c $!", "r",
        "$2$0$1$0", "$2$0$1$5", "$2$0$2$0",
    ]
    for y in range(1970, 2027):
        rules.append("$" + "$".join(list(str(y))))
    write_lines(WORK / "builtin.rule", rules)

def build_masks(cfg):
    masks = []
    mn, mx = cfg.get("min_len", 6), cfg.get("max_len", 12)
    structure = cfg.get("structure", "5")

    def letters(n, cap_first):
        if n <= 0:
            return ""
        return ("?u" + "?l" * (n - 1)) if cap_first else ("?l" * n)

    if structure == "4" or cfg.get("has_digits"):
        for L in range(max(4, mn), min(mx, 12) + 1):
            masks.append("?d" * L)
    if structure in ("1", "5"):
        for total in range(mn, mx + 1):
            for d in (2, 3, 4):
                if total - d >= 2:
                    masks.append(letters(total - d, cfg.get("cap_first", True)) + "?d" * d)
    if structure in ("2", "5") and cfg.get("has_symbols"):
        for total in range(mn, mx + 1):
            for d in (2, 4):
                base = total - d - 1
                if base >= 2:
                    masks.append(letters(base, cfg.get("cap_first", True)) + "?1" + "?d" * d)
    if structure in ("3", "5"):
        for total in range(max(mn, 6), min(mx, 12) + 1):
            masks.append(letters(total, cfg.get("cap_first", True)))

    seen, out = set(), []
    for m in masks:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out

# --------------------------------------------------------------------------
# Angriffsplan
# --------------------------------------------------------------------------

def build_attack_plan(cfg):
    """Liefert die eskalierende Stufenliste. Jede Stufe hat 'family' + stabilen 'key'.
    Personal-/Hybrid-/Kombinator-Stufen sind DELTA-faehig (nur neue Woerter)."""
    plan = []
    builtin_rule = str(WORK / "builtin.rule")
    best64 = find_first([str(Path(d) / "best64.rule") for d in COMMON_RULE_DIRS])
    onerule = find_first(
        [str(Path(d) / n) for d in COMMON_RULE_DIRS
         for n in ("OneRuleToRuleThemAll.rule", "dive.rule", "rockyou-30000.rule")])
    big_wl = cfg.get("big_wordlist", "").strip() or \
        (find_first([str(Path(d) / "rockyou.txt") for d in COMMON_WORDLIST_DIRS]) or "")

    def add(**a):
        a["key"] = akey(a["family"], a.get("kind", ""), a.get("rule", ""),
                        a.get("mask", ""), a.get("left_kind", ""),
                        a.get("right_kind", ""), a["name"])
        plan.append(a)

    # --- Phase 1: schnelle, gezielte Treffer -------------------------------
    add(family="wl_personal", kind="wl", rule=None, budget=180,
        name="P1: Persoenliche Woerter (roh)")
    add(family="wl_personal", kind="wl", rule=builtin_rule, budget=600,
        name="P1: Persoenliche Woerter + Basisregeln")

    # --- Phase 2: persoenliche Woerter mit starken Regeln ------------------
    if best64:
        add(family="wl_personal", kind="wl", rule=best64, budget=600,
            name="P2: Persoenlich + best64")
    if onerule:
        add(family="wl_personal", kind="wl", rule=onerule, budget=1200,
            name="P2: Persoenlich + grosse Regel")

    # --- Phase 3: Hybrid & Kombinationen -----------------------------------
    add(family="hybrid_personal", kind="hybrid6", mask="?d?d?d?d", budget=600,
        name="P3: Wort + 2-4 Ziffern (Hybrid)")
    add(family="hybrid_personal", kind="hybrid7", mask="?d?d?d?d", budget=600,
        name="P3: 2-4 Ziffern + Wort (Hybrid)")
    if cfg.get("symbols"):
        add(family="hybrid_personal", kind="hybrid6", mask="?1", budget=300,
            name="P3: Wort + Sonderzeichen (Hybrid)")
    add(family="combi", left_kind="words", right_kind="words", budget=600,
        name="P3: Wort + Wort (Kombinator)")
    add(family="combi", left_kind="words", right_kind="numbers", budget=600,
        name="P3: Wort + Zahl/Jahr (Kombinator)")

    # --- Phase 4: grosse Wortliste (NICHT delta - fixe externe Liste) ------
    if big_wl and Path(big_wl).exists():
        add(family="wl_big", kind="wl", wordlist=big_wl, rule=None, budget=600,
            resumable=True, name="P4: Grosse Wortliste (roh)")
        if best64:
            add(family="wl_big", kind="wl", wordlist=big_wl, rule=best64, budget=1800,
                resumable=True, name="P4: Grosse Wortliste + best64")
        if onerule:
            add(family="wl_big", kind="wl", wordlist=big_wl, rule=onerule, budget=3600,
                resumable=True, name="P4: Grosse Wortliste + grosse Regel")

    # --- Phase 5: gezielte Masken ------------------------------------------
    add(family="maskfile", maskfile=str(WORK / "masks.hcmask"), budget=3600,
        resumable=True, name="P5: Gezielte Masken (Struktur)")

    # --- Phase 6: begrenzte Brute-Force ------------------------------------
    mn, mx = cfg.get("min_len", 6), min(cfg.get("max_len", 12), 8)
    charset = "?u?l?d" if cfg.get("has_upper") else "?l?d"
    if cfg.get("has_symbols"):
        charset += "?s"
    add(family="increment", charset=charset, mn=mn, mx=mx, budget=99999,
        resumable=True, name=f"P6: Brute-Force {mn}-{mx} ({charset})")

    return plan

# --------------------------------------------------------------------------
# hashcat-Ausfuehrung
# --------------------------------------------------------------------------

def hashcat_base_cmd(cfg, tools):
    cmd = [tools["hashcat"], "-m", HASH_MODE, "-w", str(cfg.get("workload", "3")),
           "--potfile-path", str(POTFILE), "--status", "--status-timer", "30"]
    if cfg.get("use_optimized", True):
        cmd.append("-O")
    syms = cfg.get("symbols", "").strip()
    if syms:
        cmd += ["-1", syms]
    return cmd

def run_cmd(cmd):
    info("hashcat: " + " ".join(shlex.quote(x) for x in cmd))
    try:
        return subprocess.run(cmd, cwd=str(WORK)).returncode
    except KeyboardInterrupt:
        raise
    except Exception as e:
        err(f"hashcat-Start fehlgeschlagen: {e}")
        return -1

def is_cracked(tools):
    if not POTFILE.exists() or POTFILE.stat().st_size == 0:
        return None
    try:
        r = subprocess.run(
            [tools["hashcat"], "-m", HASH_MODE, str(HASH_FILE),
             "--potfile-path", str(POTFILE), "--show"],
            capture_output=True, text=True, timeout=120)
        line = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        if line and ":" in line:
            # Der $7z$-Hash enthaelt keine ':' - alles nach dem ersten ':' ist das Passwort.
            return line.split(":", 1)[1]
    except Exception:
        pass
    return None

# ---- Delta-Angriff: persoenliche Woerter / Hybrid ------------------------

def run_word_attack(atk, cfg, tools, ledger, budget):
    """Testet NUR neue Kandidaten dieser Stufe. Gibt True zurueck, wenn gelaufen."""
    entry = ledger["attacks"].setdefault(atk["key"], {"name": atk["name"], "words": []})
    processed = set(entry["words"])
    current = variant_lines(collect_base_words(cfg))
    new = sorted(current - processed)
    if not new:
        return False  # nichts Neues -> keine Wiederholung

    info(f"Neue Kandidaten in dieser Stufe: {len(new)} (bereits geprueft: {len(processed)})")
    delta = write_lines(WORK / f"delta_{atk['key']}.txt", new)

    base = hashcat_base_cmd(cfg, tools)
    cmd = base + ["--runtime", str(budget)]
    if atk["kind"] == "wl":
        cmd += ["-a", "0", str(HASH_FILE), str(delta)]
        if atk.get("rule"):
            cmd += ["-r", atk["rule"]]
    elif atk["kind"] == "hybrid6":
        cmd += ["-a", "6", str(HASH_FILE), str(delta), atk["mask"]]
    elif atk["kind"] == "hybrid7":
        cmd += ["-a", "7", str(HASH_FILE), atk["mask"], str(delta)]

    rc = run_cmd(cmd)
    if rc in (0, 1):  # abgeschlossen (0=geknackt, 1=Keyspace erschoepft)
        entry["words"] = sorted(processed | set(new))
        save_ledger(ledger)
        log_tested(atk["name"], len(new), len(entry["words"]))
    else:
        info("Zeitfenster erreicht - diese neuen Woerter werden beim naechsten Lauf fortgesetzt.")
    return True

# ---- Delta-Angriff: Kombinator (zwei Listen) -----------------------------

def _combo_source(kind, cfg):
    if kind == "numbers":
        return combo_lines(collect_numbers(cfg)) | collect_numbers(cfg)
    return combo_lines(collect_base_words(cfg))

def run_combi_attack(atk, cfg, tools, ledger, budget):
    """Kombinator L x R, aber nur die NEUEN Paare (Lneu x Ralle) u (Lalt x Rneu)."""
    entry = ledger["combi"].setdefault(
        atk["key"], {"name": atk["name"], "left": [], "right": []})
    Lall = _combo_source(atk["left_kind"], cfg)
    Rall = _combo_source(atk["right_kind"], cfg)
    Lproc, Rproc = set(entry["left"]), set(entry["right"])
    Lnew, Rnew = sorted(Lall - Lproc), sorted(Rall - Rproc)

    runs = []
    if Lnew:
        runs.append((Lnew, sorted(Rall)))          # neue linke x alle rechten
    if Rnew and Lproc:
        runs.append((sorted(Lproc), Rnew))         # alte linke x neue rechte
    if not runs:
        return False  # nichts Neues

    total_new_pairs = len(Lnew) * len(Rall) + len(Lproc) * len(Rnew)
    info(f"Neue Wort-Paare in dieser Stufe: ~{total_new_pairs}")
    per = max(30, budget // len(runs))
    base = hashcat_base_cmd(cfg, tools)
    completed_all = True
    for i, (Lf, Rf) in enumerate(runs):
        lp = write_lines(WORK / f"combi_{atk['key']}_L{i}.txt", Lf)
        rp = write_lines(WORK / f"combi_{atk['key']}_R{i}.txt", Rf)
        rc = run_cmd(base + ["--runtime", str(per), "-a", "1",
                             str(HASH_FILE), str(lp), str(rp)])
        if rc not in (0, 1):
            completed_all = False
            break
    if completed_all:
        entry["left"] = sorted(Lall)
        entry["right"] = sorted(Rall)
        save_ledger(ledger)
        log_tested(atk["name"], total_new_pairs, len(Lall) * len(Rall))
    else:
        info("Zeitfenster erreicht - restliche neue Paare folgen beim naechsten Lauf.")
    return True

# ---- Nicht-Delta-Angriff: grosse Wortliste / Masken / Brute-Force --------

def run_fixed_attack(atk, cfg, tools, budget):
    base = hashcat_base_cmd(cfg, tools)
    session = "s_" + atk["key"]
    restore_path = WORK / (session + ".restore")

    if atk.get("resumable") and restore_path.exists():
        cmd = base + ["--session", session, "--restore", "--runtime", str(budget)]
    else:
        cmd = base + ["--session", session, "--runtime", str(budget)]
        fam = atk["family"]
        if fam == "wl_big":
            cmd += ["-a", "0", str(HASH_FILE), atk["wordlist"]]
            if atk.get("rule"):
                cmd += ["-r", atk["rule"]]
        elif fam == "maskfile":
            cmd += ["-a", "3", str(HASH_FILE), atk["maskfile"]]
        elif fam == "increment":
            mask = atk["charset"] * atk["mx"]
            cmd += ["-a", "3", "--increment",
                    "--increment-min", str(atk["mn"]),
                    "--increment-max", str(atk["mx"]),
                    str(HASH_FILE), mask]
    return run_cmd(cmd)

# --------------------------------------------------------------------------
# Plan ausfuehren
# --------------------------------------------------------------------------

def run_plan(cfg, tools):
    plan = build_attack_plan(cfg)
    ledger = load_ledger()
    state = load_json(STATE_FILE, {"exhausted": []})
    exhausted = set(state.get("exhausted", []))

    deadline = time.time() + cfg.get("total_hours", 8) * 3600
    head(f"Angriffsplan startet - Laufzeit-Budget: {cfg.get('total_hours',8)} h")
    info(f"{len(plan)} Angriffsstufen. Stoppt bei Treffer oder Zeitablauf.")
    info("Abbrechen mit Strg-C ist sicher - Fortschritt wird gespeichert.")
    info("Bereits geprueftes wird uebersprungen; nur Neues wird getestet.")

    lap = 0
    while time.time() < deadline:
        lap += 1
        ran_something = False
        for atk in plan:
            if time.time() >= deadline:
                break
            pw = is_cracked(tools)
            if pw:
                return announce_result(pw)

            remaining = int(deadline - time.time())
            if remaining <= 5:
                break
            eff_budget = min(atk["budget"], remaining)
            fam = atk["family"]

            if fam in ("wl_personal", "hybrid_personal"):
                head(f"[Runde {lap}] {atk['name']}")
                if run_word_attack(atk, cfg, tools, ledger, eff_budget):
                    ran_something = True
            elif fam == "combi":
                head(f"[Runde {lap}] {atk['name']}")
                if run_combi_attack(atk, cfg, tools, ledger, eff_budget):
                    ran_something = True
            else:
                if atk["name"] in exhausted:
                    continue
                head(f"[Runde {lap}] {atk['name']}  (bis zu {eff_budget}s)")
                rc = run_fixed_attack(atk, cfg, tools, eff_budget)
                ran_something = True
                if rc == 1:
                    ok(f"Stufe erschoepft: {atk['name']}")
                    exhausted.add(atk["name"])
                    save_json(STATE_FILE, {"exhausted": sorted(exhausted)})
                elif rc in (2, 3, 4):
                    info("Zeitfenster erreicht - Stufe wird spaeter fortgesetzt.")
                elif rc < 0 or rc == 255:
                    warn(f"Fehlercode {rc} bei '{atk['name']}' - Stufe wird uebersprungen.")
                    exhausted.add(atk["name"])
                    save_json(STATE_FILE, {"exhausted": sorted(exhausted)})

            pw = is_cracked(tools)
            if pw:
                return announce_result(pw)

        if not ran_something:
            warn("Nichts Neues zu testen (alle Stufen erschoepft / keine neuen Woerter).")
            print("  Ergaenze Woerter mit:  python3 recover.py add <wort> ...")
            print("  oder erhoehe die Laufzeit / gib eine groessere Wortliste an.")
            break

    pw = is_cracked(tools)
    if pw:
        return announce_result(pw)
    head("Zeitbudget aufgebraucht - noch kein Treffer")
    print("""  Naechste sinnvolle Schritte:
    * Woerter ergaenzen (nur diese werden getestet):  python3 recover.py add <wort> ...
    * Fortschritt ansehen:                            python3 recover.py words
    * Groessere Wortliste angeben und erneut 'run'
    * Laufzeit erhoehen und erneut 'run' (setzt automatisch fort)
""")
    return False

def announce_result(pw):
    head("PASSWORT GEFUNDEN")
    print(c(f"\n    ==>  {pw}\n", "g"))
    RESULT_FILE.write_text(pw + "\n", encoding="utf-8")
    ok(f"Auch gespeichert in: {RESULT_FILE}")
    print("\n  Testen mit:")
    print(c(f"    7z t deine_datei.7z -p'{pw}'", "c"))
    return True

# --------------------------------------------------------------------------
# Woerter-Verwaltung & Fortschrittsanzeige
# --------------------------------------------------------------------------

def cmd_add(new_words):
    WORDS_DIR.mkdir(parents=True, exist_ok=True)
    existing = set()
    if DICT_FILE.exists():
        for line in DICT_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                existing.add(s)
    else:
        DICT_FILE.write_text("# Dein Woerterbuch - ein Wort pro Zeile.\n", encoding="utf-8")

    added = [w.strip() for w in new_words if w.strip() and w.strip() not in existing]
    if not added:
        warn("Keine neuen Woerter (alle schon vorhanden).")
        return
    with open(DICT_FILE, "a", encoding="utf-8") as fh:
        for w in added:
            fh.write(w + "\n")
    ok(f"{len(added)} Wort(e) ergaenzt in {DICT_FILE}:")
    for w in added:
        print("     + " + w)
    info("Beim naechsten 'run' werden NUR diese neuen Woerter/Kombinationen getestet.")

def cmd_words(cfg, tools):
    head("Woerterbuch & Pruef-Fortschritt")
    base = collect_base_words(cfg)
    variants = variant_lines(base)
    numbers = collect_numbers(cfg)

    print(f"  Woerterbuch-Dateien in {WORDS_DIR}/:")
    if WORDS_DIR.exists():
        for f in sorted(WORDS_DIR.glob("*.txt")):
            n = sum(1 for l in f.read_text(encoding='utf-8', errors='ignore').splitlines()
                    if l.strip() and not l.strip().startswith("#"))
            print(f"    - {f.name:20} {n} Eintraege")
    print(f"  Basiswoerter gesamt : {len(base)}")
    print(f"  Kandidaten (Varianten): {len(variants)}")
    print(f"  Zahlen/Jahre        : {len(numbers)}")

    ledger = load_ledger()
    print()
    print(c("  Fortschritt je Stufe (verarbeitet / aktuell / NEU offen):", "bold"))
    for atk in build_attack_plan(cfg):
        fam = atk["family"]
        if fam in ("wl_personal", "hybrid_personal"):
            proc = set(ledger["attacks"].get(atk["key"], {}).get("words", []))
            total = len(variants)
            new = len(set(variants) - proc)
            flag = c("neu!", "y") if new else c("fertig", "g")
            print(f"    [{flag}] {atk['name']:42} {len(proc):5} / {total:5} / {new}")
        elif fam == "combi":
            e = ledger["combi"].get(atk["key"], {})
            Lall = _combo_source(atk["left_kind"], cfg)
            Rall = _combo_source(atk["right_kind"], cfg)
            Lp, Rp = len(e.get("left", [])), len(e.get("right", []))
            done = (Lp >= len(Lall) and Rp >= len(Rall))
            flag = c("fertig", "g") if done else c("neu!", "y")
            print(f"    [{flag}] {atk['name']:42} L {Lp}/{len(Lall)}  R {Rp}/{len(Rall)}")

    state = load_json(STATE_FILE, {"exhausted": []})
    exhausted = set(state.get("exhausted", []))
    print()
    print(c("  Feste Stufen (grosse Wortliste / Masken / Brute-Force):", "bold"))
    for atk in build_attack_plan(cfg):
        if atk["family"] in ("wl_big", "maskfile", "increment"):
            status = c("erschoepft", "g") if atk["name"] in exhausted else c("offen/laeuft", "y")
            print(f"    [{status}] {atk['name']}")

    if TESTED_LOG.exists():
        print()
        info(f"Chronologisches Protokoll: {TESTED_LOG}")
        tail = TESTED_LOG.read_text(encoding="utf-8").splitlines()[-8:]
        for line in tail:
            print("    " + line)

# --------------------------------------------------------------------------
# Selbsttest
# --------------------------------------------------------------------------

def run_selftest(cfg, tools):
    head("Selbsttest der Toolchain")
    sevenzip = which("7z", "7za")
    if not sevenzip:
        err("7z/7za nicht gefunden - Selbsttest nicht moeglich (p7zip-full installieren).")
        return False
    testdir = WORK / "selftest"
    if testdir.exists():
        shutil.rmtree(testdir)
    testdir.mkdir(parents=True)
    (testdir / "hallo.txt").write_text("selftest content\n", encoding="utf-8")
    arc = testdir / "test.7z"
    test_pw = "Test123!"
    subprocess.run([sevenzip, "a", "-p" + test_pw, "-mhe=on", str(arc),
                    str(testdir / "hallo.txt")], capture_output=True)
    if not arc.exists():
        err("Konnte Test-Archiv nicht erstellen.")
        return False
    ok(f"Test-Archiv erstellt (Passwort: {test_pw})")

    tool = tools.get("7z2hashcat") or tools.get("7z2john")
    if not tool:
        err("Kein 7z2hashcat/7z2john gefunden.")
        return False
    r = subprocess.run([tools["perl"], tool, str(arc)], capture_output=True, text=True)
    h = ""
    for line in r.stdout.splitlines():
        idx = line.find("$7z$")
        if idx >= 0:
            h = line[idx:].strip()
            break
    if not h:
        err("Hash-Extraktion im Selbsttest fehlgeschlagen.")
        return False
    th = testdir / "hash.txt"
    th.write_text(h + "\n", encoding="utf-8")
    tp = testdir / "pot"
    cmd = [tools["hashcat"], "-m", HASH_MODE, "-a", "3", "-w", "3",
           "--potfile-path", str(tp), "-1", "!?.@", str(th), "Test?d?d?d?1"]
    info("hashcat Selbsttest laeuft ...")
    subprocess.run(cmd, cwd=str(testdir))
    r2 = subprocess.run([tools["hashcat"], "-m", HASH_MODE, str(th),
                         "--potfile-path", str(tp), "--show"],
                        capture_output=True, text=True)
    if test_pw in r2.stdout:
        ok("Selbsttest ERFOLGREICH - deine Toolchain funktioniert fuer 7-Zip (mode 11600).")
        return True
    err("Selbsttest fehlgeschlagen - hashcat konnte das Test-Passwort nicht finden.")
    warn("Pruefe GPU/OpenCL mit 'hashcat -I' und die hashcat-Version.")
    return False

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def cmd_status(tools):
    head("Status")
    print(f"  Hash-Datei : {'vorhanden' if HASH_FILE.exists() else 'fehlt'}")
    print(f"  Config     : {'vorhanden' if CONFIG_FILE.exists() else 'fehlt'}")
    pw = is_cracked(tools) if HASH_FILE.exists() else None
    if pw:
        announce_result(pw)
    else:
        print("  Passwort   : noch nicht gefunden")

def main():
    p = argparse.ArgumentParser(
        description="Interaktiver 7-Zip Passwort-Recovery-Orchestrator (eigene Dateien).")
    p.add_argument("command", nargs="?", default="run",
                   choices=["run", "intake", "extract", "selftest",
                            "status", "words", "add", "reset"],
                   help="run=alles; intake=Fragen; extract=nur Hash; selftest=Toolchain testen; "
                        "status=Stand; words=Fortschritt/Woerterbuch; add=Woerter ergaenzen; "
                        "reset=zuruecksetzen")
    p.add_argument("rest", nargs="*", help="fuer 'add': die neuen Woerter")
    p.add_argument("--archive", "-f", help="Pfad zur .7z-Datei")
    args = p.parse_args()

    tools = detect_tools()

    # Befehle ohne Tool-/Header-Rauschen:
    if args.command == "add":
        cmd_add(args.rest)
        return

    head("7-Zip Passwort-Recovery  (nur fuer EIGENE Dateien)")
    for name in ("hashcat", "perl"):
        print(f"  {name:12}: {tools.get(name) or c('FEHLT', 'r')}")
    print(f"  {'7z2hashcat':12}: {tools.get('7z2hashcat') or c('fehlt (7z2john als Fallback)','y')}")
    print(f"  {'7z2john':12}: {tools.get('7z2john') or c('fehlt','y')}")

    cfg = load_json(CONFIG_FILE, {})

    if args.command == "reset":
        for f in (CONFIG_FILE, STATE_FILE, HASH_FILE, RESULT_FILE, LEDGER_FILE, TESTED_LOG):
            if f.exists():
                f.unlink()
        for f in WORK.glob("s_*"):
            f.unlink()
        for f in WORK.glob("delta_*"):
            f.unlink()
        for f in WORK.glob("combi_*"):
            f.unlink()
        ok("Zuruckgesetzt (Potfile & words/ bleiben erhalten).")
        return

    if args.command == "words":
        cmd_words(cfg, tools)
        return

    if args.command == "status":
        cmd_status(tools)
        return

    if args.command == "intake":
        cfg = run_intake()
        prepare_inputs(cfg)
        return

    if not tools.get("hashcat") or not tools.get("perl"):
        print_install_hint()
        return

    if args.command == "selftest":
        run_selftest(cfg, tools)
        return

    # extract / run brauchen das Archiv.
    archive = args.archive or cfg.get("archive")
    if not archive:
        archive = ask("Pfad zu deiner .7z-Datei")
    if archive:
        cfg["archive"] = str(Path(archive).expanduser())
        save_json(CONFIG_FILE, cfg)

    if not extract_hash(cfg.get("archive"), tools):
        return

    if args.command == "extract":
        ok("Hash-Extraktion abgeschlossen.")
        return

    # run: ggf. Intake nachholen.
    if "words" not in cfg:
        cfg = run_intake()
        cfg["archive"] = archive
        save_json(CONFIG_FILE, cfg)
    else:
        info("Vorhandene Konfiguration wird genutzt (aendern: python3 recover.py intake).")

    prepare_inputs(cfg)

    try:
        run_plan(cfg, tools)
    except KeyboardInterrupt:
        print()
        warn("Abgebrochen. Fortschritt gespeichert - spaeter einfach 'python3 recover.py run'.")

if __name__ == "__main__":
    main()
