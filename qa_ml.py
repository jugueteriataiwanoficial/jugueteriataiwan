#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qa_ml.py — QA offline del adaptador de Mercado Libre.

No toca la red, no lee costos del Excel y no rota tokens. Prueba:
precios, rentabilidad, titulos, marcas/modelos, categorias curadas,
atributos obligatorios, plan de altas/pausas y reconciliacion de estado.

Uso: python qa_ml.py
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "hub"))

import canal_ml as c  # noqa: E402

ENV = {
    "ML_PRECIO_REDONDEO": "100",
    "ML_COMISION_PCT": "15",
    "ML_PASARELA_PCT": "5",
    "ML_ENVIO_COSTO": "23000",
    "ML_MARGEN_MINIMO": "0.25",
    "ML_STOCK_MINIMO": "2",
    "ML_STOCK_MAXIMO": "99",
    "ML_PRECIO_MINIMO": "0",
    "ML_TITULO_MAX": "60",
}

FALLOS = []
CONTADOR = {"total": 0}


def check(nombre, cond, detalle=""):
    CONTADOR["total"] += 1
    if cond:
        print(f"  OK   {nombre}")
    else:
        print(f"  FALLA {nombre} {detalle}")
        FALLOS.append(nombre)


class FakeSession:
    def __init__(self, rutas):
        self.rutas = rutas
        self.llamadas = []

    def post(self, ruta, datos):
        self.llamadas.append(ruta)
        return 200, {"id": "M-NUEVO"}

    def get(self, ruta):
        self.llamadas.append(ruta)
        for prefijo, respuesta in sorted(self.rutas.items(),
                                          key=lambda kv: len(kv[0]),
                                          reverse=True):
            if ruta.startswith(prefijo):
                return respuesta
        return 404, {"error": "ruta no simulada", "ruta": ruta}


class FakeValidacion(FakeSession):
    def __init__(self, rutas, validacion):
        super().__init__(rutas)
        self.validacion = validacion

    def post(self, ruta, datos):
        self.llamadas.append(ruta)
        if ruta == "/items/validate":
            return self.validacion
        if ruta == "/items":
            return 201, {"id": "M-NUEVO", "title": "Producto"}
        if ruta.endswith("/description"):
            return 201, {}
        return 200, {}

    def get(self, ruta):
        self.llamadas.append(ruta)
        for prefijo, respuesta in sorted(self.rutas.items(),
                                          key=lambda kv: len(kv[0]),
                                          reverse=True):
            if ruta.startswith(prefijo):
                return respuesta
        return 404, {"error": "ruta no simulada", "ruta": ruta}


def info(cid, nombre, dominio, allowed=True, max_titulo=60):
    return 200, {"name": nombre,
                 "settings": {"catalog_domain": dominio,
                              "listing_allowed": allowed,
                              "max_title_length": max_titulo}}


def attrs(*requeridos):
    return 200, {"attributes": list(requeridos)}


def attr(aid, value_type="string", values=None, required=True):
    return {"id": aid, "name": aid, "value_type": value_type,
            "values": values or [], "tags": {"required": required}}


def probar_precio_y_rentabilidad():
    print("\nPRECIO Y RENTABILIDAD")
    # Formula: (Precio5 + 23000) / 0.80, redondeado a 100
    check("precio5 10000 -> (10000+23000)/0.80 = 41250 -> 41300",
          c.precio_ml({"precio5": 10000}, ENV) == 41300)
    check("precio5 8000 -> (8000+23000)/0.80 = 38750 -> 38800",
          c.precio_ml({"precio5": 8000}, ENV) == 38800)
    check("sin precio5 no hay precio",
          c.precio_ml({"precio_detal": 10000}, ENV) == 0)
    # Rentabilidad con comision 15% + pasarela 5% = 20% total
    ok, margen = c.pasa_rentabilidad({"costo": 1000}, 2000, ENV)
    check("margen 60% pasa", ok and margen == 0.60, str((ok, margen)))
    ok, _ = c.pasa_rentabilidad({"costo": 2000}, 2000, ENV)
    check("sin utilidad no pasa", not ok)
    check("sin costo no pasa", not c.pasa_rentabilidad({}, 2000, ENV)[0])


def probar_titulo_marca_modelo():
    print("\nTITULO, MARCA Y MODELO")
    t = c.titulo_ml("  caja   cubo rubik 3x3 ivycube hoja neon  ", ENV)
    check("titulo normalizado", "  " not in t and len(t) <= 60, t)
    b, m = c.marca_modelo({"sku": "X", "nombre": "Funko Pop Harry Potter",
                           "marca": "Funko"})
    check("marca explicita", b == "Funko", b)
    check("modelo sin marca", m == "Pop Harry Potter", m)
    b2, _ = c.marca_modelo({"sku": "001309", "nombre": "ABEJA BASTON BOLSA"})
    check("marca por defecto", b2.startswith("Jugueter"), b2)
    b3, _ = c.marca_modelo({"sku": "00695",
                            "nombre": "CAJA CUBO RUBIK 3X3 IVYCUBE"})
    check("marca por palabra", b3 == "Rubik", b3)
    check("EAN-13 valido", c.ean13_valido("4006381333931"))
    check("EAN-13 invalido", not c.ean13_valido("4006381333932"))
    check("SKU corto no es EAN", not c.ean13_valido("123"))


def probar_categorias():
    print("\nCATEGORIAS")
    c.ATRIBUTOS_CACHE.clear()
    s = FakeSession({
        "/sites/MCO/domain_discovery/search": (200, [
            {"category_id": "MCO430205", "domain_id": "MCO-BEEKEEPING_BOXES"},
        ]),
        "/categories/MCO430205": info("MCO430205", "Colmenas",
                                      "MCO-BEEKEEPING_BOXES"),
        "/categories/MCO1910": info("MCO1910", "Otros", "MCO-TOYS_AND_GAMES"),
    })
    est = c.normalizar_estado({})
    est["vendedor"] = {"tags": ["user_product_seller"]}
    cid = c.categoria_para(s, "ABEJA BASTON BOLSA", est)
    clave = c.sp.sin_acentos("ABEJA BASTON BOLSA")[:48]
    check("abeja cae al fallback", cid == "MCO1910", cid)
    check("fuente fallback",
          est["categorias_fuente"].get(clave) == "fallback",
          str(est["categorias_fuente"]))
    check("no acepta colmenas", all("MCO430205" not in x for x in
                                    [cid]))
    cfg = c.cargar_categorias()
    check("dominio toy sin prefijo",
          c._es_dominio_juguete("MCO-NON_REMOTE_CONTROL_TOY_VEHICLES", cfg))
    check("dominio no toy",
          not c._es_dominio_juguete("MCO-BEEKEEPING_BOXES", cfg))
    check("dominio pet toy no es juguete",
          not c._es_dominio_juguete("MCO-PET_TOY_BALLS", cfg))

    c.ATRIBUTOS_CACHE.clear()
    s2 = FakeSession({
        "/categories/MCO189402": info("MCO189402", "Pianos y Organos",
                                      "MCO-TOY_KEYBOARDS_AND_PIANOS"),
        "/categories/MCO1910": info("MCO1910", "Otros", "MCO-TOYS_AND_GAMES"),
    })
    est2 = c.normalizar_estado({})
    est2["vendedor"] = {"tags": ["user_product_seller"]}
    cid2 = c.categoria_para(s2, "PIANO DE JUGUETE SURTIDO", est2)
    clave2 = c.sp.sin_acentos("PIANO DE JUGUETE SURTIDO")[:48]
    check("piano usa categoria curada", cid2 == "MCO189402", cid2)
    check("fuente curada",
          est2["categorias_fuente"].get(clave2) == "curada",
          str(est2["categorias_fuente"]))

    c.ATRIBUTOS_CACHE.clear()
    s3 = FakeSession({
        "/categories/MCO170729": info("MCO170729", "Cubo Rubik",
                                      "MCO-PUZZLE_CUBES"),
        "/categories/MCO1910": info("MCO1910", "Otros", "MCO-TOYS_AND_GAMES"),
    })
    est3 = c.normalizar_estado({})
    est3["vendedor"] = {"tags": []}
    cid3 = c.categoria_para(s3, "CUBO RUBIK 3X3", est3)
    check("cubo sin tag UP cae al fallback", cid3 == "MCO1910", cid3)
    est4 = c.normalizar_estado({})
    est4["vendedor"] = {"tags": ["user_product_seller"]}
    cid4 = c.categoria_para(s3, "CUBO RUBIK 3X3", est4)
    check("cubo con tag UP usa MCO170729", cid4 == "MCO170729", cid4)


def probar_atributos():
    print("\nATRIBUTOS OBLIGATORIOS")
    c.ATRIBUTOS_CACHE.clear()
    s = FakeSession({
        "/categories/MCO1910/attributes": attrs(
            attr("BRAND"),
            attr("MANUFACTURER", required=False),
            attr("SELLER_SKU", required=False),
            attr("ITEM_CONDITION", "list",
                 [{"id": "2230284", "name": "Nuevo"}]),
        ),
    })
    p = {"sku": "4006381333931", "nombre": "X", "ean": "4006381333931"}
    a, f = c.atributos_para(s, "MCO1910", p, "Marca", "Modelo")
    ids = {x.get("id") for x in a}
    check("BRAND presente", "BRAND" in ids, str(ids))
    check("MANUFACTURER opcional", "MANUFACTURER" in ids, str(ids))
    check("SELLER_SKU opcional", "SELLER_SKU" in ids, str(ids))
    check("ITEM_CONDITION presente", "ITEM_CONDITION" in ids, str(ids))
    check("GTIN real presente", "GTIN" in ids, str(ids))
    check("sin faltantes", not f, str(f))

    c.ATRIBUTOS_CACHE.clear()
    s2 = FakeSession({
        "/categories/MCO9999/attributes": attrs(
            attr("BRAND"), attr("MODEL"), attr("COLOR"),
            attr("ITEM_CONDITION", "list",
                 [{"id": "2230284", "name": "Nuevo"}]),
        ),
    })
    a2, f2 = c.atributos_para(s2, "MCO9999", p, "Marca", "Modelo")
    check("atributo desconocido falta", any(x.get("id") == "COLOR" for x in f2),
          str(f2))
    check("no publica a medias", "COLOR" not in {x.get("id") for x in a2})


def probar_payloads():
    print("\nPAYLOADS COMPLETOS")
    c.ATRIBUTOS_CACHE.clear()
    s = FakeSession({
        "/categories/MCO1910": info("MCO1910", "Otros", "MCO-TOYS_AND_GAMES"),
        "/categories/MCO1910/attributes": attrs(
            attr("BRAND"), attr("MODEL"),
            attr("ITEM_CONDITION", "list",
                 [{"id": "2230284", "name": "Nuevo"}]),
        ),
    })
    p = {"sku": "00711", "nombre": "PRODUCTO GENERICO",
         "fotos": ["https://example.com/p.jpg"], "ean": None}
    est = c.normalizar_estado({})
    est["vendedor"] = {"tags": []}
    alta = {"sku": "00711", "precio": 8000, "stock": 34,
            "producto": p, "titulo": "Producto Generico"}
    payload, meta = c.construir_payload(s, est, ENV, alta)
    ids = {a.get("id") for a in (payload or {}).get("attributes", [])}
    check("payload legacy con title", bool((payload or {}).get("title")))
    check("payload legacy sin family_name",
          bool(payload) and "family_name" not in payload)
    check("payload con seller_custom_field",
          (payload or {}).get("seller_custom_field") == "00711")
    check("payload con BRAND/MODEL",
          {"BRAND", "MODEL"}.issubset(ids), str(ids))
    check("payload con ITEM_CONDITION", "ITEM_CONDITION" in ids, str(ids))

    c.ATRIBUTOS_CACHE.clear()
    s2 = FakeSession({
        "/categories/MCO170729": info("MCO170729", "Cubo Rubik",
                                      "MCO-PUZZLE_CUBES"),
        "/categories/MCO170729/attributes": attrs(
            attr("BRAND"), attr("MODEL"),
            attr("ITEM_CONDITION", "list",
                 [{"id": "2230284", "name": "Nuevo"}]),
        ),
    })
    p2 = {"sku": "00695", "nombre": "CAJA CUBO RUBIK 3X3 IVYCUBE",
          "fotos": ["https://example.com/c.jpg"], "ean": None}
    est2 = c.normalizar_estado({})
    est2["vendedor"] = {"tags": ["user_product_seller"]}
    alta2 = {"sku": "00695", "precio": 19600, "stock": 51,
             "producto": p2, "titulo": "Caja Cubo Rubik"}
    payload2, meta2 = c.construir_payload(s2, est2, ENV, alta2)
    check("payload UP con family_name",
          bool((payload2 or {}).get("family_name")))
    check("payload UP sin title", bool(payload2) and "title" not in payload2)

    c.ATRIBUTOS_CACHE.clear()
    s3 = FakeSession({
        "/categories/MCO9999/attributes": attrs(
            attr("BRAND"), attr("COLOR"),
            attr("ITEM_CONDITION", "list",
                 [{"id": "2230284", "name": "Nuevo"}]),
        ),
    })
    p3 = {"sku": "X", "nombre": "COLOR FALTA",
          "fotos": ["https://example.com/x.jpg"], "ean": None}
    est3 = c.normalizar_estado({})
    est3["vendedor"] = {"tags": []}
    clave = c.sp.sin_acentos("COLOR FALTA")[:48]
    est3["categorias"][clave] = "MCO9999"
    est3["categorias_info"]["MCO9999"] = {
        "categoria": "MCO9999", "dominio": "MCO-TOYS_AND_GAMES",
        "listing_allowed": True, "max_titulo": 60}
    alta3 = {"sku": "X", "precio": 5000, "stock": 5,
             "producto": p3, "titulo": "Color Falta"}
    payload3, error3 = c.construir_payload(s3, est3, ENV, alta3)
    check("payload nulo si falta atributo", payload3 is None)
    check("error lista el atributo",
          any(x.get("id") == "COLOR" for x in (error3 or {}).get("faltantes", [])),
          str(error3))


def producto(sku, activo=True, stock=10, costo=1000, vendible=True,
             precio=10000):
    return {
        "sku": sku, "nombre": "Producto " + sku, "activo": activo,
        "stock": stock, "costo": costo, "precio_detal": precio,
        "precio5": precio, "fotos": ["https://example.com/f.jpg"],
        "categoria_web": "jugueteria",
        "duplicado": False, "vendible": vendible,
    }


def probar_alta():
    print("\nALTA COMPLETA (fake)")
    c.ATRIBUTOS_CACHE.clear()
    s = FakeSession({
        "/categories/MCO189402": info("MCO189402", "Pianos y Organos",
                                      "MCO-TOY_KEYBOARDS_AND_PIANOS"),
        "/categories/MCO189402/attributes": attrs(
            attr("BRAND"), attr("MODEL"),
            attr("ITEM_CONDITION", "list",
                 [{"id": "2230284", "name": "Nuevo"}]),
        ),
    })
    p = {"sku": "00711", "nombre": "PIANO DE JUGUETE SURTIDO",
         "fotos": ["https://example.com/p.jpg"]}
    est = c.normalizar_estado({})
    est["vendedor"] = {"tags": ["user_product_seller"]}
    alta = {"sku": "00711", "precio": 8000, "stock": 34,
            "producto": p, "titulo": "Piano De Juguete Surtido"}
    ok, data = c.crear_publicacion(s, est, ENV, alta)
    l = est["listings"].get("00711") or {}
    check("alta crea item", ok and l.get("listing_id") == "M-NUEVO",
          str((ok, data)))
    check("alta guarda seller_custom_field",
          l.get("seller_custom_field") == "00711", str(l))
    check("alta valida antes",
          any(x == "/items/validate" for x in s.llamadas), str(s.llamadas))
    check("alta describe despues",
          any(x.endswith("/description") for x in s.llamadas), str(s.llamadas))


def probar_validacion_warnings():
    print("\nVALIDACION ML: WARNINGS NO BLOQUEAN")
    rutas = {
        "/categories/MCO189402": info("MCO189402", "Pianos y Organos",
                                      "MCO-TOY_KEYBOARDS_AND_PIANOS"),
        "/categories/MCO189402/attributes": attrs(
            attr("BRAND"), attr("MODEL"),
            attr("ITEM_CONDITION", "list",
                 [{"id": "2230284", "name": "Nuevo"}]),
        ),
    }
    p = {"sku": "00711", "nombre": "PIANO DE JUGUETE SURTIDO",
         "fotos": ["https://example.com/p.jpg"]}
    alta = {"sku": "00711", "precio": 8000, "stock": 34,
            "producto": p, "titulo": "Piano De Juguete Surtido"}

    c.ATRIBUTOS_CACHE.clear()
    s = FakeValidacion(rutas, (400, {"cause": [
        {"type": "warning", "code": "shipping.me2_adoption_mandatory"},
    ]}))
    est = c.normalizar_estado({})
    est["vendedor"] = {"tags": ["user_product_seller"]}
    ok, data = c.crear_publicacion(s, est, ENV, alta)
    check("warning no bloquea el alta", ok and "00711" in est["listings"],
          str((ok, data)))
    val = est["validaciones"].get("00711", {})
    check("guarda causas warning", len(val.get("causas") or []) == 1 and
          val.get("errores") == [], str(val))

    c.ATRIBUTOS_CACHE.clear()
    s2 = FakeValidacion(rutas, (400, {"cause": [
        {"type": "error", "code": "item.attribute.missing_conditional_required"},
    ]}))
    est2 = c.normalizar_estado({})
    est2["vendedor"] = {"tags": ["user_product_seller"]}
    ok2, data2 = c.crear_publicacion(s2, est2, ENV, alta)
    check("error si bloquea el alta", not ok2 and "00711" not in est2["listings"],
          str((ok2, data2)))
    check("guarda error de validacion",
          len(est2["validaciones"]["00711"].get("errores") or []) == 1,
          str(est2.get("validaciones")))


def probar_alcance_descripcion_ignorados():
    print("\nALCANCE, DESCRIPCION E IGNORADOS")
    p = producto("X")
    est = c.normalizar_estado({})
    p["categoria_web"] = "cacharro"
    check("categoria fuera de alcance no es alta",
          c.elegible_para_alta(p, est, ENV) is None)
    p["categoria_web"] = "didactico"
    check("didactico si es alta",
          c.elegible_para_alta(p, est, ENV) is not None)

    crudo = "Texto Dropi\n\n-----------------------\nPie tienda\nWhatsApp 3106988465"
    limpio = c.sin_pie_dropi(crudo)
    check("quita pie y WhatsApp Dropi",
          limpio == "Texto Dropi" and "3106988465" not in limpio, repr(limpio))
    check("usa descripcion Dropi",
          c.descripcion_ml({"sku": "X"}, "X", crudo) == "Texto Dropi")

    est2 = c.normalizar_estado({
        "listings": {"X": {"listing_id": "M-X", "estado": "active"}},
        "ignorados": ["X"],
    })
    acciones, altas = c.construir_plan({"productos": [p]}, est2, ENV, 5)
    tipos = {(a["tipo"], a["sku"]) for a in acciones}
    check("ignorado se pausa", ("pausar", "X") in tipos, str(tipos))
    check("ignorado no se reactiva ni se da de alta",
          ("activar", "X") not in tipos and not altas,
          str((tipos, altas)))


def probar_plan():
    print("\nPLAN E IDEMPOTENCIA")
    hub = {"productos": [
        producto("A"), producto("E"), producto("G"),
        producto("B", stock=0), producto("C", activo=False, vendible=False),
        producto("D", costo=None),
    ]}
    est = c.normalizar_estado({"listings": {
        "E": {"listing_id": "E", "origen": "catalogo", "estado": "paused",
              "precio": 11500, "stock_publicado": 10},
        "F": {"listing_id": "F", "origen": "catalogo", "estado": "active"},
        "G": {"listing_id": "G", "origen": "catalogo", "estado": "active",
              "precio": 100, "stock_publicado": 1},
        "H": {"listing_id": "H", "origen": "externo", "estado": "active"},
    }})
    acciones, altas = c.construir_plan(hub, est, ENV, 5)
    tipos = {(a["tipo"], a["sku"]) for a in acciones}
    check("pausa F", ("pausar", "F") in tipos, str(tipos))
    check("activa E", ("activar", "E") in tipos, str(tipos))
    check("actualiza G", ("actualizar", "G") in tipos, str(tipos))
    check("no toca externo H", all(a["sku"] != "H" for a in acciones),
          str(tipos))
    skus_alta = {a["sku"] for a in altas}
    check("alta A", "A" in skus_alta, str(skus_alta))
    check("sin stock no es alta", "B" not in skus_alta, str(skus_alta))
    check("inactivo no es alta", "C" not in skus_alta, str(skus_alta))
    check("sin costo no es alta", "D" not in skus_alta, str(skus_alta))


def probar_reconciliacion():
    print("\nRECONCILIACION")
    item_a = {"id": "M1", "title": "A", "status": "active", "price": 100,
              "available_quantity": 5, "category_id": "MCO1910",
              "seller_custom_field": "A", "attributes": []}
    item_b = {"id": "M2", "title": "Sin SKU", "status": "paused", "price": 50,
              "available_quantity": 1, "category_id": "MCO1910",
              "attributes": []}
    clave, fila = c._item_a_listing(item_a, {"A"})
    check("SKU de catalogo", clave == "A" and fila["origen"] == "catalogo",
          str((clave, fila)))
    clave2, fila2 = c._item_a_listing(item_b, {"A"})
    check("sin SKU es externo", clave2 == "ML:M2" and
          fila2["origen"] == "externo", str((clave2, fila2)))

    s = FakeSession({
        "/users/me": (200, {"id": 1, "nickname": "n",
                            "tags": ["user_product_seller"]}),
        "/users/1/items/search": (200, {"results": ["M1", "M2"],
                                       "paging": {"total": 2}}),
        "/items?ids=": (200, [{"code": 200, "body": item_a},
                              {"code": 200, "body": item_b}]),
    })
    est = c.normalizar_estado({"listings": {
        "A": {"listing_id": "viejo", "creado": "2026-01-01"}}})
    vistos, total = c.reconciliar_estado(s, est, {"productos": [{"sku": "A"}]})
    check("lee 2 avisos", vistos == 2, str(vistos))
    check("conserva 2 en estado", total == 2, str(total))
    check("conserva creado", est["listings"]["A"].get("creado") == "2026-01-01")
    check("actualiza listing", est["listings"]["A"].get("listing_id") == "M1")
    check("externo marcado", est["listings"]["ML:M2"].get("origen") == "externo")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("QA ML — sin red, sin costos, sin tokens")
    probar_precio_y_rentabilidad()
    probar_titulo_marca_modelo()
    probar_categorias()
    probar_atributos()
    probar_payloads()
    probar_alta()
    probar_validacion_warnings()
    probar_alcance_descripcion_ignorados()
    probar_plan()
    probar_reconciliacion()
    total = CONTADOR["total"]
    print(f"\nRESUMEN QA ML: {total - len(FALLOS)}/{total}")
    if FALLOS:
        print("Fallaron: " + ", ".join(FALLOS))
        sys.exit(1)


if __name__ == "__main__":
    main()
