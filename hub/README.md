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
python hub/canal_ml.py --prueba         # valida credenciales (/users/me)
python hub/canal_ml.py --dry-run        # el plan sin llamar a ML (ni token)
python hub/canal_ml.py --sincronizar    # pausas/activaciones/stock/precio
python hub/canal_ml.py --publicar 5     # crea hasta 5 publicaciones nuevas
python hub/canal_ml.py --sincronizar --publicar 3   # corrida completa
```

(sin banderas = dry-run: muestra el plan y un `reportes/plan_ml_*.json`)

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
| `ML_PRECIO_FACTOR` | `0.15` | Recargo sobre el detal de la vitrina (cubre la comisión) |
| `ML_PRECIO_REDONDEO` | `100` | Redondeo del precio ML (COP) |
| `ML_COMISION_PCT` | `16` | Comisión ML estimada, para la guardia de rentabilidad |
| `ML_MARGEN_MINIMO` | `0.25` | Utilidad/costo mínima (después de comisión) para publicar |
| `ML_STOCK_MINIMO` | `2` | No publicar productos con menos existencias |
| `ML_STOCK_MAXIMO` | `99` | Techo publicado (no regala el inventario real) |
| `ML_LIMITE_ALTA` | `5` | Altas máximas por corrida |
| `ML_PRECIO_MINIMO` | `0` | No publicar por debajo de este precio |
| `ML_CATEGORIAS_EXCLUIDAS` | `cacharro` | Slugs de `categoria_web` vetados en ML |

Los `ML_*` también se aceptan como variables de proceso (así los inyecta el
workflow desde los secrets de Actions).

---

## Cómo decide el canal (reglas ML)

**Alta** — un producto entra si: está en el catálogo maestro como `vendible`
(activo + existencias + precio + categoría vendible + no duplicado), tiene
foto, cumple el stock mínimo, la categoría no está excluida, tiene costo
conocido y pasa la guardia de rentabilidad. Sin costo conocido **no** se
publica (misma regla dura de la vitrina).

**Precio ML** = detal de la vitrina × (1 + `ML_PRECIO_FACTOR`), redondeado.
La guardia exige que `precio × (1 − comisión) − costo` deje al menos
`ML_MARGEN_MINIMO` sobre el costo; si no, el producto se salta y queda
anotado en el plan.

**Ciclo de vida** — pausar antes que nada: producto ausente, inactivo o
agotado en POSGold → publicación `paused` (nunca se finaliza: es destructivo).
Vuelve el stock → `active`. Cambia stock/precio → `PUT /items/{id}` con solo
los campos que cambiaron.

**Categorización** — automática con `domain_discovery/search` de ML, cacheada
en `data/canal_ml.json` (clave = título sin acentos). EAN (SKU de 13 dígitos)
viaja como atributo `GTIN`.

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

---

## Antes de que ML funcione

1. Crear la aplicación en [developers.mercadolibre.com.co](https://developers.mercadolibre.com.co/devcenter)
   con la cuenta vendedora de la tienda (la aprobación tarda días).
2. Configurar `ML_CLIENT_ID`, `ML_CLIENT_SECRET` y (opcional para la primera
   vez) `ML_REFRESH_TOKEN` en los secrets del repo, y `ML_TOKEN_KEY`.
3. `python hub/canal_ml.py --autorizar` (local) → `--prueba` →
   `--publicar 2` con pocos productos para validar moderación de fotos y
   atributos obligatorios de cada categoría (BRAND/MODEL: si ML los pide,
   el error del alta los lista y se amplía `crear_publicacion()`).
4. Hasta entonces `sync-ml.yml` es dispatch-only (no corre solo).

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
