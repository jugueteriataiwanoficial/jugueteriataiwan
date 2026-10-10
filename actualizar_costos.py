#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
actualizar_costos.py — Cifra el ultimo Excel de costos exportado de POSGold
y lo deja listo para commitear al repo (costos.posgold.enc).

El repo es publico: el Excel con costos NUNCA se sube en claro. Se cifra
con AES-256-CBC + PBKDF2 (openssl), y la llave vive en dos lugares:
  - .env local (COSTOS_KEY)
  - secret COSTOS_KEY del repo de GitHub (la lee el workflow)

El workflow descifra en el runner hacia PRODUCTOS_cloud.xlsx y
sync_posgold.py lo encuentra solo (glob PRODUCTOS*.xlsx).

Uso:
  python actualizar_costos.py                  (toma el PRODUCTOS*.xlsx mas reciente)
  python actualizar_costos.py --costos RUTA    (un Excel especifico)
  python actualizar_costos.py --verificar      (descifra y compara hashes)

Despues de correr: git add costos.posgold.enc && git commit && git push
(o simplemente corre con --commit para que lo haga solo).
"""

import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
SALIDA = os.path.join(BASE_DIR, "costos.posgold.enc")
OPENSSL_ARGS = ["enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt"]


def log(msg):
    print(msg, flush=True)


def cargar_env():
    env = {}
    with open(ENV_FILE, encoding="utf-8-sig") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def encontrar_openssl():
    exe = shutil.which("openssl")
    if exe:
        return exe
    # Git para Windows trae openssl en usr/bin
    git = shutil.which("git")
    if git:
        for cand in (
            os.path.join(os.path.dirname(os.path.dirname(git)), "usr", "bin", "openssl.exe"),
            os.path.join(os.path.dirname(os.path.dirname(git)), "mingw64", "bin", "openssl.exe"),
        ):
            if os.path.exists(cand):
                return cand
    log("ERROR: openssl no encontrado. Instala Git para Windows o openssl.")
    sys.exit(1)


def correr_openssl(openssl, args, llave, salida=None):
    cmd = [openssl] + args + ["-pass", "env:COSTOS_KEY"]
    if salida:
        cmd += ["-out", salida]
    entorno = dict(os.environ, COSTOS_KEY=llave)
    r = subprocess.run(cmd, capture_output=True, text=True, env=entorno)
    if r.returncode != 0:
        log("ERROR openssl: " + (r.stderr or r.stdout).strip()[:300])
        sys.exit(1)


def sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def adelgazar(ruta_excel):
    """Extrae solo productoid + costos a un xlsx temporal: el blob cifrado
    queda pequeno (~100 KB) y sin datos extra del inventario."""
    try:
        import openpyxl
    except ImportError:
        log("ERROR: falta openpyxl (pip install openpyxl).")
        sys.exit(1)
    wb = openpyxl.load_workbook(ruta_excel, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    filas = ws.iter_rows(values_only=True)
    enc = list(next(filas))
    idx = {k: enc.index(k) for k in ("productoid", "costo", "costopromedio", "costoultimo")}
    tmp = os.path.join(os.environ.get("TEMP", BASE_DIR), "costos_adelgazado.xlsx")
    wbs = openpyxl.Workbook()
    wss = wbs.active
    wss.append(["productoid", "costo", "costopromedio", "costoultimo"])
    n = 0
    for f in filas:
        try:
            pid = int(f[idx["productoid"]])
        except (TypeError, ValueError):
            continue
        wss.append([pid, f[idx["costo"]], f[idx["costopromedio"]], f[idx["costoultimo"]]])
        n += 1
    wb.close()
    wbs.save(tmp)
    log(f"   adelgazado: {n:,} productos, solo columnas de costo")
    return tmp


def main():
    ap = argparse.ArgumentParser(description="Cifra los costos de POSGold para la nube")
    ap.add_argument("--costos", help="ruta del Excel (default: PRODUCTOS*.xlsx mas reciente)")
    ap.add_argument("--verificar", action="store_true", help="descifra y compara hashes")
    ap.add_argument("--commit", action="store_true", help="git add + commit + push al terminar")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    env = cargar_env()
    llave = env.get("COSTOS_KEY")
    if not llave:
        log("ERROR: falta COSTOS_KEY en .env")
        sys.exit(1)
    openssl = encontrar_openssl()

    if args.costos:
        excel = args.costos
        if not os.path.exists(excel):
            log("ERROR: no existe: " + excel)
            sys.exit(1)
    else:
        candidatos = sorted(glob.glob(os.path.join(BASE_DIR, "PRODUCTOS*.xlsx")),
                             key=os.path.getmtime, reverse=True)
        if not candidatos:
            log("ERROR: no hay PRODUCTOS*.xlsx. Exporta el inventario desde POSGold.")
            sys.exit(1)
        excel = candidatos[0]

    # 1. adelgazar (solo productoid + costos) y cifrar
    log(f"Preparando: {os.path.basename(excel)}")
    excel_delgado = adelgazar(excel)
    log(f"Cifrando   : -> {os.path.basename(SALIDA)} (AES-256-CBC, PBKDF2 200k)")
    correr_openssl(openssl, OPENSSL_ARGS + ["-in", excel_delgado], llave, salida=SALIDA)
    log(f"   listo: {os.path.getsize(SALIDA):,} bytes")

    # 2. verificar roundtrip (descifrar a temporal y comparar hash)
    temporal = SALIDA + ".check"
    correr_openssl(openssl, ["enc", "-d"] + OPENSSL_ARGS[1:] + ["-in", SALIDA], llave, salida=temporal)
    ok = sha256(excel_delgado) == sha256(temporal)
    os.remove(excel_delgado)
    os.remove(temporal)
    if not ok:
        log("ERROR: el roundtrip de verificacion no coincide. NO commitear.")
        sys.exit(1)
    log("   verificado: descifrado == original (SHA-256 igual)")

    if args.commit:
        for cmd in (
            ["git", "add", "costos.posgold.enc"],
            ["git", "commit", "-m", "costos: actualiza Excel de POSGold cifrado"],
            ["git", "push"],
        ):
            r = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True)
            if r.returncode != 0 and "commit" not in cmd[1]:
                log("AVISO git: " + (r.stderr or r.stdout).strip()[:200])
        log("   commiteado y publicado")

    log("\nEl workflow de GitHub descifra este archivo en cada corrida con")
    log("el secret COSTOS_KEY. Actualizalo si cambias la llave.")


if __name__ == "__main__":
    main()
