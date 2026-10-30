#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
canal_ml.py — FASE 1 del hub: adaptador de Mercado Libre (site MCO).

Lee el catalogo maestro (hub/productos.json, generado por
construir_hub.py) y lo lleva a Mercado Libre: altas, pausas,
activaciones, stock y precios. El estado del canal vive en
data/canal_ml.json (publicable: NUNCA contiene costos).

Comandos:
  --autorizar            primera vez: abre el navegador, autoriza la app
                         y guarda el refresh token en .env
  --prueba               valida credenciales (refresh token -> /users/me)
  --dry-run              calcula el plan (altas/pausas/activaciones/
                         stock/precio) SIN llamar a ML (no necesita token)
  --reconciliar          reconstruye data/canal_ml.json desde ML
  --sincronizar          ejecuta el plan sobre publicaciones existentes
  --publicar [N]         crea hasta N publicaciones nuevas (default: env
                         ML_LIMITE_ALTA)
  (sin banderas)         = --dry-run

Variables de .env (todas opcionales salvo las credenciales):
  ML_CLIENT_ID / ML_CLIENT_SECRET   de la app en developers.mercadolibre.com.co
  ML_REFRESH_TOKEN                  se guarda solo tras --autorizar
  ML_ACCESS_TOKEN                   modo dev: token pegado a mano (dura ~6 h,
                                     no rota; ideal para probar sin --autorizar)
  ML_TOKEN_KEY                      llave AES para hub/ml_token.enc (si se
                                     omite, se usa COSTOS_KEY)
  ML_SITE_ID=MCO
  ML_TIPO_PUBLICACION=gold_special  (gold_special=Clasica, silver=Gratuita)
  ML_PRECIO_FACTOR=0.20             recargo sobre Precio 5 (15% + 5%)
  ML_PRECIO_REDONDEO=100
  ML_COMISION_PCT=16                comision ML aproximada (cuidado del precio)
  ML_MARGEN_MINIMO=0.25             utilidad/costo minima para publicar
  ML_STOCK_MINIMO=2                 no publicar con menos existencias
  ML_STOCK_MAXIMO=99                techo publicado (no regala el inventario)
  ML_LIMITE_ALTA=5                  altas maximas por corrida
  ML_PRECIO_MINIMO=0                no publicar por debajo de este precio
  ML_CATEGORIAS_EXCLUIDAS=cacharro  slugs de categoria_web vetados en ML
  ML_VALIDAR=1                      valida el payload con /items/validate
  ML_NO_ROTAR=1                     exige ML_ACCESS_TOKEN; nunca refresca
  ML_REDIRECT_URI                   el registrado en la app (para --autorizar)

  Categorias curadas: data/categorias_ml.json (fallback MCO1910).
  Marcas conocidas: data/marcas_ml.json (sin costos).

SEGURIDAD Y TOKENS:
  - El refresh token de ML ROTA en cada uso: cada refresh devuelve uno
    nuevo. Este script reescribe ML_REFRESH_TOKEN en .env (local) y
    ademas deja el token vigente cifrado en hub/ml_token.enc (AES-256 +
    PBKDF2, mismo formato que costos.posgold.enc) para que GitHub
    Actions lo lea/renueve y lo commitee. Orden de lectura: blob
    cifrado -> .env -> error.
  - El repo es publico: el token NUNCA va en claro al repo ni a consola.
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import sync_posgold as sp  # noqa: E402
import construir_hub       # noqa: E402
import actualizar_costos as ac  # noqa: E402  (solo encontrar_openssl)

ENV_FILE = sp.ENV_FILE
HUB_PATH = construir_hub.HUB_PATH
ESTADO_PATH = os.path.join(sp.DIR_DATA, "canal_ml.json")
CATEGORIAS_PATH = os.path.join(sp.DIR_DATA, "categorias_ml.json")
MARCAS_PATH = os.path.join(sp.DIR_DATA, "marcas_ml.json")
DESCRIPCIONES_PATH = os.path.join(sp.DIR_DATA, "descripciones_dropi.json")
DIR_REPORTES = sp.DIR_REPORTES
API = "https://api.mercadolibre.com"
AUTH = "https://auth.mercadolibre.com"
CATEGORIAS_ML = {"jugueteria", "juego-de-mesa", "didactico"}
ATRIBUTOS_CACHE = {}
_DESCRIPCIONES_CACHE = None
OPENSSL_ARGS = ac.OPENSSL_ARGS  # enc -aes-256-cbc -pbkdf2 -iter 200000 -salt

log = sp.log


def log(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------
# Config y estado
# ------------------------------------------------------------------


def cargar_config():
    """Env de .env con los ML_* de proceso por encima (en Actions los
    secrets llegan como variables de entorno)."""
    env = sp.cargar_env()
    for k, v in os.environ.items():
        if k.startswith("ML_") or k in ("COSTOS_KEY",):
            env[k] = v
    return env


def normalizar_estado(est):
    """Completa claves nuevas y descarta cache de categorias viejo.

    La cache anterior guardaba el predictor crudo (p. ej. Abeja -> Colmenas).
    Es solo cache: se puede regenerar sin perder publicaciones.
    """
    if not isinstance(est, dict):
        est = {}
    est.setdefault("version", 1)
    est.setdefault("site", "MCO")
    est.setdefault("listings", {})
    if est.get("categorias_version") != 2:
        est["categorias"] = {}
        est["dominios_cat"] = {}
        est["categorias_fuente"] = {}
        est["categorias_info"] = {}
        est["faltantes"] = {}
        est["categorias_version"] = 2
    for clave in ("listings", "categorias", "dominios_cat",
                  "categorias_fuente", "categorias_info", "faltantes"):
        if not isinstance(est.get(clave), dict):
            est[clave] = {}
    if not isinstance(est.get("ignorados"), list):
        est["ignorados"] = []
    return est


def cargar_estado():
    if os.path.exists(ESTADO_PATH):
        try:
            with open(ESTADO_PATH, encoding="utf-8") as f:
                est = json.load(f)
        except Exception:
            est = {}
        return normalizar_estado(est)
    return normalizar_estado({})


def guardar_estado(est):
    est["actualizado"] = dt.datetime.now().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(ESTADO_PATH), exist_ok=True)
    tmp = ESTADO_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(est, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ESTADO_PATH)


# ------------------------------------------------------------------
# Token: rotacion persistente (blob cifrado + .env)
# ------------------------------------------------------------------


def _llave_token(env):
    return env.get("ML_TOKEN_KEY") or os.environ.get("ML_TOKEN_KEY") \
        or os.environ.get("COSTOS_KEY") or env.get("COSTOS_KEY")


def _openssl_correr(args, llave, entrada=None, salida=None):
    exe = ac.encontrar_openssl()
    cmd = [exe] + args + ["-pass", "env:JT_ML_KEY"]
    if salida:
        cmd += ["-out", salida]
    entorno = dict(os.environ, JT_ML_KEY=llave)
    r = subprocess.run(cmd, input=entrada, capture_output=True, text=True,
                       env=entorno)
    return r.returncode == 0, (r.stderr or r.stdout).strip()[:300]


def guardar_token_cifrado(refresh_token, env):
    """Deja el refresh token vigente en hub/ml_token.enc (cifrado).
    Es el que lee el workflow de Actions: el secret de .env queda viejo
    tras cada rotacion, el blob siempre viaja fresco en el repo."""
    llave = _llave_token(env)
    if not llave:
        return False
    ruta = os.path.join(BASE_DIR, "hub", "ml_token.enc")
    ok, err = _openssl_correr(list(OPENSSL_ARGS) + ["-in", "-"], llave,
                              entrada=refresh_token, salida=ruta + ".tmp")
    if ok:
        os.replace(ruta + ".tmp", ruta)
        return True
    log("  aviso: no se pudo cifrar el token (" + err + ")")
    return False


def cargar_token_cifrado(env):
    llave = _llave_token(env)
    ruta = os.path.join(BASE_DIR, "hub", "ml_token.enc")
    if not (llave and os.path.exists(ruta)):
        return None
    ok, texto = _openssl_correr(["-d"] + list(OPENSSL_ARGS) + ["-in", ruta],
                                llave)
    if ok and texto.strip():
        return texto.strip()
    return None


def actualizar_env(clave, valor):
    """Reescribe (o agrega) una linea de .env conservando el resto."""
    lineas = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8-sig") as f:
            lineas = f.read().splitlines()
    encontrado = False
    for i, l in enumerate(lineas):
        if l.strip().startswith(clave + "="):
            lineas[i] = f"{clave}={valor}"
            encontrado = True
            break
    if not encontrado:
        lineas.append(f"{clave}={valor}")
    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lineas) + "\n")


def refrescar_token(env):
    """OAuth refresh_token: devuelve access token NUEVO y guarda el
    refresh token rotado (blob cifrado + .env)."""
    cid = env.get("ML_CLIENT_ID")
    sec = env.get("ML_CLIENT_SECRET")
    rt = cargar_token_cifrado(env) or env.get("ML_REFRESH_TOKEN")
    if not (cid and sec and rt):
        log("ERROR: faltan credenciales ML (ML_CLIENT_ID, ML_CLIENT_SECRET,")
        log("       ML_REFRESH_TOKEN o ML_ACCESS_TOKEN en .env).")
        log("       Primera vez: python hub/canal_ml.py --autorizar")
        sys.exit(1)
    cuerpo = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": cid,
        "client_secret": sec,
        "refresh_token": rt,
    })
    codigo, texto = sp.http_texto(API + "/oauth/token", metodo="POST",
                                  datos=cuerpo, timeout=60,
                                  headers={"Accept": "application/json",
                                           "Content-Type": "application/x-www-form-urlencoded"})
    if codigo != 200:
        log(f"ERROR: ML rechazo el refresh token (HTTP {codigo}): {texto[:200]}")
        log("       Vuelve a autorizar: python hub/canal_ml.py --autorizar")
        sys.exit(1)
    data = json.loads(texto)
    nuevo = data.get("refresh_token")
    if nuevo and nuevo != rt:
        guardar_token_cifrado(nuevo, env)
        try:
            actualizar_env("ML_REFRESH_TOKEN", nuevo)
        except Exception:
            pass
    return data["access_token"]


# ------------------------------------------------------------------
# Cliente ML (get/post/put con 429 y re-Refresh en 401)
# ------------------------------------------------------------------


class SesionML:
    def __init__(self, env):
        self.env = env
        if env.get("ML_ACCESS_TOKEN"):
            self.token = env["ML_ACCESS_TOKEN"]
            self.rotante = False
        else:
            if env.get("ML_NO_ROTAR") == "1":
                log("ERROR: ML_NO_ROTAR=1 exige ML_ACCESS_TOKEN en .env.")
                log("       No se refresca el token: la rotacion esta bloqueada.")
                sys.exit(1)
            self.token = refrescar_token(env)
            self.rotante = True

    def _llamada(self, metodo, ruta, datos=None, reintentos=3):
        url = ruta if ruta.startswith("http") else API + ruta
        cuerpo = json.dumps(datos, ensure_ascii=False) if datos is not None else None
        headers = {"Authorization": "Bearer " + self.token,
                   "Content-Type": "application/json",
                   "Accept": "application/json"}
        pausas = (5, 10, 20)
        ultimo = None
        for i in range(reintentos + 1):
            try:
                codigo, texto = sp.http_texto(url, headers=headers,
                                              metodo=metodo, datos=cuerpo,
                                              timeout=90, limite_error=5000)
            except Exception as e:
                ultimo = str(e)[:200]
                if i < reintentos:
                    time.sleep(pausas[min(i, len(pausas) - 1)])
                    continue
                raise
            if codigo == 429 and i < reintentos:
                time.sleep(pausas[min(i, len(pausas) - 1)])
                continue
            try:
                data = json.loads(texto) if texto.strip() else {}
            except json.JSONDecodeError:
                data = {"raw": texto[:1000]}
            return codigo, data
        raise RuntimeError("ML no respondio: " + str(ultimo))

    def llamada(self, metodo, ruta, datos=None):
        codigo, data = self._llamada(metodo, ruta, datos)
        if codigo == 401 and self.rotante:
            self.token = refrescar_token(self.env)  # re-intenta una vez
            codigo, data = self._llamada(metodo, ruta, datos)
        return codigo, data

    def get(self, ruta):
        return self.llamada("GET", ruta)

    def post(self, ruta, datos):
        return self.llamada("POST", ruta, datos)

    def put(self, ruta, datos):
        return self.llamada("PUT", ruta, datos)


# ------------------------------------------------------------------
# Reglas del canal (precios, titulos, elegibilidad)
# ------------------------------------------------------------------


def precio_ml(p, env):
    """Precio ML = (Precio5 + envio) / (1 - comision - pasarela), redondeado.
    Precio5 ya incluye ganancia + IVA.
    ML cobra 15% comision + 5% pasarela = 20% total.
    Jugueteria Taiwan asume envio gratis: paga $23.000 por envio."""
    precio5 = float(p.get("precio5") or 0)
    if precio5 <= 0:
        return 0
    comision = float(env.get("ML_COMISION_PCT", "15")) / 100.0
    pasarela = float(env.get("ML_PASARELA_PCT", "5")) / 100.0
    envio = float(env.get("ML_ENVIO_COSTO", "23000"))
    divisor = 1 - comision - pasarela
    if divisor <= 0:
        return 0
    precio = (precio5 + envio) / divisor
    redondeo = int(env.get("ML_PRECIO_REDONDEO", "100"))
    # Redondeo half-up (no banker's rounding)
    precio = int((precio + redondeo / 2) // redondeo * redondeo)
    return max(precio, 0)


def pasa_rentabilidad(p, precio, env):
    """Utilidad/costo minima DESPUES de la comision de ML + pasarela.
    Sin costo conocido no se publica (misma regla de la vitrina)."""
    costo = p.get("costo")
    if not costo:
        return False, "sin costo"
    comision = float(env.get("ML_COMISION_PCT", "15")) / 100.0
    pasarela = float(env.get("ML_PASARELA_PCT", "5")) / 100.0
    mmin = float(env.get("ML_MARGEN_MINIMO", "0.25"))
    total_com = comision + pasarela
    util = precio * (1 - total_com) - costo
    return (util / costo) >= mmin, round(util / costo, 2)


def titulo_ml(nombre, env):
    t = re.sub(r"\s+", " ", (nombre or "").strip()).title()
    maximo = int(env.get("ML_TITULO_MAX", "60"))
    if len(t) > maximo:
        t = t[:maximo].rsplit(" ", 1)[0].strip()
    return t


def completar_titulo_ml(sesion, est, cid, titulo, brand):
    """ML rechaza titulos cortos o de una sola palabra
    (item.title.minimum_length: pide marca, modelo o categoria).
    Se completa con el nombre de la categoria y la marca hasta 40."""
    t = (titulo or "").strip()
    if len(t) >= 40 and " " in t:
        return t
    extras = []
    info = _categoria_info(sesion, cid, est) or {}
    if info.get("nombre"):
        extras.append(info["nombre"])
    if brand:
        extras.append(brand)
    for extra in extras:
        pieza = re.sub(r"\s+", " ", extra).title().strip()
        if pieza and pieza.lower() not in t.lower():
            t = (t + " " + pieza).strip()
        if len(t) >= 40:
            break
    return t


def cargar_descripciones_ml():
    global _DESCRIPCIONES_CACHE
    if _DESCRIPCIONES_CACHE is None:
        data = _leer_json(DESCRIPCIONES_PATH, {})
        _DESCRIPCIONES_CACHE = data if isinstance(data, dict) else {}
    return _DESCRIPCIONES_CACHE


def sin_pie_dropi(texto):
    """Quita el pie de la plantilla Dropi y su WhatsApp; elimina emojis."""
    t = str(texto or "")
    for marca in ("-----------------------",
                  "WhatsApp de atenci\u00f3n dropshippers",
                  "WhatsApp de atencion dropshippers",
                  "3106988465"):
        pos = t.find(marca)
        if pos >= 0:
            t = t[:pos]
    t = "\n".join(l for l in t.splitlines()
                  if "3106988465" not in l).strip()
    t = "".join(c for c in t if ord(c) <= 0xFFFF)
    return t


def descripcion_ml(p, titulo, base=""):
    texto = sin_pie_dropi(base)
    if texto:
        return texto
    sku = p.get("sku") or ""
    return (f"{titulo}.\n"
            f"Producto nuevo, original y sellado.\n"
            f"Referencia: {sku}.\n"
            f"Jugueteria Taiwan - Cali, Colombia. Despachos a todo el pais.")


# ------------------------------------------------------------------
# Catalogos curados (publicos, sin costos)
# ------------------------------------------------------------------


def _leer_json(ruta, defecto):
    if not os.path.exists(ruta):
        return defecto
    try:
        with open(ruta, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, (dict, list)) else defecto
    except Exception:
        return defecto


def cargar_categorias():
    defecto = {
        "fallback": "MCO1910",
        "prefijos_juguete": ["MCO-TOY", "MCO-PUZZLE", "MCO-STUFFED",
                             "MCO-DOLL", "MCO-RADIO_CONTROL"],
        "dominios_up": ["MCO-PUZZLE_CUBES"],
        "por_palabra": [],
    }
    data = _leer_json(CATEGORIAS_PATH, defecto)
    if not isinstance(data, dict):
        data = defecto
    for k, v in defecto.items():
        data.setdefault(k, v)
    return data


def cargar_marcas():
    defecto = {"marca_defecto": "Juguetería Taiwan",
               "por_sku": {}, "por_palabra": []}
    data = _leer_json(MARCAS_PATH, defecto)
    if not isinstance(data, dict):
        data = defecto
    for k, v in defecto.items():
        data.setdefault(k, v)
    return data


def marca_modelo(p, marcas=None):
    """Marca conocida por SKU/palabra; si no hay, la marca de la tienda.
    El modelo sale del nombre completo (sin la marca), nunca de la
    primera palabra suelta."""
    marcas = marcas or cargar_marcas()
    nombre = p.get("nombre") or ""
    sku = str(p.get("sku") or "")
    brand = (p.get("marca") or marcas.get("por_sku", {}).get(sku) or "").strip()
    if not brand:
        n = sp.sin_acentos(nombre)
        for regla in marcas.get("por_palabra", []):
            for palabra in regla.get("palabras", []):
                if sp.sin_acentos(palabra) in n:
                    brand = (regla.get("marca") or "").strip()
                    break
            if brand:
                break
    if not brand:
        brand = marcas.get("marca_defecto") or "Juguetería Taiwan"
    model = (p.get("modelo") or nombre).strip()
    if brand and sp.sin_acentos(model).startswith(sp.sin_acentos(brand)):
        model = model[len(brand):].strip(" -_/")
    if not model:
        model = sku or brand
    return brand, model[:60]


def ean13_valido(valor):
    s = re.sub(r"\D", "", str(valor or ""))
    if len(s) != 13:
        return False
    suma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(s[:12]))
    return (10 - suma % 10) % 10 == int(s[12])


def _attrs_categoria(sesion, cid):
    if cid in ATRIBUTOS_CACHE:
        return ATRIBUTOS_CACHE[cid]
    codigo, data = sesion.get(f"/categories/{cid}/attributes")
    attrs = data.get("attributes") if isinstance(data, dict) else data
    if codigo != 200 or not isinstance(attrs, list):
        return None
    ATRIBUTOS_CACHE[cid] = attrs
    return attrs


def _valor_requerido(req, p, brand, model, ean):
    aid = req.get("id")
    if aid == "ITEM_CONDITION":
        return {"id": aid, "value_id": "2230284", "value_name": "Nuevo"}
    if aid == "BRAND":
        valor = brand
    elif aid == "MODEL":
        valor = model
    elif aid == "MANUFACTURER":
        valor = brand
    elif aid == "SELLER_SKU":
        valor = p.get("sku")
    elif aid == "GTIN":
        valor = ean
    elif aid == "BOARD_GAME_NAME":
        valor = (p.get("nombre") or "").strip()
    else:
        return None
    if not valor:
        return None
    if req.get("value_type") == "list":
        valores = req.get("values") or []
        buscado = sp.sin_acentos(str(valor))
        for v in valores:
            if sp.sin_acentos(v.get("name") or "") == buscado:
                return {"id": aid, "value_id": v.get("id"),
                        "value_name": v.get("name")}
        return None
    return {"id": aid, "value_name": str(valor)}


def _valor_defecto_req(req, p):
    """Valores por defecto para atributos obligatorios que el hub no
    sabe responder. POWER_SUPPLY_TYPE se lee del nombre del producto
    (pilas/luz/sonido/electronico => bateria; si no, operacion manual).
    IS_FACTORY_KIT es No (juguetes sueltos, no son kits de fabrica).
    OJO: sin_acentos() devuelve MAYUSCULAS sin tildes: se normalizan
    ambos lados con la misma funcion (así compara _valor_requerido)."""
    aid = req.get("id")
    valores = req.get("values") or []

    def buscar(prefijos):
        for objetivo in prefijos:
            objetivo_n = sp.sin_acentos(objetivo)
            for v in valores:
                nombre_v = sp.sin_acentos(v.get("name") or "")
                if objetivo_n in nombre_v:
                    return {"id": aid, "value_id": v.get("id"),
                            "value_name": v.get("name")}
        return None

    if aid == "IS_FACTORY_KIT":
        return buscar(["no"])
    if aid == "POWER_SUPPLY_TYPE":
        nombre = sp.sin_acentos(p.get("nombre") or "")
        if any(x in nombre for x in ("PILA", "BATERIA", "LUZ", "LUCE",
                                     "SONIDO", "ELECTRONI", "LED",
                                     "ROTAT", "GIRA")):
            con_bateria = buscar(["bateria"])
            if con_bateria:
                return con_bateria
        return buscar(["operacion manual"])
    return None


def atributos_para(sesion, cid, p, brand=None, model=None):
    """Arma solo atributos que ML declara para la categoria. Si falta un
    requerido, devuelve la lista y el producto NO se publica."""
    brand = brand or marca_modelo(p)[0]
    model = model or marca_modelo(p)[1]
    ean = str(p.get("ean")) if ean13_valido(p.get("ean")) else None
    if not ean and ean13_valido(p.get("sku")):
        ean = str(p.get("sku"))
    attrs = _attrs_categoria(sesion, cid)
    if attrs is None:
        return [], [{"id": "*", "motivo": "no se pudieron leer los atributos"}]
    salida, faltantes = [], []
    llenables = {"BRAND", "MODEL", "MANUFACTURER", "SELLER_SKU",
                 "GTIN", "ITEM_CONDITION", "BOARD_GAME_NAME"}
    for req in attrs:
        tags = req.get("tags") or {}
        requerido = (tags.get("required") or tags.get("catalog_required")
                     or tags.get("grid_template_required"))
        if not requerido and req.get("id") not in llenables:
            continue
        val = _valor_requerido(req, p, brand, model, ean)
        if not val and requerido:
            val = _valor_defecto_req(req, p)
        if val:
            salida.append(val)
        elif requerido:
            faltantes.append({"id": req.get("id"),
                              "nombre": req.get("name"),
                              "value_type": req.get("value_type")})
    ids = {a.get("id") for a in salida}
    if "ITEM_CONDITION" not in ids:
        salida.append({"id": "ITEM_CONDITION", "value_id": "2230284",
                       "value_name": "Nuevo"})
    if ean and "GTIN" not in ids:
        salida.append({"id": "GTIN", "value_name": ean})
    return salida, faltantes


# ------------------------------------------------------------------
# Categorias: mapa curado + predictor + fallback de juguetes
# ------------------------------------------------------------------


def _categoria_info(sesion, cid, est):
    cache = est.setdefault("categorias_info", {})
    if cid in cache:
        return cache[cid]
    codigo, data = sesion.get(f"/categories/{cid}")
    if codigo != 200 or not isinstance(data, dict):
        return None
    settings = data.get("settings") or {}
    info = {
        "categoria": cid,
        "nombre": data.get("name"),
        "dominio": settings.get("catalog_domain"),
        "listing_allowed": settings.get("listing_allowed"),
        "max_titulo": settings.get("max_title_length"),
    }
    cache[cid] = info
    return info


def _es_dominio_juguete(dominio, cfg):
    d = (dominio or "").upper()
    # OJO: "PET_TOY" / "DOG_TOY" son juguetes de mascotas, no de niños.
    if any(x in d for x in ("PET_TOY", "PET_SUPPLIES", "DOG_TOY",
                            "CAT_TOY", "TOY_PET")):
        return False
    if any(d.startswith(p.upper()) for p in cfg.get("prefijos_juguete", [])):
        return True
    # Hay dominios de juguetes que no empiezan por MCO-TOY (p. ej.
    # MCO-NON_REMOTE_CONTROL_TOY_VEHICLES); se reconocen por la rama.
    return any(x in d for x in ("TOY", "PUZZLE", "STUFFED", "DOLL",
                                "MINIATURE", "JOKE"))


def _es_dominio_up(dominio, cfg):
    return (dominio or "").upper() in {d.upper() for d in cfg.get("dominios_up", [])}


def _categoria_curada(titulo, cfg):
    n = sp.sin_acentos(titulo)
    for regla in cfg.get("por_palabra", []):
        for palabra in regla.get("palabras", []):
            if sp.sin_acentos(palabra) in n:
                return regla.get("categoria")
    return None


def _categoria_valida(sesion, cid, est, cfg, tags):
    info = _categoria_info(sesion, cid, est)
    if not info or info.get("listing_allowed") is not True:
        return None
    dom = info.get("dominio")
    if _es_dominio_up(dom, cfg) and "user_product_seller" not in tags:
        return None
    curadas = {r.get("categoria") for r in cfg.get("por_palabra", [])}
    if (not _es_dominio_juguete(dom, cfg) and cid != cfg.get("fallback")
            and cid not in curadas):
        return None
    return info


def categorias_excluidas(env):
    return {s.strip().lower() for s in
            env.get("ML_CATEGORIAS_EXCLUIDAS", "cacharro").split(",") if s.strip()}


def elegible_para_alta(p, estado, env):
    """Reglas de alta: las del canal. Sin costo conocido no entra (misma
    regla dura de la vitrina)."""
    sku = p.get("sku")
    if not sku or sku in estado["listings"] or p.get("duplicado"):
        return None
    if sku in set(estado.get("ignorados") or []):
        return None
    if not p.get("vendible"):
        return None
    if (p.get("categoria_web") or "").lower() not in CATEGORIAS_ML:
        return None
    if (p.get("categoria_web") or "").lower() in categorias_excluidas(env):
        return None
    if (p.get("stock") or 0) < int(env.get("ML_STOCK_MINIMO", "2")):
        return None
    if not p.get("fotos"):
        return None
    precio = precio_ml(p, env)
    minimo = int(env.get("ML_PRECIO_MINIMO", "0"))
    if precio < minimo:
        return None
    ok, detalle = pasa_rentabilidad(p, precio, env)
    if not ok:
        return None
    return {"sku": sku, "precio": precio, "margen": detalle}


def construir_plan(hub, estado, env, limite_altas):
    """Plan completo (local, sin red): pausar / activar / actualizar /
    altas candidatas, en ese orden de riesgo (pausar primero: nunca
    seguir vendiendo sin inventario)."""
    por_sku = {p["sku"]: p for p in hub["productos"] if p.get("sku")}
    stock_max = int(env.get("ML_STOCK_MAXIMO", "99"))
    acciones, altas = [], []
    ignorados = set(estado.get("ignorados") or [])
    for sku, l in sorted(estado["listings"].items()):
        if l.get("origen") == "externo":
            continue
        if sku in ignorados:
            if l.get("estado") != "paused":
                acciones.append({"tipo": "pausar", "sku": sku,
                                 "listing_id": l.get("listing_id"),
                                 "titulo": l.get("titulo"),
                                 "razon": "SKU marcado como ignorado"})
            continue
        p = por_sku.get(sku)
        out = (p is None or not p.get("activo") or (p.get("stock") or 0) <= 0)
        if out:
            if l.get("estado") != "paused":
                acciones.append({"tipo": "pausar", "sku": sku,
                                 "listing_id": l.get("listing_id"),
                                 "titulo": l.get("titulo"),
                                 "razon": ("producto fuera de POSGold" if p is None
                                           else "agotado o inactivo en POSGold")})
            continue
        deseo_precio = precio_ml(p, env)
        deseo_stock = min(int(p["stock"]), stock_max)
        cambios = {}
        if l.get("precio") != deseo_precio:
            cambios["price"] = deseo_precio
        if l.get("stock_publicado") != deseo_stock:
            cambios["available_quantity"] = deseo_stock
        if cambios:
            acciones.append({"tipo": "actualizar", "sku": sku,
                             "listing_id": l.get("listing_id"),
                             "titulo": titulo_ml(p["nombre"], env),
                             "cambios": cambios})
        if l.get("estado") == "paused":
            acciones.append({"tipo": "activar", "sku": sku,
                             "listing_id": l.get("listing_id"),
                             "titulo": titulo_ml(p["nombre"], env)})
    for p in hub["productos"]:
        if len(altas) >= limite_altas:
            break
        cand = elegible_para_alta(p, estado, env)
        if cand:
            altas.append({"sku": cand["sku"], "precio": cand["precio"],
                          "margen": cand["margen"],
                          "titulo": titulo_ml(p["nombre"], env),
                          "stock": min(int(p["stock"]), stock_max),
                          "producto": p})
    return acciones, altas


# ------------------------------------------------------------------
# Reconciliacion: reconstruir el estado desde ML (idempotencia)
# ------------------------------------------------------------------


def _item_a_listing(item, catalogo_skus):
    attrs = {a.get("id"): a.get("value_name")
             for a in item.get("attributes") or []}
    sku = item.get("seller_custom_field") or attrs.get("SELLER_SKU")
    externo = not sku or sku not in catalogo_skus
    clave = sku or ("ML:" + str(item.get("id")))
    return clave, {
        "listing_id": item.get("id"),
        "titulo": item.get("title"),
        "permalink": item.get("permalink"),
        "estado": item.get("status"),
        "precio": item.get("price"),
        "stock_publicado": item.get("available_quantity"),
        "categoria_ml": item.get("category_id"),
        "seller_custom_field": sku,
        "user_product_id": item.get("user_product_id"),
        "family_name": item.get("family_name"),
        "origen": "externo" if externo else "catalogo",
        "sin_sku": bool(not sku),
    }


def _items_en_lote(sesion, ids):
    items = []
    for i in range(0, len(ids), 20):
        lote = ids[i:i + 20]
        codigo, data = sesion.get("/items?ids=" + ",".join(lote))
        if codigo == 200 and isinstance(data, list):
            for sobre in data:
                if isinstance(sobre, dict) and sobre.get("code") == 200:
                    body = sobre.get("body") or {}
                    if body.get("id"):
                        items.append(body)
        else:
            for lid in lote:
                codigo, item = sesion.get(f"/items/{lid}")
                if codigo == 200 and isinstance(item, dict):
                    items.append(item)
        time.sleep(0.3)
    return items


def reconciliar_estado(sesion, est, hub, limite=1000):
    """Lee las publicaciones activas/pausadas del vendedor y reconstruye
    data/canal_ml.json. Las de SKU desconocido se guardan como externas y
    NO se pausan automaticamente."""
    hub_skus = {p.get("sku") for p in hub.get("productos", []) if p.get("sku")}
    codigo, me = sesion.get("/users/me")
    if codigo != 200:
        raise RuntimeError(f"/users/me respondio {codigo}: {str(me)[:200]}")
    uid = me.get("id")
    est["vendedor"] = {"id": uid, "nickname": me.get("nickname"),
                       "tags": me.get("tags") or []}
    ids, offset = [], 0
    while len(ids) < limite:
        n = min(100, limite - len(ids))
        qs = urllib.parse.urlencode({"status": "active,paused", "limit": str(n),
                                     "offset": str(offset)})
        codigo, data = sesion.get(f"/users/{uid}/items/search?{qs}")
        if codigo != 200:
            raise RuntimeError(f"items/search respondio {codigo}: {str(data)[:200]}")
        results = (data or {}).get("results") or []
        if not results:
            break
        ids.extend(results)
        offset += len(results)
        total = ((data or {}).get("paging") or {}).get("total")
        if total is not None and offset >= int(total):
            break
        time.sleep(0.3)
    nuevos = {}
    for item in _items_en_lote(sesion, ids):
        clave, fila = _item_a_listing(item, hub_skus)
        viejo = est.get("listings", {}).get(clave) or {}
        if viejo.get("creado"):
            fila["creado"] = viejo["creado"]
        fila["actualizado"] = dt.datetime.now().isoformat(timespec="seconds")
        nuevos[clave] = fila
    for clave, viejo in (est.get("listings") or {}).items():
        if viejo.get("origen") == "externo" and clave not in nuevos:
            nuevos[clave] = viejo
    est["listings"] = nuevos
    return len(ids), len(nuevos)


# ------------------------------------------------------------------
# Ejecucion del plan
# ------------------------------------------------------------------


def categoria_para(sesion, titulo, estado, cfg=None):
    """Prioridad: mapa curado -> predictor con dominio de juguetes ->
    fallback MCO1910 (Otros, dominio MCO-TOYS_AND_GAMES).

    Nunca acepta a ciegas una categoria de otra rama (colmenas, cajones,
    pianos de verdad): si el dominio no es de juguetes, se descarta.
    """
    cfg = cfg or cargar_categorias()
    cache = estado.setdefault("categorias", {})
    clave = sp.sin_acentos(titulo)[:48]
    if clave in cache:
        return cache[clave]
    tags = (estado.get("vendedor") or {}).get("tags") or []

    curada = _categoria_curada(titulo, cfg)
    if curada and _categoria_valida(sesion, curada, estado, cfg, tags):
        cache[clave] = curada
        estado.setdefault("categorias_fuente", {})[clave] = "curada"
        return curada

    site = estado.get("site", "MCO")
    qs = urllib.parse.urlencode({"q": titulo, "limit": "5"})
    codigo, data = sesion.get(f"/sites/{site}/domain_discovery/search?{qs}")
    if codigo == 200 and isinstance(data, list):
        for item in data:
            cand = (item or {}).get("category_id")
            if not cand:
                continue
            if _categoria_valida(sesion, cand, estado, cfg, tags):
                cache[clave] = cand
                estado.setdefault("categorias_fuente", {})[clave] = "discovery"
                return cand

    fallback = cfg.get("fallback") or "MCO1910"
    if _categoria_valida(sesion, fallback, estado, cfg, tags):
        cache[clave] = fallback
        estado.setdefault("categorias_fuente", {})[clave] = "fallback"
        return fallback
    raise RuntimeError(f"sin categoria valida para '{titulo}' (HTTP {codigo})")


def ejecutar_accion(sesion, est, a):
    lid = a["listing_id"]
    if a["tipo"] == "pausar":
        codigo, data = sesion.put(f"/items/{lid}", {"status": "paused"})
    elif a["tipo"] == "activar":
        codigo, data = sesion.put(f"/items/{lid}", {"status": "active"})
    else:  # actualizar
        codigo, data = sesion.put(f"/items/{lid}", a["cambios"])
    if codigo in (200, 201):
        l = est["listings"].setdefault(a["sku"], {"listing_id": lid})
        if a["tipo"] == "pausar":
            l["estado"] = "paused"
        elif a["tipo"] == "activar":
            l["estado"] = "active"
        if "price" in (a.get("cambios") or {}):
            l["precio"] = a["cambios"]["price"]
        if "available_quantity" in (a.get("cambios") or {}):
            l["stock_publicado"] = a["cambios"]["available_quantity"]
        l["actualizado"] = dt.datetime.now().isoformat(timespec="seconds")
        return True, data
    return False, data


def construir_payload(sesion, est, env, alta):
    """Arma el payload sin publicar. Devuelve (payload, meta) o (None, error).
    Es la unica fuente de verdad para el alta y para QA offline."""
    p = alta["producto"]
    sku = p["sku"]
    titulo = titulo_ml(p.get("nombre") or alta["titulo"], env)
    cid = categoria_para(sesion, p.get("nombre") or titulo, est,
                         cargar_categorias())
    info = (est.get("categorias_info") or {}).get(cid) or {}
    max_titulo = int(info.get("max_titulo") or env.get("ML_TITULO_MAX", "60"))
    if len(titulo) > max_titulo:
        titulo = titulo[:max_titulo].rsplit(" ", 1)[0].strip()

    brand, model = marca_modelo(p)
    attributes, faltantes = atributos_para(sesion, cid, p, brand, model)
    if faltantes:
        return None, {"error": "faltan atributos obligatorios",
                      "categoria": cid, "faltantes": faltantes}

    titulo = completar_titulo_ml(sesion, est, cid, titulo, brand)
    if len(titulo) > max_titulo:
        titulo = titulo[:max_titulo].rsplit(" ", 1)[0].strip()

    tags = (est.get("vendedor") or {}).get("tags") or []
    up = "user_product_seller" in tags
    # dimensiones para Mercado Envíos: "altoxanchoxlargo,peso" en cm y g
    dims = f"{p.get('alto',10)}x{p.get('ancho',10)}x{p.get('largo',5)},{p.get('peso',200)}"
    payload = {
        "category_id": cid,
        "price": alta["precio"],
        "currency_id": "COP",
        "available_quantity": alta["stock"],
        "buying_mode": "buy_it_now",
        "listing_type_id": env.get("ML_TIPO_PUBLICACION", "gold_special"),
        "condition": "new",
        "pictures": [{"source": u} for u in (p.get("fotos") or [])[:10] if u],
        "attributes": attributes,
        "seller_custom_field": sku,
        "shipping": {
            "mode": "me2",
            "local_pick_up": False,
            "dimensions": dims,
            "free_methods": [{"id": "me2", "rule": {"free_mode": "country", "value": None}}],
        },
    }
    if up:
        payload["family_name"] = titulo
    else:
        payload["title"] = titulo
    return payload, {"categoria": cid, "titulo": titulo, "marca": brand,
                     "modelo": model, "up": up, "attributes": attributes}


def crear_publicacion(sesion, est, env, alta):
    p = alta["producto"]
    sku = p["sku"]
    payload, meta = construir_payload(sesion, est, env, alta)
    if payload is None:
        est.setdefault("faltantes", {})[sku] = {
            "categoria": meta.get("categoria"),
            "faltantes": meta.get("faltantes"),
            "actualizado": dt.datetime.now().isoformat(timespec="seconds"),
        }
        return False, meta
    cid = meta["categoria"]
    titulo = meta["titulo"]

    if env.get("ML_VALIDAR", "1") != "0":
        codigo, data = sesion.post("/items/validate", payload)
        causas = data.get("cause") if isinstance(data, dict) else None
        errores = [c for c in (causas or [])
                   if isinstance(c, dict) and c.get("type") == "error"]
        est.setdefault("validaciones", {})[sku] = {
            "http": codigo, "categoria": cid,
            "causas": causas or [], "errores": errores,
            "actualizado": dt.datetime.now().isoformat(timespec="seconds"),
        }
        if errores or (codigo not in (200, 204) and not causas):
            return False, {"error": "validacion rechazada", "http": codigo,
                           "categoria": cid, "detalle": data}

    codigo, data = sesion.post("/items", payload)
    if codigo not in (200, 201):
        return False, data
    lid = data.get("id")
    if not lid:
        return False, {"error": "ML no devolvio id del item", "detalle": data}
    desc_error = None
    if lid:
        base_desc = cargar_descripciones_ml().get(sku, "")
        dcod, ddata = sesion.post(f"/items/{lid}/description",
                                  {"plain_text": descripcion_ml(p, titulo, base_desc)})
        if dcod not in (200, 201):
            desc_error = {"http": dcod, "detalle": ddata}
    est["listings"][sku] = {
        "listing_id": lid,
        "titulo": data.get("title") or titulo,
        "permalink": data.get("permalink"),
        "estado": data.get("status"),
        "precio": alta["precio"],
        "stock_publicado": alta["stock"],
        "categoria_ml": cid,
        "seller_custom_field": sku,
        "user_product_id": data.get("user_product_id"),
        "family_name": data.get("family_name"),
        "origen": "catalogo",
        "creado": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if desc_error:
        est["listings"][sku]["descripcion_error"] = desc_error
    est.setdefault("faltantes", {}).pop(sku, None)
    return True, data


# ------------------------------------------------------------------
# Comandos
# ------------------------------------------------------------------


def cmd_autorizar(env):
    cid = env.get("ML_CLIENT_ID")
    if not cid:
        log("ERROR: falta ML_CLIENT_ID en .env (crea la app primero en")
        log("       https://developers.mercadolibre.com.co/devcenter).")
        sys.exit(1)
    redirect = env.get("ML_REDIRECT_URI",
                       "https://developers.mercadolibre.com.co/devcenter")
    url = (f"{AUTH}/authorization?response_type=code&client_id={cid}"
           f"&redirect_uri={urllib.parse.quote(redirect, safe='')}")
    log("1) Abre este enlace en tu navegador e inicia sesion con la cuenta")
    log("   vendedora de Mercado Libre de la tienda:\n")
    log("   " + url + "\n")
    log("2) Al autorizar, el navegador salta a la URL de redireccion con")
    log("   ?code=XXXX. Pega aqui ese valor (el code o la URL completa).")
    dato = input("code o URL: ").strip()
    m = re.search(r"code=([^&\s]+)", dato)
    if not m:
        log("ERROR: no veo ningun code en lo que pegaste.")
        sys.exit(1)
    sec = env.get("ML_CLIENT_SECRET")
    if not sec:
        log("ERROR: falta ML_CLIENT_SECRET en .env.")
        sys.exit(1)
    cuerpo = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": cid,
        "client_secret": sec,
        "code": m.group(1),
        "redirect_uri": redirect,
    })
    codigo, texto = sp.http_texto(API + "/oauth/token", metodo="POST",
                                  datos=cuerpo, timeout=60)
    if codigo != 200:
        log(f"ERROR: canje del code fallo (HTTP {codigo}): {texto[:300]}")
        log("       El code se usa UNA sola vez y expira en ~10 minutos:")
        log("       repite --autorizar con un code recien generado.")
        sys.exit(1)
    data = json.loads(texto)
    actualizar_env("ML_REFRESH_TOKEN", data["refresh_token"])
    guardar_token_cifrado(data["refresh_token"], env)
    log("OK: refresh token guardado en .env y cifrado en hub/ml_token.enc.")
    log("    Prueba con: python hub/canal_ml.py --prueba")


def cmd_prueba(env):
    sesion = SesionML(env)
    codigo, me = sesion.get("/users/me")
    if codigo != 200:
        log(f"ERROR: /users/me respondio {codigo}: {str(me)[:200]}")
        sys.exit(1)
    log("OK: token valido.")
    log(f"  vendedor : {me.get('nickname')} (id {me.get('id')})")
    log(f"  site     : {me.get('site_id')}")
    tags = me.get("tags") or []
    log("  tags     : " + (", ".join(tags) if tags else "(ninguno)"))
    log(f"  tipo     : {'access token de .env (modo dev)' if env.get('ML_ACCESS_TOKEN') else 'refresh token rotado y persistido'}")


def cargar_hub_o_salir(env):
    if not os.path.exists(HUB_PATH):
        log("hub/productos.json no existe: construyendolo desde POSGold...")
        construir_hub.construir(env)
    with open(HUB_PATH, encoding="utf-8") as f:
        return json.load(f)


def reporte_plan(plan, altas, acciones, estado=None):
    os.makedirs(DIR_REPORTES, exist_ok=True)
    ruta = os.path.join(DIR_REPORTES, "plan_ml_" +
                        dt.datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".json")
    limpia = [a for a in acciones]
    for a in limpia:
        a.pop("producto", None)
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"generado": dt.datetime.now().isoformat(timespec="seconds"),
                   "altas": [{k: v for k, v in a.items() if k != "producto"}
                             for a in altas],
                   "acciones": limpia,
                   "faltantes": (estado or {}).get("faltantes", {})},
                  f, ensure_ascii=False, indent=1)
    log(f"  plan completo (local, con margenes): {ruta}")


def mostrar_plan(acciones, altas, limite):
    tipos = {}
    for a in acciones:
        tipos[a["tipo"]] = tipos.get(a["tipo"], 0) + 1
    log(f"\nPLAN (altas posibles: {len(altas)}, limite de la corrida: {limite})")
    for k in ("pausar", "activar", "actualizar"):
        if tipos.get(k):
            log(f"  {k:11}: {tipos[k]}")
    if altas:
        log("  altas candidatas (margen sobre costo, despues de comision):")
        for a in altas[:10]:
            log(f"    ${a['precio']:>8,}  {a['margen']:>5.0%}  {a['titulo'][:52]}")
    if not acciones and not altas:
        log("  nada que hacer: el canal esta al dia.")


def main():
    ap = argparse.ArgumentParser(description="Adaptador Mercado Libre del hub (fase 1)")
    ap.add_argument("--autorizar", action="store_true",
                    help="flujo OAuth inicial: guarda el refresh token en .env")
    ap.add_argument("--prueba", action="store_true",
                    help="valida credenciales contra /users/me")
    ap.add_argument("--dry-run", action="store_true",
                    help="muestra el plan sin llamar a ML")
    ap.add_argument("--sincronizar", action="store_true",
                    help="ejecuta pausas/activaciones/stock/precio")
    ap.add_argument("--publicar", nargs="?", const=-1, type=int, metavar="N",
                    help="publica hasta N productos nuevos")
    ap.add_argument("--reconciliar", action="store_true",
                    help="reconstruye data/canal_ml.json desde ML")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    env = cargar_config()
    estado = cargar_estado()

    if args.autorizar:
        cmd_autorizar(env)
        return
    if args.prueba:
        cmd_prueba(env)
        return

    limite = args.publicar if (args.publicar is not None and args.publicar >= 0) \
        else int(env.get("ML_LIMITE_ALTA", "5"))
    hub = cargar_hub_o_salir(env)
    log(f"hub: {hub.get('total')} productos ({hub.get('generado')})")
    log(f"canal ML: {len(estado['listings'])} publicaciones en data/canal_ml.json")

    sesion = None
    necesita_ml = args.sincronizar or args.publicar is not None or args.reconciliar
    if args.reconciliar or (necesita_ml and (not estado["listings"]
                                             or not estado.get("vendedor"))):
        sesion = SesionML(env)
        vistos, total = reconciliar_estado(sesion, estado, hub)
        guardar_estado(estado)
        log(f"reconciliacion: {vistos} avisos leidos, {total} en el estado")

    acciones, altas = construir_plan(hub, estado, env, limite)

    if not (args.sincronizar or args.publicar is not None):
        mostrar_plan(acciones, altas, limite)
        if estado.get("faltantes"):
            log(f"  con atributos faltantes: {len(estado['faltantes'])} SKUs "
                "(ver data/canal_ml.json)")
        reporte_plan(None, altas, acciones, estado)
        log("\n(dry-run: no se llamo a ML. Con token configurado:")
        log("  python hub/canal_ml.py --sincronizar --publicar N)")
        return

    if sesion is None:
        sesion = SesionML(env)
    ok = fallos = 0
    log(f"\n[SINC] {len(acciones)} acciones sobre publicaciones existentes...")
    for a in acciones:
        try:
            bien, data = ejecutar_accion(sesion, estado, a)
        except Exception as e:
            bien, data = False, {"error": str(e)[:200]}
        if bien:
            ok += 1
            log(f"  {a['tipo']:11} OK  {a['sku']}  {a.get('titulo', '')[:44]}")
        else:
            fallos += 1
            log(f"  {a['tipo']:11} X   {a['sku']}: {str(data)[:160]}")
        guardar_estado(estado)
        time.sleep(0.4)

    if args.publicar is not None:
        log(f"\n[ALTA] hasta {limite} publicaciones nuevas...")
        for a in altas:
            try:
                bien, data = crear_publicacion(sesion, estado, env, a)
            except Exception as e:
                bien, data = False, {"error": str(e)[:200]}
            if bien:
                ok += 1
                l = estado["listings"][a["sku"]]
                log(f"  alta OK   {a['sku']}  {a['titulo'][:44]}  -> {l.get('permalink')}")
            else:
                fallos += 1
                log(f"  alta X    {a['sku']} ({a['titulo'][:40]}): {str(data)[:160]}")
            guardar_estado(estado)
            time.sleep(0.4)

    guardar_estado(estado)
    log(f"\nRESUMEN: {ok} exitos, {fallos} fallos")
    log("Estado del canal guardado en " + ESTADO_PATH)
    if fallos:
        log("(los fallos no cortan la corrida: ML modera y rechaza item por item)")
    log("Listo.")


if __name__ == "__main__":
    main()
