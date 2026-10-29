#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ml_diagnostico.py — diagnostico de Mercado Libre SIN rotar el refresh token.

Dos modos:
  --publico (default)  consulta endpoints publicos de ML (categorias,
                       atributos y domain_discovery) y compara el resultado
                       con el titulo real del catalogo. No necesita token.
  --con-token          usa SOLO ML_ACCESS_TOKEN (pegado a mano, dura ~6 h).
                       Si no existe, aborta: NUNCA llama al refresh token.

Objetivo: detectar categorias equivocadas, dominios que exigen family_name
(User Products), atributos obligatorios que faltan y publicaciones vivas que
no estan en data/canal_ml.json, antes de tocar el canal.

Uso:
  python hub/ml_diagnostico.py                      # las 5 muestras del plan
  python hub/ml_diagnostico.py --sku 001309 --sku 00723
  python hub/ml_diagnostico.py --con-token --limite 50
  python hub/ml_diagnostico.py --payloads          # arma payloads, no publica
  python hub/ml_diagnostico.py --json              # guarda reportes/ml_diagnostico_*.json

El reporte no imprime costos: solo SKU, titulo, categoria y atributos.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import sync_posgold as sp  # noqa: E402
import canal_ml as cml      # noqa: E402

API = "https://api.mercadolibre.com"
SITE = "MCO"
HUB_PATH = os.path.join(BASE_DIR, "hub", "productos.json")
DIR_REPORTES = os.path.join(BASE_DIR, "reportes")

MUESTRAS = [
    ("001309", "Abeja Baston Bolsa"),
    ("00695", "Caja Cubo Rubik 3X3 Ivycube Hoja Neon"),
    ("00711", "Piano De Juguete Surtido"),
    ("00723", "Nobody Sausage Magic Dancer"),
    ("00742", "Caja Marimba De Colores Madera"),
]

FALLBACK = "MCO1910"


def log(msg):
    print(msg, flush=True)


def leer_env():
    env = {}
    if not os.path.exists(sp.ENV_FILE):
        return env
    with open(sp.ENV_FILE, encoding="utf-8-sig") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in os.environ.items():
        if k.startswith("ML_"):
            env[k] = v
    return env


def pedir(ruta, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    codigo, texto = sp.http_texto(API + ruta, headers=headers, timeout=60)
    try:
        data = json.loads(texto) if texto.strip() else {}
    except Exception:
        data = {"raw": texto[:500]}
    return codigo, data


def es_juguete(dominio):
    if not dominio:
        return False
    d = dominio.upper()
    return ("TOY" in d or "GAMES" in d or "PUZZLE" in d
            or "DOLL" in d or "STUFFED" in d or "RADIO_CONTROL" in d)


def requeridos(attrs):
    if isinstance(attrs, dict):
        attrs = attrs.get("attributes") or []
    salida = []
    for a in attrs or []:
        tags = a.get("tags") or {}
        if (tags.get("required") or tags.get("catalog_required")
                or tags.get("grid_template_required")):
            salida.append({
                "id": a.get("id"),
                "name": a.get("name"),
                "value_type": a.get("value_type"),
                "values": len(a.get("values") or []),
            })
    return salida


def info_categoria(cid, token=None):
    codigo, data = pedir(f"/categories/{cid}", token)
    if codigo != 200 or not isinstance(data, dict):
        return {"categoria": cid, "error": f"HTTP {codigo}"}
    settings = data.get("settings") or {}
    codigo_a, attrs = pedir(f"/categories/{cid}/attributes", token)
    return {
        "categoria": cid,
        "nombre": data.get("name"),
        "dominio": settings.get("catalog_domain"),
        "listing_allowed": settings.get("listing_allowed"),
        "max_titulo": settings.get("max_title_length"),
        "requeridos": requeridos(attrs) if codigo_a == 200 else [],
    }


def discover(titulo, token=None):
    qs = urllib.parse.urlencode({"q": titulo, "limit": "5"})
    codigo, data = pedir(f"/sites/{SITE}/domain_discovery/search?{qs}", token)
    if codigo != 200 or not isinstance(data, list):
        return codigo, []
    return codigo, data


def diagnosticar_publico(sku, titulo, token=None):
    codigo, cands = discover(titulo, token)
    filas = []
    for c in cands:
        cid = c.get("category_id")
        if not cid:
            continue
        info = info_categoria(cid, token)
        info["domain_id"] = c.get("domain_id")
        info["es_juguete"] = es_juguete(info.get("dominio") or c.get("domain_id"))
        filas.append(info)
        time.sleep(0.25)
    fallback = info_categoria(FALLBACK, token)
    return {
        "sku": sku,
        "titulo": titulo,
        "http": codigo,
        "candidatas": filas,
        "fallback": fallback,
        "alertas": _alertas(titulo, filas, fallback),
    }


def _alertas(titulo, filas, fallback):
    alertas = []
    if not filas:
        alertas.append("domain_discovery no devolvio categoria")
        return alertas
    primera = filas[0]
    if not primera.get("es_juguete"):
        alertas.append(
            "la primera candidata NO es de juguetes: "
            f"{primera.get('categoria')} {primera.get('dominio')}")
    if any(f.get("dominio") == "MCO-PUZZLE_CUBES" for f in filas):
        alertas.append("dominio PUZZLE_CUBES exige family_name (User Products)")
    if fallback.get("listing_allowed") is not True:
        alertas.append("el fallback MCO1910 no permite publicar")
    req = {a.get("id") for a in (primera.get("requeridos") or [])}
    if req - {"BRAND", "MODEL", "ITEM_CONDITION", "GTIN"}:
        alertas.append("la primera candidata pide atributos extra: "
                       + ", ".join(sorted(req - {"BRAND", "MODEL", "ITEM_CONDITION", "GTIN"})))
    if primera.get("max_titulo") and len(titulo) > int(primera["max_titulo"]):
        alertas.append(f"titulo de {len(titulo)} supera el maximo {primera['max_titulo']}")
    return alertas


def mostrar_publico(r):
    log(f"\n[{r['sku']}] {r['titulo']}")
    if not r["candidatas"]:
        log("  sin candidatas")
    for c in r["candidatas"]:
        log(f"  -> {c.get('categoria')}  {c.get('nombre')}")
        log(f"     dominio={c.get('dominio')} juguete={c.get('es_juguete')} "
            f"listing_allowed={c.get('listing_allowed')} "
            f"max_titulo={c.get('max_titulo')}")
        if c.get("requeridos"):
            log("     requeridos: " + ", ".join(
                a.get("id") or "?" for a in c["requeridos"]))
    f = r["fallback"]
    log(f"  fallback {f.get('categoria')} {f.get('nombre')} "
        f"dominio={f.get('dominio')} listing_allowed={f.get('listing_allowed')}")
    for a in r["alertas"]:
        log("  ALERTA: " + a)


def diagnosticar_token(env, limite):
    token = env.get("ML_ACCESS_TOKEN")
    if not token:
        log("ERROR: --con-token exige ML_ACCESS_TOKEN en .env o en el proceso.")
        log("       No se usa el refresh token: rotarlo esta bloqueado.")
        log("       Consigue un access token temporal y vuelve a intentar.")
        sys.exit(2)
    codigo, me = pedir("/users/me", token)
    if codigo != 200:
        log(f"ERROR: /users/me respondio {codigo}: {str(me)[:200]}")
        sys.exit(1)
    tags = me.get("tags") or []
    log("\nVENDEDOR")
    log(f"  id={me.get('id')} nick={me.get('nickname')} site={me.get('site_id')}")
    log("  tags: " + (", ".join(tags) if tags else "(ninguno)"))
    for t in ("user_product_seller", "catalog_seller", "business", "official_store"):
        if t in tags:
            log(f"  OJO: tag activo {t}")
    uid = me.get("id")
    qs = urllib.parse.urlencode({"status": "active,paused", "limit": str(limite)})
    codigo, bus = pedir(f"/users/{uid}/items/search?{qs}", token)
    ids = (bus or {}).get("results") or []
    log(f"\nPUBLICACIONES activas/pausadas: {len(ids)} (limite {limite})")
    filas = []
    for lid in ids:
        codigo, item = pedir(f"/items/{lid}", token)
        if codigo != 200:
            log(f"  {lid}: HTTP {codigo}")
            continue
        attrs = {a.get("id"): a.get("value_name") for a in item.get("attributes") or []}
        sku = item.get("seller_custom_field") or attrs.get("SELLER_SKU")
        fila = {
            "listing_id": lid,
            "sku": sku,
            "titulo": item.get("title"),
            "estado": item.get("status"),
            "categoria": item.get("category_id"),
            "precio": item.get("price"),
            "stock": item.get("available_quantity"),
            "permalink": item.get("permalink"),
            "user_product_id": item.get("user_product_id"),
            "family_name": item.get("family_name"),
        }
        filas.append(fila)
        log(f"  {lid} {fila['estado']:7} {sku or 'SIN-SKU':12} "
            f"{fila['categoria']} {str(fila['titulo'])[:50]}")
        if fila["user_product_id"] or fila["family_name"]:
            log("       (User Products: revisar family_name/user_product_id)")
        time.sleep(0.25)
    return {"vendedor": {"id": uid, "nickname": me.get("nickname"), "tags": tags},
            "publicaciones": filas}


def cargar_muestras(skus):
    por_sku = {}
    if os.path.exists(HUB_PATH):
        with open(HUB_PATH, encoding="utf-8") as f:
            hub = json.load(f)
        por_sku = {p.get("sku"): p for p in hub.get("productos") or []}
    if skus:
        return [(sku, (por_sku.get(sku) or {}).get("nombre") or sku)
                for sku in skus]
    productos = []
    for sku, titulo in MUESTRAS:
        p = por_sku.get(sku) or {}
        productos.append((sku, p.get("nombre") or titulo))
    return productos


class SesionPublica:
    def get(self, ruta):
        return pedir(ruta)


def payloads_publicos(env, limite, skus, token=None):
    """Arma payloads completos con endpoints publicos, sin publicar ni
    validar (el validador exige token). Si hay access token, lee los tags
    del vendedor para decidir el flujo User Products."""
    if not os.path.exists(HUB_PATH):
        log("ERROR: falta hub/productos.json; corre construir_hub.py")
        return []
    with open(HUB_PATH, encoding="utf-8") as f:
        hub = json.load(f)
    est = cml.normalizar_estado({})
    tags = []
    if token:
        codigo, me = pedir("/users/me", token)
        if codigo == 200:
            tags = me.get("tags") or []
        else:
            log(f"AVISO: /users/me respondio {codigo}; se asume flujo legacy.")
    est["vendedor"] = {"tags": tags}
    sesion = SesionPublica()
    limite_plan = len(hub.get("productos") or []) if skus else limite
    _, altas = cml.construir_plan(hub, est, env, limite_plan)
    if skus:
        altas = [a for a in altas if a["sku"] in set(skus)]
    log(f"\nPAYLOADS (modo publico, sin publicar; "
        f"{'tags=' + ','.join(tags) if tags else 'sin token: se asume legacy'})")
    salida = []
    for a in altas:
        payload, meta = cml.construir_payload(sesion, est, env, a)
        if payload is None:
            log(f"  {a['sku']:14} ERROR {meta}")
            salida.append({"sku": a["sku"], "error": meta})
            continue
        ids = [x.get("id") for x in payload.get("attributes", [])]
        log(f"  {a['sku']:14} {meta['categoria']:9} "
            f"${payload['price']:>8,} stock={payload['available_quantity']:>3} "
            f"attrs={','.join(ids)}")
        salida.append({"sku": a["sku"], "categoria": meta["categoria"],
                       "marca": meta["marca"], "modelo": meta["modelo"],
                       "up": meta["up"], "payload": payload})
    return salida


def main():
    ap = argparse.ArgumentParser(description="Diagnostico ML sin rotar el refresh token")
    ap.add_argument("--con-token", action="store_true",
                    help="usa solo ML_ACCESS_TOKEN; nunca refresca")
    ap.add_argument("--limite", type=int, default=None,
                    help="maximo a revisar (publicaciones: 50; payloads: 5)")
    ap.add_argument("--sku", action="append", default=[],
                    help="SKU a diagnosticar (repetible)")
    ap.add_argument("--payloads", action="store_true",
                    help="arma payloads completos con datos publicos (no publica)")
    ap.add_argument("--json", action="store_true",
                    help="guarda el reporte en reportes/")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    env = leer_env()
    token = env.get("ML_ACCESS_TOKEN") if args.con_token else None
    limite = args.limite if args.limite is not None else (5 if args.payloads else 50)
    if args.con_token and not token:
        diagnosticar_token(env, limite)  # aborta con instrucciones
    if args.payloads:
        payloads = payloads_publicos(env, limite, args.sku, token)
        if args.json:
            os.makedirs(DIR_REPORTES, exist_ok=True)
            ruta = os.path.join(DIR_REPORTES, "ml_payloads_" +
                                dt.datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".json")
            with open(ruta, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"generado": dt.datetime.now().isoformat(timespec="seconds"),
                           "payloads": payloads}, f, ensure_ascii=False, indent=1)
            log("\nReporte: " + ruta)
        log("\nListo. No se publico ni se roto ningun token.")
        return
    muestras = cargar_muestras(args.sku)
    log("DIAGNOSTICO ML - modo " + ("ACCESS TOKEN (sin refresh)" if args.con_token
                                    else "PUBLICO (sin credenciales)"))
    resultados = []
    for sku, titulo in muestras:
        r = diagnosticar_publico(sku, titulo, token)
        mostrar_publico(r)
        resultados.append(r)
    reporte = {
        "generado": dt.datetime.now().isoformat(timespec="seconds"),
        "modo": "access_token" if args.con_token else "publico",
        "resultados": resultados,
    }
    if args.con_token:
        reporte["vendedor"] = diagnosticar_token(env, limite)
    if args.json:
        os.makedirs(DIR_REPORTES, exist_ok=True)
        ruta = os.path.join(DIR_REPORTES, "ml_diagnostico_" +
                            dt.datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".json")
        with open(ruta, "w", encoding="utf-8", newline="\n") as f:
            json.dump(reporte, f, ensure_ascii=False, indent=1)
        log("\nReporte: " + ruta)
    log("\nListo. No se roto ningun token.")


if __name__ == "__main__":
    main()
