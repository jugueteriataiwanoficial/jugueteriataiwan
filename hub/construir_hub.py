#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
construir_hub.py — FASE 0 del hub: el catalogo maestro de productos.

Genera hub/productos.json: la fuente unica de verdad que LEEN los
adaptadores de canal (hoy canal_ml.py; manana otros marketplaces).
Reutiliza las mismas primitivas de sync_posgold.py (login JWT, consulta
de productos, Excel de costos, cache de slugs) para que hub y vitrina
nunca diverjan.

Que trae el hub:
  - TODOS los productos de POSGold en la bodega configurada (activos o
    no, con o sin existencias): los canales deciden que publicar, y
    necesitan saber cuando un producto se agoto para PAUSAR la
    publicacion, no solo cuando aparece.
  - Costo unido por Productoid (mismo Excel/contrato de sync_posgold).
  - Fotos originales de POSGold (URLs publicas, sin descargarlas: el
    servidor de fotos es lento y aqui no hace falta verificarlas).
  - Reglas precalculadas: vendible (la regla de la vitrina sin foto ni
    costo), slug_web conocido del cache, EAN cuando el SKU lo es.

SEGURIDAD: este archivo SIEMPRE viaja con costos. Queda en .gitignore
(hub/productos.json) y NUNCA se commitea ni se imprime a consola con
valores de costo. La consola solo muestra conteos y porcentajes.

Uso:
  python hub/construir_hub.py                 (construye hub/productos.json)
  python hub/construir_hub.py --costos RUTA   (Excel de costos explicito)
  python hub/construir_hub.py --salida RUTA   (destino distinto, para pruebas)
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import sync_posgold as sp  # noqa: E402  (primitivas compartidas del POS)

HUB_DIR = os.path.join(BASE_DIR, "hub")
HUB_PATH = os.path.join(HUB_DIR, "productos.json")


def log(msg):
    print(msg, flush=True)


def ean_de(sku):
    """El SKU numerico de 13 digitos es un EAN real (para el GTIN de ML)."""
    return sku if re.fullmatch(r"\d{13}", sku or "") else None


def construir(env, ruta_costos=None, salida=HUB_PATH):
    campo_costo = env.get("COSTO_CAMPO", "costopromedio")
    bodega = env.get("POSGOLD_BODEGAID", "2")

    log("=" * 66)
    log("HUB — catalogo maestro | " + dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log("=" * 66)

    # --- costos (mismo Excel y mismo contrato que sync_posgold) ---
    excel = sp.encontrar_excel_costos(ruta_costos)
    if not excel:
        log("ERROR: no hay Excel de costos (PRODUCTOS*.xlsx) en la carpeta.")
        sys.exit(1)
    log(f"[1/3] Costos desde: {os.path.basename(excel)} (campo: {campo_costo})")
    costos = sp.cargar_costos(excel, campo_costo)
    log(f"      {len(costos)} productos con costo conocido")

    # --- productos de POSGold ---
    log("\n[2/3] Login POSGold y consulta de productos...")
    jwt = sp.login_jwt(env)
    productos_api = sp.consultar_productos(env, jwt)
    log(f"      {len(productos_api)} productos en bodega {bodega}")

    # --- cache de slugs conocidos (el mismo de la vitrina) ---
    cache = sp.cargar_cache()
    por_codigo_cache = cache.get("por_codigo", {})

    log("\n[3/3] Normalizando al modelo del hub...")
    productos = []
    codigos_vistos = set()

    # Defaults de dimensiones por categoría web (cm, g)
    # ML exige dimensions = "altoxanchoxlargo,peso" en cm y g
    DIMS_DEFAULT = {
        "jugueteria":        {"alto": 10, "ancho": 10, "largo": 5, "peso": 200},
        "juego-de-mesa":     {"alto": 30, "ancho": 20, "largo": 5, "peso": 500},
        "didactico":         {"alto": 20, "ancho": 15, "largo": 5, "peso": 300},
        "cubo-rubik":        {"alto": 6,  "ancho": 6,  "largo": 6, "peso": 100},
        "peluches":          {"alto": 25, "ancho": 15, "largo": 10, "peso": 200},
        "animales":          {"alto": 10, "ancho": 8,  "largo": 5, "peso": 150},
        "burbujeros":        {"alto": 15, "ancho": 10, "largo": 5, "peso": 150},
        "colecciones":       {"alto": 10, "ancho": 8,  "largo": 5, "peso": 100},
        "deporte":           {"alto": 20, "ancho": 15, "largo": 10, "peso": 300},
        "escolar":           {"alto": 15, "ancho": 10, "largo": 5, "peso": 200},
        "hogar":             {"alto": 10, "ancho": 10, "largo": 5, "peso": 200},
        "piñateria":         {"alto": 10, "ancho": 10, "largo": 5, "peso": 100},
        "slime":             {"alto": 8,  "ancho": 8,  "largo": 8, "peso": 150},
        "termos":            {"alto": 15, "ancho": 8,  "largo": 8, "peso": 200},
        "armatodo":          {"alto": 15, "ancho": 10, "largo": 5, "peso": 200},
        "bebe":              {"alto": 10, "ancho": 10, "largo": 5, "peso": 150},
        "cacharro":          {"alto": 10, "ancho": 10, "largo": 5, "peso": 100},
    }
    FALLBACK_DIMS = {"alto": 10, "ancho": 10, "largo": 5, "peso": 200}
    for it in productos_api:
        pid = it.get("Productoid")
        sku = sp.codigo_limpio(it.get("Codigo") or it.get("Producto_cod"))
        if not sku:
            continue
        nombre = (it.get("Producto") or "").strip()
        iva = float(it.get("ProductoImpuestoPorcentaje") or 0)
        p2 = float(it.get("Precio2") or 0)
        p5 = float(it.get("Precio5") or 0)
        base_sin_iva = (p2 / (1 + iva / 100)) if iva > 0 else p2
        costo = costos.get(pid)
        cat_web = sp.categoria_web(it.get("Categoria"))

        # dimensiones: POSGold trae paca_altura, paca_ancho, paca_profundidad, cubicaje
        # ML exige "altoxanchoxlargo,peso" en cm y g
        try:
            alto = float(it.get("paca_altura") or it.get("PacaAltura") or 0)
            ancho = float(it.get("paca_ancho") or it.get("PacaAncho") or 0)
            largo = float(it.get("paca_profundidad") or it.get("PacaProfundidad") or 0)
            peso = float(it.get("Peso") or 0)
        except Exception:
            alto = ancho = largo = peso = 0
        # Usar defaults por categoría si POSGold no trae datos
        dims = DIMS_DEFAULT.get(cat_web, FALLBACK_DIMS)
        if not alto: alto = dims["alto"]
        if not ancho: ancho = dims["ancho"]
        if not largo: largo = dims["largo"]
        if not peso: peso = dims["peso"]

        # fotos originales de POSGold (publicas): sin descargar nada.
        # Se descarta el placeholder "sin imagen" y las repetidas.
        base_fotos = env["POSGOLD_BASE"].rstrip("/") + "/Files/Images/"
        fotos, vistos = [], set()
        for x in (it.get("Imagenes") or []):
            a = str(x).strip()
            if not a or sp.PLACEHOLDER_POSGOLD in a.lower() or a in vistos:
                continue
            vistos.add(a)
            import urllib.parse
            fotos.append(base_fotos + urllib.parse.quote(a))

        activo = it.get("Activo") is True
        stock = int(it.get("Disponible") or 0)
        prod = {
            "productoid": pid,
            "sku": sku,
            "ean": ean_de(sku),
            "nombre": nombre,
            "categoria_posgold": (it.get("Categoria") or "").strip(),
            "categoria_web": cat_web,
            "activo": activo,
            "stock": stock,
            "precio_detal": int(round(p2)) if p2 > 0 else None,
            "precio5": int(round(p5)) if p5 > 0 else None,
            "precio_base_sin_iva": round(base_sin_iva, 1) if p2 > 0 else None,
            "precio1": int(round(float(it.get("Precio1") or 0))) or None,
            "iva": iva,
            "costo": costo,
            "utilidad": (round(base_sin_iva - costo, 1) if (costo and p2 > 0) else None),
            "margen_costo_pct": (round((base_sin_iva - costo) / costo * 100, 2)
                                 if (costo and base_sin_iva > 0) else None),
            "fotos": fotos,
            "slug_web": (por_codigo_cache.get(sku) or {}).get("slug"),
            "duplicado": sku in codigos_vistos,
            "alto": int(round(alto)),
            "ancho": int(round(ancho)),
            "largo": int(round(largo)),
            "peso": int(round(peso)),
        }
        codigos_vistos.add(sku)
        # vendible = la regla de la vitrina SIN exigir foto ni costo:
        # cada canal decide si esas dos condiciones le aplican.
        prod["vendible"] = bool(activo and stock > 0 and p2 > 0 and cat_web
                                and not prod["duplicado"])
        productos.append(prod)

    hub = {
        "version": 1,
        "generado": dt.datetime.now().isoformat(timespec="seconds"),
        "bodega": bodega,
        "campo_costo": campo_costo,
        "total": len(productos),
        "productos": productos,
    }

    os.makedirs(os.path.dirname(salida), exist_ok=True)
    tmp = salida + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(hub, f, ensure_ascii=False, indent=1)
    os.replace(tmp, salida)

    # resumen SOLO con conteos (los costos nunca van a consola)
    activos = sum(1 for p in productos if p["activo"])
    con_stock = sum(1 for p in productos if p["stock"] > 0)
    vendibles = sum(1 for p in productos if p["vendible"])
    con_costo = sum(1 for p in productos if p["costo"])
    con_foto = sum(1 for p in productos if p["fotos"])
    con_ean = sum(1 for p in productos if p["ean"])
    dup = sum(1 for p in productos if p["duplicado"])
    log(f"      {len(productos)} productos normalizados")
    log(f"      activos: {activos} | con existencias: {con_stock} | "
        f"vendibles (regla vitrina): {vendibles}")
    log(f"      con costo: {con_costo} | con foto: {con_foto} | "
        f"con EAN: {con_ean} | codigos duplicados: {dup}")
    log(f"\nHub escrito en: {salida} ({os.path.getsize(salida):,} bytes)")
    log("  (archivo con costos: gitignored, NUNCA se commitea)")
    return hub


def main():
    ap = argparse.ArgumentParser(description="Catalogo maestro del hub (fase 0)")
    ap.add_argument("--costos", help="ruta del Excel de costos (default: PRODUCTOS*.xlsx mas reciente)")
    ap.add_argument("--salida", default=HUB_PATH, help="destino del JSON (default: hub/productos.json)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    env = sp.cargar_env()
    construir(env, ruta_costos=args.costos, salida=args.salida)
    log("\nListo.")


if __name__ == "__main__":
    main()
