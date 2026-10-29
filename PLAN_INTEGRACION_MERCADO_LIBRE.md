# Plan de Integración con Mercado Libre — Juguetería Taiwan

**Versión:** 1.0
**Fecha:** 28/10/2026
**Alcance:** Fases 0 y 1 (solo sincronización de productos — catálogo, stock, precios, altas, pausas)
**Fuera de alcance:** Gestión de pedidos (OMS), webhooks, facturación, mensajería post-venta

---

## 1. APIs Requeridas (Resumen)

| Área | Endpoint | Método | Uso en el Proyecto |
|------|----------|--------|-------------------|
| **Auth** | `/oauth/token` | POST | Refresh token → Access token (rotación obligatoria) |
| **Auth** | `/authorization` | GET | Flujo OAuth inicial (`--autorizar`) |
| **Usuario** | `/users/me` | GET | Validar credenciales (`--prueba`) |
| **Usuario** | `/users/{id}/items/search` | GET | Listar publicaciones propias (sync estado) |
| **Items** | `/items` | POST | Crear publicación nueva (alta) |
| **Items** | `/items/{id}` | GET | Consultar detalle de publicación |
| **Items** | `/items/{id}` | PUT | Actualizar precio, stock, estado (pausar/activar) |
| **Items** | `/items/bulk?ids=` | GET | Consulta múltiple (reemplaza `/items?ids=`) |
| **Categorías** | `/sites/MCO/categories` | GET | Árbol de categorías Colombia |
| **Categorías** | `/categories/{id}` | GET | Detalle de categoría (atributos, settings) |
| **Categorías** | `/categories/{id}/attributes` | GET | Atributos obligatorios/opcionales por categoría |
| **Categorías** | `/sites/MCO/domain_discovery/search?q=` | GET | Predictor de categoría automático |
| **Atributos** | `/attributes` | GET | Catálogo global de atributos |
| **Listing Types** | `/sites/MCO/listing_types` | GET | Tipos de publicación y comisiones |
| **Listing Prices** | `/sites/MCO/listing_prices?price=` | GET | Costos de publicación por tipo |

---

## 2. Flujo de Autenticación (OAuth 2.0 + Refresh Token)

### 2.1 Configuración Inicial (Una sola vez)
```
1. Crear app en DevCenter → Client ID + Client Secret
2. Redirect URI: https://oauth.pstmn.io/v1/callback
3. Scopes: read, write, offline_access
4. Guardar en .env: ML_CLIENT_ID, ML_CLIENT_SECRET, ML_TOKEN_KEY
```

### 2.2 Autorización Inicial (`--autorizar`)
```
GET https://auth.mercadolibre.com/authorization?response_type=code&client_id={id}&redirect_uri={uri}
→ Usuario loguea y autoriza
→ Redirect con ?code=TG-xxx
POST /oauth/token (grant_type=authorization_code) → access_token + refresh_token
→ Guardar refresh_token en .env + cifrar en hub/ml_token.enc (AES-256+PBKDF2)
```

### 2.3 Renovación Automática (Cada corrida)
```
POST /oauth/token (grant_type=refresh_token) → NUEVO access_token + NUEVO refresh_token
→ Actualizar .env + Sobrescribir hub/ml_token.enc
→ El workflow commitea el blob cifrado para la siguiente corrida
```

> **Crítico:** El refresh token **rota en cada uso**. Sin persistencia cifrada en repo, el cron falla en la 2da corrida.

---

## 3. Arquitectura de la Integración (Hub & Spoke)

```
POSGold (ERP)
    │
    ├──► sync_posgold.py (4×/día) ──► catalogo.js ──► Vitrina GitHub Pages
    │
    └──► construir_hub.py ──► hub/productos.json (maestro, con costos, gitignored)
                                    │
                                    ▼
                          canal_ml.py ──► Mercado Libre (MCO)
                                    │
                                    ▼
                          data/canal_ml.json (estado canal: SKU→listing_id, precio, stock)
                                    │
                                    ▼
                          hub/ml_token.enc (token rotado, commiteado)
```

### 3.1 Catálogo Maestro (`hub/productos.json`)
- **Fuente:** `GetProduct_V6` (POSGold) + Excel costos (PRODUCTOS*.xlsx)
- **Contiene:** 6,171 productos (activos + agotados), costos, fotos, slug_web, EAN, vendible
- **Regla vendible:** `activo && stock>0 && precio>0 && categoria_web && !duplicado` (SIN exigir foto ni costo)

### 3.2 Adaptador ML (`canal_ml.py`)
Lee `hub/productos.json` + `data/canal_ml.json` → Calcula plan → Ejecuta

---

## 4. Reglas de Negocio del Canal ML

### 4.1 Elegibilidad para Alta
```python
elegible = (
    vendible == True                    # regla hub (activo, stock, precio, cat, no dup)
    and categoria_web not in EXCLUIDAS  # ej: "cacharro"
    and stock >= ML_STOCK_MINIMO        # default 2
    and fotos no vacías
    and costo conocido                  # REGLA DURA: sin costo no publica
    and pasa_rentabilidad()             # ver abajo
    and precio_ml >= ML_PRECIO_MINIMO
)
```

### 4.2 Precio ML
```
precio_ml = round( precio_detal_vitrina × (1 + ML_PRECIO_FACTOR) / ML_PRECIO_REDONDEO ) × ML_PRECIO_REDONDEO
```
- `ML_PRECIO_FACTOR=0.15` (cubre comisión ~16% + margen)
- `ML_PRECIO_REDONDEO=100` (COP)

### 4.3 Guardia de Rentabilidad
```
utilidad_neta = precio_ml × (1 - ML_COMISION_PCT/100) - costo
margen = utilidad_neta / costo
publica SI margen >= ML_MARGEN_MINIMO (default 0.25 = 25%)
```
> Sin costo conocido → **no publica** (misma regla dura de la vitrina)

### 4.4 Ciclo de Vida
| Evento en POSGold | Acción en ML |
|-------------------|--------------|
| Producto nuevo elegible | `POST /items` (alta) |
| Agotado / Inactivo / Borrado | `PUT /items/{id} {status: "paused"}` |
| Vuelve a tener stock | `PUT /items/{id} {status: "active"}` |
| Cambio precio/stock | `PUT /items/{id} {price, available_quantity}` |

> **Nunca** `DELETE` / finaliza: es destructivo. Solo `paused`.

### 4.5 Categorización Automática
```
domain_discovery/search?q={titulo} → category_id
Cache en data/canal_ml.json["categorias"] por título normalizado (sin acentos, 48 chars)
```

### 4.6 Atributos Obligatorios por Categoría
- **BRAND** y **MODEL** son comunes en juguetes
- `GET /categories/{cat_id}/attributes` → filtrar `tags.required=true`
- `crear_publicacion()` debe enviar todos los `required`
- Si ML rechaza → error en log → ampliar `crear_publicacion()` con el atributo faltante

### 4.7 Stock Publicado
```
stock_publicado = min(stock_posgold, ML_STOCK_MAXIMO)  # default 99
```
Techo para no regalar inventario real.

### 4.8 Identificadores
- SKU de 13 dígitos numérico → `GTIN` (EAN) en atributos
- `seller_custom_field` = SKU interno para trazabilidad

---

## 5. Endpoints Detallados por Funcionalidad

### 5.1 Validación y Health Check
```bash
GET /users/me
# Respuesta esperada: {id, nickname, site_id: "MCO"}
# Usado en: --prueba
```

### 5.2 Listar Publicaciones Propias (Estado Actual)
```bash
GET /users/{seller_id}/items/search?status=active,paused&limit=1000
# Para >1000: search_type=scan + scroll_id
# Usado en: cargar_estado() → sincronizar data/canal_ml.json
```

### 5.3 Consultar Publicación Específica
```bash
GET /items/{item_id}?attributes=id,title,price,available_quantity,status,category_id,permalink,pictures,attributes,seller_custom_field
# Usado en: validar tras alta/actualización
```

### 5.4 Consulta Múltiple (Bulk) — NUEVO (oct 2026)
```bash
GET /items/bulk?ids=MLA123,MLA456&attributes=body.id,body.price,body.available_quantity,body.status,body.category_id
# Reemplaza /items?ids= (deprecado 25/10/2026)
# Usado en: validación masiva post-sync
```

### 5.5 Crear Publicación (Alta)
```bash
POST /items
Content-Type: application/json
Authorization: Bearer {access_token}

{
  "title": "Título optimizado ≤60 chars",
  "category_id": "MCOxxxxxx",
  "price": 15000,
  "currency_id": "COP",
  "available_quantity": 10,
  "buying_mode": "buy_it_now",
  "listing_type_id": "gold_special",  # o silver (gratuita)
  "condition": "new",  # legacy, usar item_condition en attributes
  "pictures": [{"source": "https://..."}, ...],  # máx 10-12 según categoría
  "attributes": [
    {"id": "BRAND", "value_name": "Marca"},
    {"id": "MODEL", "value_name": "Modelo"},
    {"id": "ITEM_CONDITION", "value_id": "2230284", "value_name": "Nuevo"},
    {"id": "GTIN", "value_name": "7701234567890"}  # si EAN válido
  ],
  "description": {"plain_text": "Texto plano..."},
  "seller_custom_field": "SKU-INTERNO-001"
}
```
> **Importante:** Desde sep 2024, `exclusive_channel` eliminado → usar array `channels`.
> `condition` deprecado → usar `ITEM_CONDITION` en attributes.

### 5.6 Actualizar Publicación
```bash
PUT /items/{item_id}
{
  "price": 15500,
  "available_quantity": 8
}
# O para pausar/activar:
{"status": "paused"}  /  {"status": "active"}
```

### 5.7 Predictor de Categoría
```bash
GET /sites/MCO/domain_discovery/search?q={titulo}&limit=3
# Respuesta: [{domain_id, category_id, attributes: [{id: BRAND, value_name}, ...]}]
# Usado en: categoria_para() con cache en data/canal_ml.json
```

### 5.8 Atributos por Categoría
```bash
GET /categories/{category_id}/attributes
# Filtrar: tags.required=true, tags.catalog_required=true, tags.grid_template_required=true
# value_type: list, number, number_unit, string, boolean, color
```

---

## 6. Estado Actual del Código (`hub/canal_ml.py`)

### ✅ Ya Implementado
- [x] OAuth flow completo (`--autorizar`, refresh rotativo, cifrado AES)
- [x] `SesionML` con retry 429/401, backoff exponencial
- [x] Carga hub + estado canal (`data/canal_ml.json`)
- [x] Planificador: pausar → activar → actualizar → altas (orden de riesgo)
- [x] `precio_ml()`, `pasa_rentabilidad()`, `titulo_ml()`, `descripcion_ml()`
- [x] `categoria_para()` con domain_discovery + cache
- [x] `crear_publicacion()` con pictures, attributes (BRAND, MODEL, GTIN, ITEM_CONDITION)
- [x] `ejecutar_accion()`: pausar, activar, actualizar
- [x] Reportes `plan_ml_*.json` (dry-run)
- [x] Límites: `ML_LIMITE_ALTA`, `ML_STOCK_MINIMO/MAXIMO`
- [x] Categorías excluidas (`ML_CATEGORIAS_EXCLUIDAS`)

### ⚠️ Pendientes / Mejoras Identificadas
| Item | Descripción | Prioridad |
|------|-------------|-----------|
| Atributos requeridos dinámicos | Leer `/categories/{id}/attributes` y enviar todos los `required` | Alta |
| Validación pre-alta | Simular con validador ML o dry-run local | Media |
| Manejo variaciones | Juguetes con talla/color → structure variations | Media |
| Envíos (Mercado Envíos) | Configurar `shipping.mode` + dimensiones | Baja (fase 2) |
| User Products (nuevo modelo) | Migración futura cuando ML lo exija | Baja |
| Health check items | Detectar `incomplete_technical_specs` tag | Media |

---

## 7. Variables de Configuración (`.env` + GitHub Secrets)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `ML_CLIENT_ID` | — | App ID (206449781003470) |
| `ML_CLIENT_SECRET` | — | Client Secret (solo .env + Secrets) |
| `ML_REFRESH_TOKEN` | — | Se genera con `--autorizar` |
| `ML_ACCESS_TOKEN` | — | Modo dev: token manual (~6h, no rota) |
| `ML_TOKEN_KEY` | `COSTOS_KEY` | Llave AES para `hub/ml_token.enc` |
| `ML_SITE_ID` | `MCO` | Sitio Colombia |
| `ML_TIPO_PUBLICACION` | `gold_special` | Clásica (paga) / `silver` (gratuita) |
| `ML_PRECIO_FACTOR` | `0.15` | Recargo sobre detal vitrina |
| `ML_PRECIO_REDONDEO` | `100` | Redondeo COP |
| `ML_COMISION_PCT` | `16` | Comisión ML estimada |
| `ML_MARGEN_MINIMO` | `0.25` | Utilidad/costo mínima (25%) |
| `ML_STOCK_MINIMO` | `2` | Stock mínimo para publicar |
| `ML_STOCK_MAXIMO` | `99` | Techo stock publicado |
| `ML_LIMITE_ALTA` | `5` | Máx altas por corrida |
| `ML_PRECIO_MINIMO` | `0` | Precio piso |
| `ML_CATEGORIAS_EXCLUIDAS` | `cacharro` | Slugs vetados (coma-sep) |
| `ML_REDIRECT_URI` | `https://oauth.pstmn.io/v1/callback` | Debe coincidir con DevCenter |

---

## 8. GitHub Actions Workflow (`sync-ml.yml`)

### Estado Actual: `workflow_dispatch` only
```yaml
on:
  workflow_dispatch:
    inputs:
      publicar:
        description: "Cuantos productos nuevos publicar (0 = solo sincronizar)"
        default: "0"
```

### Pasos del Job
1. `checkout` (fetch-depth: 0)
2. `setup-python` (3.12)
3. `pip install openpyxl`
4. **Descifrar costos**: `openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in costos.posgold.enc -out PRODUCTOS_cloud.xlsx`
5. **Crear .env** desde secrets (POSGOLD_* + ML_*)
6. `python hub/construir_hub.py --costos PRODUCTOS_cloud.xlsx`
7. `python hub/canal_ml.py --sincronizar --publicar "${{ inputs.publicar }}"`
8. **Commit + push**: `data/canal_ml.json` + `hub/ml_token.enc`

### Secrets Requeridos en GitHub
```
POSGOLD_TOKEN_USER
POSGOLD_TOKEN_PASS
COSTOS_KEY
ML_CLIENT_ID
ML_CLIENT_SECRET
ML_TOKEN_KEY
ML_REFRESH_TOKEN  # solo primera vez si no hay ml_token.enc commiteado
```

### Próximo Paso: Agendar Cron
```yaml
on:
  schedule:
    - cron: "30 11 * * *"  # 06:30 Colombia (UTC-5) = 11:30 UTC
    - cron: "30 23 * * *"  # 18:30 Colombia
  workflow_dispatch:
```

---

## 9. Checklist de Puesta en Producción

### Pre-requisitos (Hechos)
- [x] App creada en DevCenter (ID: 206449781003470)
- [x] Redirect URI configurada: `https://oauth.pstmn.io/v1/callback`
- [x] Scopes: read, write, offline_access
- [x] `.env` local con credenciales
- [x] `hub/canal_ml.py` código completo y probado en dry-run

### Pendientes de Ejecución
- [ ] **Autorizar app**: `python hub/canal_ml.py --autorizar` (cuenta vendedora real)
- [ ] **Validar**: `python hub/canal_ml.py --prueba` → "OK: token valido. site: MCO"
- [ ] **Dry-run completo**: `python hub/construir_hub.py && python hub/canal_ml.py --dry-run`
- [ ] Revisar `reportes/plan_ml_*.json`: verificar elegibles, precios, categorías
- [ ] **Prueba 2 productos**: `python hub/canal_ml.py --sincronizar --publicar 2`
- [ ] Verificar en ML: fotos, categoría, precio (detal × 1.15 redondeado $100), atributos
- [ ] Si ML rechaza atributos (BRAND/MODEL) → ampliar `crear_publicacion()`
- [ ] **Commitear token**: `git add hub/ml_token.enc && git commit -m "hub: token ML cifrado" && git push`
- [ ] **Configurar Secrets** en GitHub Actions (ver sección 7)
- [ ] **Ejecutar workflow** Actions → sync-ml → `publicar=0` → verde → `publicar=2`
- [ ] **Añadir schedule** a `sync-ml.yml` cuando todo conforme

---

## 10. Riesgos y Mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| Rate limit 429 | Fallos intermitentes | Backoff exponencial en `SesionML` (5s, 10s, 20s) |
| Moderación fotos/atributos | Altas rechazadas item×item | Log detallado, corrida continúa, ajustar `crear_publicacion()` |
| Refresh token inválido (401) | Cron se detiene | Re-autorizar `--autorizar`; blob cifrado siempre fresco en repo |
| Categoría incorrecta | Moderación / baja exposición | Predictor `domain_discovery` + cache; revisar manualmente primeras 50 |
| Sobreventa stock | Vender sin inventario | `ML_STOCK_MAXIMO=99` + sync 4×/día (misma ventana vitrina) |
| User Products (migración ML) | Cambio breaking futuro | Monitorear novedades ML; `canal_ml.py` modular para adaptar |

---

## 11. Próximos Pasos (Post-Fase 1)

Cuando la empresa decida ampliar alcance:

| Fase | Qué | Dependencias |
|------|-----|--------------|
| 2 | Webhooks ML (stock/precio en vivo) + renovación token en caliente | Backend hosting (Cloud Run/Fly.io), URL pública |
| 3 | OMS: Orden ML → reserva inventario POSGold → factura → guía Mercado Envíos | **Activar endpoints POSGold Inactivos** (ver `docs/plan_hub.md`) |
| 4 | Panel unificado multi-canal | Extender panel admin actual |

> **Nota:** Decisión 23-10-2026: Fases 2-4 **fuera de alcance** por ahora. Pedidos se gestionan en plataforma ML.

---

## 12. Referencias

- [Guía productos ML](https://developers.mercadolibre.com.co/es_ar/guia-para-producto)
- [API Docs ML](https://developers.mercadolibre.com.co/es_ar/api-docs-es)
- [Categorización](https://developers.mercadolibre.com.co/es_ar/categoriza-productos)
- [Publicar productos](https://developers.mercadolibre.com.co/es_ar/publica-productos)
- [Atributos](https://developers.mercadolibre.com.co/es_ar/atributos)
- [Dominios y Categorías](https://developers.mercadolibre.com.co/es_ar/dominios-y-categorias)
- [Items y Búsquedas](https://developers.mercadolibre.com.co/es_ar/items-y-busquedas)
- [Rate Limit / Error 429](https://developers.mercadolibre.com.co/es_ar/rate-limit-error-429)
- [Validaciones](https://developers.mercadolibre.com.co/es_ar/validaciones)
- [Imágenes](https://developers.mercadolibre.com.co/es_ar/trabajar-con-imagenes)
- `hub/README.md` — Documentación técnica del hub
- `docs/plan_hub.md` — Plan completo fases 0-4
- `MERCADOLIBRE/Integracion_Mercado_Libre.md` — Guía operativa

---

## 13. Plan de continuación (29-10-2026)

### 13.1 Estado de partida

- La app está creada y autorizada; el refresh token rota y se guarda cifrado en
  `hub/ml_token.enc` y en `.env`.
- `data/canal_ml.json` no tiene `listings`: **todavía no hay publicaciones
  registradas**.
- El último dry-run propuso 5 altas (Abeja Bastón, Cubo Rubik, Piano, Nobody
  Sausage, Marimba) con precios y márgenes calculados.
- Hay cambios locales sin commitear en `hub/canal_ml.py`: evita dominios que
  exigen `family_name` y arma atributos BRAND/MODEL/GTIN. **No están probados
  contra ML.**
- `sync-ml.yml` sigue siendo `workflow_dispatch` (sin cron).
- Riesgos detectados:
  1. El Client Secret está en texto claro en
     `MERCADOLIBRE/Integracion_Mercado_Libre.md:76`; ese archivo no está en
     `.gitignore`.
  2. Los scripts sueltos `check_*.py`, `test_*.py` y `exchange_token.py`
     refrescan el token **sin persistir el refresh rotado**. Cada ejecución
     invalida el token anterior y rompe la cadena de autenticación.
  3. La conciliación de publicaciones existentes no está implementada: si la
     cuenta ya tiene avisos en ML, el script no los conoce y podría duplicarlos.

### 13.2 Reglas del plan

1. **Una sola sesión ML a la vez.** Todo refresh pasa por `SesionML` y se
   persiste de inmediato en `hub/ml_token.enc` y `.env`. No se vuelven a usar
   scripts que refresquen por su cuenta.
2. **Ninguna alta sin validación previa** (`/items/validate` o el validador del
   flujo User Products) y sin revisión humana de la primera tanda.
3. **Pausar, nunca finalizar ni borrar.** Es la regla de ciclo de vida ya
   definida.
4. **Categorías y atributos dinámicos.** Nada de IDs de categoría hardcodeados
   salvo el fallback documentado; se leen de `/categories/{id}` y
   `/categories/{id}/attributes`.
5. **Cero secretos y cero costos en el repo.** Los reportes con costos o
   márgenes viven en `reportes/` (gitignored).

### 13.3 Fase 0 — Seguridad y orden (0,5 día)

> **Instrucción vigente (29-10-2026): NO rotar el token ni el Client Secret
> todavía.** No se ejecuta ningún comando contra Mercado Libre ni se refresca
> el token. `hub/ml_token.enc` y `.env` se dejan intactos hasta autorización
> expresa. La limpieza local (documento y scripts) sí se puede hacer sin tocar
> las credenciales.

- Rotar el Client Secret en DevCenter y actualizarlo en `.env` y en el secret
  `ML_CLIENT_SECRET` de GitHub Actions **solo cuando la dueña lo autorice**.
- Quitar la clave en claro de `MERCADOLIBRE/Integracion_Mercado_Libre.md` y
  dejar un placeholder del tipo `ver .env / Secrets` (esto no rota nada).
- Retirar del root los scripts de exploración (`check_*.py`, `test_*.py`,
  `exchange_token.py`). Si se conservan, moverlos a una carpeta gitignored y
  reescribirlos para usar `canal_ml.SesionML`.
- Verificar que `MERCADOLIBRE/*.txt`, `.env`, `reportes/` y `hub/productos.json`
  sigan ignorados; ningún `.md` con credenciales.
- **Criterio de aceptación:** `git grep` no encuentra `ML_CLIENT_SECRET` ni
  tokens; `git status` no muestra archivos con credenciales.

### 13.4 Fase 1 — Diagnóstico del vendedor y del catálogo (0,5 día)

Crear `hub/ml_diagnostico.py` (solo lectura, sin publicar) que:

1. Use `SesionML` para refrescar y persistir el token una sola vez.
2. Consulte `GET /users/me` y muestre: id, nickname, `site_id` y **tags**,
   especialmente `user_product_seller` y `warehouse_management`.
3. Cuente las publicaciones existentes con
   `GET /users/{id}/items/search?status=active,paused` (y paginación
   `search_type=scan` si hay más de 1.000) y guarde
   `reportes/ml_inventario_actual.json` con item_id, SKU, título, precio,
   estado y `seller_custom_field`.
4. Para 5 SKUs de muestra obtenga: categoría sugerida, `catalog_domain`,
   `listing_allowed`, `max_title_length`, atributos requeridos y si el dominio
   exige `family_name`.
5. Arme el payload propuesto y lo valide contra el validador correspondiente,
   sin crear nada.

Comando previsto:

```bash
python hub/ml_diagnostico.py
python hub/ml_diagnostico.py --sku 001309 --sku 00695
```

**Criterio de aceptación:** reporte legible con el tag del vendedor, el
inventario actual de ML y un veredicto por SKU: publicable, faltan atributos,
requiere `family_name` o categoría no permite publicar.

#### 13.4.1 Hallazgos del diagnóstico público (29-10-2026, sin token)

- Los endpoints de categorías, atributos y `domain_discovery` responden sin
  autenticación; se usaron para avanzar sin tocar el refresh token.
- El predictor por título **desvía juguetes**: "Abeja Baston Bolsa" sugiere
  Colmenas (`MCO430205`), "Caja Marimba De Colores Madera" sugiere Cajones de
  Madera (`MCO378056`) y "Nobody Sausage Magic Dancer" no sugiere categoría.
- Las categorías de juguetes revisadas exigen **BRAND y MODEL**; `MCO1910`
  (Otros, dominio `MCO-TOYS_AND_GAMES`) solo exige BRAND, tiene
  `listing_allowed=true` y precio mínimo $2.900.
- Conclusión: no confiar en el predictor como única fuente. Hace falta un mapa
  curado por palabras clave/título hacia categorías de juguetería y un fallback
  seguro a `MCO1910`, siempre validando atributos antes de publicar.

### 13.5 Fase 2 — Corrección del adaptador (1,5 a 2 días)

Archivos:

- `hub/canal_ml.py`
  - **Token:** mantener una única ruta de refresh; eliminar cualquier lógica
    duplicada y no permitir ejecuciones paralelas.
  - **Marca y modelo:** reemplazar `palabras[0]` como marca por una función
    `marca_modelo(p)` que lea `data/marcas_ml.json` y use un valor por defecto
    ("Juguetería Taiwan") cuando no haya marca conocida. El modelo sale del
    nombre, no de la primera palabra.
  - **Atributos:** `atributos_para(cid, p)` lee los requeridos de la categoría,
    los llena con los datos del producto y guarda en el estado los que falten.
    Si un requerido no se puede llenar, el producto se salta y queda anotado;
    no se publica a medias.
  - **User Products:** rama según el tag `user_product_seller`.
    - Sin tag: flujo legacy (`title`, sin `family_name`).
    - Con tag: flujo UP (`family_name`, sin `title` ni array de variaciones;
      un UP por variante real). Confirmar el endpoint exacto en la doc al
      implementar.
  - **Categorías:** preferir categorías con `listing_allowed=true`; no mapear
    en silencio a categorías de otra rama (el workaround actual a MCO1910 y
    MCO176652 puede perjudicar exposición y moderación). Si no hay categoría
    válida, saltar el producto.
  - **Validación:** llamar al validador antes de cada alta y guardar el
    resultado en el reporte.
  - **Estado e idempotencia:** reconstruir `data/canal_ml.json` desde ML
    (item_id, `seller_custom_field`, estado, precio, stock, `user_product_id`,
    `family_name`, categoría). Si el SKU ya existe, actualizar; nunca crear
    otro.
  - **Descripción, fotos, GTIN:** descripción en texto plano; máximo 10 fotos;
    GTIN solo si el SKU es un EAN-13 real; `seller_custom_field` con el SKU.
  - **Límites y ritmo:** `sleep` entre llamadas, tope de altas por corrida y
    manejo de 429 ya existente.
- `data/marcas_ml.json` (nuevo, público, sin costos): marcas conocidas por SKU
  o por prefijo de nombre.
- `qa_ml.py` (nuevo, sin red): pruebas de `precio_ml()`, `pasa_rentabilidad()`,
  `titulo_ml()`, `marca_modelo()`, elegibilidad y reconciliación de estado.
- `hub/README.md`: documentar el diagnóstico, la rama User Products y el
  validador.

**Criterio de aceptación:** `python hub/canal_ml.py --dry-run` genera payloads
completos para 5 SKUs; `qa_ml.py` pasa; ningún payload omite un atributo
requerido.

### 13.6 Fase 3 — Publicación controlada (1 día)

1. Correr el diagnóstico y corregir los atributos faltantes de los 5 SKUs.
2. Validar los 5 payloads; no avanzar si alguno falla.
3. Publicar **1 solo producto** de bajo riesgo (por ejemplo, 001309 o 00695)
   con:
   ```bash
   python hub/canal_ml.py --publicar 1
   ```
4. Revisar en el panel de ML: título, categoría, fotos, precio, stock,
   descripción, SKU e `ITEM_CONDITION`.
5. Esperar 24-48 h de moderación. Si queda activo, publicar 2-3 más.
6. Verificar que `data/canal_ml.json` guarde los IDs y que un segundo
   `--sincronizar` no duplique ni cambie nada.
7. Probar el ciclo de vida con un solo aviso: pausar, reactivar y actualizar
   stock/precio desde el script; confirmar en ML.

**Criterio de aceptación:** 3-5 publicaciones activas, sin errores de
moderación, estado reconstruido y ciclo pausar/reactivar funcionando.

### 13.7 Fase 4 — Operación y escalado (2-3 días)

- Reconciliar todo el inventario actual de ML y pausar lo que esté fuera del
  alcance decidido.
- Configurar los secrets de Actions (`ML_CLIENT_ID`, `ML_CLIENT_SECRET`,
  `ML_TOKEN_KEY`) y commitear `hub/ml_token.enc`.
- Probar `sync-ml.yml` con `publicar=0` y luego con `publicar=2`.
- Añadir `schedule` al workflow solo después de 1-2 semanas estable; mientras
  tanto, corridas manuales.
- Publicación progresiva: 5-10 altas por corrida, priorizando productos con
  stock, foto y margen; revisar el reporte antes de cada tanda.
- Reporte operativo `reportes/ml_estado_*.json`: altas, actualizaciones,
  pausas, errores y atributos faltantes; sin costos.
- Definir el volumen objetivo y el criterio de pausa por categoría o producto.

**Criterio de aceptación:** workflow verde, estado estable, sin duplicados y
sin publicaciones fuera de alcance.

### 13.8 Decisiones de la dueña (bloqueantes)

| Decisión | Propuesta inicial |
|---|---|
| Categorías que se publican | Empezar con JUGUETERIA, JUEGO DE MESA y DIDACTICO (mismo alcance que Dropi); ampliar después |
| Marca por defecto | "Juguetería Taiwan" salvo marca conocida en `data/marcas_ml.json` |
| Dimensiones y peso | Necesarios para Mercado Envíos; definir tabla de empaque por categoría o pedir el dato a POSGold |
| GTIN | Solo EAN-13 real; los SKUs internos no se inventan |
| Volumen inicial | 5-10 altas por corrida, revisión humana de la primera tanda |
| Precio | Factor 1,15 y comisión 16 % como supuesto; ajustar con la comisión real del primer aviso |
| Variaciones | Solo si el producto tiene color/talla real; una variante = un User Product |

### 13.9 Cronograma

| Día | Trabajo |
|---|---|
| 1 | Fase 0 (seguridad) y Fase 1 (diagnóstico) |
| 2-3 | Fase 2 (adaptador, marcas, atributos, estado, QA offline) |
| 4 | Fase 3 (validar y publicar 1-3 productos) |
| 5-7 | Fase 4 (reconciliación, workflow, escalado controlado) |

### 13.10 Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Token invalidado por scripts sueltos | Una sola sesión; borrar scripts ad-hoc; persistir cada refresh |
| Migración a User Products | Detectar el tag y implementar la rama UP ahora; no depender del flujo legacy |
| Moderación por categoría/atributos | Validador previo, categorías con `listing_allowed`, revisión de la primera tanda |
| Duplicados con avisos existentes | Reconciliación por `seller_custom_field`/SKU antes de la primera alta |
| Envío incompleto | Definir dimensiones/peso antes de escalar; mientras tanto, no publicar masivamente |
| Secreto expuesto | Rotar la clave, sanitizar el documento y revisar el repo antes de cada commit |
| Sobreventa | `ML_STOCK_MAXIMO`, stock mínimo y sync 4×/día; pausar antes que nada |

### 13.11 Definition of done

- [ ] Secreto rotado y eliminado de la documentación.
- [ ] Diagnóstico del vendedor y del inventario actual de ML ejecutado.
- [ ] `canal_ml.py` con categorías y atributos dinámicos, rama UP y
      reconciliación de estado.
- [ ] `qa_ml.py` y el dry-run pasan.
- [ ] 3-5 publicaciones reales activas y verificadas.
- [ ] Ciclo pausar/reactivar/actualizar probado.
- [ ] `sync-ml.yml` verde con secrets y token cifrado commiteado.
- [ ] Reporte operativo y decisión de categorías/volumen documentados.
- [ ] QA existente intacto: `qa_e2e.py` 42/42 y `qa_carrito.py` 38/38.

### 13.12 Orden de ejecución inmediata

1. Fase 0: rotar el secreto y limpiar los scripts sueltos.
2. Fase 1: correr `hub/ml_diagnostico.py` y revisar el tag `user_product_seller`.
3. Fase 2: implementar las correcciones del adaptador y `qa_ml.py`.
4. Fase 3: validar y publicar **un** producto.
5. Revisar con la dueña y escalar a 5-10 altas por corrida.

### 13.13 Estado de ejecución (29-10-2026)

- **Fase 0 local hecha:** Client Secret eliminado de
  `MERCADOLIBRE/Integracion_Mercado_Libre.md`; scripts ML ad-hoc retirados
  (contenían posibles credenciales y refrescaban el token sin persistirlo).
  La rotación del secreto sigue pendiente por instrucción expresa.
- **Fase 1 hecha en modo público:** `hub/ml_diagnostico.py` consulta
  categorías, atributos y `domain_discovery` sin token; `--payloads` arma los
  payloads completos y `--con-token` usa solo `ML_ACCESS_TOKEN`. El inventario
  real de ML y los tags del vendedor siguen bloqueados hasta tener access
  token.
- **Fase 2 hecha y probada offline:** mapa curado
  `data/categorias_ml.json`, marcas `data/marcas_ml.json`, atributos
  dinámicos, rama User Products, validador previo, descripción posterior al
  alta y reconciliación por `seller_custom_field`. `qa_ml.py` pasa 59/59 y el
  dry-run propone las mismas 5 altas con precios y márgenes.
- **Corrección de dominios:** también se reconocen dominios de juguete que no
  empiezan por `MCO-TOY` (p. ej.
  `MCO-NON_REMOTE_CONTROL_TOY_VEHICLES`). El SKU `00752` dejó de caer en
  Cajas Registradoras (`POWER_SUPPLY_TYPE`) y ahora arma payload válido en
  `MCO433685`. También se rechazan dominios de juguetes de mascotas
  (`PET_TOY`, `DOG_TOY`, etc.): `00841` y `00955` caen a `MCO1910` en vez de
  quedar bloqueados por `MATERIAL`.
- **Muestreo público:** de 50 altas armadas, 49 payloads completos; el único
  fallo es `00888` en `MCO413461` por `POWER_SUPPLY_TYPE` (se salta y anota).
- **Workflow:** `sync-ml.yml` sigue dispatch-only y ahora exige los mapas
  versionados y corre `qa_ml.py` antes de sincronizar.
- **Pendiente:** validar los 5 payloads contra ML, publicar 1 producto,
  reconciliar el inventario real y definir dimensiones/peso, GTIN y volumen.
- **Bloqueo vigente:** no se rota el refresh token ni el Client Secret; para
  avanzar con autenticación hace falta un `ML_ACCESS_TOKEN` temporal o
  autorización expresa para rotar una vez y persistir el token nuevo.
