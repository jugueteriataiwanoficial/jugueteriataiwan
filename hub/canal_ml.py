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
  ML_PRECIO_FACTOR=0.15             recargo sobre el detal de la vitrina
  ML_PRECIO_REDONDEO=100
  ML_COMISION_PCT=16                comision ML aproximada (cuidado del precio)
  ML_MARGEN_MINIMO=0.25             utilidad/costo minima para publicar
  ML_STOCK_MINIMO=2                 no publicar con menos existencias
  ML_STOCK_MAXIMO=99                techo publicado (no regala el inventario)
  ML_LIMITE_ALTA=5                  altas maximas por corrida
  ML_PRECIO_MINIMO=0                no publicar por debajo de este precio
  ML_CATEGORIAS_EXCLUIDAS=cacharro  slugs de categoria_web vetados en ML
  ML_REDIRECT_URI                   el registrado en la app (para --autorizar)

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
DIR_REPORTES = sp.DIR_REPORTES
API = "https://api.mercadolibre.com"
AUTH = "https://auth.mercadolibre.com"
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


def cargar_estado():
    if os.path.exists(ESTADO_PATH):
        try:
            with open(ESTADO_PATH, encoding="utf-8") as f:
                est = json.load(f)
        except Exception:
            est = {}
        if isinstance(est, dict) and est.get("listings"):
            return est
    return {"version": 1, "site": "MCO", "actualizado": None,
            "listings": {}, "categorias": {}}


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
                                              timeout=90)
            except Exception as e:
                ultimo = str(e)[:200]
                if i < reintentos:
                    time.sleep(pausas[min(i, len(pausas) - 1)])
                    continue
                raise
            if codigo == 429 and i < reintentos:
                time.sleep(pausas[min(i, len(pausas) - 1)])
                continue
            return codigo, (json.loads(texto) if texto.strip() else {})
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
    """Precio del canal: detal de la vitrina x recargo, redondeado.
    El recargo cubre la comision de ML; la rentabilidad final se vigila
    aparte con pasa_rentabilidad()."""
    base = float(p.get("precio_detal") or 0)
    factor = float(env.get("ML_PRECIO_FACTOR", "0.15"))
    redondeo = int(env.get("ML_PRECIO_REDONDEO", "100"))
    precio = base * (1 + factor)
    precio = int(round(precio / redondeo) * redondeo) if redondeo > 1 else int(round(precio))
    return max(precio, 0)


def pasa_rentabilidad(p, precio, env):
    """Utilidad/costo minima DESPUES de la comision de ML. Sin costo
    conocido no se publica (misma regla de la vitrina)."""
    costo = p.get("costo")
    if not costo:
        return False, "sin costo"
    com = float(env.get("ML_COMISION_PCT", "16")) / 100.0
    mmin = float(env.get("ML_MARGEN_MINIMO", "0.25"))
    util = precio * (1 - com) - costo
    return (util / costo) >= mmin, round(util / costo, 2)


def titulo_ml(nombre, env):
    t = re.sub(r"\s+", " ", (nombre or "").strip()).title()
    maximo = int(env.get("ML_TITULO_MAX", "60"))
    if len(t) > maximo:
        t = t[:maximo].rsplit(" ", 1)[0].strip()
    return t


def descripcion_ml(p, titulo):
    sku = p.get("sku") or ""
    return (f"{titulo}.\n"
            f"Producto nuevo, original y sellado.\n"
            f"Referencia: {sku}.\n"
            f"Jugueteria Taiwan - Cali, Colombia. Despachos a todo el pais.")


def categorias_excluidas(env):
    return {s.strip().lower() for s in
            env.get("ML_CATEGORIAS_EXCLUIDAS", "cacharro").split(",") if s.strip()}


def elegible_para_alta(p, estado, env):
    """Reglas de alta: las del canal. Sin costo conocido no entra (misma
    regla dura de la vitrina)."""
    sku = p.get("sku")
    if not sku or sku in estado["listings"] or p.get("duplicado"):
        return None
    if not p.get("vendible"):
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
    for sku, l in sorted(estado["listings"].items()):
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
# Ejecucion del plan
# ------------------------------------------------------------------


def categoria_para(sesion, titulo, estado):
    """Categorizador automatico: domain discovery de ML con cache en el
    estado (evita repetir la busqueda por producto)."""
    cache = estado.setdefault("categorias", {})
    clave = sp.sin_acentos(titulo)[:48]
    if clave in cache:
        return cache[clave]
    site = estado.get("site", "MCO")
    qs = urllib.parse.urlencode({"q": titulo, "limit": "3"})
    codigo, data = sesion.get(f"/sites/{site}/domain_discovery/search?{qs}")
    cid = None
    if codigo == 200 and isinstance(data, list) and data:
        cid = (data[0] or {}).get("category_id")
    if not cid:
        raise RuntimeError(f"sin categoria para '{titulo}' (HTTP {codigo})")
    cache[clave] = cid
    return cid


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


def crear_publicacion(sesion, est, env, alta):
    p = alta["producto"]
    site = est.get("site", "MCO")
    titulo = alta["titulo"]
    cid = categoria_para(sesion, titulo, est)
    payload = {
        "title": titulo,
        "category_id": cid,
        "price": alta["precio"],
        "currency_id": "COP",
        "available_quantity": alta["stock"],
        "buying_mode": "buy_it_now",
        "listing_type_id": env.get("ML_TIPO_PUBLICACION", "gold_special"),
        "condition": "new",
        "description": {"plain_text": descripcion_ml(p, titulo)},
        "pictures": [{"source": u} for u in p["fotos"][:10]],
    }
    if p.get("ean"):
        payload["attributes"] = [{"id": "GTIN", "value_name": p["ean"]}]
    codigo, data = sesion.post(f"/items", payload)
    if codigo in (200, 201):
        sku = p["sku"]
        est["listings"][sku] = {
            "listing_id": data.get("id"),
            "titulo": titulo,
            "permalink": data.get("permalink"),
            "estado": data.get("status"),
            "precio": alta["precio"],
            "stock_publicado": alta["stock"],
            "categoria_ml": cid,
            "creado": dt.datetime.now().isoformat(timespec="seconds"),
        }
        return True, data
    return False, data


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
    log(f"  tipo     : {'access token de .env (modo dev)' if env.get('ML_ACCESS_TOKEN') else 'refresh token rotado y persistido'}")


def cargar_hub_o_salir(env):
    if not os.path.exists(HUB_PATH):
        log("hub/productos.json no existe: construyendolo desde POSGold...")
        construir_hub.construir(env)
    with open(HUB_PATH, encoding="utf-8") as f:
        return json.load(f)


def reporte_plan(plan, altas, acciones):
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
                   "acciones": limpia}, f, ensure_ascii=False, indent=1)
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

    acciones, altas = construir_plan(hub, estado, env, limite)

    if not (args.sincronizar or args.publicar is not None):
        mostrar_plan(acciones, altas, limite)
        reporte_plan(None, altas, acciones)
        log("\n(dry-run: no se llamo a ML. Con token configurado:")
        log("  python hub/canal_ml.py --sincronizar --publicar N)")
        return

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
