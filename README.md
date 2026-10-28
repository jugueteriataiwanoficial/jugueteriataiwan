# Juguetería Taiwan — Tienda al detal

Tienda en línea de **Juguetería Taiwan** (Cali, Colombia): juguetería, piñatería,
didácticos, cubos Rubik, cacharros y más, al detal con envíos a toda Colombia.
Se sirve por GitHub Pages desde este repositorio:
**[saldosjugueteriataiwan.com](https://saldosjugueteriataiwan.com/)**

Todo el frente vive en **`index.html`** (HTML + CSS + JS en un solo archivo) y el
catálogo se sincroniza solo, 4 veces al día, desde el sistema POSGold de la tienda.

---

## Arquitectura

| Archivo / carpeta | Qué es |
|---|---|
| `index.html` | La tienda completa: vitrina, carruseles, lightbox, carrito, checkout por WhatsApp y panel de administración |
| `catalogo.js` | Catálogo generado automáticamente en cada corrida. **No se edita a mano**: el cron lo regenera completo |
| `data/configuracion.js` | Configuración publicada desde el panel: la que ven **todos** los visitantes. La mantiene el botón "Publicar para todos" del panel vía la API de GitHub — nadie la edita a mano |
| `assets/fotos/` | Miniaturas WebP (~25 KB) de cada foto de producto, generadas por el cron desde POSGold y servidas por el CDN de GitHub Pages |
| `data/mapa_imagenes.json` | Cache de la relación SKU ↔ slug ↔ foto |
| `sync_posgold.py` | Sincronizador: POSGold → filtros de la tienda → miniaturas → `catalogo.js` |
| `.github/workflows/sync-top300.yml` | Cron en GitHub Actions (07:15, 08:15, 19:15 y 20:15, hora Colombia) |
| `hub/` | **Hub (mini-VTEX)**: catálogo maestro con costos + adaptadores de canal (Mercado Libre activo). Ver [`hub/README.md`](hub/README.md) y [`docs/plan_hub.md`](docs/plan_hub.md) |
| `gen_dropi.py` + `gen_descripciones.py` | Canal **Dropi** para dropshippers: regeneran la carga masiva espejo del catálogo al Precio 5 y sus copys de campaña (en `data/descripciones_dropi.json`). Ver la sección "Dropi" abajo |
| `costos.posgold.enc` | Excel de costos de POSGold **cifrado** (AES-256-CBC + PBKDF2) — el repo es público y los costos jamás viajan en claro |
| `assets/` (favicon, og) | Iconos de la marca |

Archivos **locales** (excluidos del repo por `.gitignore`): `.env` (credenciales
POSGold y llave de cifrado), `*.xlsx` (exportes con costos), `qa_e2e.py` (suite QA
completa; contiene la clave del panel en su escenario de admin),
`reportes/` (CSVs con costos y márgenes + el Excel de novedades de cada corrida)
y `generar_enlace_config.py` (arma el
enlace de configuración de un solo toque para cargar llave y tokens en el
navegador de la administración). `qa_carrito.py` sí vive en el repo: no toca
el panel y no lleva secretos.

## El catálogo automático

En cada corrida, `sync_posgold.py`:

1. Genera un JWT fresco de la API de POSGold (`/apiGold`).
2. Descarga el inventario de la bodega 2 y lo cruza con los costos del Excel cifrado.
   El precio detal sale del **Precio 2** del sistema; el tachado (`comparePrice`),
   del Precio 1 **solo cuando queda por encima** (no se inventan rebajas). Cada
   producto viaja con `stock` (las existencias de la bodega): las reglas de la
   tienda del panel lo usan para decidir qué mostrar.
3. Aplica los **filtros duros de la tienda**: activo + existencias + foto real +
   categoría vendible + costo conocido + rentabilidad positiva.
4. Ordena por margen y resuelve cada producto:
   - **Slug**: el conocido del cache o, para productos nuevos, el derivado del
     nombre en POSGold.
   - **Fotos**: descarga las de POSGold (`Files/Images/`), genera UNA miniatura
     WebP por foto (el servidor de POSGold tarda ~2 s por foto: la vitrina usa
     las miniaturas del repo y el lightbox sube al original bajo demanda).
5. Regenera `catalogo.js` (con el top de utilidad para "Los imperdibles") y
   publica: GitHub Pages republica solo. **Conciliación dura con POSGold**: antes
   y después de generar verifica que el catálogo quede 100 % con productos
   activos y con existencias; si algo no cuadra, aborta sin tocar lo publicado.
6. Deja `reportes/novedades_*.xlsx`: el Excel de la tienda con todo lo que NO
   publica y por qué (inactivos, agotados, duplicados, sin precio, sin foto, sin
   costo, rentabilidad negativa), marcando los que estaban publicados. El cron lo
   sube como artefacto descargable en la pestaña Actions.

Los pedidos no tienen backend: el checkout arma el mensaje de WhatsApp con la
referencia de cada producto y su enlace directo a la ficha en esta misma tienda
(`?producto={slug}`), que al abrirse resalta el juguete con su panel de cantidades.

### Productos nuevos: qué entra solo y qué necesita la tienda

Un juguete recién creado en POSGold entra **solo** en la próxima corrida si
cumple los filtros: el cron le genera automáticamente la miniatura WebP, el
slug, la categoría web, el precio de venta (el Precio 2 tal cual: margen y
redondeo se configuran desde el panel), las existencias, el tachado (solo
si hay un precio mayor real) y el enlace de ficha. Lo único que el cron no
puede inventar es el **costo**: un producto sin costo en el Excel queda
oculto con el motivo "sin costo en Excel" hasta que alguien refresque los
costos (abajo).

La vitrina no tiene tope práctico de productos: el grid pagina de 60 en 60
con scroll infinito y los carruseles muestran máximo 32 por categoría, así
que el inventario puede crecer sin congelar el navegador. Lo que sí crece es
el peso: `catalogo.js` (~0,5 KB por producto) y las miniaturas (~25 KB por
foto) viven en el repo.

### Costos: por qué van por Excel y no por API

El API de POSGold **no expone los costos** (verificado contra
`GetProduct_V6`: 34 campos y ninguno de costos; su login web tiene captcha),
y este repo es público: el Excel con costos y márgenes nunca puede subir en
claro. Por eso el flujo es: exportar → cifrar → commitear cifrado
(`costos.posgold.enc`, solo 4 columnas, ~115 KB).

El formato es **el que POSGold genera tal cual** (una hoja "Productos",
86 columnas, 6.000+ filas, encabezados en minúscula: `productoid`, `costo`,
`costopromedio`, `costoultimo`): exportar y subir, sin prepararlo. Y es a
prueba de error:

- El **panel** valida el archivo antes de cifrarlo: rechaza uno incompleto,
  sin columnas de costo o con menos de 1.000 filas (export filtrado).
- `actualizar_costos.py` verifica el roundtrip SHA-256 antes de commitear.
- El **cron** lee por nombre de columna (el orden y las otras 82 columnas no
  importan). Si POSGold renombrara un encabezado en el futuro, el script
  aborta **antes de tocar `catalogo.js`**: la tienda publicada queda
  intacta con los costos anteriores hasta corregir. Un producto sin costo
  válido queda oculto, nunca rompe la vitrina.

La frecuencia correcta es semanal o mensual: los costos solo cambian cuando
compran inventario; stock, precios y fotos se refrescan solos en cada
corrida. El panel avisa "⚠️ Costos desactualizados" cuando el archivo pasa
de 15 días. Si POSGold algún día agrega el campo de costo al API, el
workflow documenta cómo retirar la dependencia del Excel.

## El hub: catálogo maestro y canales (nuestro mini-VTEX)

El **hub** (`hub/`) es la capa que convierte este repo en una plataforma de
canales: un catálogo maestro único (todo el inventario de POSGold con costos,
gitignored) del que beben los adaptadores. El primero en producción planeado:
**Mercado Libre** — altas, pausas/activaciones, stock y precios automáticos
con guardia de rentabilidad sobre la comisión real de ML.

- Documentación operativa (comandos, variables, token rotativo):
  [`hub/README.md`](hub/README.md).
- Plan completo (fases 2–4: webhooks, OMS, panel unificado) y decisiones
  abiertas: [`docs/plan_hub.md`](docs/plan_hub.md).
- **Decisión de alcance (23-10-2026)**: la empresa definió que la
  integración es **solo sincronización de productos** (fases 0+1); los
  pedidos de Mercado Libre se gestionan directamente en la plataforma de
  ML, **no** en POSGold. Por eso los endpoints de pedidos y webhooks de
  POSGold (Inactivos) no bloquean nada y el correo a GoldPOS
  ([`docs/correo_goldpos.md`](docs/correo_goldpos.md)) queda **archivado
  como plan B** — no se espera respuesta para el alcance vigente.

```bash
python hub/construir_hub.py                       # catálogo maestro (requiere Excel de costos)
python hub/canal_ml.py --dry-run                  # plan del canal ML sin token ni red
python hub/canal_ml.py --autorizar                # primera vez: OAuth de ML
python hub/canal_ml.py --sincronizar --publicar 3 # corrida completa del canal
```


## Dropi: la carga masiva espejo del catálogo

El marketplace **Dropi** se alimenta con
`DROPI/carga-masiva-productos-con-variables.xlsx` (formato oficial de 20
columnas), un **espejo del catálogo** que regenera `gen_dropi.py` con las
mismas reglas de la tienda: solo elegibles de `catalogo.js` y solo
**JUGUETERIA, JUEGO DE MESA y DIDACTICO** (el alcance del
marketplace), PRECIO =
**Precio 5 de POSGold** (en vivo con `GetProduct_V6`) **cerrado al
siguiente $1.000** ($10.500 → $11.000, siempre hacia arriba),
PRECIO SUGERIDO =
**PRECIO + $10.000 como mínimo** en todas las filas (el Precio 2 real
solo se conserva si ya lo supera; también cerrado a $1.000),
rentabilidad sobre `costopromedio`,
mínimo **50 unidades**, **piso de precio de $2.000** (con
**$5.000 para piñatería y cacharrería** —no son producto de
dropshipping— y **slime conservado a $500**: el gancho viral),
sin duplicar lo ya publicado en Dropi (conciliación con **todos**
los exports `products_*.xlsx` y `variaciones*.xlsx`: el listado
puede partirse en varias páginas) y sin imagen no se
carga. Además del principal, `gen_dropi.py` genera la **serie de
cargas en partes de hasta 50 filas** (`carga-masiva-parte-01-de-
NN.xlsx`, `-02-`, ...) que cubre el 100 % de la carga y se sube
**en orden**. El archivo se escribe con **xlsxwriter** (shared strings):
openpyxl escribe inline strings y el cargue de Dropi responde
"Encabezado no reconocido". Toda descripción es **texto plano** (sin
HTML, por decisión de la
tienda) y cierra con el pie de Juguetería Taiwan: importadores directos,
despachos antes/después de la 1:00 p. m. y WhatsApp de atención
dropshippers **3106988465**.

Los textos de campaña —pensados para que los dropshippers los copien a
sus anuncios— los produce `gen_descripciones.py`: un modelo de **visión**
(API de NVIDIA, llave `NVIDIA_API_KEY` en el entorno, nunca en el repo)
lee la **foto real de cada producto** y escribe el copy sin inventar
medidas, edades ni materiales. El resultado vive en
`data/descripciones_dropi.json` (por SKU; las correcciones de la dueña
siempre mandan y nunca se pisan). Si Dropi ya tiene un producto
publicado, la carga masiva no lo duplica; lo que sale del catálogo hay
que pausarlo a mano en el panel de Dropi.

**Estado (26-10-2026) — dos rechazos corregidos; export absorbido,
carga lista para subir limpia.** El primer intento (26-10 11:06) fue
rechazado por Dropi con **"Encabezado no reconocido en la estructura
del archivo"**: openpyxl escribe inline strings y el parser de Dropi
no los lee; el archivo se reescribió con **xlsxwriter** (shared
strings) y ahora el script aborta si el xlsx no trae
`sharedStrings.xml`. Con la misma corrección entró la regla de la
dueña: **PRECIO SUGERIDO = precio + $10.000 mínimo en todas las
filas** (99 iban vacías, 1.045 por debajo del mínimo; solo 82
conservan el Precio 2 real porque ya lo superan). El cargue de
etapas (13:34, juguetería + juego de mesa + didáctico) marcó **95
filas por NOMBRE de 40+ caracteres** ("supera el máximo permitido y
será truncado"); el reintento (13:36) marcó solo 7. Corregido:
**nombre máximo 39** (recorte por palabra completa, medido en
bytes UTF-8 para no romper la Ñ). **El export `products_20261026_
154402.xlsx` reveló que NADA entró de esos intentos** (solo viven
los 10 productos históricos): el cargue de Dropi es todo-o-nada —
si UNA fila falla, no se importa nada — así que no hay duplicados
y el archivo corregido se sube limpio. La carga quedó completa y
verificada: **1.144 productos**, todos con Precio 5 rentable,
imagen viva, 50+ unidades, copy de campaña, pie, sugerido mínimo y
nombre ≤ 39.
**140 suben PRIVADO=SI** — ocultos del marketplace, activables
después desde el panel de Dropi — por curación de la tienda: las
figuras de colección y licencias (la misma categoría que la
vitrina mantiene oculta), un solo ejemplar público por cada grupo
de nombres repetidos y una selección propia de la dueña.
`gen_dropi.py` preserva esa marca por SKU en cada regeneración y
los SKUs nuevos entran siempre públicos. **Piso de precio
(27-10-2026)**: el Precio 5 mínimo para enlistar es **$2.000**
(por debajo, empacar y despachar una unidad cuesta más de lo que
deja), con **$5.000 para piñatería y cacharrería** y **slime
conservado a $500**; aplicado, la carga quedó en **1.003
productos** (112 salieron por el piso, incluidos 10 privados; los
75 slime se conservan todos).

**Estado (28-10-2026) — cargue por partes en marcha, con alcance de
3 categorías y precios cerrados.** Tras corregir formato
(xlsxwriter), nombres (≤ 39 bytes) y choques de SKU, una prueba de
50 productos públicos entró limpia por el panel y el listado
publicado resultó **partirse en varias páginas** (la conciliación
absorbe TODOS los exports). Regla nueva: **el cargue se sube en
partes de hasta 50 filas en orden serie** — `gen_dropi.py` genera
`carga-masiva-parte-01-de-NN.xlsx`, `-02-`, ... hasta cubrir el
100 % (partición disjunta: se suben seguidas, sin re-exportar
entre ellas; al terminar la serie o si una falla, re-exportar
todas las páginas y regenerar). Las partes 01 y 02 de la serie
vieja entraron limpias (100 publicados); la 03 fue rechazada en
el todo-o-nada por UN SKU con listado viejo invisible (03371) —
mismo patrón de 04521 y 04539, que viven en
`SKU_BLOQUEADOS_DROPI` y se prueban aparte con
`carga-masiva-mini-bloqueados.xlsx` (generado en cada corrida).
Reglas de la dueña aplicadas después: **la carga solo lleva
JUGUETERIA, JUEGO DE MESA y DIDACTICO** (616 productos salieron
del alcance; los 125 ya publicados en Dropi de otras categorías
se pausan a mano) y **todos los precios van cerrados al siguiente
$1.000, siempre hacia arriba**. El principal quedó en **333
productos (13 privados)** y la serie nueva es **7 partes** que se
suben desde la 01. El
panel de Dropi trae dos opciones — **Carga masiva** (crear) y
**Actualizar** (lo publicado) —: el modelo objetivo es altas
incrementales por carga masiva + resubida periódica de precios y
existencias por actualizar (pendiente probar con 2-3 SKUs que
actualiza sin pisar los privados); las bajas siempre a mano.

## Características de la tienda

- Vitrina con grid paginado, carruseles por categoría y lightbox tipo Amazon.
- **Venta al Precio 2 exacto de POSGold** (desde el 22-10-2026): margen 0 y
  sin redondeo — el precio que ve el cliente es el del sistema, tal cual
  (si algún día se quiere sumar ganancia, se sube desde el panel). El
  tachado es honesto: solo aparece si POSGold trae un Precio 1 por encima
  del precio de venta; no se fabrican rebajas.
- **Reglas de la tienda** activables desde el panel, varias a la vez y
  reversibles al instante: *Un solo ejemplar de cada juguete* (de los
  duplicados exactos queda el de más existencias; activa) y *Ocultar
  juguetes agotados* (existencias en cero o menos; opción disponible,
  aún sin activar).
- Efecto de compra: al agregar al carrito, la foto del juguete vuela hasta
  el icono y el contador rebota; al enviar el pedido, un confeti breve
  celebra el cierre (sin librerías: reusa la miniatura en cache, respeta
  `prefers-reduced-motion` y se omite si el checkout ya está abierto).
- Checkout por WhatsApp estilo COD en 5 fases (pedido → datos → entrega → pago → notas).
- Pago a crédito en cuotas (Addi / Sistecrédito) con simulador en tarjetas,
  carrito y checkout — configurable desde el panel.
- Panel de administración (enlace "Administración" del pie, `?admin=1` o
  Ctrl+Shift+A): precios y márgenes, reglas de la tienda, producto en
  tendencia, favoritos, ocultar categorías y productos, temporada
  (Navidad), analítica (Pixel de Meta, GA4, Clarity) y actualización de
  costos cifrando el Excel del navegador.
- **Publicación autónoma**: el botón "Publicar para todos" del panel sube
  la configuración vigente al proyecto de GitHub (API de Contents, el
  mismo token de los costos) y todos los visitantes la ven en ~1 minuto
  — sin editar archivos ni pedirle nada a nadie. "Guardar en MI
  computador" previsualiza solo en ese navegador; lo publicado manda
  sobre la fábrica y sobre lo guardado cuando su versión es más nueva.
  La clave del panel nunca se publica.
- Ocultamiento reversible de categorías y productos individuales.
- Analítica con píxeles activables por ID, sin código adicional.

## Panel de administración (el día a día de la tienda)

Se abre con el enlace "Administración" del pie, `?admin=1` o Ctrl+Shift+A
(clave de `TIENDA_DEFECTO.claveAdmin`).

- **Una sola vez**: la persona de confianza corre `python generar_enlace_config.py`
  y envía por WhatsApp el enlace de configuración; al abrirlo, la llave de cifrado
  y los tokens de GitHub quedan cargados en ese navegador para siempre (nunca van
  escritos en el archivo de la página: el repo es público).
- **Guardar en MI computador** — previsualiza solo en ese navegador.
- **Publicar para todos** — sube la configuración al proyecto (API de GitHub) y
  todos los visitantes la ven en ~1 minuto. Es el botón normal: nadie edita
  archivos ni publica por ella.
- **Actualizar costos** — exporta el Excel de PRODUCTOS de POSGold (todo el
  inventario, sin filtros) y súbelo en el panel: se cifra en el navegador y el
  cron lo aplica al ranking.
- **Sincronizar ahora** — lanza la corrida del catálogo al instante (o espera al
  cron de las 07:15, 08:15, 19:15 y 20:15).

Si una corrida del cron termina con "AVISO git push" (el panel publicó algo a
media corrida), no es un error: la siguiente corrida lo publica todo.

## Seguridad

- El repositorio es **público**: las credenciales viven en `.env` (local) y en
  los *secrets* de GitHub Actions. **Nunca** se suben tokens, llaves ni costos en claro.
- Los costos viajan cifrados (`costos.posgold.enc`) y la llave solo existe en
  `.env` y en el secret `COSTOS_KEY`.
- El panel de administración es una barrera de conveniencia del lado del
  cliente, no seguridad real: quien tenga acceso al archivo puede ver su clave.

## Desarrollo local

```bash
# suite QA completa (Playwright): 42 pruebas en Chrome y WebKit/Safari,
# escritorio y móvil — debe pasar 42/42 antes de publicar
python qa_e2e.py

# suite del carrito (38/38): identidad de los productos agregados,
# aritmetica exacta (totales, cantidades, fusion de lineas, persistencia),
# mensaje de WhatsApp, vuelo al carrito y confeti — sin secretos, vive en el repo
python qa_carrito.py

# sincronización de prueba (no toca catalogo.js ni publica)
python sync_posgold.py --dry-run

# regenerar TODAS las miniaturas desde POSGold (migraciones / auditorías)
python sync_posgold.py --verificar-todas

# enlace de configuración de un solo toque (llave + tokens para un navegador)
python generar_enlace_config.py

# regenerar la carga masiva de Dropi (espejo del catalogo al Precio 5,
# reglas de la tienda + conciliacion dura; --dry-run solo diagnostica)
python gen_dropi.py

# copys de campana para Dropi con vision (API NVIDIA; llena por lotes
# data/descripciones_dropi.json con lo que le ve a la FOTO real de POSGold)
python gen_descripciones.py --limit 20

# verificar sintaxis JS del index (bloques <script> sin src)
node -e "const h=require('fs').readFileSync('index.html','utf8');const re=/<script(?![^>]*\bsrc=)(?![^>]*ld\+json)[^>]*>([\s\S]*?)<\/script>/gi;let m;while((m=re.exec(h))!==null){if(m[1].trim())new Function(m[1]);}console.log('OK')"
```

Publicar = `git push origin main` (GitHub Pages reconstruye en ~1 minuto).

## Checkpoint — octubre de 2026

Estado del proyecto tras la última tanda de mejoras (actualizado a finales de octubre de 2026):

| Área | Estado |
|---|---|
| Precios | **Venta al Precio 2 exacto de POSGold** (margen 0, sin redondeo; fábrica en `PRECIOS_DEFECTO` y publicado en `data/configuracion.js`, sincronizados). Tachado honesto: solo del Precio 1 real por encima del precio de venta — hoy ningún producto lo trae, así que no hay badges de descuento inventados |
| Reglas de la tienda | Sección nueva del panel con selección múltiple y vista previa viva: *deduplicar por mayor existencias* (activa: de cada grupo de repetidos queda el de más stock) y *ocultar agotados* (opción inactiva); conmutan entre sí y cada producto de `catalogo.js` viaja con `stock` |
| Efecto de compra | **Vuelo al carrito** + pop del badge al agregar; **confeti** al enviar el pedido — sin librerías, respetan `prefers-reduced-motion` |
| QA | `qa_e2e.py` **42/42** y `qa_carrito.py` **38/38** (verde antes de publicar; el carrito exige el Precio 2 exacto en todo el catálogo) |
| Costos | Documentado: el API de POSGold no trae costos (verificado); el Excel de PRODUCTOS es el contrato de formato y el pipeline falla seguro en cada etapa |
| Catálogo | 1.943 productos publicados, **todos activos y con existencias**: la conciliación dura con POSGold (3 puertas) aborta la corrida sin publicar si algo no cuadra; los nuevos de POSGold entran solos en la próxima corrida si cumplen los filtros (incluido costo en el Excel) |
| Dropi | Carga masiva espejo del catálogo (`gen_dropi.py`), **solo JUGUETERIA, JUEGO DE MESA y DIDACTICO** (alcance de la dueña 28-10): **Precio 5 de POSGold** en vivo **cerrado al siguiente $1.000** (siempre hacia arriba), **PRECIO SUGERIDO = precio + $10.000 mínimo** (también cerrado), **nombre máximo 39 caracteres** (recorte por palabra completa, en bytes UTF-8), rentabilidad sobre costo, mínimo 50 unidades, **piso de precio $2.000**, sin duplicar lo ya publicado (absorbe **todos** los exports: el listado se parte en páginas) y descripciones de campaña en **texto plano** con el pie de la tienda (importadores directos, despachos 1:00 p. m., WhatsApp dropshippers **3106988465**); los copys los produce `gen_descripciones.py` leyendo la **foto real** de cada producto con un modelo de visión (API de NVIDIA). **Cargue en serie (28-10): partes de hasta 50 filas** (disjuntas, en orden, cubren el 100 % y se regeneran en cada choque) — 150 ya publicados (prueba-50 + partes 01-02 viejas); la 03 vieja cayó por el SKU bloqueado 03371 y la serie se regeneró en **7 partes** (principal: **333 filas, 13 privados**); los bloqueados del alcance (04521, 04539) se prueban aparte con `carga-masiva-mini-bloqueados.xlsx`; **125 publicados de otras categorías quedaron fuera del alcance nuevo — pausarlos es manual** |
| Novedades | Cada corrida deja `reportes/novedades_*.xlsx` para la tienda: lo que no publica y por qué (inactivos, agotados, duplicados, sin precio, sin foto, sin costo), marcando lo que estaba publicado; el cron lo sube como artefacto de Actions (`novedades-sync`) |
| Hub | **Fase 0+1 construidas** (21 oct 2026): catálogo maestro con costos + adaptador de Mercado Libre (`hub/`); pendiente aprobar la app en developers.mercadolibre.com.co. **Decisión de alcance (23 oct 2026)**: solo sincronización de productos — los pedidos de ML se gestionan en la plataforma de ML, no en POSGold; el correo a GoldPOS queda archivado como plan B (`docs/correo_goldpos.md`) |
| Panel | Publica configuración y costos solo (API de GitHub); nada se edita a mano |
| Dopamina descartada | Urgencia artificial y metas falsas: probadas/evaluadas y rechazadas (la urgencia ya costó pedidos una vez) |
