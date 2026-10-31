#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mejorar_titulo.py — Fase A de curacion de titulos (deterministica,
sin IA): los nombres de POSGold llegan tal cual los escribe el
proveedor en la factura y viajan crudos a Mercado Libre y a la
vitrina. Aqui se limpian: ruido de empaque, codigos, unidades,
medidas, tildes de un diccionario curado y capitalizacion natural.

Reglas de la casa:
- POSGold NUNCA se toca: el nombre original queda en el hub.
- El titulo final de cada SKU vive en data/titulos_productos.json
  ({sku: titulo}) y NUNCA se pisa lo que ya este en el archivo:
  las correcciones de la duena (o de la fase B con vision) mandan.
- La fase A es conservadora: no inventa palabras, no reordena
  terminos salvo el sustantivo de categoria de emergencia y no
  corrige marcas salvo las ambiguamente ciertas (RUBICK->Rubik).

Uso:
    python hub/mejorar_titulo.py --vista 40      # antes -> despues
    python hub/mejorar_titulo.py --escribir      # llena el JSON (fase A)
"""

import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUB_PATH = os.path.join(BASE_DIR, "hub", "productos.json")
TITULOS_PATH = os.path.join(BASE_DIR, "data", "titulos_productos.json")
MAX_TITULO = 60

# ------------------------------------------------------------------
# Diccionario de tildes y ortografia curada. Llave SIN acentos
# (como el nombre crudo del proveedor), valor con la palabra bien
# escrita. Solo palabras que aqui significan UNA cosa.
# ------------------------------------------------------------------
TILDES = {
    "carton": "cartón", "construccion": "construcción", "didactico": "didáctico",
    "didactica": "didáctica", "didacticos": "didácticos", "didacticas": "didácticas",
    "coleccion": "colección", "coleccionista": "coleccionista",
    "metalico": "metálico", "metalicos": "metálicos", "magnetico": "magnético",
    "magneticos": "magnéticos", "plastico": "plástico", "plasticos": "plásticos",
    "futbol": "fútbol", "futbolin": "futbolín", "balon": "balón", "balones": "balones",
    "avion": "avión", "aviones": "aviones", "camion": "camión", "bebe": "bebé",
    "bebes": "bebés", "domino": "dominó", "raton": "ratón", "medico": "médico",
    "medica": "médica", "logica": "lógica", "matematicas": "matemáticas",
    "matematica": "matemática", "multiplicacion": "multiplicación",
    "division": "división", "adiccion": "adicción", "geometria": "geometría",
    "profesion": "profesión", "anatomia": "anatomía", "musica": "música",
    "telefono": "teléfono", "camara": "cámara", "camaras": "cámaras",
    "atencion": "atención", "educacion": "educación", "pelicula": "película",
    "peliculas": "películas", "television": "televisión", "cancion": "canción",
    "canciones": "canciones", "numeros": "números", "numero": "número",
    "decoracion": "decoración", "cumpleanos": "cumpleaños", "jugueteria": "juguetería",
    "lampara": "lámpara", "ingles": "inglés", "frances": "francés",
    "vehiculo": "vehículo", "vehiculos": "vehículos", "microfono": "micrófono",
    "bateria": "batería", "baterias": "baterías", "volcan": "volcán",
    "rapido": "rápido", "economico": "económico", "economica": "económica",
    "basico": "básico", "basica": "básica", "electronico": "electrónico",
    "electronica": "electrónica", "electrico": "eléctrico", "electrica": "eléctrica",
    "mecanico": "mecánico", "mecanica": "mecánica", "olimpico": "olímpico",
    "baston": "bastón", "tiburon": "tiburón", "circulo": "círculo",
    "metalica": "metálica", "neon": "neón", "pokemon": "pokémon",
    "cocina": "cocina", "espacio": "espacio",
    # ortografia curada (marca o palabra siempre inambigua en jugueteria)
    "rubick": "rubik", "lufy": "luffy", "slim": "slime", "pcs": "piezas",
    "paq": "pack", "pqt": "pack", "pq": "pack", "cj": "caja",
}

# multi-palabra: la Ñ del proveedor a veces se pierde como espacio
TILDES_ESPACIO = {
    "peque o": "pequeño", "peque a": "pequeña", "peque os": "pequeños",
    "peque as": "pequeñas", "espa ol": "español", "espa ola": "española",
    "multiplicaci n": "multiplicación", "ara a": "araña", "ara as": "arañas",
    "pi a": "piña", "pias": "piñas", "nin o": "niño", "nin a": "niña",
    "nin os": "niños", "nin as": "niñas",
}

SUSTANTIVO_CATEGORIA = {
    "jugueteria": "Juguete",
    "juego-de-mesa": "Juego de Mesa",
    "didactico": "Material Didáctico",
}

PALABRAS_BAJAS = {
    "de", "del", "la", "el", "los", "las", "y", "o", "en", "a", "con",
    "sin", "para", "al", "un", "una", "con", "tu", "su", "by",
}


def sin_acentos(texto):
    import unicodedata
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").replace("ñ", "n")


def limpiar_nombre(nombre):
    """Nombre de factura -> titular legible. Deterministico: solo
    quita ruido que nadie puede querer, no reordena terminos."""
    t = " ".join((nombre or "").split())
    if not t:
        return ""
    # medidas y separadores: 15*15CM -> 15x15cm ; 9.9CMX7.0CM -> 9.9x7cm
    t = re.sub(r"(\d)\s*\*\s*(\d)", r"\1x\2", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*CM\s*X\s*(\d+(?:\.\d+)?)\s*CM", r"\1x\2cm", t, flags=re.I)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*CM\b", r"\1cm", t, flags=re.I)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*ML\b", r"\1ml", t, flags=re.I)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*PCS\b", r"\1 piezas", t, flags=re.I)
    # cantidades: X 100 / X100 -> x100 ; X448PCS ya quedo x448 piezas
    t = re.sub(r"\bX\s*(\d+)\b", r"x\1", t)
    # C/ PUNTERA -> con puntera
    t = re.sub(r"\bC/\s*", "con ", t)
    # multi-palabra (Ñ perdida como espacio) antes de partir
    t_bajo = t.lower()
    for clave, buena in TILDES_ESPACIO.items():
        if clave in t_bajo:
            t_bajo_iter = re.sub(re.escape(clave), buena, t_bajo, flags=re.I)
            t, t_bajo = t_bajo_iter, t_bajo_iter
    # tokens: diccionario + capitalizacion natural
    partes = []
    for tok in t.split():
        clave = sin_acentos(tok)
        if tok.lower() in PALABRAS_BAJAS:
            partes.append(tok.lower())
        elif clave in TILDES:
            buena = TILDES[clave]
            partes.append(buena[0].upper() + buena[1:])
        elif re.search(r"\d", tok):
            partes.append(tok.lower())
        elif len(tok) <= 3 and tok == tok.upper():
            partes.append(tok.title())
        else:
            partes.append(tok.capitalize() if tok.isupper() else tok)
    t = " ".join(partes)
    t = re.sub(r"\s+", " ", t).strip(" -,")
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def agregar_sustantivo(titulo, categoria_web):
    """Si el nombre quedo demasiado corto (< 4 palabras), agrega el
    sustantivo generico de la categoria al final (para que ML y el
    buscador sepan de que se trata)."""
    sust = SUSTANTIVO_CATEGORIA.get(categoria_web or "", "")
    t = (titulo or "").strip()
    if not sust or len(t.split()) >= 4:
        return t
    clave = sin_acentos(sust)
    if clave in sin_acentos(t):
        return t
    return (t + " " + sust).strip()


def recortar_a_palabra(titulo, maximo=MAX_TITULO):
    t = (titulo or "").strip()
    if len(t) <= maximo:
        return t
    rec = t[:maximo]
    if " " in rec:
        rec = rec.rsplit(" ", 1)[0]
    return rec.strip()


def titulo_determinista(nombre, categoria_web="", maximo=MAX_TITULO):
    """El titulo de la fase A, completo: limpiar + sustantivo de
    emergencia + recorte por palabra. Nunca devuelve vacio."""
    t = limpiar_nombre(nombre)
    if not t:
        t = " ".join((nombre or "").split())
    t = agregar_sustantivo(t, categoria_web)
    return recortar_a_palabra(t, maximo)


def cargar_titulos(ruta=TITULOS_PATH):
    if os.path.exists(ruta):
        try:
            with open(ruta, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
    return {}


def guardar_titulos(titulos, ruta=TITULOS_PATH):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(titulos, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, ruta)


def fusionar_titulos(titulos, nuevos):
    """Objetivo: {"sku": "titulo"} -> merge; lo que ya existe NUNCA
    se pisa (la curacion de la duena y la previa siempre gana)."""
    fusionado = dict(titulos)
    for sku, titulo in nuevos.items():
        if sku and titulo:
            fusionado.setdefault(str(sku), str(titulo))
    return fusionado


def main():
    ap = __import__("argparse").ArgumentParser(
        description="Fase A: titulos deterministas desde los nombres de POSGold")
    ap.add_argument("--vista", type=int, default=None, metavar="N",
                    help="muestra los N primeros antes->despues y sale")
    ap.add_argument("--escribir", action="store_true",
                    help="llena data/titulos_productos.json con la fase A")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not os.path.exists(HUB_PATH):
        print("ERROR: falta hub/productos.json (corre construir_hub.py)")
        sys.exit(1)
    with open(HUB_PATH, encoding="utf-8") as f:
        hub = json.load(f)
    productos = [p for p in hub["productos"] if p.get("sku") and p.get("vendible")]

    if args.vista is not None:
        for p in productos[:args.vista]:
            antes = p["nombre"]
            despues = titulo_determinista(antes, p.get("categoria_web"))
            marca = "  (cambia)" if despues != antes else " (igual)"
            print(f"  {p['sku']:>16} {antes[:44]:44} -> {despues}{marca}")
        return

    if args.escribir:
        existentes = cargar_titulos()
        nuevos = {}
        for p in productos:
            t = titulo_determinista(p["nombre"], p.get("categoria_web"))
            if t and t != " ".join(p["nombre"].split()):
                nuevos[p["sku"]] = t
        fusionado = fusionar_titulos(existentes, nuevos)
        guardar_titulos(fusionado)
        agregados = sum(1 for k in fusionado if k not in existentes)
        print(f"OK: {len(fusionado)} titulos en {TITULOS_PATH} "
              f"(+{agregados}; lo existente nunca se pisa).")


if __name__ == "__main__":
    main()
