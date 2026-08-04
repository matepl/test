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

Du musst am Algorithmus NICHTS anpassen. Einmal die Fragen beantworten,
dann `python3 recover.py run` starten und laufen lassen. Abbrechen (Strg-C)
und spaeter erneut starten setzt automatisch fort (Sessions + Potfile).

Rechtlicher Hinweis: Nur fuer Dateien verwenden, die dir gehoeren / fuer die du
autorisiert bist. Passwort-Recovery an fremden Daten ist illegal.
"""

import argparse
import json
import os
import re
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
CONFIG_FILE = WORK / "config.json"   # Deine Antworten (persoenlicher Kontext)
STATE_FILE = WORK / "state.json"     # Fortschritt der Angriffe
HASH_FILE = WORK / "hash.txt"        # Extrahierter 7z-Hash (hashcat-Format)
POTFILE = WORK / "cracked.pot"       # Gefundene Passwoerter (hashcat potfile)
RESULT_FILE = WORK / "PASSWORT_GEFUNDEN.txt"

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

# --------------------------------------------------------------------------
# Tool-Erkennung
# --------------------------------------------------------------------------

def detect_tools():
    tools = {
        "hashcat": which("hashcat"),
        "perl": which("perl"),
        "7z2hashcat": which("7z2hashcat.pl", "7z2hashcat"),
        "7z2john": which("7z2john.pl", "7z2john"),
        "john": which("john"),
    }
    return tools

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
    """Extrahiert den 7z-Hash im hashcat-Format nach HASH_FILE."""
    if HASH_FILE.exists() and HASH_FILE.stat().st_size > 0:
        ok(f"Hash bereits extrahiert: {HASH_FILE}")
        return True

    archive = Path(archive).expanduser()
    if not archive.exists():
        err(f"Archiv nicht gefunden: {archive}")
        return False

    WORK.mkdir(parents=True, exist_ok=True)
    raw = ""

    # 7z2hashcat (philsmd) ist ideal: liefert direkt hashcat-Format und geht
    # mit grossen Dateien schlau um (waehlt den kleinsten pruefbaren Stream).
    if tools.get("7z2hashcat") and tools.get("perl"):
        info("Extrahiere Hash mit 7z2hashcat ...")
        try:
            r = subprocess.run([tools["perl"], tools["7z2hashcat"], str(archive)],
                               capture_output=True, text=True, timeout=3600)
            raw = r.stdout.strip()
        except Exception as e:
            warn(f"7z2hashcat fehlgeschlagen: {e}")

    # Fallback: 7z2john (John the Ripper). Gibt "datei:$7z$..." aus -> Prefix weg.
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

    # Nur die $7z$-Zeile(n) behalten und evtl. Datei-Prefix entfernen.
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
# Schritt 2: Interaktive Abfrage des persoenlichen Kontexts
# --------------------------------------------------------------------------

def run_intake():
    head("Persoenlicher Kontext - einmalig beantworten")
    print("""  Je besser diese Angaben, desto hoeher die Erfolgschance. Alles optional -
  einfach Enter druecken zum Ueberspringen. Denk an das Passwort so, wie DU
  frueher Passwoerter gebildet hast (Namen, Jahre, Lieblingswoerter, Muster).
""")
    cfg = {}

    # --- Basis-Woerter -----------------------------------------------------
    words = []
    words += ask_list("Vornamen/Nachnamen (du, Familie, Partner, Kinder, Haustiere)")
    words += ask_list("Wichtige Woerter (Hobbys, Orte, Bands, Vereine, Firmen, Spitznamen)")
    words += ask_list("Weitere moegliche Basiswoerter")
    cfg["words"] = sorted(set(w for w in words if w))

    # --- Zahlen / Daten ----------------------------------------------------
    numbers = ask_list("Wichtige Jahre/Zahlen (Geburtsjahre, PLZ, Lieblingszahlen)")
    dates = ask_list("Wichtige Daten (z.B. 1985, 0304, 03041985, ddmmyyyy)")
    cfg["numbers"] = sorted(set(numbers + dates))

    # --- Sonderzeichen -----------------------------------------------------
    default_syms = "!?.@#$_-*+1"
    syms = ask("Welche Sonderzeichen/Endungen nutzt du typischerweise", default_syms)
    cfg["symbols"] = syms

    # --- Laenge ------------------------------------------------------------
    cfg["min_len"] = int(ask("Vermutete MINIMALE Passwortlaenge", "6") or "6")
    cfg["max_len"] = int(ask("Vermutete MAXIMALE Passwortlaenge", "12") or "12")

    # --- Zeichenklassen-Gewohnheiten --------------------------------------
    cfg["cap_first"] = ask_yesno("Faengt dein Passwort oft mit einem Grossbuchstaben an?", True)
    cfg["has_upper"] = ask_yesno("Nutzt du (auch mitten drin) Grossbuchstaben?", True)
    cfg["has_digits"] = ask_yesno("Enthaelt es meistens Ziffern?", True)
    cfg["digits_at_end"] = ask_yesno("Stehen Ziffern meist am ENDE?", True)
    cfg["has_symbols"] = ask_yesno("Enthaelt es Sonderzeichen?", True)

    # --- Struktur ----------------------------------------------------------
    print("""
  Typische Struktur deines Passworts?
    1) Wort + Ziffern am Ende        (z.B. Berlin2010, hund123)
    2) Wort + Sonderzeichen + Ziffern (z.B. Berlin!2010)
    3) Zwei Woerter kombiniert        (z.B. redhouse, BierGarten)
    4) Nur Ziffern (PIN-artig)        (z.B. 04031985)
    5) Unbekannt / gemischt
""")
    cfg["structure"] = ask("Auswahl 1-5", "5")

    # --- Leetspeak ---------------------------------------------------------
    cfg["leet"] = ask_yesno("Ersetzt du Buchstaben durch Zeichen (a->@, e->3, o->0, s->$)?", False)

    # --- Externe Ressourcen ------------------------------------------------
    print()
    guessed_wl = find_first([str(Path(d) / "rockyou.txt") for d in COMMON_WORDLIST_DIRS])
    cfg["big_wordlist"] = ask("Pfad zu grosser Wortliste (z.B. rockyou.txt), leer=auto",
                              guessed_wl or "")
    cfg["extra_wordlist_dir"] = ask("Ordner mit weiteren Wortlisten (optional)", "")

    # --- Laufzeit / Hardware ----------------------------------------------
    cfg["total_hours"] = float(ask("Gesamtlaufzeit in Stunden bevor gestoppt wird", "8") or "8")
    cfg["use_optimized"] = ask_yesno("Optimierten Kernel nutzen (-O, schneller, Laenge<=~31)?", True)
    cfg["workload"] = ask("hashcat Workload-Profil 1..4 (3=Desktop, 4=nur dediziert)", "3")

    save_json(CONFIG_FILE, cfg)
    ok(f"Konfiguration gespeichert: {CONFIG_FILE}")
    return cfg

# --------------------------------------------------------------------------
# Schritt 3: Wortlisten / Masken aus dem Kontext generieren
# --------------------------------------------------------------------------

def generate_wordlists(cfg):
    """Erzeugt kompakte, persoenliche Basis-Listen. Mutationen macht hashcat via Regeln."""
    WORK.mkdir(parents=True, exist_ok=True)

    base_words = set()
    for w in cfg.get("words", []):
        w = w.strip()
        if not w:
            continue
        base_words.add(w)
        base_words.add(w.lower())
        base_words.add(w.capitalize())
        base_words.add(w.upper())

    base_file = WORK / "personal_base.txt"
    base_file.write_text("\n".join(sorted(base_words)) + "\n", encoding="utf-8")

    num_file = WORK / "personal_numbers.txt"
    nums = set(cfg.get("numbers", []))
    # Ein paar generische, aber wahrscheinliche Zahlen ergaenzen.
    for y in range(1970, 2027):
        nums.add(str(y))
    num_file.write_text("\n".join(sorted(nums)) + "\n", encoding="utf-8")

    ok(f"Persoenliche Basiswoerter: {len(base_words)} -> {base_file.name}")
    ok(f"Zahlen/Jahre: {len(nums)} -> {num_file.name}")

    # Eigene Regel-Datei erzeugen (falls keine grosse Regel vorhanden ist).
    write_builtin_rules(cfg)

    # Masken aus Struktur ableiten.
    masks = build_masks(cfg)
    mask_file = WORK / "masks.hcmask"
    mask_file.write_text("\n".join(masks) + "\n", encoding="utf-8")
    ok(f"Masken generiert: {len(masks)} -> {mask_file.name}")

    return base_file, num_file, mask_file

def write_builtin_rules(cfg):
    """Kompaktes Regelset (haeufige Mutationen) als Fallback zu best64/OneRule."""
    rules = [
        ":",            # unveraendert
        "c", "C", "u", "l", "t",      # Gross/Klein-Varianten
        "$1", "$2", "$3", "$!", "$.", "$@", "$#",   # ein Zeichen anhaengen
        "$1$2$3", "$1$2$3$4",
        "$0$1", "$1$2", "$2$0$1$1",
        "$!$!", "$1$!",
        "^!",            # Zeichen voranstellen
        "so0", "se3", "sa@", "si1", "ss$",          # einzelne Leet-Substitutionen
        "c $1", "c $1$2$3", "c $!",
        "r",             # umgedreht
        "$2$0$1$0", "$2$0$1$5", "$2$0$2$0",         # Jahre
    ]
    # Jahre 19xx/20xx anhaengen
    for y in list(range(1970, 2027)):
        rules.append("$" + "$".join(list(str(y))))
    (WORK / "builtin.rule").write_text("\n".join(rules) + "\n", encoding="utf-8")

def build_masks(cfg):
    """Erzeugt gezielte hashcat-Masken aus den Struktur-Angaben.
    Custom-Charset 1 (?1) = die Sonderzeichen des Users."""
    masks = []
    mn, mx = cfg.get("min_len", 6), cfg.get("max_len", 12)
    structure = cfg.get("structure", "5")

    def letters(n, cap_first):
        if n <= 0:
            return ""
        if cap_first:
            return "?u" + "?l" * (n - 1)
        return "?l" * n

    # 4) Nur Ziffern (PIN) -> vollstaendig sinnvoll, auch laenger.
    if structure == "4" or cfg.get("has_digits"):
        for L in range(max(4, mn), min(mx, 12) + 1):
            masks.append("?d" * L)

    # 1) Wort + Ziffern am Ende
    if structure in ("1", "5"):
        for total in range(mn, mx + 1):
            for d in (2, 3, 4):
                if total - d >= 2:
                    masks.append(letters(total - d, cfg.get("cap_first", True)) + "?d" * d)

    # 2) Wort + Sonderzeichen + Ziffern
    if structure in ("2", "5") and cfg.get("has_symbols"):
        for total in range(mn, mx + 1):
            for d in (2, 4):
                base = total - d - 1
                if base >= 2:
                    masks.append(letters(base, cfg.get("cap_first", True)) + "?1" + "?d" * d)

    # 3) Zwei Woerter (nur Buchstaben) - grob per Masken abgedeckt
    if structure in ("3", "5"):
        for total in range(max(mn, 6), min(mx, 12) + 1):
            masks.append(letters(total, cfg.get("cap_first", True)))

    # Duplikate entfernen, Reihenfolge (kurz -> lang) beibehalten.
    seen, out = set(), []
    for m in masks:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out

# --------------------------------------------------------------------------
# Schritt 4: Angriffs-Engine
# --------------------------------------------------------------------------

def hashcat_base_cmd(cfg, tools):
    cmd = [tools["hashcat"], "-m", HASH_MODE, "-w", str(cfg.get("workload", "3")),
           "--potfile-path", str(POTFILE), "--status", "--status-timer", "30"]
    if cfg.get("use_optimized", True):
        cmd.append("-O")
    # Sonderzeichen als Custom-Charset 1 verfuegbar machen.
    syms = cfg.get("symbols", "").strip()
    if syms:
        cmd += ["-1", syms]
    return cmd

def is_cracked(tools):
    """Autoritative Pruefung ueber das Potfile via --show."""
    if not POTFILE.exists() or POTFILE.stat().st_size == 0:
        return None
    try:
        r = subprocess.run(
            [tools["hashcat"], "-m", HASH_MODE, str(HASH_FILE),
             "--potfile-path", str(POTFILE), "--show"],
            capture_output=True, text=True, timeout=120)
        line = r.stdout.strip()
        if line and ":" in line:
            # Format: <hash>:<passwort>  -> alles nach dem ersten ':' des $7z$-Teils
            # Der Hash selbst enthaelt ':' nicht (er nutzt '$' und '*'), also rsplit reicht.
            pw = line.split(":", 1)[1] if line.count(":") == 1 else line[line.rfind(":") + 1:]
            return pw
    except Exception:
        pass
    return None

def build_attack_plan(cfg, base_file, num_file, mask_file, tools):
    """Definiert die eskalierende Reihenfolge. Jeder Eintrag ist ein dict."""
    plan = []

    big_wl = cfg.get("big_wordlist", "").strip()
    if not big_wl:
        big_wl = find_first([str(Path(d) / "rockyou.txt") for d in COMMON_WORDLIST_DIRS]) or ""

    best64 = find_first([str(Path(d) / "best64.rule") for d in COMMON_RULE_DIRS])
    onerule = find_first(
        [str(Path(d) / n) for d in COMMON_RULE_DIRS
         for n in ("OneRuleToRuleThemAll.rule", "dive.rule", "rockyou-30000.rule")])
    builtin_rule = str(WORK / "builtin.rule")

    # --- Phase 1: Schnelle, gezielte Treffer (Minuten) --------------------
    plan.append(dict(name="P1: Persoenliche Woerter (roh)", kind="wl",
                     wordlist=str(base_file), rule=None, budget=120, resumable=False))
    plan.append(dict(name="P1: Persoenliche Woerter + Basisregeln", kind="wl",
                     wordlist=str(base_file), rule=builtin_rule, budget=300, resumable=False))

    # --- Phase 2: Persoenliche Woerter mit starken Regeln -----------------
    if best64:
        plan.append(dict(name="P2: Persoenlich + best64", kind="wl",
                         wordlist=str(base_file), rule=best64, budget=300, resumable=False))
    if onerule:
        plan.append(dict(name="P2: Persoenlich + grosse Regel", kind="wl",
                         wordlist=str(base_file), rule=onerule, budget=900, resumable=True))

    # --- Phase 3: Hybrid & Kombinationen ----------------------------------
    # Wort + Ziffern (Hybrid -a 6) und Ziffern + Wort (-a 7)
    plan.append(dict(name="P3: Wort + 2-4 Ziffern (Hybrid)", kind="hybrid6",
                     wordlist=str(base_file), mask="?d?d?d?d", budget=600, resumable=True))
    plan.append(dict(name="P3: 2-4 Ziffern + Wort (Hybrid)", kind="hybrid7",
                     wordlist=str(base_file), mask="?d?d?d?d", budget=600, resumable=True))
    if cfg.get("symbols"):
        plan.append(dict(name="P3: Wort + Sonderzeichen (Hybrid)", kind="hybrid6",
                         wordlist=str(base_file), mask="?1", budget=300, resumable=True))
    # Zwei persoenliche Woerter kombiniert (-a 1)
    plan.append(dict(name="P3: Wort + Wort (Kombinator)", kind="combi",
                     left=str(base_file), right=str(base_file), budget=300, resumable=False))
    # Wort + Datum/Jahr (-a 1)
    plan.append(dict(name="P3: Wort + Zahl/Jahr (Kombinator)", kind="combi",
                     left=str(base_file), right=str(num_file), budget=300, resumable=False))

    # --- Phase 4: Grosse Wortliste ----------------------------------------
    if big_wl and Path(big_wl).exists():
        plan.append(dict(name="P4: Grosse Wortliste (roh)", kind="wl",
                         wordlist=big_wl, rule=None, budget=600, resumable=True))
        if best64:
            plan.append(dict(name="P4: Grosse Wortliste + best64", kind="wl",
                             wordlist=big_wl, rule=best64, budget=1800, resumable=True))
        if onerule:
            plan.append(dict(name="P4: Grosse Wortliste + grosse Regel", kind="wl",
                             wordlist=big_wl, rule=onerule, budget=3600, resumable=True))

    # --- Phase 5: Gezielte Masken -----------------------------------------
    plan.append(dict(name="P5: Gezielte Masken (Struktur)", kind="maskfile",
                     maskfile=str(mask_file), budget=3600, resumable=True))

    # --- Phase 6: Begrenzte Brute-Force (letzte Instanz) ------------------
    mn, mx = cfg.get("min_len", 6), min(cfg.get("max_len", 12), 8)
    charset = "?l?d"
    if cfg.get("has_upper"):
        charset = "?u?l?d"
    if cfg.get("has_symbols"):
        charset += "?s"
    plan.append(dict(name=f"P6: Brute-Force {mn}-{mx} ({charset})", kind="increment",
                     charset=charset, mn=mn, mx=mx, budget=99999, resumable=True))

    return plan

def run_attack(atk, cfg, tools):
    """Fuehrt einen einzelnen Angriff aus. Gibt hashcat-Returncode zurueck."""
    base = hashcat_base_cmd(cfg, tools)
    session = "s_" + re.sub(r"[^a-zA-Z0-9]", "_", atk["name"])[:40]
    restore_path = WORK / (session + ".restore")

    # Wenn Angriff schon laeuft/lief und resumable -> fortsetzen.
    if atk.get("resumable") and restore_path.exists():
        cmd = base + ["--session", session, "--restore",
                      "--runtime", str(atk["budget"])]
    else:
        cmd = base + ["--session", session, "--runtime", str(atk["budget"])]
        kind = atk["kind"]
        if kind == "wl":
            cmd += ["-a", "0", str(HASH_FILE), atk["wordlist"]]
            if atk.get("rule"):
                cmd += ["-r", atk["rule"]]
        elif kind == "combi":
            cmd += ["-a", "1", str(HASH_FILE), atk["left"], atk["right"]]
        elif kind == "hybrid6":
            cmd += ["-a", "6", str(HASH_FILE), atk["wordlist"], atk["mask"]]
        elif kind == "hybrid7":
            cmd += ["-a", "7", str(HASH_FILE), atk["mask"], atk["wordlist"]]
        elif kind == "maskfile":
            cmd += ["-a", "3", str(HASH_FILE), atk["maskfile"]]
        elif kind == "increment":
            mask = atk["charset"] * atk["mx"]
            cmd += ["-a", "3", "--increment",
                    "--increment-min", str(atk["mn"]),
                    "--increment-max", str(atk["mx"]),
                    str(HASH_FILE), mask]
        else:
            warn(f"Unbekannter Angriffstyp: {kind}")
            return 1

    info("hashcat: " + " ".join(cmd))
    try:
        # stdout/stderr durchreichen, damit du den Live-Status siehst.
        proc = subprocess.run(cmd, cwd=str(WORK))
        return proc.returncode
    except KeyboardInterrupt:
        raise
    except Exception as e:
        err(f"hashcat-Start fehlgeschlagen: {e}")
        return -1

def run_plan(cfg, tools):
    plan_meta = build_attack_plan(cfg, WORK / "personal_base.txt",
                                  WORK / "personal_numbers.txt",
                                  WORK / "masks.hcmask", tools)
    state = load_json(STATE_FILE, {"exhausted": []})
    exhausted = set(state.get("exhausted", []))

    deadline = time.time() + cfg.get("total_hours", 8) * 3600
    head(f"Angriffsplan startet - Laufzeit-Budget: {cfg.get('total_hours',8)} h")
    info(f"{len(plan_meta)} Angriffsstufen. Stoppt bei Treffer oder Zeitablauf.")
    info("Abbrechen mit Strg-C ist sicher - Fortschritt wird gespeichert.")

    lap = 0
    while time.time() < deadline:
        lap += 1
        made_progress = False
        for atk in plan_meta:
            if atk["name"] in exhausted:
                continue
            if time.time() >= deadline:
                break

            # Treffer schon vorhanden?
            pw = is_cracked(tools)
            if pw:
                return announce_result(pw)

            # Restbudget dieser Runde begrenzen.
            remaining = int(deadline - time.time())
            if remaining <= 5:
                break
            eff_budget = min(atk["budget"], remaining)
            atk_run = dict(atk, budget=eff_budget)

            head(f"[Runde {lap}] {atk['name']}  (bis zu {eff_budget}s)")
            rc = run_attack(atk_run, cfg, tools)
            made_progress = True

            pw = is_cracked(tools)
            if pw:
                return announce_result(pw)

            if rc == 1:
                # Exhausted -> Keyspace komplett durch, nie wieder starten.
                ok(f"Stufe erschoepft: {atk['name']}")
                exhausted.add(atk["name"])
                save_json(STATE_FILE, {"exhausted": sorted(exhausted)})
            elif rc in (2, 3, 4):
                info("Zeitfenster erreicht - Stufe wird spaeter fortgesetzt.")
            elif rc < 0 or rc == 255:
                warn(f"Fehlercode {rc} bei '{atk['name']}' - Stufe wird uebersprungen.")
                exhausted.add(atk["name"])
                save_json(STATE_FILE, {"exhausted": sorted(exhausted)})

        if not made_progress:
            warn("Alle Stufen erschoepft oder uebersprungen. Nichts mehr zu tun.")
            break

    # Zeit abgelaufen ohne Treffer.
    pw = is_cracked(tools)
    if pw:
        return announce_result(pw)

    head("Zeitbudget aufgebraucht - noch kein Treffer")
    print("""  Naechste sinnvolle Schritte:
    * Mehr/bessere persoenliche Woerter ergaenzen:   python3 recover.py intake
    * Groessere Wortliste angeben (rockyou, SecLists) und erneut 'run'
    * Laufzeit erhoehen (Frage 'Gesamtlaufzeit') und erneut 'run'
    * Bereits gelaufene Stufen werden dank Session/Potfile fortgesetzt.
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
# Selbsttest: erzeugt ein Mini-7z mit bekanntem Passwort und knackt es.
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

    # Hash extrahieren in separate Datei.
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

    # Kleine Maske, die Test123! trifft.
    cmd = [tools["hashcat"], "-m", HASH_MODE, "-a", "3", "-w", "3",
           "--potfile-path", str(tp), "-1", "!?.@", str(th),
           "Test?d?d?d?1"]
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
                   choices=["run", "intake", "extract", "selftest", "status", "reset"],
                   help="run=alles; intake=nur Fragen; extract=nur Hash; "
                        "selftest=Toolchain testen; status=Stand; reset=zuruecksetzen")
    p.add_argument("--archive", "-f", help="Pfad zur .7z-Datei")
    args = p.parse_args()

    head("7-Zip Passwort-Recovery  (nur fuer EIGENE Dateien)")
    tools = detect_tools()
    for name in ("hashcat", "perl"):
        print(f"  {name:12}: {tools.get(name) or c('FEHLT', 'r')}")
    print(f"  {'7z2hashcat':12}: {tools.get('7z2hashcat') or c('fehlt (7z2john als Fallback)','y')}")
    print(f"  {'7z2john':12}: {tools.get('7z2john') or c('fehlt','y')}")

    if args.command == "reset":
        for f in (CONFIG_FILE, STATE_FILE, HASH_FILE, RESULT_FILE):
            if f.exists():
                f.unlink()
        for f in WORK.glob("s_*"):
            f.unlink()
        ok("Zuruckgesetzt (Potfile bleibt erhalten). Config/State/Hash geloescht.")
        return

    if not tools.get("hashcat") or not tools.get("perl"):
        print_install_hint()
        if args.command not in ("intake",):
            return

    if args.command == "status":
        cmd_status(tools)
        return

    if args.command == "intake":
        run_intake()
        cfg = load_json(CONFIG_FILE, {})
        generate_wordlists(cfg)
        return

    if args.command == "selftest":
        cfg = load_json(CONFIG_FILE, {})
        run_selftest(cfg, tools)
        return

    # command == extract oder run: Archiv wird gebraucht.
    cfg = load_json(CONFIG_FILE, {})
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

    # command == run: ggf. Intake nachholen.
    if not CONFIG_FILE.exists() or "words" not in cfg:
        run_intake()
        cfg = load_json(CONFIG_FILE, {})
        cfg["archive"] = archive
        save_json(CONFIG_FILE, cfg)
    else:
        info("Vorhandene Konfiguration wird genutzt (aendern mit: python3 recover.py intake).")

    generate_wordlists(cfg)

    try:
        run_plan(cfg, tools)
    except KeyboardInterrupt:
        print()
        warn("Abgebrochen. Fortschritt gespeichert - einfach spaeter 'python3 recover.py run'.")

if __name__ == "__main__":
    main()
