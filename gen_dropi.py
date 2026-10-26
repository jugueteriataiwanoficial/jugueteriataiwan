#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_dropi.py — Actualiza la plantilla de carga masiva de DROPI
(DROPI/carga-masiva-productos-con-variables.xlsx) espejando el
catalogo publicado (catalogo.js), con las MISMAS reglas duras de
la tienda web:

  1. SOLO productos del catalogo: catalogo.js ya filtró activo +
     existencias + foto + categoria + costo + rentabilidad. Un
     SKU que salió del catalogo sale del archivo (igual que la
     vitrina en la proxima corrida del cron).
  2. PRECIO = Precio 5 de POSGold (IVA incluido), consultado EN
     VIVO con GetProduct_V6 (mismo origen que usa el cron). Sin
     Precio 5 el producto no se carga (regla "sin precio").
  3. PRECIO SUGERIDO solo con tachado honesto: el Precio 2 real
     (el de la vitrina) unicamente si queda POR ENCIMA del
     Precio 5; si no, va vacio.
  4. Sin imagen no se carga: se conserva la URL viva de la fila
     (curación previa), se rellena la faltante con el original
     JPEG de POSGold (fulls[0], el que la vitrina usa en el
     lightbox) o la miniatura del repo como respaldo; las URLs
     muertas se reemplazan.
  5. Nivel de existencias: solo se enlistan productos con 50
     unidades o mas (STOCK < 50 no se carga; umbral ajustable
     con --min-stock). El STOCK queda con las existencias del
     catalogo.
   6. Conciliacion con lo ya publicado en Dropi: los SKUs (y
     nombres normalizados) de los exports DROPI/products_*.xlsx
     y DROPI/variaciones*.xlsx no se enlistan — evita duplicar
     productos que ya viven en la tienda Dropi.
   7. Rentabilidad al Precio 5: utilidad = Precio 5 sin IVA −
     costopromedio (Excel PRODUCTOS*.xlsx, unido por productoid,
     igual que el cron). En o bajo el costo no se enlista; sin
     costo en el Excel tampoco (fail-safe, como el catalogo).
   8. Descripciones de campana para dropshippers: si el SKU
     existe en data/descripciones_dropi.json ese texto manda
     sobre la curacion. TODO va en TEXTO PLANO (por decision de
     la tienda: Dropi puede no renderizar HTML), con viñetas
     "•" y saltos de linea; si algo llega en HTML se convierte
     automaticamente. TODAS las filas cierran con el pie de la
     tienda (importadores directos, horario de despachos y
     WhatsApp de atencion a dropshippers).
   9. Curación DROPI preservada por SKU: PRIVADO, CATEGORIA,
     DESCRIPCION e IMAGENES que sirven. Los nuevos heredan
     categoria del export 07-10 (ENRIQUECIDO) o del mapeo de
     categorias web.

Uso:
    python gen_dropi.py              # actualiza el Excel (escribe)
    python gen_dropi.py --dry-run    # solo diagnostico, no escribe
    (requiere .env con las credenciales POSGold, como el cron)
"""

import argparse
import concurrent.futures
import glob
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import openpyxl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
RUTA_CATALOGO = os.path.join(BASE_DIR, "catalogo.js")
RUTA_CARGA = os.path.join(BASE_DIR, "DROPI", "carga-masiva-productos-con-variables.xlsx")
PLANTILLAS_ENRIQUECIDO = sorted(
    glob.glob(os.path.join(BASE_DIR, "DROPI_carga_masiva*ENRIQUECIDO*.xlsx")))
# Exports de Dropi: lo que ya vive en la tienda Dropi no se vuelve
# a enlistar. Se toma el mas reciente de cada patron.
EXPORTES_DROPI = [
    ("products_*.xlsx", ("SKU", "VARIATION SKU"), "NOMBRE"),
    ("variaciones*.xlsx", ("SKU",), "NOMBRE PRODUCTO"),
]
# Descripciones de campana por SKU (curadas: IA + ediciones de la
# duena). Manda sobre cualquier otro texto de descripcion.
RUTA_DESCRIPCIONES = os.path.join(BASE_DIR, "data", "descripciones_dropi.json")
# El texto va PLANO en Dropi (por decision de la tienda): este
# separador corta el pie de la tienda al releer el Excel (evita
# que se duplique en corridas sucesivas).
SEPARADOR_PIE = "———————————————————————"
PIE_DROPI = (
    "🏢 JUGUETERÍA TAIWAN — IMPORTADORES DIRECTOS\n"
    "Somos importadores directos: te garantizamos el mejor precio del "
    "mercado y stock constante para tus campañas.\n"
    "🚚 Despachos rápidos:\n"
    "• Pedidos confirmados antes de las 1:00 p. m. se despachan el mismo día.\n"
    "• Pedidos confirmados después de las 1:00 p. m. se despachan al día siguiente.\n"
    "💬 Atención personalizada para dropshippers: si tienes dudas, necesitas "
    "apoyo con garantías o material promocional adicional, escríbenos al "
    "WhatsApp de atención dropshippers: 3106988465."
)


def html_a_texto(t):
    """Dropi recibe texto plano: cualquier HTML que llegue (copys
    viejos, curacion heredada) se convierte a texto con saltos."""
    t = str(t)
    if "<" not in t:
        return t.strip()
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</p>\s*<p[^>]*>", "\n\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("&nbsp;", " ")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
ID_BODEGA = 23457
MIN_PRODUCTOS = 100
STOCK_MINIMO = 50
UA = {"User-Agent": "Mozilla/5.0 (JugueteriaTaiwan gen_dropi)"}

ENCABEZADOS = [
    "PRODUCTO (NOMBRE)", "PRECIO", "PRECIO SUGERIDO", "DESCRIPCION",
    "CATEGORIA", "PRIVADO (SI/NO)", "TIPO DE PRODUCTO", "SKU", "SKU PADRE",
    "STOCK", "ID BODEGA", "ATRIBUTO 1", "ATRIBUTO 2", "ATRIBUTO 3",
    "COMBINACIONES", "IMAGENES", "PESO (KG)", "LONGITUD (CM)", "ANCHO (CM)",
    "ALTO (CM)",
]

# Categoria web (catalogo.js) -> categoria del formato DROPI.
# Se usa solo para SKUs sin historial en ningun archivo DROPI previo.
CATEGORIA_WEB_A_DROPI = {
    "jugueteria": "JUGUETERIA",
    "juego-de-mesa": "JUEGO DE MESA",
    "piñateria": "PIÑATERIA",
    "colecciones": "COLECCION",
    "armatodo": "ARMATODO",
    "didactico": "DIDACTICO",
    "cacharro": "CACHARRO",
    "slime": "SLIME",
    "deporte": "DEPORTIVO",
    "peluches": "BOLSO Y PELUCHES",
    "bebe": "BEBES",
    "termos": "TERMOS",
    "burbujeros": "BURBUJEROS",
    "escolar": "ESCOLAR",
    "animales": "ANIMALES DE CAUCHO EN BOLSA",
    "cubo-rubik": "CUBO RUBIK",
    "hogar": "MADEROTERAPIA",
}


def log(msg):
    print(msg, flush=True)


def abortar(msg):
    log("ABORTADO: " + msg + " No se toca el Excel.")
    sys.exit(1)


# ------------------------------------------------------------------
# catalogo.js: la fuente de verdad (Precio 2, stock, fotos, elegibilidad)
# ------------------------------------------------------------------

def parsear_catalogo():
    try:
        h = open(RUTA_CATALOGO, encoding="utf-8").read()
    except Exception as e:
        abortar(f"no se pudo leer catalogo.js: {e}")
    ini = h.find("const CATALOGO_BASE")
    fin = h.find("];", ini)
    if ini < 0 or fin < 0:
        abortar("catalogo.js no tiene CATALOGO_BASE (formato desconocido)")
    bloque = h[ini:fin]

    m = re.search(r'url:"(https://[^/"]+)', bloque)
    origen_web = m.group(1) if m else "https://saldosjugueteriataiwan.com"

    productos, duplicados = {}, 0
    for trozo in bloque.split('{id:"')[1:]:
        def campo(pat):
            mm = re.search(pat, trozo)
            return mm.group(1) if mm else ""

        sku = campo(r'sku:"(.*?)"').strip()
        if not sku:
            continue
        mf = re.search(r'fulls:\[(.*?)\]', trozo)
        fulls = re.findall(r'"(.*?)"', mf.group(1)) if mf else []
        mi = re.search(r'image:"(.*?)"', trozo)
        precio = int(campo(r"price:(\d+)") or 0)
        stock = int(campo(r"stock:(\d+)") or 0)
        comp = int(campo(r"comparePrice:(\d+)") or 0)
        p = {
            "nombre": campo(r'name:"(.*?)"').strip(),
            "categoria_web": campo(r'category:"(.*?)"'),
            "precio": precio,
            "compare_price": comp,
            "stock": stock,
            "thumb": (mi.group(1) if mi else "").strip(),
            "fulls": [u for u in fulls if u],
        }
        if sku in productos:
            duplicados += 1
            if stock > productos[sku]["stock"]:
                productos[sku] = p
        else:
            productos[sku] = p

    if len(productos) < MIN_PRODUCTOS:
        abortar(f"catalogo.js parseo solo {len(productos)} productos "
                f"(minimo {MIN_PRODUCTOS}); algo se rompio")
    if duplicados:
        log(f"  aviso: {duplicados} SKUs duplicados en catalogo.js "
            f"(se conserva el de mas existencias)")
    return productos, origen_web


# ------------------------------------------------------------------
# POSGold en vivo: Precio 5 (y Precio 2 real para el tachado honesto)
# salen de GetProduct_V6, el mismo origen que usa el cron del catalogo
# ------------------------------------------------------------------

def cargar_env(ruta=ENV_FILE):
    if not os.path.exists(ruta):
        abortar("falta el archivo .env (credenciales POSGold, ver sync_posgold.py)")
    env = {}
    with open(ruta, encoding="utf-8-sig") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def http_texto(url, headers=None, timeout=90, metodo="GET", datos=None, reintentos=3):
    hs = {"User-Agent": "Mozilla/5.0"}
    if headers:
        hs.update(headers)
    ultimo_error = None
    for intento in range(reintentos):
        req = urllib.request.Request(url, headers=hs, method=metodo,
                                     data=datos.encode("utf-8") if datos else None)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                return r.status, r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code >= 500 and intento < reintentos - 1:
                time.sleep(2 * (intento + 1))
                continue
            cuerpo = ""
            try:
                cuerpo = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            abortar(f"POSGold respondio {e.code}: {cuerpo}")
        except Exception as e:
            ultimo_error = e
            if intento < reintentos - 1:
                time.sleep(2 * (intento + 1))
                continue
    abortar("no se pudo hablar con POSGold: " + str(ultimo_error))


def login_jwt(env):
    qs = urllib.parse.urlencode({"user": env["POSGOLD_TOKEN_USER"],
                                 "Password": env["POSGOLD_TOKEN_PASS"]})
    url = f"{env['POSGOLD_BASE']}/apiGold/Login_Api/LoginJWTToken?{qs}"
    codigo, texto = http_texto(url, metodo="POST", datos="{}", timeout=60)
    if codigo != 200:
        abortar(f"login JWT respondio {codigo}: {texto[:200]}")
    data = json.loads(texto)
    if not data.get("Status") or not data.get("Token"):
        abortar("POSGold no entrego token: " + str(data)[:200])
    return data["Token"]


def consultar_precios(env, jwt):
    """Un pase por GetProduct_V6: {sku: {p5, p2, pid, iva}} con IVA
    incluido (misma convencion con la que el catalogo publica el
    Precio 2). El pid (Productoid) une con el Excel de costos."""
    precios, pagina = {}, 1
    while True:
        params = {
            "empresaid": env.get("POSGOLD_EMPRESAID", "1"),
            "usuarioid": env.get("POSGOLD_USUARIOID", "1"),
            "codigo": "", "descripcion": "", "categoriaid": "", "grupoid": "",
            "inicial": "0", "items_x_pagina": "50000", "pagina": str(pagina),
            "subgrupoid": "", "precio_min": "", "precio_max": "",
            "bodegaid": env.get("POSGOLD_BODEGAID", "2"),
            "orden": "", "api": "1", "api2": "1", "nombreFull": "",
            "ult_mov": "2000-01-01", "activo": "",
        }
        url = (f"{env['POSGOLD_BASE']}/apiGold/ProductoApi/GetProduct_V6?"
               + urllib.parse.urlencode(params))
        codigo, texto = http_texto(url, headers={"Authorization": "Bearer " + jwt},
                                  timeout=180)
        if codigo != 200:
            abortar(f"consulta de productos respondio {codigo}")
        data = json.loads(texto)
        if not data.get("Status"):
            abortar("POSGold rechazo la consulta: " + str(data.get("Msj"))[:200])
        lote = data.get("Datos") or []
        for it in lote:
            c = str(it.get("Codigo") or it.get("Producto_cod") or "").strip().strip("%").strip()
            if not c or c in precios:
                continue
            try:
                pid = int(it.get("Productoid") or 0)
            except (TypeError, ValueError):
                pid = 0
            p5 = float(it.get("Precio5") or 0)
            p2 = float(it.get("Precio2") or 0)
            precios[c] = {"p5": int(round(p5)) if p5 > 0 else 0,
                          "p2": int(round(p2)) if p2 > 0 else 0,
                          "pid": pid,
                          "iva": float(it.get("ProductoImpuestoPorcentaje") or 0)}
        log(f"      pagina {pagina}: +{len(lote)} productos de POSGold")
        total = data.get("Conteo", len(precios))
        if len(precios) >= total or not lote:
            break
        pagina += 1
    return precios


# ------------------------------------------------------------------
# Costos del Excel PRODUCTOS*.xlsx (el mismo contrato de formato
# del cron: encabezados en minuscula, unido por productoid). El
# costo no viene en la API de POSGold, solo en este export.
# ------------------------------------------------------------------

def encontrar_excel_costos():
    candidatos = sorted(glob.glob(os.path.join(BASE_DIR, "PRODUCTOS*.xlsx")),
                        key=os.path.getmtime, reverse=True)
    return candidatos[0] if candidatos else None


def cargar_costos(ruta_excel, campo="costopromedio"):
    wb = openpyxl.load_workbook(ruta_excel, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    filas = ws.iter_rows(values_only=True)
    enc = [str(c or "").strip().lower() for c in next(filas)]
    if "productoid" not in enc or campo not in enc:
        abortar(f"el Excel de costos no trae las columnas del contrato "
                f"('productoid' / '{campo}'); export rechazado")
    i_pid, i_c = enc.index("productoid"), enc.index(campo)
    costos = {}
    for f in filas:
        try:
            pid = int(f[i_pid])
        except (TypeError, ValueError):
            continue
        try:
            val = float(f[i_c])
        except (TypeError, ValueError):
            val = None
        if pid and val and val > 0:
            costos[pid] = val
    wb.close()
    return costos


def utilidad_precio5(pr, costos):
    """Utilidad por unidad al Precio 5 (sin IVA); None si no hay costo."""
    if not pr or not pr["pid"]:
        return None
    costo = costos.get(pr["pid"])
    if costo is None:
        return None
    base = pr["p5"] / (1 + pr["iva"] / 100) if pr["iva"] > 0 else pr["p5"]
    return base - costo


# ------------------------------------------------------------------
# Archivos DROPI: curacion previa (PRIVADO, CATEGORIA, DESCRIPCION,
# IMAGENES) leida por NOMBRE de columna, no por posicion
# ------------------------------------------------------------------

def leer_carga(ruta):
    try:
        wb = openpyxl.load_workbook(ruta, read_only=True)
        ws = wb[wb.sheetnames[0]]
        filas = ws.iter_rows(values_only=True)
        enc = [str(c or "").strip().upper() for c in next(filas)]
        datos = {}
        for f in filas:
            fila = dict(zip(enc, f))
            sku = str(fila.get("SKU") or "").strip()
            if sku:
                datos[sku] = {
                    "nombre": fila.get("PRODUCTO (NOMBRE)"),
                    "precio": fila.get("PRECIO"),
                    "sugerido": fila.get("PRECIO SUGERIDO"),
                    "descripcion": str(fila.get("DESCRIPCION") or "")
                        .split("<hr")[0]
                        .split(SEPARADOR_PIE)[0].strip(),
                    "categoria": fila.get("CATEGORIA"),
                    "privado": fila.get("PRIVADO (SI/NO)"),
                    "stock": fila.get("STOCK"),
                    "imagenes": str(fila.get("IMAGENES") or "").strip(),
                }
        wb.close()
        return datos
    except FileNotFoundError:
        return {}


# ------------------------------------------------------------------
# Conciliacion con Dropi: lo que ya esta publicado alli (exports
# products_*.xlsx / variaciones*.xlsx) no se enlista otra vez.
# El SKU manda; el nombre normalizado cubre los exportados sin SKU.
# ------------------------------------------------------------------

def normalizar_sku(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().upper()


def normalizar_nombre(v):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v).strip().upper())


def cargar_descripciones():
    """Copys de campana por SKU (data/descripciones_dropi.json).
    Lo que la duena o el pipeline de vision escriban ahi manda
    sobre cualquier otra fuente de descripcion."""
    if not os.path.exists(RUTA_DESCRIPCIONES):
        return {}
    try:
        with open(RUTA_DESCRIPCIONES, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k).strip(): str(v).strip()
                    for k, v in data.items() if str(k).strip() and str(v).strip()}
    except Exception as e:
        log(f"      aviso: no se pudo leer {RUTA_DESCRIPCIONES}: {e}")
    return {}


def leer_publicados_dropi(carpeta=os.path.join(BASE_DIR, "DROPI")):
    skus, nombres, fuentes = set(), set(), []
    for patron, cols_sku, col_nombre in EXPORTES_DROPI:
        candidatos = sorted(glob.glob(os.path.join(carpeta, patron)))
        if not candidatos:
            continue
        ruta = candidatos[-1]  # el mas reciente
        try:
            wb = openpyxl.load_workbook(ruta, read_only=True)
            ws = wb[wb.sheetnames[0]]
            filas = ws.iter_rows(values_only=True)
            enc = [str(c or "").strip().upper() for c in next(filas)]
            idx = {c: i for i, c in enumerate(enc)}
            n = 0
            for f in filas:
                for col in cols_sku:
                    if col in idx:
                        s = normalizar_sku(f[idx[col]])
                        if s:
                            skus.add(s)
                if col_nombre in idx:
                    nom = normalizar_nombre(f[idx[col_nombre]])
                    if nom:
                        nombres.add(nom)
                n += 1
            wb.close()
            fuentes.append(os.path.basename(ruta))
            log(f"      {os.path.basename(ruta)}: {n} productos publicados en Dropi")
        except Exception as e:
            log(f"      aviso: no se pudo leer {patron}: {e}")
    return skus, nombres, fuentes


# ------------------------------------------------------------------
# Verificacion de URLs de imagen (sin imagen viva = sin imagen)
# ------------------------------------------------------------------

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def url_viva(url, intentos=2):
    for _ in range(intentos):
        try:
            req = urllib.request.Request(url, method="HEAD", headers=UA)
            with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
                if 200 <= r.status < 400:
                    return True
        except Exception:
            continue
    return False


def verificar_urls(urls):
    vivas = set()
    pendientes = [u for u in urls if u]
    log(f"      verificando {len(pendientes)} URLs unicas "
        f"(HEAD, puede tardar unos minutos)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        for u, ok in zip(pendientes, ex.map(url_viva, pendientes)):
            if ok:
                vivas.add(u)
    return vivas


# ------------------------------------------------------------------
# Armado de filas con las reglas del catalogo
# ------------------------------------------------------------------

def armar_filas(productos, actual, historico, precios, vivas, origen_web,
                stock_minimo=STOCK_MINIMO, dropi_skus=None, dropi_nombres=None,
                costos=None, descripciones=None):
    dropi_skus = dropi_skus or set()
    dropi_nombres = dropi_nombres or set()
    costos = costos or {}
    descripciones = descripciones or {}
    filas = []
    resumen = {
        "permanecen": 0, "entran": 0, "salen": 0, "sin_precio5": 0,
        "stock_bajo": 0, "ya_en_dropi": 0, "sin_costo": 0,
        "sin_rentabilidad": 0, "sin_imagen": 0, "img_rellenadas": 0,
        "img_reparadas": 0, "sugerido": 0, "cat_mapeadas": 0,
        "con_copia": 0, "salidas": [],
    }

    for sku, p in productos.items():
        # --- precio: Precio 5 en vivo; sin el, el producto no se carga ---
        pr = precios.get(sku)
        if not pr or pr["p5"] <= 0:
            resumen["sin_precio5"] += 1
            resumen["salidas"].append((sku, p["nombre"], "sin Precio 5 en POSGold"))
            continue

        # --- rentabilidad al Precio 5: en o bajo el costo no se enlista ---
        utilidad = utilidad_precio5(pr, costos)
        if utilidad is None:
            resumen["sin_costo"] += 1
            resumen["salidas"].append((sku, p["nombre"],
                                       "sin costo en Excel (rentabilidad no evaluable)"))
            continue
        if utilidad <= 0:
            resumen["sin_rentabilidad"] += 1
            resumen["salidas"].append((sku, p["nombre"],
                                       f"rentabilidad al Precio 5: base sin IVA "
                                       f"{int(round(pr['p5'] / (1 + pr['iva'] / 100) if pr['iva'] > 0 else pr['p5']))}"
                                       f" <= costo {int(round(costos[pr['pid']]))}"))
            continue

        # --- nivel de existencias: por debajo del minimo no se enlista ---
        if p["stock"] < stock_minimo:
            resumen["stock_bajo"] += 1
            resumen["salidas"].append((sku, p["nombre"],
                                       f"existencias {p['stock']} < {stock_minimo}"))
            continue

        # --- conciliacion con Dropi: lo ya publicado alli no se enlisto ---
        if sku.upper() in dropi_skus or normalizar_nombre(p["nombre"]) in dropi_nombres:
            resumen["ya_en_dropi"] += 1
            resumen["salidas"].append((sku, p["nombre"], "ya publicado en Dropi"))
            continue

        act = actual.get(sku)
        hist = historico.get(sku)

        # --- imagen: conserva la viva, rellena la faltante ---
        if act and act["imagenes"]:
            urls = [u.strip() for u in act["imagenes"].split(",") if u.strip()]
            vivas_fila = [u for u in urls if u in vivas]
            if vivas_fila:
                if len(vivas_fila) != len(urls):
                    resumen["img_reparadas"] += 1
                imagen = ",".join(vivas_fila)
            else:
                resumen["img_reparadas"] += 1
                imagen = ""
        else:
            imagen = ""
            if act:
                resumen["img_rellenadas"] += 1
        if not imagen:
            imagen = p["fulls"][0] if p["fulls"] else (
                origen_web + "/" + p["thumb"] if p["thumb"] else "")
        if not imagen:
            resumen["sin_imagen"] += 1
            resumen["salidas"].append((sku, p["nombre"], "sin imagen"))
            continue

        # --- curacion preservada ---
        if act:
            resumen["permanecen"] += 1
            categoria = act["categoria"] or ""
            privado = act["privado"] or "NO"
            descripcion = act["descripcion"]
        else:
            resumen["entran"] += 1
            categoria = (hist or {}).get("categoria") or ""
            privado = "NO"
            descripcion = (hist or {}).get("descripcion")
            if not categoria:
                categoria = CATEGORIA_WEB_A_DROPI.get(p["categoria_web"], "JUGUETERIA")
                resumen["cat_mapeadas"] += 1
        if not categoria:
            categoria = "JUGUETERIA"
        if sku in descripciones:
            descr_txt = descripciones[sku]
            resumen["con_copia"] += 1
        else:
            descr_txt = str(descripcion).strip() if descripcion else ""
        descr_txt = html_a_texto(descr_txt)
        if not descr_txt:
            descr_txt = p["nombre"]
        descr_final = descr_txt + "\n\n" + SEPARADOR_PIE + "\n" + PIE_DROPI

        sugerido = pr["p2"] if pr["p2"] > pr["p5"] else None
        if sugerido:
            resumen["sugerido"] += 1

        filas.append([
            p["nombre"], pr["p5"], sugerido, descr_final,
            categoria, privado, "SIMPLE", sku, None,
            p["stock"], ID_BODEGA, None, None, None, None,
            imagen, 1, 1, 1, 1,
        ])

    for sku, fila in actual.items():
        if sku not in productos:
            resumen["salen"] += 1
            resumen["salidas"].append((sku, fila["nombre"],
                                       "no elegible en catalogo (reglas de la tienda)"))
    return filas, resumen


def conciliar(filas, productos, precios, stock_minimo=STOCK_MINIMO,
              dropi_skus=None, dropi_nombres=None, costos=None):
    """Garantia dura antes de escribir: ninguna regla violada."""
    dropi_skus = dropi_skus or set()
    dropi_nombres = dropi_nombres or set()
    costos = costos or {}
    vistos, problemas = set(), []
    for f in filas:
        sku = str(f[7]).strip()
        if not f[1] or f[1] <= 0:
            problemas.append(f"{sku}: sin PRECIO")
        if not f[9] or f[9] <= 0:
            problemas.append(f"{sku}: STOCK <= 0")
        elif f[9] < stock_minimo:
            problemas.append(f"{sku}: STOCK {f[9]} < minimo {stock_minimo}")
        if not f[15]:
            problemas.append(f"{sku}: sin IMAGENES")
        descr = str(f[3] or "")
        if "<" in descr:
            problemas.append(f"{sku}: DESCRIPCION con HTML (debe ir texto plano)")
        elif SEPARADOR_PIE not in descr or "3106988465" not in descr:
            problemas.append(f"{sku}: DESCRIPCION sin el pie de la tienda")
        pr = precios.get(sku)
        if pr is None or pr["p5"] <= 0:
            problemas.append(f"{sku}: sin Precio 5")
        elif f[1] != pr["p5"]:
            problemas.append(f"{sku}: PRECIO {f[1]} != Precio 5 {pr['p5']}")
        elif (f[2] or None) != (pr["p2"] if pr["p2"] > pr["p5"] else None):
            problemas.append(f"{sku}: PRECIO SUGERIDO {f[2]} no es el tachado honesto")
        else:
            utilidad = utilidad_precio5(pr, costos)
            if utilidad is None:
                problemas.append(f"{sku}: sin costo en Excel")
            elif utilidad <= 0:
                problemas.append(f"{sku}: rentabilidad negativa al Precio 5")
        if sku.upper() in dropi_skus:
            problemas.append(f"{sku}: ya publicado en Dropi (SKU)")
        if normalizar_nombre(f[0]) in dropi_nombres:
            problemas.append(f"{sku}: ya publicado en Dropi (nombre)")
        if sku not in productos:
            problemas.append(f"{sku}: no esta en catalogo.js")
        if sku in vistos:
            problemas.append(f"{sku}: duplicado")
        vistos.add(sku)
    if len(filas) < MIN_PRODUCTOS:
        problemas.append(f"quedarian solo {len(filas)} filas")
    return problemas


def main():
    ap = argparse.ArgumentParser(description="Actualiza la carga masiva DROPI "
                                             "con las reglas del catalogo")
    ap.add_argument("--dry-run", action="store_true",
                    help="solo diagnostico, no escribe el Excel")
    ap.add_argument("--min-stock", type=int, default=STOCK_MINIMO,
                    help=f"nivel minimo de existencias para enlistar "
                         f"(default: {STOCK_MINIMO})")
    args = ap.parse_args()
    stock_minimo = args.min_stock

    log("[1/6] Leyendo catalogo.js (fuente de verdad de elegibilidad)...")
    productos, origen_web = parsear_catalogo()
    log(f"      {len(productos)} productos elegibles (reglas de la tienda ya aplicadas)")
    log(f"      web: {origen_web}")

    log("[2/6] Consultando Precio 5 en POSGold y costos del Excel...")
    env = cargar_env()
    jwt = login_jwt(env)
    precios = consultar_precios(env, jwt)
    sin_p5 = sum(1 for s in productos if s not in precios or precios[s]["p5"] <= 0)
    log(f"      {len(precios)} precios de POSGold | elegibles sin Precio 5: {sin_p5}")

    excel_costos = encontrar_excel_costos()
    if not excel_costos:
        abortar("falta el Excel de costos (PRODUCTOS*.xlsx): la regla de "
                "rentabilidad no se puede evaluar")
    campo_costo = env.get("COSTO_CAMPO", "costopromedio")
    costos = cargar_costos(excel_costos, campo_costo)
    log(f"      costos: {os.path.basename(excel_costos)} (campo: {campo_costo}) "
        f"-> {len(costos)} productos con costo")

    log("[3/6] Leyendo curacion DROPI previa...")
    if not os.path.exists(RUTA_CARGA):
        abortar(f"no existe {RUTA_CARGA}")
    actual = leer_carga(RUTA_CARGA)
    historico = {}
    if PLANTILLAS_ENRIQUECIDO:
        historico = leer_carga(PLANTILLAS_ENRIQUECIDO[-1])
        log(f"      carga actual: {len(actual)} SKUs | historico 07-10: {len(historico)} SKUs")
    else:
        log(f"      carga actual: {len(actual)} SKUs | sin historico ENRIQUECIDO")
    if not actual:
        abortar("la carga actual no tiene filas legibles")

    dropi_skus, dropi_nombres, fuentes = leer_publicados_dropi()
    if not fuentes:
        log("      aviso: no se encontraron exports de Dropi "
            "(products_*.xlsx / variaciones*.xlsx); sin conciliacion con lo publicado")
    log(f"      conciliacion Dropi: {len(dropi_skus)} SKUs y "
        f"{len(dropi_nombres)} nombres ya publicados")

    descripciones = cargar_descripciones()
    log(f"      descripciones de campana: {len(descripciones)} SKUs "
        f"en data/descripciones_dropi.json")

    log("[4/6] Verificando URLs de imagen existentes...")
    urls = set()
    for fila in actual.values():
        for u in fila["imagenes"].split(","):
            u = u.strip()
            if u:
                urls.add(u)
    vivas = verificar_urls(sorted(urls))
    log(f"      {len(vivas)}/{len(urls)} URLs vivas")

    log(f"[5/6] Aplicando reglas: espejo del catalogo, Precio 5 rentable, "
        f"minimo {stock_minimo} unidades, sin duplicar lo ya en Dropi...")
    filas, res = armar_filas(productos, actual, historico, precios, vivas,
                             origen_web, stock_minimo, dropi_skus, dropi_nombres,
                             costos, descripciones)
    log(f"      permanecen {res['permanecen']} | entran {res['entran']} "
        f"| salen {res['salen']} | sin Precio 5 {res['sin_precio5']} "
        f"| sin costo {res['sin_costo']} | sin rentabilidad {res['sin_rentabilidad']} "
        f"| bajo {stock_minimo} unidades {res['stock_bajo']} "
        f"| ya en Dropi {res['ya_en_dropi']} -> total {len(filas)} filas")
    log(f"      imagenes: {res['img_rellenadas']} rellenadas, "
        f"{res['img_reparadas']} reparadas, {res['sin_imagen']} excluidos sin imagen")
    log(f"      precio sugerido (Precio 2 real sobre el Precio 5): "
        f"{res['sugerido']} filas lo llevan | categorias mapeadas: {res['cat_mapeadas']} "
        f"| copys de campana: {res['con_copia']}")
    if res["salidas"]:
        log("      salen del archivo (primeros 8):")
        for sku, nombre, motivo in res["salidas"][:8]:
            log(f"        {sku}  {str(nombre)[:40]:40} {motivo}")

    problemas = conciliar(filas, productos, precios, stock_minimo,
                          dropi_skus, dropi_nombres, costos)
    if problemas:
        abortar("violaciones de reglas: " + "; ".join(problemas[:10]))

    if args.dry_run:
        log("[6/6] DRY-RUN: no se escribe el Excel. Todo verificado OK.")
        return

    log(f"[6/6] Escribiendo {os.path.basename(RUTA_CARGA)}...")
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Worksheet"
        ws.append(ENCABEZADOS)
        for f in sorted(filas, key=lambda x: (str(x[4]), str(x[0]))):
            ws.append(f)
        for i in range(2, len(filas) + 2):
            ws.cell(row=i, column=8).number_format = "@"
        wb.save(RUTA_CARGA)
    except PermissionError:
        abortar("el Excel esta abierto en otra ventana; cierralo y reintenta")

    wb = openpyxl.load_workbook(RUTA_CARGA, read_only=True)
    ws = wb[wb.sheetnames[0]]
    filas_escritas = list(ws.iter_rows(values_only=True))
    wb.close()
    n = len(filas_escritas) - 1
    sin_img = sum(1 for f in filas_escritas[1:] if not f[15])
    sin_precio = sum(1 for f in filas_escritas[1:] if not f[1])
    sin_stock = sum(1 for f in filas_escritas[1:] if (f[9] or 0) <= 0)
    if n != len(filas) or sin_img or sin_precio or sin_stock:
        abortar(f"el archivo escrito no concilia: {n} filas, "
                f"{sin_img} sin imagen, {sin_precio} sin precio, {sin_stock} sin stock")

    log("")
    log(f"OK: {n} productos en {os.path.basename(RUTA_CARGA)} — todos con "
        f"Precio 5 de POSGold rentable (sobre costo), imagen y "
        f"{stock_minimo}+ unidades (reglas del catalogo aplicadas).")
    log("Recordatorio: Dropi no borra lo ya publicado; los que salen "
        "del catalogo, quedan sin Precio 5, pierden rentabilidad, bajan "
        "del minimo de existencias o ya viven en Dropi se gestionan alla "
        "(carga masiva solo crea/actualiza; pausar/ocultar es manual).")


if __name__ == "__main__":
    main()
