# Hub — catálogo maestro y adaptadores de canal (mini-VTEX)

El **hub** es la capa intermedia entre POSGold (el ERP/POS de la tienda) y los
canales de venta. Cada canal (la vitrina de GitHub Pages, Mercado Libre, y los
que vengan) lee del mismo catálogo maestro; nadie habla directo con POSGold.

```
POSGold (ERP: inventario, costos, ventas físicas)
   │  pull bajo demanda (construir_hub.py)
   ▼
┌─────────────────── HUB ───────────────────┐
│  1. Catálogo maestro (hub/productos.json) │
│  2. Reglas por canal (precio, stock,      │
│     rentabilidad, elegibilidad)           │
│  3. Adaptadores:                          │
│     ├─ canal_web  → catalogo.js (vitrina) │
│     ├─ canal_ml   → Mercado Libre (MCO)   │
│     └─ (canal_X   → futuros marketplaces) │
└───────────────────────────────────────────┘
```

Estado actual: **fase 0 y 1 construidas**. Véase `docs/plan_hub.md` para el
plan completo (fases 2–4: backend de webhooks, OMS, panel unificado).

---

## Archivos

| Archivo | Qué es | ¿Al repo? |
|---|---|---|
| `hub/construir_hub.py` | Genera el catálogo maestro desde POSGold + Excel de costos | Sí (código) |
| `hub/canal_ml.py` | Adaptador de Mercado Libre: altas, pausas, stock, precios | Sí (código) |
| `hub/ml_diagnostico.py` | Diagnóstico de categorías, atributos y publicaciones; no publica | Sí (código) |
| `data/categorias_ml.json` | Mapa curado de palabras → categoría ML y fallback | Sí (sin costos) |
| `data/marcas_ml.json` | Marcas conocidas por SKU o palabra | Sí (sin costos) |
| `qa_ml.py` | QA offline del adaptador (precios, atributos, payloads, estado) | Sí |
| `hub/productos.json` | **El catálogo maestro, SIEMPRE trae costos** | **NUNCA** (gitignored) |
| `hub/ml_token.enc` | Refresh token de ML cifrado (AES-256-CBC + PBKDF2) | Sí (cifrado) |
| `data/canal_ml.json` | Estado del canal ML: SKU → listing, precios, stock publicado | Sí (sin costos) |
| `.github/workflows/sync-ml.yml` | Cron de ML en Actions (dispatch-only por ahora) | Sí |

Regla de oro: `hub/productos.json` y los reportes de plan viven fuera del repo
porque contienen costos. La consola de los scripts nunca imprime valores de
costo (solo conteos y porcentajes).

---

## Uso

### Fase 0 — catálogo maestro

```bash
python hub/construir_hub.py                 # POSGold + PRODUCTOS*.xlsx más reciente
python hub/construir_hub.py --costos RUTA   # Excel de costos explícito
python hub/construir_hub.py --salida RUTA   # destino distinto (pruebas)
```

Reutiliza las primitivas de `sync_posgold.py` (login JWT, `GetProduct_V6`,
lectura del Excel por nombre de columna, cache de slugs) para que hub y
vitrina nunca diverjan. Trae **todo** el inventario (activos y agotados:
un canal necesita saber cuándo pausar, no solo cuándo aparece stock).

### Fase 1 — Mercado Libre

```bash
python hub/canal_ml.py --autorizar      # primera vez: OAuth → refresh token en .env
python hub/canal_ml.py --prueba         # valida credenciales (/users/me + tags)
python hub/canal_ml.py --dry-run        # el plan sin llamar a ML (ni token)
python hub/canal_ml.py --reconciliar    # reconstruye data/canal_ml.json desde ML
python hub/canal_ml.py --sincronizar    # pausas/activaciones/stock/precio
python hub/canal_ml.py --publicar 5     # crea hasta 5 publicaciones nuevas
python hub/canal_ml.py --sincronizar --publicar 3   # corrida completa
```

(sin banderas = dry-run: muestra el plan y un `reportes/plan_ml_*.json`)

Antes de la primera alta, `--publicar` reconcilia solo si el estado está vacío
o no conoce los tags del vendedor. También se puede forzar con `--reconciliar`.
Los avisos de SKU desconocido quedan marcados como `origen: externo` y **no**
se pausan automáticamente.

Diagnóstico y payloads sin publicar:

```bash
python hub/ml_diagnostico.py                 # 5 SKUs de muestra, endpoints públicos
python hub/ml_diagnostico.py --sku 001309 --sku 00723
python hub/ml_diagnostico.py --payloads      # payloads completos, no publica
python hub/ml_diagnostico.py --con-token     # usa SOLO ML_ACCESS_TOKEN (no rota)
python qa_ml.py                              # QA offline, sin red ni costos
```

---

## Configuración (`.env`)

| Variable | Default | Qué hace |
|---|---|---|
| `ML_CLIENT_ID` / `ML_CLIENT_SECRET` | — | Credenciales de la app en [developers.mercadolibre.com.co](https://developers.mercadolibre.com.co/devcenter) |
| `ML_REFRESH_TOKEN` | — | Se guarda solo tras `--autorizar` (rota en cada uso) |
| `ML_ACCESS_TOKEN` | — | Modo dev: token pegado a mano, dura ~6 h, no rota |
| `ML_TOKEN_KEY` | `COSTOS_KEY` | Llave AES para `hub/ml_token.enc` |
| `ML_SITE_ID` | `MCO` | Sitio de ML (Colombia) |
| `ML_TIPO_PUBLICACION` | `gold_special` | Clásica (`gold_special`), Gratuita (`silver`) |
| `ML_PRECIO_REDONDEO` | `100` | Redondeo del precio ML (COP) |
| `ML_COMISION_PCT` | `15` | Comisión ML (15% en juguetes) |
| `ML_PASARELA_PCT` | `5` | Pasarela de pago (5%) |
| `ML_ENVIO_COSTO` | `23000` | Costo envío gratis asumido por vendedor |
| `ML_MARGEN_MINIMO` | `0.25` | Utilidad/costo mínima (después de comisión) para publicar |
| `ML_STOCK_MINIMO` | `2` | No publicar productos con menos existencias |
| `ML_STOCK_MAXIMO` | `99` | Techo publicado (no regala el inventario real) |
| `ML_LIMITE_ALTA` | `5` | Altas máximas por corrida |
| `ML_PRECIO_MINIMO` | `0` | No publicar por debajo de este precio |
| `ML_CATEGORIAS_EXCLUIDAS` | `cacharro` | Slugs de `categoria_web` vetados en ML |
| `ML_VALIDAR` | `1` | Valida el payload con `POST /items/validate` antes del alta |
| `ML_NO_ROTAR` | — | `1` exige `ML_ACCESS_TOKEN`; nunca refresca el token |

Los `ML_*` también se aceptan como variables de proceso (así los inyecta el
workflow desde los secrets de Actions).

---

## Cómo decide el canal (reglas ML)

**Alta** — un producto entra si: está en el catálogo maestro como `vendible`
(activo + existencias + precio + categoría vendible + no duplicado), tiene
foto, cumple el stock mínimo, la categoría no está excluida, tiene costo
conocido y pasa la guardia de rentabilidad. Sin costo conocido **no** se
publica (misma regla dura de la vitrina).

**Precio ML** = (Precio 5 + `ML_ENVIO_COSTO`) / (1 − `ML_COMISION_PCT` − `ML_PASARELA_PCT`), redondeado.
Precio 5 ya incluye ganancia + IVA. ML cobra 15% comisión + 5% pasarela = 20% total.
Juguetería Taiwan asume envío gratis: paga $23.000 por envío.
La guardia exige que `precio × (1 − comisión − pasarela) − costo` deje al menos
`ML_MARGEN_MINIMO` sobre el costo; si no, el producto se salta y queda
anotado en el plan.

**Ciclo de vida** — pausar antes que nada: producto ausente, inactivo o
agotado en POSGold → publicación `paused` (nunca se finaliza: es destructivo).
Vuelve el stock → `active`. Cambia stock/precio → `PUT /items/{id}` con solo
los campos que cambiaron.

**Categorización** — primero el mapa curado `data/categorias_ml.json`
(palabras → categoría), luego `domain_discovery/search` y, si ninguna
candidata es de juguetes, el fallback `MCO1910` (Otros, dominio
`MCO-TOYS_AND_GAMES`). Se descartan dominios de otras ramas (colmenas,
cajones de madera, pianos reales, juguetes de mascotas) y categorías con
`listing_allowed=false`.
La caché vive en `data/canal_ml.json`; su versión se invalida sola al cambiar
el criterio.

**Atributos** — se leen de `/categories/{id}/attributes`. BRAND, MODEL,
MANUFACTURER, SELLER_SKU, GTIN e ITEM_CONDITION se llenan con datos reales;
si falta un requerido que el adaptador no sabe responder, el producto **no
se publica** y queda anotado en `faltantes` con la categoría y el atributo.
Dos atributos obligatorios tienen valor por defecto honesto
(`_valor_defecto_req`): `IS_FACTORY_KIT` = No (juguetes sueltos, no kits de
fábrica) y `POWER_SUPPLY_TYPE` se lee del nombre del producto
(pilas/luz/sonido/electrónico/gira → Batería; si no, Operación manual).
GTIN solo viaja si es un EAN-13 con dígito verificador válido; las
categorías que lo exigen sin EAN en el hub (p. ej. cubos de Rubik en UP)
van a `ignorados` y el plan deja de intentarlas.

**Títulos** — ML rechaza títulos de una sola palabra o demasiado cortos
(`item.title.minimum_length`: "pide marca, modelo o categoría").
`completar_titulo_ml()` los completa con el nombre de la categoría y la
marca hasta 40 caracteres, siempre respetando el máximo de la categoría.

**User Products** — si `/users/me` trae el tag `user_product_seller`, el alta
usa `family_name` y omite `title`; sin el tag usa el flujo legacy con `title`.
Cada SKU es un ítem (sin variaciones inventadas). La descripción se crea
después del ítem con `POST /items/{id}/description`.

---

## Tokens: rotación obligatoria

El OAuth de ML **rota el refresh token en cada uso** (el access token dura
~6 h). Un secret fijo de GitHub moriría en la segunda corrida. Por eso:

1. Cada `refrescar_token()` guarda el refresh NUEVO en `hub/ml_token.enc`
   (AES-256-CBC + PBKDF2 con `ML_TOKEN_KEY` o `COSTOS_KEY`) y en `.env` local.
2. `sync-ml.yml` commitea el blob cifrado: la siguiente corrida siempre
   arranca con el token vigente.
3. Orden de lectura: blob cifrado → `.env` → error con instrucciones de
   `--autorizar`.

Si ML rechaza el refresh (`HTTP 401` en `/oauth/token`), vuelve a autorizar:
`python hub/canal_ml.py --autorizar`.

Modo sin rotación (pruebas): pega un `ML_ACCESS_TOKEN` temporal en `.env` y
pon `ML_NO_ROTAR=1`. Con esa bandera el script nunca llama al refresh; si falta
el access token, aborta con instrucciones. `hub/ml_diagnostico.py --con-token`
también usa solo el access token.

---

## Puesta en marcha

1. App creada y autorizada (29 oct 2026) en
   [developers.mercadolibre.com.co](https://developers.mercadolibre.com.co/devcenter)
   con la cuenta vendedora de la tienda.
2. Configurar `ML_CLIENT_ID`, `ML_CLIENT_SECRET` y (opcional para la primera
   vez) `ML_REFRESH_TOKEN` en los secrets del repo, y `ML_TOKEN_KEY`.
3. `python hub/canal_ml.py --autorizar` (local) → `--prueba` →
   `python hub/ml_diagnostico.py --con-token` (tags e inventario actual) →
   `python hub/ml_diagnostico.py --payloads` (payloads completos) →
   `python qa_ml.py` → `--publicar 1` y revisar la moderación antes de subir
   a 2-3. Si ML pide un atributo nuevo, el alta lo lista en `faltantes` y se
   agrega al producto o al mapa curado, nunca se inventa.
4. Hasta entonces `sync-ml.yml` es dispatch-only (no corre solo).

## Estado del canal ML (29-10-2026)

Campaña de altas de fase 1 **completa** (corridas `--publicar 50` en lote):
678 publicaciones en `data/canal_ml.json` (658 `active`, 20 `paused`),
0 elegibles pendientes y 0 `faltantes`. Verificación: `python qa_ml.py`
(70/70).

- **Altas recién creadas en `paused`** (9 al cierre): esperando moderación
  de ML; el sync las activa en la corrida siguiente (patrón confirmado:
  el alta se crea, la activación entra en la pasada siguiente).
- **Atascadas en `item.status.not_modifiable`** (10): ML no permite cambiar
  el estado por API (moderación colgada o transición vedada). Solo manual
  en el panel de ML — cada corrida lo reintentará en vano hasta que se
  liberen allá: 01389, 01476, 01731, 02110, 02280, 02418, 02419, 02896,
  02973 y 03119 (llaveros, trompos, rummy, encajable, caja didáctica).
- **`ignorados`** (excluidos del plan para siempre, el canal persiste la
  lista en el estado): `001309` Abeja Baston Bolsa (ignorado preexistente,
  queda `paused`) y `02039` Cubo Rubik Soma Madera (la categoría UP de
  cubos exige GTIN y el producto no trae EAN válido).
- Lecciones de la campaña, ya en código: los 14 SKUs rechazados se
  arreglaron con `_valor_defecto_req` (atributos con valor honesto por
  defecto) y `completar_titulo_ml` (títulos de una palabra). Gotcha para
  mantenerlo: **`sp.sin_acentos()` devuelve MAYÚSCULAS sin tildes** — toda
  comparación de nombres de valor ML (`_valor_requerido`,
  `_valor_defecto_req`) se normaliza con esa misma función en ambos lados;
  comparar en minúscula nunca hace match.

## Estado de la integración POSGold (21-10-2026)

> **Decisión de alcance — Juguetería Taiwan (23-10-2026):** la integración
> es **solo sincronización de productos** (fases 0+1 de este hub). La
> gestión de pedidos queda en la plataforma de Mercado Libre: los pedidos
> NO se empujan a POSGold. Por eso los endpoints de pedidos y los webhooks
> Inactivos que siguen **no bloquean nada**, y el correo a GoldPOS
> ([`../docs/correo_goldpos.md`](../docs/correo_goldpos.md)) queda
> **archivado como plan B** — no se espera respuesta para el alcance
> vigente.

- **Funcionando:** `LoginJWTToken`, `GetProduct_V6` (bodega 2, JWT por
  corrida) — base de `construir_hub.py` y del cron de la vitrina.
- **Registrados pero Inactivos en la cuenta** (panel Desarrollador →
  Api Logs, 8 registros): 5 endpoints de creación de pedidos
  (`PedidoWebCreateAnonimo_v3/_v5`, `PedidoCreateExternal`,
  `PedidoCreateExternal_Anonimo`, `PedidoCreate_PluginWordPress`),
  `PedidoGestionApi/CrearPedido` y `CiudadApi/CiudadesGet`. Verificado:
  `CiudadesGet` responde 404 con JWT válido. Eran la base de la fase 3
  (OMS), hoy fuera de alcance por decisión de la empresa (ver recuadro
  arriba): el correo queda archivado como plan B en
  `../docs/correo_goldpos.md`.
- **Webhooks**: la sección existe (Desarrollador → WebHooks) pero está
  vacía; pedir catálogo de eventos. Si algún día se activan (fase 2
  futura, hoy fuera de alcance), reemplazarían el polling.
- **Token maestro** de la empresa (Empresa → Token): guardado en `.env`
  como `POSGOLD_API_TOKEN` (local, gitignored, jamás al repo; el cron no
  lo usa todavía).

Referencias: [guía de productos ML](https://developers.mercadolibre.com.co/es_ar/guia-para-producto) ·
[API docs ML](https://developers.mercadolibre.com.co/es_ar/api-docs-es) ·
[colección Postman de POSGold](https://www.postman.com/posgold-desarrollo/posgold-api)
(documenta 3 endpoints: `LoginJWTToken`, `GetProduct_V6` y
`JWTValidateToken`; los detalles viven en `info taiwan.txt` y en los
rastros de `.firecrawl/`) · plan completo en
[`docs/plan_hub.md`](../docs/plan_hub.md) · correo a GoldPOS (archivado
como plan B) en [`docs/correo_goldpos.md`](../docs/correo_goldpos.md).
