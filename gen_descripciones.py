#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_descripciones.py — Produce las descripciones de campana de Dropi
leyendo la FOTO real de cada producto con un modelo de vision (API
de NVIDIA NIM, compatible OpenAI; llave en la variable de entorno
NVIDIA_API_KEY). El resultado va a data/descripciones_dropi.json
en TEXTO PLANO (la tienda decidio no usar HTML en Dropi), y
gen_dropi.py lo une al Excel de carga masiva (lo que ya exista ahi
NUNCA se pisa: las correcciones de la duena mandan).

Flujo por producto:
  1. Toma los elegibles del Excel Dropi (los que van a publicarse).
  2. Salta los que ya tienen descripcion en el JSON.
  3. Descarga la foto original de POSGold (fulls[0] del catalogo).
  4. Le pide al modelo de vision el copy (molde de la tienda: gancho
     con emoji, valor de venta y caracteristicas; solo hechos de la
     foto o del nombre, sin inventar).
  5. Normaliza el HTML, lo valida y lo guarda incrementalmente.

Uso:
    python gen_descripciones.py                  # lote de 20 (top stock primero)
    python gen_descripciones.py --limit 100      # lote mas grande
    python gen_descripciones.py --sku 7020251238901   # un solo producto
    python gen_descripciones.py --modelo nvidia/nemotron-3.5-super-vl-120b-a12b
    python gen_descripciones.py --pausa 3        # mas lento (limites de la API)
"""

import argparse
import base64
import concurrent.futures
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

import openpyxl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_CATALOGO = os.path.join(BASE_DIR, "catalogo.js")
RUTA_CARGA = os.path.join(BASE_DIR, "DROPI", "carga-masiva-productos-con-variables.xlsx")
RUTA_JSON = os.path.join(BASE_DIR, "data", "descripciones_dropi.json")
API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODELO_DEFECTO = "nvidia/nemotron-3.5-super-vl-120b-a12b"
MAX_TOKENS = 4000
MIN_CARACTERES, MAX_CARACTERES = 250, 1500
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

PROMPT = """Escribe la descripción de un producto para una tienda online de juguetes en Cali, Colombia. La publicación la leen revendedores que necesitan conocer bien el producto antes de comprarlo para revenderlo.

Producto: {nombre} (categoría: {categoria}). La imagen adjunta es la foto real del producto.

Requisitos:
- Español de Colombia con tildes, tuteo, tono cálido y comercial.
- Empieza con un emoji que combine con el producto.
- TEXTO PLANO: sin HTML, sin markdown (nada de ** ni #). Usa saltos de línea para separar párrafos y el carácter "•" para las viñetas.
- Estructura: (1) párrafo gancho con emoji y el nombre del producto; (2) párrafo corto de uso y público ideal; (3) línea "Características:" y debajo 4 o 5 viñetas, cada una en su propia línea empezando con "• ".
- CRÍTICO — no inventes: NO incluyas medidas, pesos, capacidades, edades recomendadas, número de jugadores ni materiales SALVO que se vean claramente en la foto o estén escritos en el nombre del producto. Describe lo que ves (colores, formas, presentación, empaque) y el uso del juguete.
- Sin datos de contacto, sin nombre de tienda, sin notas al final.
- Entre 400 y 700 caracteres. Responde SOLO con el texto."""


def log(msg):
    print(msg, flush=True)


def leer_llave():
    key = (os.environ.get("NVIDIA_API_KEY")
           or os.environ.get("VISION_API_KEY") or "")
    if not key:
        log("ERROR: falta la llave. Define la variable de entorno NVIDIA_API_KEY "
            "(o VISION_API_KEY). Nunca va al repo.")
        sys.exit(1)
    return key


def parsear_catalogo():
    h = open(RUTA_CATALOGO, encoding="utf-8").read()
    ini = h.find("const CATALOGO_BASE")
    fin = h.find("];", ini)
    if ini < 0 or fin < 0:
        log("ERROR: catalogo.js sin CATALOGO_BASE")
        sys.exit(1)
    origen = (re.search(r'url:"(https://[^/"]+)', h[ini:fin]) or [None, ""])[1]
    prod = {}
    for trozo in h[ini:fin].split('{id:"')[1:]:
        def c(p):
            m = re.search(p, trozo)
            return m.group(1) if m else ""
        sku = c(r'sku:"(.*?)"').strip()
        if not sku:
            continue
        mf = re.search(r'fulls:\["(.*?)"', trozo)
        mi = re.search(r'image:"(.*?)"', trozo)
        mc = re.search(r'category:"(.*?)"', trozo)
        prod[sku] = {
            "nombre": c(r'name:"(.*?)"'),
            "categoria": mc.group(1) if mc else "",
            "foto": (mf.group(1) if mf else "") or (mi.group(1) if mi else ""),
            "es_full": bool(mf),
            "origen": origen or "https://saldosjugueteriataiwan.com",
        }
    if len(prod) < 100:
        log(f"ERROR: catalogo parseo muy poco ({len(prod)})")
        sys.exit(1)
    return prod


def leer_excel():
    if not os.path.exists(RUTA_CARGA):
        log(f"ERROR: no existe {RUTA_CARGA}; corre gen_dropi.py primero")
        sys.exit(1)
    wb = openpyxl.load_workbook(RUTA_CARGA, read_only=True)
    ws = wb[wb.sheetnames[0]]
    filas = ws.iter_rows(values_only=True)
    next(filas)
    datos = []
    for f in filas:
        sku = str(f[7] or "").strip()
        if sku:
            datos.append({"sku": sku, "nombre": str(f[0] or "").strip(),
                          "categoria": str(f[4] or "").strip(), "stock": int(f[9] or 0)})
    wb.close()
    if not datos:
        log("ERROR: el Excel Dropi no tiene filas legibles")
        sys.exit(1)
    return datos


def cargar_json():
    if os.path.exists(RUTA_JSON):
        try:
            with open(RUTA_JSON, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            log(f"ERROR: {RUTA_JSON} corrupto ({e}); revisalo antes de continuar")
            sys.exit(1)
    return {}


def guardar_json(descripciones):
    os.makedirs(os.path.dirname(RUTA_JSON), exist_ok=True)
    with open(RUTA_JSON, "w", encoding="utf-8") as f:
        json.dump(descripciones, f, ensure_ascii=False, indent=2)
        f.write("\n")


def descargar_foto(url, sku):
    rq = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(rq, timeout=45, context=SSL_CTX) as r:
        return r.read()


def limpiar_texto(texto):
    t = texto.strip()
    t = re.sub(r"^```(?:html)?\s*|\s*```$", "", t)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</p>\s*<p[^>]*>", "\n\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\*\*\s*(.+?)\s*\*\*", r"\1", t)
    t = re.sub(r"^#+\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    lineas = [ln.strip() for ln in t.split("\n")]
    return "\n".join(lineas).strip()


def pedir_copy(llave, modelo, nombre, categoria, foto_bytes, intentos=3):
    b64 = base64.b64encode(foto_bytes).decode()
    cuerpo = {
        "model": modelo,
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + b64}},
            {"type": "text",
             "text": PROMPT.format(nombre=nombre, categoria=categoria)},
        ]}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.3,
    }
    for intento in range(intentos):
        rq = urllib.request.Request(
            API_URL, data=json.dumps(cuerpo).encode(),
            headers={"Authorization": "Bearer " + llave,
                     "Content-Type": "application/json",
                     "Accept": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(rq, timeout=300, context=SSL_CTX) as r:
                resp = json.loads(r.read().decode())
            contenido = (resp["choices"][0]["message"].get("content") or "").strip()
            if not contenido:
                raise ValueError("respuesta sin contenido")
            return limpiar_texto(contenido)
        except urllib.error.HTTPError as e:
            if e.code == 429 and intento < intentos - 1:
                time.sleep(15 * (intento + 1))
                continue
            raise
        except Exception:
            if intento < intentos - 1:
                time.sleep(5 * (intento + 1))
                continue
            raise
    return None


def validar(texto, nombre):
    if not texto or not (MIN_CARACTERES <= len(texto) <= MAX_CARACTERES):
        return False
    if "<" in texto or "**" in texto:
        return False
    if "•" not in texto:
        return False
    prohibidos = ["3106988465", "jugueteria taiwan", "juguetería taiwan"]
    if any(p in texto.lower() for p in prohibidos):
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="Genera copys de campana Dropi con vision")
    ap.add_argument("--limit", type=int, default=20, help="cuantos productos en esta corrida")
    ap.add_argument("--sku", help="procesar solo este SKU")
    ap.add_argument("--categoria", help="filtrar por categoria del Excel")
    ap.add_argument("--modelo", default=MODELO_DEFECTO)
    ap.add_argument("--pausa", type=float, default=1.0, help="segundos entre llamadas")
    args = ap.parse_args()

    llave = leer_llave()
    catalogo = parsear_catalogo()
    excel = leer_excel()
    descripciones = cargar_json()

    pendientes = []
    for fila in excel:
        s = fila["sku"]
        if s in descripciones:
            continue
        if args.sku and s != args.sku:
            continue
        if args.categoria and fila["categoria"].upper() != args.categoria.upper():
            continue
        p = catalogo.get(s)
        if not p or not p["foto"]:
            continue
        pendientes.append((fila, p))
    pendientes.sort(key=lambda x: -x[0]["stock"])

    if not pendientes:
        log("Nada pendiente: todos los elegibles ya tienen descripcion "
            "(o el filtro no encontro productos).")
        return

    lote = pendientes[:args.limit] if not args.sku else pendientes
    log(f"pendientes: {len(pendientes)} | en esta corrida: {len(lote)} "
        f"| modelo: {args.modelo}")
    log(f"orden: mayor stock primero "
        f"(rango de stock: {lote[-1][0]['stock']} - {lote[0][0]['stock']})" if len(lote) > 1 else "")

    exitos, fallos = 0, 0
    for i, (fila, p) in enumerate(lote, 1):
        url = p["foto"] if p["es_full"] else f"{p['origen']}/{p['foto']}"
        t0 = time.time()
        try:
            foto = descargar_foto(url, fila["sku"])
            texto = pedir_copy(llave, args.modelo, fila["nombre"],
                               fila["categoria"], foto)
            if texto and validar(texto, fila["nombre"]):
                descripciones[fila["sku"]] = texto
                guardar_json(descripciones)
                exitos += 1
                log(f"  [{i}/{len(lote)}] OK  {fila['sku']:16} "
                    f"{fila['nombre'][:34]:34} ({len(texto)} chars, "
                    f"{time.time()-t0:.0f}s)")
            else:
                fallos += 1
                log(f"  [{i}/{len(lote)}] DESCARTADO {fila['sku']} "
                    f"{fila['nombre'][:34]} (respuesta no valida)")
        except KeyboardInterrupt:
            log(f"\nInterrumpido. Progreso guardado: {exitos} copys.")
            return
        except Exception as e:
            fallos += 1
            log(f"  [{i}/{len(lote)}] ERROR {fila['sku']} {str(e)[:100]}")
        time.sleep(args.pausa)

    log(f"\nListo: +{exitos} copys nuevos ({fallos} fallos). "
        f"Total en el JSON: {len(descripciones)}.")
    log("Ahora corre gen_dropi.py para volcarlos al Excel de carga masiva.")


if __name__ == "__main__":
    main()
