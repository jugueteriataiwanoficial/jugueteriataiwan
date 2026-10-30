#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_posgold.py — Carga automatica de los 300 productos mas rentables de
POSGold al catalogo.js de Jugueteria Taiwan (saldosjugueteriataiwan.com).

Como funciona (cron: .github/workflows/sync-top300.yml):
  1. Carga credenciales de .env — JAMAS van al repo de GitHub
  2. Genera JWT fresco: POST /apiGold/Login_Api/LoginJWTToken
  3. Descarga productos: GET /apiGold/ProductoApi/GetProduct_V6
     (bodega configurada en .env, por defecto la 2)
   4. Carga costos del ultimo Excel exportado de POSGold (por Productoid)
   5. FILTROS DUROS (regla de la tienda): activo + Disponible>0 en bodega
      + foto real en POSGold + categoria vendible + costo conocido
   6. Rentabilidad sobre el precio detal (PRECIO 2 del sistema):
        precio_base_sin_iva = Precio2 / (1 + IVA/100)
        margen_costo  = (precio_base_sin_iva - costo) / costo     (default)
        margen_precio = (precio_base_sin_iva - costo) / precio_base_sin_iva
        utilidad      = precio_base_sin_iva - costo
   7. Toma el TOP N (los destacados que cumplen entran primero si
      INCLUIR_DESTACADOS=true). TODO sale de POSGold — la web mayorista
      ya no participa:
       - slug (id): el conocido del cache data/mapa_imagenes.json o, para
         productos nuevos, el derivado del nombre en POSGold
       - fotos: {POSGOLD_BASE}/Files/Images/{archivo}, verificadas por
         HTTP (muestra diaria; --verificar-todas para auditar todo), con
         la foto vieja del cache como respaldo si alguna falla
   8. Genera catalogo.js: el enlace del pedido apunta a ESTA tienda al
      detal ({DETAL_URL}/?producto={slug}), no a la web de mayoristas.
      CONCILIACION dura con POSGold: antes y despues de generar se
      verifica que el catalogo quede 100% con productos ACTIVOS y con
      existencias; si no, aborta SIN tocar lo publicado.
   9. Reportes locales en reportes/: sync_*.csv (todo, con costos: NO
      publicar) y novedades_*.xlsx (Excel para la tienda: inactivos,
      agotados, duplicados, sin precio, sin foto, sin costo...)
  10. Con --publicar: git commit + push -> GitHub Pages republica solo

Uso:
  python sync_posgold.py --dry-run     (prueba sin tocar el catalogo)
  python sync_posgold.py               (carga el catalogo, sin publicar)
  python sync_posgold.py --publicar    (carga y publica en GitHub)
  python sync_posgold.py --verificar-todas   (audita todas las fotos)
"""

import argparse
import csv
import datetime as dt
import glob
import io
import json
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

# ------------------------------------------------------------------
# Configuracion general
# ------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(BASE_DIR, "index.html")
CATALOGO_PATH = os.path.join(BASE_DIR, "catalogo.js")
DIR_DATA = os.path.join(BASE_DIR, "data")
DIR_REPORTES = os.path.join(BASE_DIR, "reportes")
CACHE_IMAGENES = os.path.join(DIR_DATA, "mapa_imagenes.json")
ENV_FILE = os.path.join(BASE_DIR, ".env")

MARCA_INICIO = "/*__TOP300:INICIO__*/"
MARCA_FIN = "/*__TOP300:FIN__*/"
PLACEHOLDER_POSGOLD = "no-disponible"  # nombre de foto "sin imagen" de POSGold

# El certificado del servidor POSGold no siempre cuadra en Windows y el
# script solo hace consultas de lectura: se desactiva la verificacion.
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sync-posgold/1.0"

# Categorias de POSGold -> slugs de categoria del index.html
CATEGORIAS_POSGOLD = {
    "JUGUETERIA": "jugueteria",
    "COLECCION": "colecciones",
    "CACHARRO": "cacharro",
    "SLIME": "slime",
    "PINATERIA": "piñateria",
    "JUEGO DE MESA": "juego-de-mesa",
    "BLOQUES DE CONSTRUCCION": "armatodo",
    "DIDACTICO": "didactico",
    "CARTONERIA": "piñateria",
    "DEPORTIVO": "deporte",
    "BOLSO Y PELUCHES": "peluches",
    "BEBES": "bebe",
    "ESCOLAR": "escolar",
    "TERMOS": "termos",
    "CUBO RUBIK": "cubo-rubik",
    "BURBUJEROS": "burbujeros",
    "ANIMALES DE CAUCHO EN BOLSA": "animales",
    "ARMATODO": "armatodo",
    "LLAVEROS": "jugueteria",
    "MADEROTERAPIA": "hogar",
    "DINOSAURIO": "animales",
}
# Categorias que no son productos de venta (se ocultan siempre)
CATEGORIAS_NO_VENDIBLES = {"HONORARIOS Y SERVICIOS", "ARRENDAMIENTOS"}

# Fallback por palabras clave cuando la categoria exacta no esta mapeada
CLAVES_CATEGORIA = [
    ("PELUCH", "peluches"),
    ("CARRO", "vehiculos"),
    ("VEHICUL", "vehiculos"),
    ("RELOJ", "relojeria"),
    ("TERMO", "termos"),
    ("RUBIK", "cubo-rubik"),
    ("CUBO", "cubo-rubik"),
    ("ANIMAL", "animales"),
    ("ESCOLAR", "escolar"),
    ("DIDACT", "didactico"),
    ("JUEGO DE MESA", "juego-de-mesa"),
    ("BURBUJA", "burbujeros"),
    ("BURBUJER", "burbujeros"),
    ("SLIME", "slime"),
    ("ARENA", "slime"),
    ("HIDROGEL", "slime"),
    ("DEPORT", "deporte"),
    ("PINATA", "piñateria"),
    ("CACHARR", "cacharro"),
    ("COLECC", "colecciones"),
    ("FIGURA", "colecciones"),
    ("MUNECA", "colecciones"),
    ("ANIME", "colecciones"),
    ("BLOQUE", "armatodo"),
    ("ARMATODO", "armatodo"),
    ("ARMABLE", "armatodo"),
    ("BEBE", "bebe"),
    ("TECNOL", "tecnologia"),
    ("AUDIFON", "tecnologia"),
    ("TECLADO", "tecnologia"),
    ("HOGAR", "hogar"),
    ("COCINA", "hogar"),
]

# ------------------------------------------------------------------
# Utilidades
# ------------------------------------------------------------------


def log(msg):
    print(msg, flush=True)


def sin_acentos(txt):
    txt = unicodedata.normalize("NFD", txt or "")
    return "".join(c for c in txt if unicodedata.category(c) != "Mn").upper()


def cargar_env(ruta=ENV_FILE):
    env = {}
    if not os.path.exists(ruta):
        log("ERROR: falta el archivo .env (credenciales POSGold).")
        log("       Crea .env en la raiz del proyecto con POSGOLD_BASE,")
        log("       POSGOLD_TOKEN_USER y POSGOLD_TOKEN_PASS (ver info taiwan.txt).")
        sys.exit(1)
    with open(ruta, encoding="utf-8-sig") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def http_texto(url, headers=None, timeout=90, metodo="GET", datos=None,
               reintentos=3, limite_error=300):
    """GET/POST que devuelve texto. Reintenta errores de red y 5xx."""
    hs = {"User-Agent": UA}
    if headers:
        hs.update(headers)
    ultimo_error = None
    for intento in range(reintentos):
        req = urllib.request.Request(url, headers=hs, method=metodo,
                                     data=datos.encode("utf-8") if datos else None)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.status, r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            cuerpo = ""
            try:
                cuerpo = e.read().decode("utf-8", errors="replace")[:limite_error]
            except Exception:
                pass
            if e.code >= 500 and intento < reintentos - 1:
                time.sleep(2 * (intento + 1))
                continue
            return e.code, cuerpo
        except Exception as e:  # timeout, conexion caida...
            ultimo_error = e
            if intento < reintentos - 1:
                time.sleep(2 * (intento + 1))
                continue
            raise
    raise RuntimeError("http_texto fallo: " + str(ultimo_error))


# ------------------------------------------------------------------
# PASO 1-2: JWT fresco
# ------------------------------------------------------------------


def login_jwt(env):
    qs = urllib.parse.urlencode({"user": env["POSGOLD_TOKEN_USER"],
                                "Password": env["POSGOLD_TOKEN_PASS"]})
    url = f"{env['POSGOLD_BASE']}/apiGold/Login_Api/LoginJWTToken?{qs}"
    codigo, texto = http_texto(url, metodo="POST", datos="{}", timeout=60)
    if codigo != 200:
        log(f"ERROR: login JWT respondio {codigo}: {texto[:200]}")
        sys.exit(1)
    data = json.loads(texto)
    if not data.get("Status") or not data.get("Token"):
        log("ERROR: POSGold no entrego token: " + str(data)[:200])
        sys.exit(1)
    return data["Token"]


# ------------------------------------------------------------------
# PASO 3: productos de POSGold (bodega configurada)
# ------------------------------------------------------------------


def consultar_productos(env, jwt):
    productos = []
    pagina = 1
    total = None
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
        url = f"{env['POSGOLD_BASE']}/apiGold/ProductoApi/GetProduct_V6?{urllib.parse.urlencode(params)}"
        codigo, texto = http_texto(url, headers={"Authorization": "Bearer " + jwt}, timeout=180)
        if codigo != 200:
            log(f"ERROR: consulta de productos respondio {codigo}: {texto[:200]}")
            sys.exit(1)
        data = json.loads(texto)
        if not data.get("Status"):
            log("ERROR: POSGold rechazo la consulta: " + str(data.get("Msj"))[:200])
            sys.exit(1)
        lote = data.get("Datos") or []
        productos.extend(lote)
        total = data.get("Conteo", len(productos))
        log(f"  pagina {pagina}: {len(lote)} productos (total declarado: {total})")
        if len(productos) >= total or not lote:
            break
        pagina += 1
    return productos


# ------------------------------------------------------------------
# PASO 4: costos del ultimo Excel exportado de POSGold
# ------------------------------------------------------------------


def encontrar_excel_costos(ruta_explicita=None):
    if ruta_explicita:
        if not os.path.exists(ruta_explicita):
            log("ERROR: no existe el Excel de costos: " + ruta_explicita)
            sys.exit(1)
        return ruta_explicita
    candidatos = sorted(glob.glob(os.path.join(BASE_DIR, "PRODUCTOS*.xlsx")),
                        key=os.path.getmtime, reverse=True)
    return candidatos[0] if candidatos else None


def cargar_costos(ruta_excel, campo="costopromedio"):
    try:
        import openpyxl
    except ImportError:
        log("ERROR: falta openpyxl (pip install openpyxl) para leer los costos.")
        sys.exit(1)
    wb = openpyxl.load_workbook(ruta_excel, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    filas = ws.iter_rows(values_only=True)
    enc = list(next(filas))
    idx = {k: enc.index(k) for k in ("productoid", "costo", "costopromedio", "costoultimo")}
    costos = {}
    for f in filas:
        pid = f[idx["productoid"]]
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if campo not in idx:
            continue
        val = f[idx[campo]]
        try:
            val = float(val)
        except (TypeError, ValueError):
            val = None
        if val and val > 0:
            costos[pid] = val
    wb.close()
    return costos


# ------------------------------------------------------------------
# Categorias y normalizacion
# ------------------------------------------------------------------


def categoria_web(categoria_posgold):
    cat = sin_acentos((categoria_posgold or "").strip())
    if not cat or cat in CATEGORIAS_NO_VENDIBLES:
        return None
    for origen, slug in CATEGORIAS_POSGOLD.items():
        if sin_acentos(origen) == cat:
            return slug
    for clave, slug in CLAVES_CATEGORIA:
        if clave in cat:
            return slug
    return "jugueteria"


def codigo_limpio(codigo):
    """Los codigos a veces llegan envueltos en % (comodines de POSGold)."""
    c = str(codigo or "").strip().strip("%").strip()
    return c or None


# ------------------------------------------------------------------
# Cache de imagenes (data/mapa_imagenes.json)
# ------------------------------------------------------------------


def cargar_cache():
    if os.path.exists(CACHE_IMAGENES):
        try:
            with open(CACHE_IMAGENES, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"actualizado": None, "por_codigo": {}}


def guardar_cache(cache):
    os.makedirs(DIR_DATA, exist_ok=True)
    cache["actualizado"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(CACHE_IMAGENES, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def sembrar_cache_desde_catalogo(cache):
    """Lee el catalogo.js actual y guarda codigo -> {slug, imagen}: es la
    mejor semilla porque ya paso QA en corridas anteriores."""
    if not os.path.exists(CATALOGO_PATH):
        return 0
    with open(CATALOGO_PATH, encoding="utf-8") as f:
        contenido = f.read()
    bloque = contenido[contenido.find("const CATALOGO_BASE"):contenido.find("const TOP_UTILIDAD_IDS")]
    if "const CATALOGO_BASE" not in contenido or not bloque:
        return 0
    por_codigo = cache.setdefault("por_codigo", {})
    nuevos = 0
    posiciones = [mm.start() for mm in re.finditer(r'id:\s*"([^"]+)"', bloque)]
    for i, pos in enumerate(posiciones):
        fin = posiciones[i + 1] if i + 1 < len(posiciones) else len(bloque)
        trozo = bloque[pos:fin]
        slug = re.match(r'id:\s*"([^"]+)"', trozo).group(1)
        sku = re.search(r'sku:\s*"([^"]*)"', trozo)
        img = re.search(r'image:\s*"([^"]+)"', trozo)
        nom = re.search(r'name:\s*"([^"]*)"', trozo)
        if not (sku and img):
            continue
        codigo = sku.group(1).strip()
        imagen = img.group(1)
        if not codigo or imagen.startswith("data:image/svg"):
            continue
        ent = por_codigo.get(codigo) or {}
        if not ent.get("slug") and slug:
            ent["slug"] = slug
        if not ent.get("imagen") and imagen:
            ent["imagen"] = imagen
        if nom and not ent.get("nombre"):
            ent["nombre"] = nom.group(1)
        if ent:
            por_codigo[codigo] = ent
            nuevos += 1
    return nuevos


def sembrar_cache_desde_dropi(cache):
    """El Excel DROPI enriquecido trae codigo -> URL de foto de la web."""
    candidatos = glob.glob(os.path.join(BASE_DIR, "DROPI_carga_masiva*ENRIQUECIDO*.xlsx"))
    if not candidatos:
        return 0
    try:
        import openpyxl
        wb = openpyxl.load_workbook(candidatos[0], read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        filas = ws.iter_rows(values_only=True)
        enc = list(next(filas))
        i_sku = enc.index("SKU")
        i_img = enc.index("IMAGENES")
        por_codigo = cache.setdefault("por_codigo", {})
        nuevos = 0
        for f in filas:
            sku = str(f[i_sku] or "").strip()
            imgs = str(f[i_img] or "").strip()
            if not sku or not imgs or "storage/products/" not in imgs:
                continue
            url = imgs.split(",")[0].strip()
            if "/full/" in url:
                url = url.replace("/full/", "/thumb/")
            ent = por_codigo.setdefault(sku, {})
            # La foto de la web mayorista queda como RESPALDO (imagen_web):
            # la principal (imagen) ahora sale de Files/Images de POSGold.
            if not ent.get("imagen_web"):
                ent["imagen_web"] = url
                nuevos += 1
        wb.close()
        return nuevos
    except Exception as e:
        log("  aviso: no se pudo sembrar desde DROPI: " + str(e)[:120])
        return 0


# ------------------------------------------------------------------
# Slug y fotos DIRECTOS de POSGold (la web mayorista ya no participa)
#
# La vitrina NO usa las fotos originales de POSGold: ese servidor tarda
# ~2 segundos por foto (y varias a la vez lo saturan: 129 descargas
# simultaneas demoraban el heroe 12s). La corrida genera UNA VEZ una
# miniatura WebP por foto y la publica en el repo (assets/fotos/):
# GitHub Pages las sirve por CDN en milisegundos y pesan ~8 veces menos.
# El lightbox sube a la foto original de POSGold bajo demanda (fulls[]).
# ------------------------------------------------------------------

DIR_MINIATURAS = os.path.join(BASE_DIR, "assets", "fotos")
MINI_TAMANO = (640, 640)   # lado mayor de la miniatura, en pixeles
MINI_CALIDAD = 72          # calidad WebP de la miniatura


def slug_posgold(nombre):
    """Slug determinista derivado del nombre del producto en POSGold:
    minusculas, sin acentos, separado por guiones. Para productos nuevos
    reemplaza el slug que antes se raspaba de la web mayorista; para los
    que ya estan en el cache se conserva el slug conocido (favoritos,
    temporada y destacado dependen de que no cambie)."""
    txt = unicodedata.normalize("NFD", nombre or "")
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    txt = re.sub(r"[^a-z0-9]+", "-", txt.lower()).strip("-")
    return txt[:120].strip("-") or None


def http_binario(url, timeout=90):
    """GET binario (descarga de fotos) con un reintento de red."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for intento in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read()
        except urllib.error.HTTPError:
            raise          # 404/403: la foto no existe; no es una caida
        except Exception:
            if intento:
                raise
            time.sleep(1)
    return b""


def miniatura_webp(url, archivo, refrescar=False):
    """Miniatura WebP servida por el repo para la foto {archivo} de POSGold.
    La genera UNA VEZ (si no existe) y devuelve (ruta_publica, fallo_red):
    - ruta 'assets/fotos/xxx.webp' lista para el catalogo, o None si la
      foto no se pudo descargar y no habia miniatura vieja.
    - fallo_red=True cuando el servidor no respondio (cuenta para el
      cortacircuitos); si ya existia miniatura, se conserva y no es fallo.
    Con refrescar=True la regenera (muestra diaria / --verificar-todas):
    si la descarga falla, conserva la ultima version buena."""
    base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(os.path.basename(archivo))[0])
    destino = os.path.join(DIR_MINIATURAS, base + ".webp")
    publica = "assets/fotos/" + base + ".webp"
    if os.path.exists(destino) and not refrescar:
        return publica, False
    try:
        from PIL import Image
        cuerpo = http_binario(url, timeout=60)
        im = Image.open(io.BytesIO(cuerpo))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail(MINI_TAMANO, Image.LANCZOS)
        os.makedirs(DIR_MINIATURAS, exist_ok=True)
        tmp = destino + ".tmp"
        im.save(tmp, "WEBP", quality=MINI_CALIDAD, method=6)
        os.replace(tmp, destino)   # nunca deja una miniatura a medias
        return publica, False
    except urllib.error.HTTPError:
        return (publica, False) if os.path.exists(destino) else (None, False)
    except Exception:
        return (publica, False) if os.path.exists(destino) else (None, True)


def fotos_del_producto(env, item, refrescar=False):
    """(miniaturas, originales, fallo_red) de las fotos del producto:
    se descarta el placeholder 'Imagen-no-disponible.jpg' y las repetidas.
    miniaturas[i] es la URL servida por el repo (lista para el catalogo)
    y originales[i] la de POSGold para el lightbox (mismo indice)."""
    base = env["POSGOLD_BASE"].rstrip("/") + "/Files/Images/"
    minis, origs, fallo = [], [], False
    vistos = set()
    for x in (item.get("Imagenes") or []):
        a = str(x).strip()
        if not a or PLACEHOLDER_POSGOLD in a.lower() or a in vistos:
            continue
        vistos.add(a)
        url = base + urllib.parse.quote(a)
        mini, red = miniatura_webp(url, a, refrescar)
        if red:
            fallo = True
        if mini:
            minis.append(mini)
            origs.append(url)
    return minis, origs, fallo


# ------------------------------------------------------------------
# Generacion del bloque JS
# ------------------------------------------------------------------


def js_texto(valor):
    return json.dumps(valor, ensure_ascii=False)


def generar_catalogo_js(env, seleccion, cantidad):
    """Contenido completo de catalogo.js (archivo independiente, indentacion
    desde columna 0). El index.html lo carga con <script src=catalogo.js>:
    el navegador lo cachea aparte y el index se mantiene liviano aunque el
    inventario completo entre al catalogo.

    El slug (id) de cada producto viene de POSGold — el conocido del cache
    o, para productos nuevos, el derivado del nombre en POSGold — y el
    enlace del pedido apunta a ESTA tienda al detal con ese slug
    (?producto=...), no a la pagina de mayoristas.

    Cada producto viaja con 'stock' (existencias Disponibles de la bodega
    en POSGold): las REGLAS del panel de administración lo usan para
    decidir, entre duplicados exactos, cual referencia mostrar. Viaja tal
    cual: es el dato que necesita la vitrina para elegir bien.

    Si POSGold define PRECIO 5 para el producto, viaja tambien 'price5'
    (tal cual, con IVA) como campo ADITIVO: los canales del grupo (JUGA)
    lo usan como precio base. Esta vitrina NO lo lee: su precio de venta
    sigue siendo el Precio 2 (price). Sin Precio 5 definido, el campo no
    viaja y cada canal decide su respaldo."""
    fecha = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    detal = (env.get("DETAL_URL") or "https://saldosjugueteriataiwan.com").rstrip("/")
    L = []
    L.append("// ==================================================================")
    L.append(f"// CATALOGO DE JUGUETERIA TAIWAN — generado automaticamente el {fecha}")
    L.append("// por sync_posgold.py (cron). Todo el inventario elegible de")
    L.append("// POSGold que cumple la regla de la tienda: activo + existencias")
    L.append("// en bodega + foto real. El producto que deja de cumplir se")
    L.append("// oculta solo en la proxima corrida. NO editar a mano: este")
    L.append("// archivo se regenera completo en cada sincronizacion.")
    L.append("// Cada producto trae 'stock' (existencias en POSGold): las")
    L.append("// reglas de la tienda del panel lo usan para deduplicar.")
    L.append("// ==================================================================")
    L.append("const CATALOGO_BASE = [")
    # Cada producto en UNA sola linea compacta (sin indentacion): con el
    # inventario completo el archivo con indentacion pesaba ~760 bytes por
    # producto; compactado pesa ~430 (43% menos) y el navegador parsea
    # mas rapido. La estructura es identica — solo menos espacios.
    for p in seleccion:
        precio = p["precio"]
        compare = p["compare"]
        stock = int(p.get("disponible") or 0)
        # Precio 5 del sistema: campo aditivo para los canales del grupo
        # (JUGA lo usa como base). 0 = POSGold no lo define: no viaja.
        p5 = int(p.get("precio5") or 0)
        campo_p5 = f"price5:{p5}," if p5 > 0 else ""
        galeria = p.get("imagenes") or [p["imagen"]]
        # Originales de POSGold para el lightbox (fulls), mismo indice que
        # la galeria de miniaturas; si faltara, el frente cae a la miniura
        originales = p.get("fulls") or list(galeria)
        L.append(
            f"{{id:{js_texto(p['slug'])},name:{js_texto(p['nombre'])},"
            f"description:{js_texto('SKU: ' + p['codigo'])},"
            f"image:{js_texto(p['imagen'])},images:[{','.join(js_texto(x) for x in galeria)}],"
            f"fulls:[{','.join(js_texto(x) for x in originales)}],"
            f"category:{js_texto(p['categoria_web'])},sku:{js_texto(p['codigo'])},"
            f"url:{js_texto(detal + '/?producto=' + p['slug'])},"
            f"price:{precio},comparePrice:{compare},stock:{stock},"
            f"{campo_p5}"
            f"sizeGroups:[{{key:'unidad',label:'Unidades',options:["
            f"{{id:'unidad',label:'Unidad',price:{precio}}}]}}]}},"
        )
    L.append("];")
    # Carrusel "Los imperdibles": los 16 productos con mayor UTILIDAD
    # (plata absoluta que deja cada venta). Viajan SOLO los ids en ese
    # orden — costos y utilidades exactas viven en el reporte local y
    # jamas se publican. El frente arma la fila con los que esten en el
    # catalogo (initTopUtilidadCarousel).
    top_utilidad = sorted(seleccion, key=lambda p: -(p.get("utilidad") or 0))[:16]
    L.append("")
    L.append("// Carrusel 'Los imperdibles': IDs ordenados por utilidad")
    L.append("// (plata que deja cada venta). Lo decide el cron con los costos")
    L.append("// reales; esos costos nunca viajan al sitio publico.")
    L.append("const TOP_UTILIDAD_IDS = [")
    for p in top_utilidad:
        L.append(f"    {js_texto(p['slug'])},")
    L.append("];")
    return "\n".join(L)


def validar_js_nodo(bloque_js):
    """node --check del bloque generado: si hay error de sintaxis, aborta."""
    nodo = shutil.which("node")
    if not nodo:
        log("  aviso: node no disponible, se salta la validacion de sintaxis")
        return True
    ruta = os.path.join(os.environ.get("TEMP", DIR_REPORTES), "jt_top300_check.js")
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write(bloque_js)
    r = subprocess.run([nodo, "--check", ruta], capture_output=True, text=True)
    if r.returncode != 0:
        log("ERROR: sintaxis JS invalida, NO se toca el index:")
        log(r.stderr[:800])
        return False
    return True


def escribir_catalogo(contenido):
    """Escribe catalogo.js (el catálogo vive en archivo propio: el navegador
    lo cachea aparte y el index.html se mantiene liviano). El historial de
    git es el respaldo: cualquier version anterior es recuperable.

    DELTA DETECTION: si el contenido (sin el timestamp del header) es
    identico al archivo existente, solo se actualiza el header — el
    cuerpo del commit es identico y git lo comprime casi a cero. Esto es
    critico con el inventario completo: 4 corridas al dia que no cambian
    nada no deben engordar el historial del repo."""
    import hashlib
    nuevo_cuerpo = contenido[contenido.find("const CATALOGO_BASE"):]
    if os.path.exists(CATALOGO_PATH):
        viejo = open(CATALOGO_PATH, encoding="utf-8").read()
        viejo_cuerpo = viejo[viejo.find("const CATALOGO_BASE"):]
        if viejo_cuerpo == nuevo_cuerpo:
            # el catalogo es identico: solo refrescar el header (fecha)
            with open(CATALOGO_PATH, "w", encoding="utf-8", newline="\n") as f:
                f.write(contenido + "\n")
            return "sin-cambios"
    with open(CATALOGO_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(contenido + "\n")
    return "actualizado"


def index_referencia_catalogo():
    """El index debe cargar catalogo.js ANTES del script principal."""
    if not os.path.exists(INDEX):
        return False
    with open(INDEX, encoding="utf-8") as f:
        return 'src="catalogo.js"' in f.read() or "src='catalogo.js'" in f.read()


# ------------------------------------------------------------------
# Reporte local (contiene costos: nunca publicar)
# ------------------------------------------------------------------


def leer_skus_publicados():
    """Codigos (SKU) del catalogo.js publicado en este checkout: la
    conciliacion contra POSGold parte de lo que la tienda muestra AHORA."""
    if not os.path.exists(CATALOGO_PATH):
        return set()
    with open(CATALOGO_PATH, encoding="utf-8") as f:
        return set(re.findall(r'sku:"([^"]+)"', f.read()))


# Tipos de novedad -> nombre de hoja del Excel (por prefijo del motivo)
HOJAS_NOVEDAD = (
    ("id duplicado", "Duplicados"),
    ("inactivo", "Inactivos POSGold"),
    ("sin existencias", "Sin existencias"),
    ("sin foto", "Sin foto"),
    ("sin precio", "Sin precio"),
    ("sin costo", "Sin costo en Excel"),
    ("rentabilidad", "Rentabilidad negativa"),
    ("categoria no vendible", "Categoria no vendible"),
)


def hoja_novedad(motivo):
    m = (motivo or "").lower()
    for prefijo, hoja in HOJAS_NOVEDAD:
        if prefijo in m:
            return hoja
    return "Otras novedades"


def escribir_reporte_excel(filas, publicados):
    """Excel de NOVEDADES para la tienda: todo producto que no publica y
    por que (inactivos, agotados, duplicados, sin precio, sin foto, sin
    costo, rentabilidad negativa...). Hoja Resumen + una hoja por tipo.
    Marca que productos seguian publicados en la tienda al correr: son
    los que salen del catalogo en esta corrida."""
    try:
        import openpyxl
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        log("AVISO: falta openpyxl: no se genera el Excel de novedades.")
        return None
    novedades = [f for f in filas if f.get("estado") == "OCULTO"]
    if not novedades:
        return None
    negrita = Font(bold=True)
    wb = openpyxl.Workbook()

    resumen = wb.active
    resumen.title = "Resumen"
    conteo = {}
    for f in novedades:
        hoja = hoja_novedad(f.get("motivo"))
        conteo[hoja] = conteo.get(hoja, 0) + 1
    salen = sum(1 for f in novedades if f.get("codigo") in publicados)
    resumen.append(["NOVEDADES POSGOLD — Jugueteria Taiwan"])
    resumen.cell(row=1, column=1).font = negrita
    resumen.append(["Corrida: " + dt.datetime.now().strftime("%Y-%m-%d %H:%M")])
    resumen.append([])
    for etiqueta, valor in (
        ("Productos analizados en POSGold", len(filas)),
        ("Publicados en la tienda al correr", len(publicados)),
        ("Publicados que ya no cumplen (salen en esta corrida)", salen),
        ("Productos que entran", sum(1 for f in filas if f.get("estado") == "ENTRA")),
        ("Total novedades (no publican)", len(novedades)),
    ):
        resumen.append([etiqueta, valor])
        resumen.cell(row=resumen.max_row, column=1).font = negrita
    resumen.append([])
    resumen.append(["Tipo de novedad", "Productos"])
    resumen.cell(row=resumen.max_row, column=1).font = negrita
    resumen.cell(row=resumen.max_row, column=2).font = negrita
    for hoja, n in sorted(conteo.items(), key=lambda kv: -kv[1]):
        resumen.append([hoja, n])
    resumen.column_dimensions["A"].width = 52
    resumen.column_dimensions["B"].width = 12

    hojas = {}
    for f in novedades:
        hojas.setdefault(hoja_novedad(f.get("motivo")), []).append(f)
    encabezado = ["productoid", "codigo", "nombre", "categoria_posgold",
                  "categoria_web", "disponible", "precio_detal", "novedad",
                  "estaba_publicado"]
    for hoja, filas_hoja in sorted(hojas.items()):
        ws = wb.create_sheet(hoja[:31])
        ws.append(encabezado)
        for f in sorted(filas_hoja, key=lambda x: (x.get("codigo") or "")):
            ws.append([
                f.get("productoid") or "", f.get("codigo") or "",
                f.get("nombre") or "", f.get("categoria_posgold") or "",
                f.get("categoria_web") or "", f.get("disponible") or "",
                f.get("precio_detal_con_iva") or "", f.get("motivo") or "",
                "SI" if f.get("codigo") in publicados else "",
            ])
        for c in ws[1]:
            c.font = negrita
        for i, ancho in enumerate((12, 16, 52, 26, 16, 12, 13, 36, 16), 1):
            ws.column_dimensions[get_column_letter(i)].width = ancho
        ws.freeze_panes = "A2"

    ruta = os.path.join(DIR_REPORTES, "novedades_" +
                        dt.datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".xlsx")
    wb.save(ruta)
    return ruta


def escribir_reporte(filas, publicados=frozenset()):
    os.makedirs(DIR_REPORTES, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    ruta = os.path.join(DIR_REPORTES, f"sync_{stamp}.csv")
    campos = ["estado", "motivo", "productoid", "codigo", "nombre", "categoria_posgold",
              "categoria_web", "precio_detal_con_iva", "precio_base_sin_iva", "costo",
              "utilidad", "margen_costo_pct", "margen_precio_pct", "disponible",
              "destacado", "slug_web", "imagen", "origen_imagen"]
    for fila in filas:  # normalizar: la seleccion guarda el slug en "slug"
        if "slug" in fila and not fila.get("slug_web"):
            fila["slug_web"] = fila["slug"]
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in campos})
    ruta_xlsx = escribir_reporte_excel(filas, publicados)
    if ruta_xlsx:
        log("Excel de novedades: " + ruta_xlsx)
    return ruta


# ------------------------------------------------------------------
# Publicar en GitHub (solo con --publicar)
# ------------------------------------------------------------------


def publicar_git(env, cantidad):
    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                      capture_output=True, cwd=BASE_DIR).returncode != 0:
        log("AVISO: esta carpeta no es un repositorio git; no se publico.")
        log("       Conecta el repo (git init + remote add + push) y repite.")
        return False
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    len_x = f"{cantidad:,}"
    for cmd in (
        ["git", "add", "index.html"],
        ["git", "add", "catalogo.js"],
        ["git", "add", "data/mapa_imagenes.json"],
        ["git", "add", "assets/fotos"],
        ["git", "commit", "-m", f"sync: catalogo de POSGold ({len_x} productos, {stamp})"],
    ):
        r = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True)
        if r.returncode != 0 and "commit" not in cmd[1]:
            log("AVISO git: " + (r.stderr or r.stdout).strip()[:200])
    # En los runners de GitHub Actions el checkout queda en HEAD separado:
    # se empuja explicitamente HEAD a la rama principal del repo.
    rama = os.environ.get("GITHUB_REF_NAME") or "main"
    r = subprocess.run(["git", "push", "origin", "HEAD:" + rama],
                       cwd=BASE_DIR, capture_output=True, text=True)
    if r.returncode != 0:
        log("AVISO git push: " + (r.stderr or r.stdout).strip()[:300])
        return False
    return True


# ------------------------------------------------------------------
# Proceso principal
# ------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description="Carga el TOP de POSGold al index.html")
    ap.add_argument("--dry-run", action="store_true", help="no escribe el index ni publica")
    ap.add_argument("--publicar", action="store_true", help="git commit + push tras cargar")
    ap.add_argument("--costos", help="ruta del Excel de costos (default: PRODUCTOS*.xlsx mas reciente)")
    ap.add_argument("--limite", type=int, help="cantidad de productos (default: .env TOP300_CANTIDAD)")
    ap.add_argument("--sin-online", action="store_true",
                    help="obsoleto (antes raspeaba la web mayorista): ahora equivale a --sin-verificar-fotos")
    ap.add_argument("--sin-verificar-fotos", action="store_true",
                    help="saltar el refresco diario de miniaturas (solo genera las faltantes)")
    ap.add_argument("--verificar-todas", action="store_true",
                    help="regenerar TODAS las miniaturas desde POSGold (migraciones y auditorias)")
    args = ap.parse_args()
    if args.sin_online:
        args.sin_verificar_fotos = True

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    env = cargar_env()
    cantidad = args.limite or int(env.get("TOP300_CANTIDAD", "99999"))
    campo_costo = env.get("COSTO_CAMPO", "costopromedio")
    criterio = env.get("RANKING", "margen_costo")
    incluir_destacados = env.get("INCLUIR_DESTACADOS", "true").lower() == "true"
    destacados_ids = [s.strip() for s in env.get("DESTACADOS_IDS", "").split(",") if s.strip()]

    log("=" * 66)
    log("SYNC POSGOLD -> catalogo.js | " + dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log("=" * 66)

    # --- costos ---
    excel = encontrar_excel_costos(args.costos)
    if not excel:
        log("ERROR: no hay Excel de costos (PRODUCTOS *.xlsx) en la carpeta.")
        log("       Exporta el inventario desde POSGold con costo y vuelve a correr.")
        sys.exit(1)
    log(f"\n[1/6] Costos desde: {os.path.basename(excel)} (campo: {campo_costo})")
    costos = cargar_costos(excel, campo_costo)
    log(f"      {len(costos)} productos con costo conocido")

    # --- POSGold ---
    log("\n[2/6] Login POSGold y consulta de productos...")
    jwt = login_jwt(env)
    productos_api = consultar_productos(env, jwt)
    log(f"      {len(productos_api)} productos en bodega {env.get('POSGOLD_BODEGAID')}")

    # --- cache de imagenes ---
    log("\n[3/6] Cache de fotos y slugs...")
    cache = cargar_cache()
    n1 = sembrar_cache_desde_catalogo(cache)
    n2 = sembrar_cache_desde_dropi(cache)
    log(f"      sembrado desde catalogo.js: {n1} | desde DROPI: {n2} | cache total: {len(cache['por_codigo'])}")

    # --- conciliacion: lo que la tienda publica hoy, contra POSGold en vivo ---
    log("\n[4/6] Conciliacion con lo publicado, filtros y rentabilidad...")
    publicados_antes = leer_skus_publicados()
    por_codigo_api = {}
    for it in productos_api:
        c = codigo_limpio(it.get("Codigo") or it.get("Producto_cod"))
        if c:
            por_codigo_api.setdefault(c, it)
    if publicados_antes:
        razones = {}
        for c in publicados_antes:
            it = por_codigo_api.get(c)
            if it is None:
                razon = "ya no esta en la bodega consultada"
            elif it.get("Activo") is not True:
                razon = "inactivo en POSGold"
            elif (it.get("Disponible") or 0) <= 0:
                razon = "sin existencias"
            else:
                continue
            razones[razon] = razones.get(razon, 0) + 1
        if razones:
            log("      CONCILIACION: " + str(len(publicados_antes)) + " publicados; "
                + str(sum(razones.values())) + " ya no cumplen (salen en esta corrida): "
                + ", ".join(r + " x" + str(n) for r, n in sorted(razones.items(), key=lambda kv: -kv[1])))
        else:
            log("      CONCILIACION: los " + str(len(publicados_antes)) +
                " productos publicados siguen activos con existencias en POSGold")

    # --- analisis y filtros ---
    analisis = []
    contadores = {}
    for it in productos_api:
        pid = it.get("Productoid")
        nombre = (it.get("Producto") or "").strip()
        codigo = codigo_limpio(it.get("Codigo") or it.get("Producto_cod"))
        fila = {"productoid": pid, "codigo": codigo or "", "nombre": nombre,
                "categoria_posgold": it.get("Categoria") or "", "destacado": False,
                "disponible": it.get("Disponible") or 0}
        cat = categoria_web(it.get("Categoria"))
        fila["categoria_web"] = cat or ""
        if not cat:
            fila.update({"estado": "OCULTO", "motivo": "categoria no vendible"})
        elif it.get("Activo") is not True:
            fila.update({"estado": "OCULTO", "motivo": "inactivo en POSGold"})
        elif (it.get("Disponible") or 0) <= 0:
            fila.update({"estado": "OCULTO", "motivo": "sin existencias en bodega"})
        elif not it.get("Imagenes") or all(
                PLACEHOLDER_POSGOLD in str(x).lower() for x in it.get("Imagenes")):
            fila.update({"estado": "OCULTO", "motivo": "sin foto en POSGold"})
        elif not (it.get("Precio2") or 0) > 0:
            fila.update({"estado": "OCULTO", "motivo": "sin precio detal"})
        elif pid not in costos:
            fila.update({"estado": "OCULTO", "motivo": f"sin costo en Excel ({campo_costo})"})
        else:
            iva = float(it.get("ProductoImpuestoPorcentaje") or 0)
            p2 = float(it["Precio2"])
            base = p2 / (1 + iva / 100) if iva > 0 else p2
            costo = costos[pid]
            utilidad = base - costo
            fila.update({
                "precio_detal_con_iva": int(round(p2)),
                "precio_base_sin_iva": round(base, 1),
                "costo": costo,
                "utilidad": round(utilidad, 1),
                "margen_costo_pct": round(utilidad / costo * 100, 2) if costo else None,
                "margen_precio_pct": round(utilidad / base * 100, 2) if base else None,
            })
            if utilidad <= 0:
                fila.update({"estado": "OCULTO", "motivo": "rentabilidad negativa"})
            else:
                fila["estado"] = "CANDIDATO"
                fila["motivo"] = ""
        contadores[fila["motivo"] or "candidatos"] = contadores.get(fila["motivo"] or "candidatos", 0) + 1
        analisis.append(fila)

    candidatos = [f for f in analisis if f["estado"] == "CANDIDATO"]
    if criterio == "margen_precio":
        clave = lambda f: f["margen_precio_pct"] or 0
    elif criterio == "utilidad":
        clave = lambda f: f["utilidad"] or 0
    else:
        clave = lambda f: f["margen_costo_pct"] or 0
    candidatos.sort(key=lambda f: (-clave(f), -(f["disponible"] or 0), f["nombre"]))
    log(f"      {len(candidatos)} cumplen todas las reglas (ranking: {criterio})")
    for motivo, n in sorted(contadores.items(), key=lambda kv: -kv[1]):
        log(f"        {n:5}  {motivo}")

    # --- resolucion de fotos y seleccion ---
    log("\n[5/6] Seleccion del TOP " + str(cantidad) + " y resolucion de fotos...")

    seleccion = []
    slugs_usados = {}

    # Cortacircuitos: si POSGold cae o bloquea al runner, TODAS las
    # verificaciones de foto fallan seguidas (errores de red o 5xx; los
    # 404 de fotos que faltan NO cuentan). Antes el script se arrastraba
    # por horas (re-resolucion producto por producto con reintentos)
    # hasta que el timeout del workflow lo mataba. Ahora: 25 fallos
    # seguidos = aborto limpio en minutos con diagnostico.
    verificacion = {"fallos_seguidos": 0, "motivos": {}, "muestras": []}
    FALLOS_ABORTAR = 25

    def nota_fallo(motivo, muestra=None):
        verificacion["fallos_seguidos"] += 1
        verificacion["motivos"][motivo] = verificacion["motivos"].get(motivo, 0) + 1
        if muestra and len(verificacion["muestras"]) < 6:
            verificacion["muestras"].append(muestra)

    def abortar_si_web_caida(contexto):
        if verificacion["fallos_seguidos"] < FALLOS_ABORTAR:
            return
        log("")
        log(f"ABORTADO ({contexto}): {verificacion['fallos_seguidos']} intentos "
            f"SEGUIDOS sin poder verificar o encontrar fotos.")
        log("El servidor de POSGold no esta respondiendo desde este entorno")
        log("(sitio caido, en mantenimiento o bloqueando la IP del runner).")
        resumen = ", ".join(f"{m} x{n}" for m, n in
                            sorted(verificacion["motivos"].items(), key=lambda kv: -kv[1]))
        log("Motivos: " + resumen)
        for m in verificacion["muestras"][:6]:
            log("  muestra: " + m)
        log("El catalogo NO se toco: la tienda sigue con la ultima version publicada.")
        log("Reintentar manualmente desde la pestana Actions o esperar la")
        log("siguiente corrida programada.")
        escribir_reporte(analisis, publicados_antes)
        sys.exit(1)

    def resolver_producto(fila, slug_fijo=None, refrescar=False):
        """Resuelve slug y fotos de un candidato usando SOLO POSGold y el
        cache local — la web mayorista ya no participa.
        - Slug: el conocido del cache (estable: favoritos, temporada y
          producto destacado dependen de que no cambie) o, para productos
          nuevos, el derivado del nombre en POSGold (slug_posgold).
        - Fotos: miniaturas WebP servidas por el propio repo (assets/fotos,
          generadas UNA VEZ desde POSGold; el original viaja en fulls[]
          para que el lightbox suba a la version grande bajo demanda).
          La descarga ES la verificacion: la foto que no existe no entra.
        Devuelve (slug, imagen, origen) o (None, None, motivo).
        Solo hace IO: no toca la seleccion."""
        try:
            # cortacircuitos ya disparado: no iniciar trabajo nuevo
            if verificacion["fallos_seguidos"] >= FALLOS_ABORTAR and not slug_fijo:
                return None, None, "omitido (POSGold caido)"
            it = por_codigo_api.get(fila["codigo"]) or {}
            por_codigo = cache.setdefault("por_codigo", {})
            ent = por_codigo.get(fila["codigo"]) or {}

            # --- slug: el conocido, o el derivado del nombre de POSGold ---
            slug = slug_fijo or ent.get("slug")
            if not slug:
                slug = slug_posgold(fila["nombre"])
            if not slug:
                return None, None, "sin nombre para derivar el slug"
            if not slug_fijo and slug in slugs_usados:
                base = slug_posgold(fila["nombre"]) or slug
                n = 1
                while slug in slugs_usados and n < 60:
                    n += 1
                    slug = f"{base}-{n}"
                if slug in slugs_usados:
                    return None, None, "id duplicado (mismo nombre en POSGold)"

            # --- fotos: miniaturas del repo, originales para el lightbox ---
            minis, origs, fallo_red = fotos_del_producto(env, it, refrescar)
            if fallo_red:
                nota_fallo("descarga de foto fallida (POSGold no responde)",
                           f"{fila['nombre'][:36]}")
                abortar_si_web_caida("descarga de fotos")
            if not minis:
                return None, None, "sin foto utilizable en POSGold"

            por_codigo[fila["codigo"]] = {"slug": slug, "imagen": origs[0],
                                         "nombre": fila["nombre"]}
            fila["imagenes"] = minis
            fila["fulls"] = origs
            return slug, minis[0], "miniatura-repo"
        except Exception as e:
            return None, None, "error de red: " + type(e).__name__

    # destacados primero (si cumplen las mismas reglas)
    if incluir_destacados and destacados_ids:
        slug_a_codigo = {}
        for k, v in cache.get("por_codigo", {}).items():
            if v.get("slug"):
                slug_a_codigo[v["slug"]] = k
        codigos_candidatos = {f["codigo"] for f in candidatos}
        for slug_dest in destacados_ids:
            fila_dest = None
            codigo_dest = slug_a_codigo.get(slug_dest)
            if codigo_dest and codigo_dest in codigos_candidatos:
                fila_dest = next(f for f in candidatos if f["codigo"] == codigo_dest)
            else:
                # productos nuevos: el slug se deriva del nombre de POSGold
                fila_dest = next((f for f in candidatos if f.get("estado") == "CANDIDATO"
                                  and (cache.get("por_codigo", {}).get(f["codigo"], {}).get("slug")
                                       or slug_posgold(f["nombre"])) == slug_dest), None)
            if not fila_dest:
                log(f"  destacado fuera: {slug_dest} (no cumple o no esta en POSGold)")
                continue
            slug, imagen, motivo = resolver_producto(fila_dest, slug_fijo=slug_dest)
            if slug:
                verificacion["fallos_seguidos"] = 0
                fila_dest.update({"estado": "ENTRA", "motivo": "", "slug": slug,
                                  "imagen": imagen, "origen_imagen": motivo, "destacado": True})
                if slug not in slugs_usados:
                    slugs_usados[slug] = fila_dest["codigo"]
                    seleccion.append(fila_dest)
                    log(f"  destacado dentro: {fila_dest['nombre'][:40]}")
            else:
                nota_fallo(motivo, f"{motivo} | {fila_dest['nombre'][:36]}")
                abortar_si_web_caida("destacados")
                log(f"  destacado fuera: {slug_dest} ({motivo})")
        guardar_cache(cache)

    # Refresco diario de miniaturas: una muestra aleatoria re-descarga sus
    # originales de POSGold y regenera las miniaturas — asi una foto que
    # cambiara con el mismo nombre se actualiza sola en unas semanas.
    # --verificar-todas refresca TODO (migraciones y auditorias);
    # --sin-verificar-fotos salta el refresco (solo genera las faltantes).
    tamano_muestra = 0 if getattr(args, "verificar_todas", False) else int(env.get("REFRESCO_MINIATURAS", "40"))
    if args.sin_verificar_fotos:
        tamano_muestra = 0
    if tamano_muestra <= 0 or tamano_muestra >= len(candidatos):
        muestra_pos = set(range(len(candidatos))) if tamano_muestra == 0 else set()
    else:
        muestra_pos = set(random.sample(range(len(candidatos)), tamano_muestra))
    log(f"      miniaturas: faltantes se generan; refresco del dia: {len(muestra_pos)} de "
        f"{len(candidatos)} candidatos" + (" (TODAS)" if args.verificar_todas else ""))

    # resto por rentabilidad, en lotes con hilos (el orden del ranking se
    # respeta: los resultados se consumen en el mismo orden en que salieron).
    # El cache se guarda tras cada lote: si la corrida se interrumpe, el
    # trabajo web ya hecho queda y la siguiente corrida es rapida.
    i = 0
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        while len(seleccion) < cantidad and i < len(candidatos):
            lote = [(pos, f) for pos, f in enumerate(candidatos[i:i + 40], start=i)
                    if f.get("estado") == "CANDIDATO"]
            i += 40
            if not lote:
                continue
            # submit + consumo en orden: los fallos se cuentan en vivo y
            # el cortacircuitos puede abortar a mitad del lote
            futuros = [pool.submit(resolver_producto, f, None, pos in muestra_pos)
                       for pos, f in lote]
            for (pos, fila), fut in zip(lote, futuros):
                slug, imagen, extra = fut.result()
                if len(seleccion) >= cantidad:
                    break
                if not slug:
                    fila.update({"estado": "OCULTO", "motivo": extra or "sin foto en POSGold"})
                    continue
                verificacion["fallos_seguidos"] = 0
                if slug in slugs_usados:
                    fila.update({"estado": "OCULTO", "motivo": "id duplicado (mismo nombre en POSGold)"})
                    continue
                fila.update({"estado": "ENTRA", "motivo": "", "slug": slug,
                             "imagen": imagen, "origen_imagen": extra})
                slugs_usados[slug] = fila["codigo"]
                seleccion.append(fila)
            guardar_cache(cache)
            log(f"      lote {i // 40}: {len(seleccion)}/{cantidad} seleccionados "
                f"(candidatos revisados: {min(i, len(candidatos))})")

    # comparePrice: el tachado sale del PRECIO 1 del sistema (el otro precio
    # de POSGold) y SOLO cuando queda por encima del detal (Precio 2). Si
    # no hay precio mayor, no hay tachado: el badge de descuento solo se
    # publica cuando POSGold trae un precio real mas alto — nada de
    # tachados inventados (el x1.19 de respaldo se retiro 22-10-2026).
    por_codigo_precio1 = {}
    por_codigo_precio5 = {}
    for it in productos_api:
        c = codigo_limpio(it.get("Codigo") or it.get("Producto_cod"))
        if c:
            por_codigo_precio1[c] = float(it.get("Precio1") or 0)
            por_codigo_precio5[c] = float(it.get("Precio5") or 0)
    for fila in seleccion:
        precio = fila["precio_detal_con_iva"]
        p1 = int(round(por_codigo_precio1.get(fila["codigo"], 0) or 0))
        fila["compare"] = p1 if p1 > precio else 0
        fila["precio"] = precio
        # PRECIO 5 del sistema, tal cual (con IVA): campo aditivo para los
        # canales del grupo (JUGA lo toma como base; respaldo Precio 2).
        # 0 = POSGold no lo define o la API no lo trae: no viaja.
        p5 = int(round(por_codigo_precio5.get(fila["codigo"], 0) or 0))
        fila["precio5"] = p5 if p5 > 0 else 0

    # los que cumplen pero no alcanzaron cupo en el TOP quedan marcados
    for fila in analisis:
        if fila.get("estado") == "CANDIDATO":
            fila.update({"estado": "FUERA_DEL_TOP", "motivo": "cumple pero el TOP ya estaba lleno"})

    log(f"\n      seleccion final: {len(seleccion)} productos")
    if len(seleccion) < cantidad:
        log(f"      AVISO: quedaron {cantidad - len(seleccion)} cupos sin llenar "
            f"(productos ocultos por foto rota o duplicada). Revisa el reporte.")

    # --- generacion y escritura ---
    log("\n[6/6] Generando catalogo.js...")
    if not seleccion:
        log("ERROR: ningun producto seleccionado; NO se toca el catalogo.")
        escribir_reporte(analisis, publicados_antes)
        sys.exit(1)

    # CONCILIACION dura, garantia "solo activos, si o si": antes de
    # generar, TODO seleccionado debe seguir activo y con existencias en
    # el estado que reporto POSGold en esta corrida. Si algo no cuadra,
    # aborta SIN tocar el catalogo publicado.
    violaciones = []
    for f in seleccion:
        it = por_codigo_api.get(f["codigo"]) or {}
        if it.get("Activo") is not True:
            violaciones.append((f, "inactivo en POSGold"))
        elif (it.get("Disponible") or 0) <= 0:
            violaciones.append((f, "sin existencias"))
    if violaciones:
        log("ERROR DE CONCILIACION: el catalogo quedaria con productos "
            "que POSGold no reporta activos. NO se toca lo publicado.")
        for f, razon in violaciones[:10]:
            log(f"  {f['codigo']} · {razon} · {f['nombre'][:48]}")
        escribir_reporte(analisis, publicados_antes)
        sys.exit(1)

    contenido = generar_catalogo_js(env, seleccion, len(seleccion))
    if not validar_js_nodo(contenido):
        escribir_reporte(analisis, publicados_antes)
        sys.exit(1)

    # segunda puerta: el contenido ya generado no debe traer un solo SKU
    # inactivo (mismo estado de POSGold, ahora sobre el texto final)
    skus_generados = set(re.findall(r'sku:"([^"]+)"', contenido))
    inactivos_generados = [c for c in skus_generados
                           if (por_codigo_api.get(c) or {}).get("Activo") is not True]
    if inactivos_generados:
        log("ERROR DE CONCILIACION: el catalogo generado trae SKUs inactivos: "
            + ", ".join(inactivos_generados[:10]) + ". NO se publica.")
        escribir_reporte(analisis, publicados_antes)
        sys.exit(1)
    log(f"      conciliacion OK: {len(skus_generados)} productos, "
        "todos activos y con existencias en POSGold")

    if args.dry_run:
        log("DRY-RUN: catalogo.js NO se modifica. Primer producto:")
        log("\n".join(contenido.splitlines()[:22]))
    else:
        resultado = escribir_catalogo(contenido)
        log(f"      catalogo.js {resultado}: {len(seleccion)} productos "
            f"({os.path.getsize(CATALOGO_PATH):,} bytes)")
        if resultado == "sin-cambios":
            log("      (productos identicos a la corrida anterior — commit minimo)")
        if not index_referencia_catalogo():
            log("      AVISO: index.html no carga catalogo.js — agregale "
                '<script src="catalogo.js"></script> antes del script principal.')
        # tercera puerta sobre el archivo ya escrito: lo que quedo
        # publicado debe ser 100% activo; si no, se aborta sin publicar
        inactivos_escritos = [c for c in leer_skus_publicados()
                              if (por_codigo_api.get(c) or {}).get("Activo") is not True]
        if inactivos_escritos:
            log("ERROR DE CONCILIACION: catalogo.js quedo con SKUs inactivos: "
                + ", ".join(inactivos_escritos[:10]) + ". NO se publica.")
            escribir_reporte(analisis, publicados_antes)
            sys.exit(1)
        if args.publicar:
            if publicar_git(env, len(seleccion)):
                log("      publicado en GitHub (Pages republica solo)")

    guardar_cache(cache)
    ruta_reporte = escribir_reporte(analisis, publicados_antes)
    log(f"\nReporte completo: {ruta_reporte}")

    # resumen de consola
    log("\n" + "=" * 66)
    log("RESUMEN")
    log("=" * 66)
    estados = {}
    for f in analisis:
        k = f["estado"]
        estados[k] = estados.get(k, 0) + 1
    log(f"  productos POSGold analizados : {len(analisis)}")
    for k in sorted(estados, key=lambda x: -estados[x]):
        log(f"  {k:16}: {estados[k]:5}")
    log(f"  top 10 por {criterio}:")
    for f in seleccion[:10]:
        mc = f["margen_costo_pct"] or 0
        log(f"    {mc:6.1f}% margen | ${f['precio_detal_con_iva']:>9,} | {f['nombre'][:42]}")
    fotos_rotas = [f for f in analisis if (f.get("motivo") or "").startswith("foto rota")]
    if fotos_rotas:
        log(f"\n  FOTOS ROTAS: {len(fotos_rotas)} sin foto utilizable en POSGold "
            f"(ni respaldo en cache) — quedan ocultas hasta que POSGold las tenga:")
        log("  (los primeros 5): " + " | ".join(f["nombre"][:30] for f in fotos_rotas[:5]))
    log("\nListo.")


if __name__ == "__main__":
    main()
