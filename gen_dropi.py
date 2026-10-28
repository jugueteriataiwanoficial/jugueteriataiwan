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
      Precio 5 el producto no se carga (regla "sin precio"). El
      precio va CERRADO al siguiente multiplo de $1.000 (regla de
      la tienda 28-10-2026: 10.500 -> 11.000), siempre hacia
      ARRIBA: jamas queda por debajo del Precio 5 rentable.
    3. PRECIO SUGERIDO: regla de la tienda (26-10-2026) — TODA fila
       lleva sugerido = PRECIO + $10.000 como MINIMO; el Precio 2
       real se conserva solo si ya supera ese minimo. Ninguna celda
       de esta columna va vacia (el cargue del 26-10 rechazo los
       blancos). El sugerido tambien va cerrado al siguiente
       multiplo de $1.000 y nunca por debajo del precio redondeado
       + $10.000.
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
      nombres normalizados) de TODOS los exports DROPI/products_*.xlsx
      y DROPI/variaciones*.xlsx no se enlistan — evita duplicar
      productos que ya viven en la tienda Dropi. Se absorben todos
      los archivos de cada patron porque la paginacion del panel
      puede partir el listado en varias paginas (28-10: la prueba de
      50 entro y el listado se partio en 2 exports).
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
   10. SKUs bloqueados por listados viejos de Dropi que los
      exports no muestran (solo los revela el intento de cargue):
      se excluyen (SKU_BLOQUEADOS_DROPI) y se prueban aparte con
      DROPI/carga-masiva-mini-bloqueados.xlsx, que se genera en
      cada corrida con datos frescos de POSGold.
   11. Piso de precio (regla de la tienda 27-10-2026): solo se
       enlistan productos con Precio 5 de $2.000 o mas — por
       debajo, empacar y despachar una unidad cuesta mas de lo que
       deja y nadie lo revende al detal. Excepciones por categoria
       (PRECIO_MINIMO_CATEGORIA): PIÑATERIA y CACHARRO piden
       $5.000 (suministros de fiesta y cachareria barata no son
       producto de dropshipping) y SLIME baja a $500 (el gancho
       viral de TikTok se conserva aunque sea barato). El piso
        general se ajusta con --min-precio; los pisos por categoria
        son decision de la tienda.
   12. Partes en serie de hasta 50 filas (regla de la tienda
       28-10-2026, tras el cargue exitoso de la prueba de 50):
       ademas del principal, se genera la serie DROPI/carga-masiva-
       parte-01-de-NN.xlsx, -02-, ... que cubre el 100 % de la carga
       en tandas de hasta 50 filas, para subirlas EN ORDEN por el
       panel. Las partes son disjuntas (particion del principal):
       se suben seguidas sin re-exportar entre ellas; al terminar la
       serie (o si una falla), re-exportar products_*.xlsx con la
       paginacion al maximo y correr gen_dropi.py de nuevo — las
       partes de generaciones anteriores se borran solas en cada
       regeneracion, nunca se mezclan generaciones. Ajustable con
       --filas-parte (0 = solo el principal).
   13. Alcance por categoria (regla de la tienda 28-10-2026): la
       carga solo lleva JUGUETERIA, JUEGO DE MESA y DIDACTICO
       (CATEGORIAS_CARGA); las demas categorias quedan fuera del
       alcance del marketplace. Lo ya publicado en Dropi de otras
       categorias no se toca: pausarlo es manual en el panel.

   El archivo se escribe con xlsxwriter (shared strings): openpyxl
   3.1.5 escribe inline strings (sin xl/sharedStrings.xml) y el
   parser de Dropi no los lee — el primer cargue (26-10 11:06)
   respondio "Encabezado no reconocido en la estructura del
   archivo" justo por eso.

   El NOMBRE del producto tiene limite de 39 caracteres en Dropi
   (los de 40+ generan "supera el maximo permitido", cargue de
   etapas 26-10 13:34): los nombres largos se recortan a 39 por
   palabra completa, nunca a la mitad de una palabra.

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
import zipfile

import openpyxl

try:
    import xlsxwriter  # escribe shared strings; openpyxl escribe inline
except ImportError:
    xlsxwriter = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
RUTA_CATALOGO = os.path.join(BASE_DIR, "catalogo.js")
RUTA_CARGA = os.path.join(BASE_DIR, "DROPI", "carga-masiva-productos-con-variables.xlsx")
PLANTILLAS_ENRIQUECIDO = sorted(
    glob.glob(os.path.join(BASE_DIR, "DROPI_carga_masiva*ENRIQUECIDO*.xlsx")))
# Exports de Dropi: lo que ya vive en la tienda Dropi no se vuelve
# a enlistar. Se absorben TODOS los archivos de cada patron: la
# paginacion del panel puede partir el listado en varias paginas y
# un export de primera pagina dejaria ciega la conciliacion.
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
SUGERIDO_MINIMO = 10000  # regla 26-10: el PRECIO SUGERIDO aumenta minimo $10.000 sobre el PRECIO, nunca vacio
NOMBRE_MAXIMO = 39  # Dropi rechaza nombres de 40+ caracteres (cargue 26-10 13:34)
# SKUs con listados viejos en Dropi que NO salen en los exports (solo los
# revela el intento de cargue): 04521 y 04539 (IDs 2031517/2030269,
# archivados por la duena el 26-10) y 03371 (ID 1840409 "BOLSO CAPIBARA
# ECONOMICO", revelado por la parte-03 el 28-10 01:51). Se excluyen para
# no arriesgar el todo-o-nada del cargue y se prueban aparte con
# DROPI/carga-masiva-mini-bloqueados.xlsx (se genera en cada corrida).
# Si el mini entra, quitar el SKU de esta lista y correr de nuevo.
SKU_BLOQUEADOS_DROPI = {"04521", "04539", "03371"}
# Regla de la tienda (27-10-2026): piso de precio del Precio 5 para
# enlistar en Dropi. General $2.000 (por debajo, empacar y despachar
# una unidad cuesta mas de lo que deja); PIÑATERIA y CACHARRO exigen
# $5.000 (suministros de fiesta y cachareria barata no son producto
# de dropshipping: nadie los revende al detal) y SLIME baja a $500
# (el gancho viral se conserva aunque sea barato). Los pisos por
# categoria son decision de la tienda; el general, de --min-precio.
PRECIO_MINIMO = 2000
PRECIO_MINIMO_CATEGORIA = {"PIÑATERIA": 5000, "CACHARRO": 5000, "SLIME": 500}
# Regla de la tienda (28-10-2026): el cargue masivo se sube en tandas
# de 50 filas o menos — la prueba de 50 (carga-masiva-prueba-50.xlsx)
# entro limpia tras 4 intentos fallidos con archivos grandes. La
# generacion escribe la serie completa (parte 01, 02, ... hasta
# cubrir el 100 % de la carga); las partes son disjuntas, se suben en
# orden y no hace falta re-exportar entre ellas.
FILAS_POR_PARTE = 50
# Regla de la tienda (28-10-2026): la carga de Dropi solo lleva las
# categorias del alcance del marketplace — JUGUETERIA, JUEGO DE MESA
# y DIDACTICO. Las demas quedan fuera; lo ya publicado de otras
# categorias se gestiona (pausa) a mano en el panel de Dropi.
CATEGORIAS_CARGA = {"JUGUETERIA", "JUEGO DE MESA", "DIDACTICO"}
# Regla de la tienda (28-10-2026): los precios de la carga van
# CERRADOS al siguiente multiplo de $1.000 (10.500 -> 11.000),
# siempre hacia ARRIBA: jamas por debajo del Precio 5 rentable.
REDONDEO_PRECIO = 1000
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


def sugerido_para(pr):
    """Regla de la tienda (26-10-2026): el PRECIO SUGERIDO es el PRECIO
    (Precio 5) + $10.000 como minimo. El Precio 2 real solo se conserva
    si ya supera ese minimo. Nunca devuelve vacio."""
    minimo = pr["p5"] + SUGERIDO_MINIMO
    return pr["p2"] if pr["p2"] >= minimo else minimo


def redondear_precio(p):
    """Regla de la tienda (28-10-2026): los precios de la carga van
    cerrados al siguiente multiplo de $1.000 (10.500 -> 11.000; lo que
    ya es multiplo queda igual). Siempre hacia ARRIBA: jamas por debajo
    del Precio 5 rentable."""
    if not p or p <= 0:
        return p
    resto = p % REDONDEO_PRECIO
    return p if resto == 0 else p + (REDONDEO_PRECIO - resto)


def precio_carga(pr):
    """El PRECIO que viaja al Excel: el Precio 5 cerrado al siguiente
    $1.000 (regla 28-10). La rentabilidad y el piso se evaluan sobre
    el Precio 5 REAL; el redondeo solo suma."""
    return redondear_precio(pr["p5"])


def sugerido_carga(pr):
    """El PRECIO SUGERIDO que viaja al Excel: el de la regla 26-10
    (minimo precio + $10.000, conservando el Precio 2 real si supera)
    cerrado al siguiente $1.000, y jamas por debajo del precio de
    carga + $10.000 (que tambien es multiplo de $1.000)."""
    s = redondear_precio(sugerido_para(pr))
    minimo = precio_carga(pr) + SUGERIDO_MINIMO
    return s if s >= minimo else minimo


def recortar_nombre(nombre, maximo=NOMBRE_MAXIMO):
    """Dropi limita el nombre del producto a 39 (cargue 26-10 13:34:
    los de 40+ generan "supera el maximo permitido"). Se mide en BYTES
    UTF-8 para cubrir tambien la lectura de la Ñ y los acentos: se
    recorta por palabra completa, nunca a la mitad de una palabra."""
    n = " ".join(str(nombre or "").split())
    if len(n.encode("utf-8")) <= maximo:
        return n
    recorte = ""
    for palabra in n.split(" "):
        prueba = (recorte + " " + palabra).strip()
        if len(prueba.encode("utf-8")) > maximo:
            break
        recorte = prueba
    if not recorte:  # una sola palabra mas larga que el maximo: corte duro
        recorte = n
        while recorte and len(recorte.encode("utf-8")) > maximo:
            recorte = recorte[:-1]
    return recorte


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
        # TODOS los archivos del patron se absorben: la paginacion
        # del panel puede partir el listado en varios exports (el
        # 28-10 el listado publicado se partio en 2 paginas) —
        # quedarse solo con el ultimo dejaria ciega la conciliacion.
        for ruta in sorted(glob.glob(os.path.join(carpeta, patron))):
            try:
                antes = len(skus)
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
                nuevos = len(skus) - antes
                log(f"      {os.path.basename(ruta)}: {n} filas de Dropi "
                    f"(+{nuevos} SKUs nuevos; total acumulado: {len(skus)})")
                if "products" in patron and n <= 15 and nuevos > 0:
                    log(f"      aviso: {os.path.basename(ruta)} trae solo {n} filas "
                        f"— el export saca la PAGINA visible del panel; si el "
                        f"listado tiene mas paginas, exportarlas TODAS y volver "
                        f"a dejarlas en DROPI (un export incompleto ciega la "
                        f"conciliacion y duplicaria lo publicado)")
            except Exception as e:
                log(f"      aviso: no se pudo leer {ruta}: {e}")
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
                costos=None, descripciones=None, precio_minimo=PRECIO_MINIMO):
    dropi_skus = dropi_skus or set()
    dropi_nombres = dropi_nombres or set()
    costos = costos or {}
    descripciones = descripciones or {}
    filas = []
    resumen = {
        "permanecen": 0, "entran": 0, "salen": 0, "sin_precio5": 0,
        "stock_bajo": 0, "ya_en_dropi": 0, "categoria_fuera": 0,
        "bloqueados": 0, "sin_costo": 0,
        "sin_rentabilidad": 0, "precio_bajo": 0, "sin_imagen": 0,
        "img_rellenadas": 0, "img_reparadas": 0, "sugerido_p2": 0,
        "sugerido_minimo": 0, "nombres_recortados": 0, "cat_mapeadas": 0,
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

        # --- categoria: se resuelve temprano porque el piso de
        # precio (regla 11) depende de ella ---
        if act:
            categoria = act["categoria"] or ""
        else:
            categoria = (hist or {}).get("categoria") or ""
            if not categoria:
                categoria = CATEGORIA_WEB_A_DROPI.get(p["categoria_web"], "JUGUETERIA")
                resumen["cat_mapeadas"] += 1
        if not categoria:
            categoria = "JUGUETERIA"

        # --- regla 13: alcance de la carga — solo las categorias que
        # la tienda decide cargar en Dropi (28-10-2026) ---
        if categoria not in CATEGORIAS_CARGA:
            resumen["categoria_fuera"] += 1
            resumen["salidas"].append((sku, p["nombre"],
                                       f"categoria {categoria} fuera del alcance de la carga"))
            continue

        # --- SKUs bloqueados por listados viejos invisibles en Dropi
        # (se mira despues del alcance: un bloqueado de otra categoria
        # sale por categoria, no por bloqueo) ---
        if sku in SKU_BLOQUEADOS_DROPI:
            resumen["bloqueados"] += 1
            resumen["salidas"].append((sku, p["nombre"],
                                       "SKU bloqueado por listado viejo de Dropi (invisible en exports)"))
            continue

        # --- piso de precio (regla 11): general $2.000;
        # PIÑATERIA y CACHARRO $5.000; SLIME $500. El valor de la
        # categoria PISA el general (arriba o abajo) ---
        piso = PRECIO_MINIMO_CATEGORIA.get(categoria.strip().upper(), precio_minimo)
        if pr["p5"] < piso:
            resumen["precio_bajo"] += 1
            motivo = (f"precio {pr['p5']} < piso {piso} de {categoria}"
                      if categoria in PRECIO_MINIMO_CATEGORIA
                      else f"precio {pr['p5']} < piso {piso}")
            resumen["salidas"].append((sku, p["nombre"], motivo))
            continue

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
            privado = act["privado"] or "NO"
            descripcion = act["descripcion"]
        else:
            resumen["entran"] += 1
            privado = "NO"
            descripcion = (hist or {}).get("descripcion")
        if sku in descripciones:
            descr_txt = descripciones[sku]
            resumen["con_copia"] += 1
        else:
            descr_txt = str(descripcion).strip() if descripcion else ""
        descr_txt = html_a_texto(descr_txt)
        if not descr_txt:
            descr_txt = p["nombre"]
        descr_final = descr_txt + "\n\n" + SEPARADOR_PIE + "\n" + PIE_DROPI

        sugerido = sugerido_carga(pr)
        if sugerido_para(pr) == pr["p2"]:
            resumen["sugerido_p2"] += 1
        else:
            resumen["sugerido_minimo"] += 1

        nombre = recortar_nombre(p["nombre"])
        if nombre != " ".join(p["nombre"].split()):
            resumen["nombres_recortados"] += 1

        filas.append([
            nombre, precio_carga(pr), sugerido, descr_final,
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
              dropi_skus=None, dropi_nombres=None, costos=None,
              precio_minimo=PRECIO_MINIMO):
    """Garantia dura antes de escribir: ninguna regla violada."""
    dropi_skus = dropi_skus or set()
    dropi_nombres = dropi_nombres or set()
    costos = costos or {}
    vistos, problemas = set(), []
    for f in filas:
        sku = str(f[7]).strip()
        nombre = str(f[0] or "").strip()
        if not nombre:
            problemas.append(f"{sku}: sin NOMBRE")
        elif len(nombre.encode("utf-8")) > NOMBRE_MAXIMO:
            problemas.append(f"{sku}: NOMBRE de {len(nombre)} caracteres "
                             f"(Dropi rechaza los que pasan de {NOMBRE_MAXIMO})")
        if not f[1] or f[1] <= 0:
            problemas.append(f"{sku}: sin PRECIO")
        else:
            piso = PRECIO_MINIMO_CATEGORIA.get(
                str(f[4] or "").strip().upper(), precio_minimo)
            if f[1] < piso:
                problemas.append(f"{sku}: PRECIO {f[1]} < piso {piso} ({f[4]})")
        if not f[9] or f[9] <= 0:
            problemas.append(f"{sku}: STOCK <= 0")
        elif f[9] < stock_minimo:
            problemas.append(f"{sku}: STOCK {f[9]} < minimo {stock_minimo}")
        if str(f[4] or "").strip().upper() not in CATEGORIAS_CARGA:
            problemas.append(f"{sku}: categoria {f[4]} fuera del alcance de la carga")
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
        elif f[1] != precio_carga(pr):
            problemas.append(f"{sku}: PRECIO {f[1]} != Precio 5 redondeado "
                             f"{precio_carga(pr)} (real {pr['p5']})")
        elif f[1] < pr["p5"]:
            problemas.append(f"{sku}: PRECIO {f[1]} por debajo del Precio 5 real {pr['p5']}")
        elif (f[1] % REDONDEO_PRECIO) or (f[2] or 0) % REDONDEO_PRECIO:
            problemas.append(f"{sku}: precios sin cerrar a {REDONDEO_PRECIO}")
        elif f[2] is None or f[2] != sugerido_carga(pr):
            problemas.append(f"{sku}: PRECIO SUGERIDO {f[2]} no cumple la regla "
                             f"(minimo {precio_carga(pr)} + {SUGERIDO_MINIMO}, "
                             f"cerrado a {REDONDEO_PRECIO})")
        else:
            utilidad = utilidad_precio5(pr, costos)
            if utilidad is None:
                problemas.append(f"{sku}: sin costo en Excel")
            elif utilidad <= 0:
                problemas.append(f"{sku}: rentabilidad negativa al Precio 5")
        if sku.upper() in dropi_skus:
            problemas.append(f"{sku}: ya publicado en Dropi (SKU)")
        if sku in SKU_BLOQUEADOS_DROPI:
            problemas.append(f"{sku}: SKU bloqueado por listado viejo de Dropi")
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


# ------------------------------------------------------------------
# Escritura: el principal y las partes usan la misma estructura
# (xlsxwriter con shared strings, SKU como texto). regla 12
# ------------------------------------------------------------------

def escribir_xlsx_carga(ruta, filas_ordenadas):
    """Escribe un archivo de carga masiva: xlsxwriter (shared
    strings: el parser de Dropi no lee los inline strings de
    openpyxl), encabezado oficial de 20 columnas y SKU como texto
    para que Dropi no lo trate como numero."""
    wb = xlsxwriter.Workbook(ruta, {"use_inline_strings": False,
                                    "strings_to_urls": False})
    ws = wb.add_worksheet("Worksheet")
    fmt_texto = wb.add_format({"num_format": "@"})
    ws.set_column(7, 7, 20, fmt_texto)
    for c, h in enumerate(ENCABEZADOS):
        ws.write_string(0, c, h)
    for i, f in enumerate(filas_ordenadas, 1):
        for c, v in enumerate(f):
            if v is None:
                continue
            if c == 7:  # SKU siempre como texto
                ws.write_string(i, c, str(v), fmt_texto)
            else:
                ws.write(i, c, v)
    wb.close()


def partir_filas(filas_ordenadas, por_parte):
    """Regla 12: particion serial en partes de hasta por_parte
    filas. El orden del principal se conserva: la parte 01 son las
    primeras filas, la 02 las siguientes, etc. Las partes son
    disjuntas y su union es el 100 % de la carga."""
    if por_parte <= 0:
        return []
    return [filas_ordenadas[i:i + por_parte]
            for i in range(0, len(filas_ordenadas), por_parte)]


def generar_partes(ruta_principal, filas_ordenadas, filas_por_parte):
    """Escribe la serie de partes en DROPI/ y la verifica dura:
    mismas reglas que el principal (encabezado, sharedStrings,
    conteo), partes disjuntas y cobertura del 100 %. Devuelve
    [(ruta, filas), ...]. Aborta si algo no cuadra."""
    carpeta = os.path.dirname(ruta_principal)
    viejas = sorted(glob.glob(os.path.join(carpeta, "carga-masiva-parte-*.xlsx")))
    for v in viejas:
        try:
            os.remove(v)
        except OSError:
            abortar(f"no se pudo borrar la parte vieja {os.path.basename(v)} "
                    f"(¿esta abierta en Excel?); cierrala y reintenta")
    if viejas:
        log(f"      {len(viejas)} partes de generaciones anteriores borradas "
            f"(nunca se mezclan generaciones)")
    grupos = partir_filas(filas_ordenadas, filas_por_parte)
    total = len(grupos)
    ancho = max(2, len(str(total)))
    rutas = []
    try:
        for numero, grupo in enumerate(grupos, 1):
            ruta = os.path.join(carpeta, "carga-masiva-parte-"
                                f"{numero:0{ancho}d}-de-{total:0{ancho}d}.xlsx")
            escribir_xlsx_carga(ruta, grupo)
            rutas.append((ruta, len(grupo)))
    except PermissionError:
        abortar("una parte esta abierta en Excel; cierrala y reintenta")
    except Exception as e:
        abortar(f"no se pudo escribir una parte: {e}")
    skus_principal = {str(f[7]).strip() for f in filas_ordenadas}
    vistos = set()
    for ruta, esperadas in rutas:
        with zipfile.ZipFile(ruta) as z:
            if "xl/sharedStrings.xml" not in z.namelist():
                abortar(f"{os.path.basename(ruta)} quedo sin xl/sharedStrings.xml: "
                        f"Dropi no parsea inline strings ('Encabezado no reconocido')")
        wbp = openpyxl.load_workbook(ruta, read_only=True)
        wsp = wbp[wbp.sheetnames[0]]
        fps = list(wsp.iter_rows(values_only=True))
        wbp.close()
        if [str(c) for c in fps[0]] != ENCABEZADOS:
            abortar(f"{os.path.basename(ruta)} no tiene el encabezado oficial")
        skus_p = {str(f[7]).strip() for f in fps[1:]}
        if len(fps) - 1 != esperadas or len(skus_p) != esperadas:
            abortar(f"{os.path.basename(ruta)} no concilia: {len(fps) - 1} filas "
                    f"(esperadas {esperadas})")
        if skus_p & vistos:
            abortar(f"{os.path.basename(ruta)} repite SKUs de partes anteriores")
        vistos |= skus_p
    if vistos != skus_principal:
        abortar(f"las partes no cubren el 100 % de la carga: "
                f"{len(skus_principal - vistos)} SKUs quedaron fuera")
    return rutas


def generar_mini_bloqueados(ruta_principal, productos, actual, historico,
                            precios, costos, descripciones, vivas,
                            origen_web, stock_minimo=STOCK_MINIMO):
    """Regla 10: el mini-archivo prueba APARTE los SKUs bloqueados por
    listados viejos de Dropi invisibles en los exports (solo el cargue
    los revela). Se regenera en cada corrida con datos frescos y solo
    lleva bloqueados que hoy cumplirian todas las reglas de la carga
    (Precio 5, rentabilidad, existencias, imagen): si el mini entra,
    el listado viejo ya no existe y el SKU sale de
    SKU_BLOQUEADOS_DROPI para volver al principal."""
    carpeta = os.path.dirname(ruta_principal)
    filas = []
    for sku in sorted(SKU_BLOQUEADOS_DROPI):
        p = productos.get(sku)
        pr = precios.get(sku)
        if not p or not pr or pr["p5"] <= 0:
            log(f"      aviso: bloqueado {sku} sin Precio 5; no va al mini")
            continue
        utilidad = utilidad_precio5(pr, costos)
        if utilidad is None or utilidad <= 0:
            log(f"      aviso: bloqueado {sku} sin rentabilidad al Precio 5; no va al mini")
            continue
        if p["stock"] < stock_minimo:
            log(f"      aviso: bloqueado {sku} con {p['stock']} unidades "
                f"(< {stock_minimo}); no va al mini")
            continue
        act = actual.get(sku)
        hist = (historico or {}).get(sku) or {}
        categoria = ((act["categoria"] if act else "") or hist.get("categoria")
                    or CATEGORIA_WEB_A_DROPI.get(p["categoria_web"], "JUGUETERIA")
                    or "JUGUETERIA")
        if categoria not in CATEGORIAS_CARGA:
            log(f"      aviso: bloqueado {sku} de categoria {categoria}, "
                f"fuera del alcance de la carga; no va al mini")
            continue
        privado = (act["privado"] if act else "") or "NO"
        imagen = ""
        if act and act["imagenes"]:
            imagen = ",".join(u.strip() for u in act["imagenes"].split(",")
                              if u.strip() and u.strip() in vivas)
        if not imagen:
            imagen = p["fulls"][0] if p["fulls"] else (
                origen_web + "/" + p["thumb"] if p["thumb"] else "")
        if not imagen:
            log(f"      aviso: bloqueado {sku} sin imagen; no va al mini")
            continue
        descr = descripciones.get(sku)
        if not descr:
            descr = str((act or {}).get("descripcion") or
                         hist.get("descripcion") or p["nombre"]).strip()
        descr = html_a_texto(descr) or p["nombre"]
        descr_final = descr + "\n\n" + SEPARADOR_PIE + "\n" + PIE_DROPI
        filas.append([
            recortar_nombre(p["nombre"]), precio_carga(pr), sugerido_carga(pr),
            descr_final, categoria, privado, "SIMPLE", sku, None,
            p["stock"], ID_BODEGA, None, None, None, None,
            imagen, 1, 1, 1, 1,
        ])
    if not filas:
        log("      sin bloqueados viables: no se escribe el mini")
        return None
    ruta = os.path.join(carpeta, "carga-masiva-mini-bloqueados.xlsx")
    try:
        escribir_xlsx_carga(ruta, filas)
    except Exception as e:
        abortar(f"no se pudo escribir el mini de bloqueados: {e}")
    with zipfile.ZipFile(ruta) as z:
        if "xl/sharedStrings.xml" not in z.namelist():
            abortar("el mini de bloqueados quedo sin xl/sharedStrings.xml: "
                    "Dropi no parsea inline strings ('Encabezado no reconocido')")
    wbm = openpyxl.load_workbook(ruta, read_only=True)
    wsm = wbm[wbm.sheetnames[0]]
    fpm = list(wsm.iter_rows(values_only=True))
    wbm.close()
    if ([str(c) for c in fpm[0]] != ENCABEZADOS or len(fpm) - 1 != len(filas)):
        abortar("el mini de bloqueados no concilia con lo calculado")
    log(f"      mini de bloqueados: {len(filas)} SKUs "
        f"({', '.join(str(f[7]) for f in filas)}) -> {os.path.basename(ruta)}: "
        f"probarlo aparte por el panel; si entra, quitar esos SKUs de "
        f"SKU_BLOQUEADOS_DROPI y correr de nuevo")
    return ruta


def main():
    ap = argparse.ArgumentParser(description="Actualiza la carga masiva DROPI "
                                             "con las reglas del catalogo")
    ap.add_argument("--dry-run", action="store_true",
                    help="solo diagnostico, no escribe el Excel")
    ap.add_argument("--min-stock", type=int, default=STOCK_MINIMO,
                    help=f"nivel minimo de existencias para enlistar "
                         f"(default: {STOCK_MINIMO})")
    ap.add_argument("--min-precio", type=int, default=PRECIO_MINIMO,
                    help=f"precio minimo del Precio 5 para enlistar "
                         f"(default: {PRECIO_MINIMO}; las categorias de "
                         f"PRECIO_MINIMO_CATEGORIA pisan este piso)")
    ap.add_argument("--filas-parte", type=int, default=FILAS_POR_PARTE,
                    help=f"filas por archivo de la serie de cargues "
                         f"(regla 12; default: {FILAS_POR_PARTE}, el "
                         f"tamano probado en Dropi; 0 = solo el principal)")
    args = ap.parse_args()
    stock_minimo = args.min_stock
    precio_minimo = args.min_precio
    filas_por_parte = args.filas_parte

    log("[1/7] Leyendo catalogo.js (fuente de verdad de elegibilidad)...")
    productos, origen_web = parsear_catalogo()
    log(f"      {len(productos)} productos elegibles (reglas de la tienda ya aplicadas)")
    log(f"      web: {origen_web}")

    log("[2/7] Consultando Precio 5 en POSGold y costos del Excel...")
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

    log("[3/7] Leyendo curacion DROPI previa...")
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

    log("[4/7] Verificando URLs de imagen existentes...")
    urls = set()
    for fila in actual.values():
        for u in fila["imagenes"].split(","):
            u = u.strip()
            if u:
                urls.add(u)
    vivas = verificar_urls(sorted(urls))
    log(f"      {len(vivas)}/{len(urls)} URLs vivas")

    log(f"[5/7] Aplicando reglas: espejo del catalogo, Precio 5 rentable, "
        f"alcance solo {sorted(CATEGORIAS_CARGA)}, precios cerrados a "
        f"{REDONDEO_PRECIO}, minimo {stock_minimo} unidades, piso de precio "
        f"{precio_minimo}+ (por categoria: "
        + ", ".join(f"{c} {v}" for c, v in PRECIO_MINIMO_CATEGORIA.items())
        + f"), sugerido minimo +{SUGERIDO_MINIMO} sobre el precio, "
        f"sin duplicar lo ya en Dropi...")
    filas, res = armar_filas(productos, actual, historico, precios, vivas,
                             origen_web, stock_minimo, dropi_skus, dropi_nombres,
                             costos, descripciones, precio_minimo)
    log(f"      permanecen {res['permanecen']} | entran {res['entran']} "
        f"| salen {res['salen']} | sin Precio 5 {res['sin_precio5']} "
        f"| sin costo {res['sin_costo']} | sin rentabilidad {res['sin_rentabilidad']} "
        f"| bajo {stock_minimo} unidades {res['stock_bajo']} "
        f"| bajo el piso de precio {res['precio_bajo']} "
        f"| fuera del alcance {res['categoria_fuera']} "
        f"| ya en Dropi {res['ya_en_dropi']} | bloqueados {res['bloqueados']} "
        f"-> total {len(filas)} filas")
    log(f"      imagenes: {res['img_rellenadas']} rellenadas, "
        f"{res['img_reparadas']} reparadas, {res['sin_imagen']} excluidos sin imagen")
    log(f"      piso de precio (regla 11): {res['precio_bajo']} excluidos — "
        f"general {precio_minimo}, "
        + ", ".join(f"{c} {v}" for c, v in PRECIO_MINIMO_CATEGORIA.items()))
    log(f"      nombres recortados al maximo de {NOMBRE_MAXIMO} caracteres "
        f"de Dropi: {res['nombres_recortados']}")
    log(f"      precio sugerido: minimo +{SUGERIDO_MINIMO} sobre el precio en "
        f"TODAS las filas ({res['sugerido_p2']} conservan el Precio 2 real, "
        f"{res['sugerido_minimo']} ajustados al minimo) | precios CERRADOS al "
        f"siguiente {REDONDEO_PRECIO} (siempre hacia arriba) | categorias "
        f"mapeadas: {res['cat_mapeadas']} | copys de campana: {res['con_copia']}")
    if res["salidas"]:
        log("      salen del archivo (primeros 8):")
        for sku, nombre, motivo in res["salidas"][:8]:
            log(f"        {sku}  {str(nombre)[:40]:40} {motivo}")

    problemas = conciliar(filas, productos, precios, stock_minimo,
                          dropi_skus, dropi_nombres, costos, precio_minimo)
    if problemas:
        abortar("violaciones de reglas: " + "; ".join(problemas[:10]))

    if args.dry_run:
        n_partes = ((len(filas) + filas_por_parte - 1) // filas_por_parte
                    if filas_por_parte > 0 else 0)
        log("[6/7] DRY-RUN: no se escribe nada. Todo verificado OK. "
            f"Se generarian {n_partes} partes en serie de hasta "
            f"{filas_por_parte} filas ({len(filas)} en total)"
            + (f" y el mini de bloqueados ({', '.join(sorted(SKU_BLOQUEADOS_DROPI))})."
               if SKU_BLOQUEADOS_DROPI else "."))
        return

    log(f"[6/7] Escribiendo {os.path.basename(RUTA_CARGA)} con xlsxwriter "
        f"(shared strings; openpyxl escribe inline strings y Dropi "
        f"responde 'Encabezado no reconocido')...")
    if xlsxwriter is None:
        abortar("falta xlsxwriter (pip install xlsxwriter): es el que escribe "
                "los shared strings que Dropi si parsea")
    filas_ordenadas = sorted(filas, key=lambda x: (str(x[4]), str(x[0])))
    try:
        escribir_xlsx_carga(RUTA_CARGA, filas_ordenadas)
    except PermissionError:
        abortar("el Excel esta abierto en otra ventana; cierralo y reintenta")
    except Exception as e:
        abortar(f"no se pudo escribir el Excel: {e}")

    wb = openpyxl.load_workbook(RUTA_CARGA, read_only=True)
    ws = wb[wb.sheetnames[0]]
    filas_escritas = list(ws.iter_rows(values_only=True))
    wb.close()
    n = len(filas_escritas) - 1
    if [str(c) for c in filas_escritas[0]] != ENCABEZADOS:
        abortar("el encabezado escrito no coincide con el formato oficial")
    sin_img = sum(1 for f in filas_escritas[1:] if not f[15])
    sin_precio = sum(1 for f in filas_escritas[1:] if not f[1])
    sin_stock = sum(1 for f in filas_escritas[1:] if (f[9] or 0) <= 0)
    sin_sugerido = sum(1 for f in filas_escritas[1:] if not f[2])
    sugerido_bajo = sum(1 for f in filas_escritas[1:]
                        if (f[2] or 0) - (f[1] or 0) < SUGERIDO_MINIMO)
    nombres_largos = sum(1 for f in filas_escritas[1:]
                         if len(str(f[0] or "").strip().encode("utf-8")) > NOMBRE_MAXIMO)
    bajo_piso = sum(
        1 for f in filas_escritas[1:]
        if (f[1] or 0) < PRECIO_MINIMO_CATEGORIA.get(
            str(f[4] or "").strip().upper(), precio_minimo))
    sin_redondeo = sum(
        1 for f in filas_escritas[1:]
        if ((f[1] or 0) % REDONDEO_PRECIO) or ((f[2] or 0) % REDONDEO_PRECIO))
    fuera_alcance = sum(
        1 for f in filas_escritas[1:]
        if str(f[4] or "").strip().upper() not in CATEGORIAS_CARGA)
    privados_antes = {s for s, a in actual.items()
                      if str(a["privado"]).strip().upper() == "SI"}
    skus_escritos = {str(f[7]).strip() for f in filas_escritas[1:]}
    privados_despues = {str(f[7]).strip() for f in filas_escritas[1:]
                        if str(f[5]).strip().upper() == "SI"}
    marcas_perdidas = (privados_antes & skus_escritos) - privados_despues
    marcas_inventadas = privados_despues - privados_antes
    if (n != len(filas) or sin_img or sin_precio or sin_stock
            or sin_sugerido or sugerido_bajo or nombres_largos
            or bajo_piso or sin_redondeo or fuera_alcance
            or marcas_perdidas or marcas_inventadas):
        abortar(f"el archivo escrito no concilia: {n} filas, {sin_img} sin "
                f"imagen, {sin_precio} sin precio, {sin_stock} sin stock, "
                f"{sin_sugerido} sin precio sugerido, {sugerido_bajo} con "
                f"sugerido menor a +{SUGERIDO_MINIMO}, {nombres_largos} con "
                f"nombre de mas de {NOMBRE_MAXIMO} caracteres, {bajo_piso} "
                f"bajo el piso de precio, {sin_redondeo} sin cerrar a "
                f"{REDONDEO_PRECIO}, {fuera_alcance} fuera del alcance "
                f"de la carga, PRIVADO: "
                f"{len(marcas_perdidas)} marcas perdidas y "
                f"{len(marcas_inventadas)} inventadas")
    with zipfile.ZipFile(RUTA_CARGA) as z:
        if "xl/sharedStrings.xml" not in z.namelist():
            abortar("el archivo escrito no tiene xl/sharedStrings.xml: Dropi "
                    "no parsea inline strings ('Encabezado no reconocido')")

    # ------------------------------------------------------------------
    # Regla 12: partes en serie de hasta filas_por_parte filas
    # ------------------------------------------------------------------
    log(f"\n[7/7] Regla 12: partes en serie de hasta {filas_por_parte} "
        f"filas (la prueba de 50 del 28-10 entro limpia; la serie cubre "
        f"el 100 % de la carga y se sube EN ORDEN)...")
    if filas_por_parte <= 0:
        log("      --filas-parte 0: no se generan partes (solo el principal)")
        rutas = []
    else:
        rutas = generar_partes(RUTA_CARGA, filas_ordenadas, filas_por_parte)
    if rutas:
        log(f"      {len(rutas)} partes — "
            + ", ".join(f"{os.path.basename(r).replace('carga-masiva-', '')} ({m})"
                        for r, m in rutas[:4])
            + (", ..." if len(rutas) > 4 else ""))
        log("      flujo: subirlas EN ORDEN (01, 02, ...); son disjuntas, "
            "no hace falta re-exportar entre ellas. Al terminar la serie "
            "(o si UNA falla): re-exportar products_*.xlsx con la "
            "paginacion al maximo a DROPI/ y correr gen_dropi.py de nuevo.")

    if SKU_BLOQUEADOS_DROPI:
        log("\n      mini de bloqueados (regla 10): los SKUs con listados "
            "viejos invisibles se prueban aparte...")
        generar_mini_bloqueados(RUTA_CARGA, productos, actual, historico,
                                precios, costos, descripciones, vivas,
                                origen_web, stock_minimo)

    log("")
    log(f"OK: {n} productos en {os.path.basename(RUTA_CARGA)} — todos con "
        f"Precio 5 de POSGold rentable (sobre costo), imagen, "
        f"{stock_minimo}+ unidades, PRECIO SUGERIDO minimo "
        f"+{SUGERIDO_MINIMO} sobre el precio, NOMBRE de hasta "
        f"{NOMBRE_MAXIMO} caracteres y piso de precio respetado "
        f"({precio_minimo} general, "
        + ", ".join(f"{c} {v}" for c, v in PRECIO_MINIMO_CATEGORIA.items())
        + f") (reglas de la tienda y de Dropi aplicadas)."
        + (f" Serie lista para el cargue: {len(rutas)} partes de hasta "
           f"{filas_por_parte} filas." if rutas else ""))
    log("Recordatorio: Dropi no borra lo ya publicado; los que salen "
        "del catalogo, quedan sin Precio 5, pierden rentabilidad, bajan "
        "del minimo de existencias o ya viven en Dropi se gestionan alla "
        "(carga masiva solo crea/actualiza; pausar/ocultar es manual).")


if __name__ == "__main__":
    main()
