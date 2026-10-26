# AGENTS.md — Guía para trabajar en este repositorio

Tienda al detal de Juguetería Taiwan (Cali): un `index.html` monolítico publicado
por GitHub Pages en saldosjugueteriataiwan.com, con catálogo sincronizado 4×/día
desde POSGold por un cron de GitHub Actions. Ver `README.md` para la
arquitectura completa.

## Reglas de oro

1. **El repo es público.** Nada de tokens, llaves, contraseñas ni costos en claro.
   Las credenciales viven en `.env` (local, gitignored) y en los *secrets* de
   GitHub Actions. Los `.txt` están TODOS en `.gitignore` precisamente porque
   guardan material privado.
2. **`catalogo.js` y `assets/fotos/` son generados** por `sync_posgold.py`:
   jamás se editan a mano. El historial de git es su respaldo.
   `data/configuracion.js` lo escribe el PANEL (API de GitHub), no el cron
   ni personas; tampoco se edita a mano.
3. Al publicar, `index.html` es lo único que se suele tocar a mano; el cron
   commitea `catalogo.js`, `data/mapa_imagenes.json` y `assets/fotos`.
4. Textos de la tienda: español de Colombia, tuteo, tono cálido. Los mensajes
   de commit del repo no llevan tildes (estilo histórico).
5. Nunca commitear sin que pase el QA (regla 42/42 de `qa_e2e.py` y 38/38 de
   `qa_carrito.py`, abajo).

## Comandos de verificación

```bash
python qa_e2e.py            # suite completa: 42/42 esperado (Playwright + WebKit)
python qa_carrito.py        # carrito: identidad, aritmetica exacta, mensaje WhatsApp, vuelo y confeti (38/38)
python sync_posgold.py --dry-run   # sync de prueba sin tocar catalogo.js
python sync_posgold.py --verificar-todas   # auditar/regenerar miniaturas
python generar_enlace_config.py    # enlace de config (llave+tokens) para un navegador
python gen_dropi.py          # actualiza la carga masiva DROPI con Precio 5 de POSGold (--dry-run para probar)
python gen_descripciones.py  # copys de campana con vision (API NVIDIA, llena data/descripciones_dropi.json por lotes)
```

Verificación de sintaxis JS del index (los dos primeros `<script>` son JSON-LD,
no JavaScript):

```bash
node -e "const h=require('fs').readFileSync('index.html','utf8');const re=/<script(?![^>]*\bsrc=)(?![^>]*ld\+json)[^>]*>([\s\S]*?)<\/script>/gi;let m;while((m=re.exec(h))!==null){if(m[1].trim())new Function(m[1]);}console.log('OK')"
```

Publicar: `git push origin main`. GitHub Pages republica en ~1 min. Si el push
es rechazado, el cron movió el remoto: `git pull --rebase origin main` y de nuevo.

## Mapa del código (index.html)

- **Configuración editable** (todo lo que cambia la dueña va aquí):
  `TIENDA_DEFECTO` y `PRECIOS_DEFECTO` son la FÁBRICA; `data/configuracion.js`
  es lo PUBLICADO desde el panel (botón "Publicar para todos", vía API de
  GitHub con el token Contents) y **manda sobre la fábrica**. Si cambias
  TIENDA_DEFECTO o PRECIOS_DEFECTO a mano, actualiza o borra también
  `data/configuracion.js` o tu cambio será invisible. "Guardar en MI
  computador" queda en localStorage del navegador (con marca de versión
  `jt_config_base`: lo publicado más nuevo lo reemplaza al recargar).
  `CONFIG` → valores de fábrica en ejecución.
- **Publicación del panel**: `publicarConfigNavegador()` sube TIENDA+PRECIOS a
  `data/configuracion.js` con versión siempre creciente (`Math.max(Date.now(),
  publicada+1)`) y SIN `claveAdmin` (la clave nunca se publica). La absorción
  vive arriba de `cargarTienda` (`CONFIG_PUBLICA`, `TIENDA_FABRICA`,
  `PRECIOS_FABRICA`, `BASE_CONFIG_KEY`).
- **Panel de administración**: login con `TIENDA_DEFECTO.claveAdmin`, abre con
  el enlace del pie, `?admin=1` o Ctrl+Shift+A. Sus secciones se pintan desde
  funciones `pintar*Admin()`.
- **Visibilidad**: `catalogoVisible()` filtra categorías/productos ocultos
  (`TIENDA.categoriasOcultas` / `productosOcultos`) y luego aplica las reglas
  activas (`aplicarReglasTienda()`); el panel siempre ve `CATALOGO_BASE`
  completo. La clave `"navidad"` esconde la sección de temporada, no sus
  productos.
- **Reglas de la tienda**: `TIENDA.reglas` (ids separados por comas, como
  categoriasOcultas) enciende/apaga las reglas de `REGLAS_TIENDA` desde el
  panel (selección múltiple, `pintarReglasAdmin()` con vista previa viva de
  cuántos ocultaría). Regla 1 `deduplicar-mayor-existencia` (activa de
  fábrica): de los publicados con nombre o descripción EXACTAMENTE iguales
  (`gruposDuplicadosExactos`, insensible a mayúsculas y espacios repetidos,
  uniendo ambos criterios) solo muestra el de MÁS existencias de POSGold
  (campo `stock` de `catalogo.js`; empate → el más barato; sin `stock` se
  lee 0 y la regla nunca rompe). Regla 2 `ocultar-sin-existencias`
  (desarrollada 22-10-2026, INACTIVA de fábrica y en lo publicado; se
  activa desde el panel): esconde los juguetes con `stock` ≤ 0; con un
  catálogo viejo sin el campo se duerme en vez de esconder todo. Las dos
  reglas conmutan (mismo resultado en cualquier orden; un grupo
  duplicado agotado por completo desaparece entero). Agregar una regla
  nueva = objeto en `REGLAS_TIENDA` con `id`, textos y `filtrar()`.
- **Precios**: `precioDetal()` = base × multiplicador × (1+margen) y
  `redondearPrecio()` al múltiplo de `PRECIOS.redondeo`. **Desde el
  22-10-2026 la tienda vende al Precio 2 exacto de POSGold**: margen 0,
  redondeo 0 (la fábrica en `PRECIOS_DEFECTO` y lo publicado en
  `data/configuracion.js` deben llevar los mismos valores o lo publicado
  tapa a la fábrica). La base llega del cron: el **Precio 2** del sistema
  que trae `catalogo.js`. El tachado (`compareDetal`) es **honesto**:
  solo existe si POSGold trae un Precio 1 real por encima del Precio 2
  (el cron ya no inventa respaldos ×1,19 y el index no fuerza la banda
  20–30%); hoy ningún producto lo trae, así que no hay badges de
  descuento. Por debajo del paso de redondeo no se redondea (el piso de
  $1.000 que disparaba los baratos se retiró). El margen de temporada
  (`margenesPorCategoria.temporada`) solo aplica entre **octubre y
  diciembre** (`margenDeCategoria`); hoy está en 0.
- **Carrito**: `cart` fusiona líneas por `lineId` (`id::talla`), el total
  sale de `getCartSummary()` (Σ precio×cantidad) y `qa_carrito.py` (38/38)
  vigila identidad de líneas, aritmética exacta hasta el mensaje de
  WhatsApp, persistencia y vuelo.
- **Vuelo al carrito**: `volarAlCarrito(product)` clona la miniatura de la
  tarjeta (`.jt-fly`) y la anima con Web Animations hasta el icono; abre el
  checkout ~300 ms después para que el ojo siga el recorrido. Guardas: no
  vuela con el checkout abierto (sugeridos), con
  `prefers-reduced-motion`, sin Web Animations ni sin foto cargada.
- **Confeti del pedido**: `lanzarConfeti()` celebra el envío del pedido
  (`.jt-confeti`, ~70 partículas, ~1,4 s) con las mismas guardas; NUNCA debe
  romper el pedido (try/catch total).
- **ScrollLock**: congela el scroll del body conservando la posición (menú,
  carrito, lightbox y checkout). Toda pantalla completa debe usarlo, nunca
  toggles directos de `body-no-scroll`.
- **Lightbox**: muestra la miniatura del repo al instante y sube al original de
  POSGold con `imgFullUrl()` (usa `fulls[]` del catálogo, mismo índice que
  `images[]`).
- **Pedidos**: `sendWhatsAppOrder()` arma el mensaje de WhatsApp; el enlace de
  cada producto apunta a `?producto={slug}` de esta misma tienda (deep link que
  resalta la tarjeta con `scrollToProduct`).
- **Sincronización (`sync_posgold.py`)**: el detal sale del **Precio 2** del
  sistema (el tachado, del Precio 1 cuando queda encima) y los filtros duros son
  activo + existencias + foto + categoría + costo + rentabilidad. Cada producto
  de `catalogo.js` viaja con `stock` (existencias de la bodega: lo usan las
  reglas de la tienda del panel para deduplicar, desde 2026-10-22). Conciliación
  dura con POSGold en 3 puertas (pre-generación, contenido y archivo escrito):
  si el catálogo quedara con UN solo producto inactivo o sin existencias, aborta
  SIN tocar lo publicado. Cada corrida deja `reportes/sync_*.csv` (con costos:
  nunca publicar) y `reportes/novedades_*.xlsx` para la tienda (lo que no
  publica y por qué, marcando lo que estaba publicado); el cron sube ese Excel
  como artefacto de Actions.
- **Hub (`hub/`, fase 0+1)**: catálogo maestro + adaptador de Mercado Libre.
  `hub/construir_hub.py` genera `hub/productos.json` — TODO el inventario de
  POSGold (activos y agotados: los canales necesitan saber cuándo pausar) con
  costos unidos por `Productoid`; es **gitignored, NUNCA se commitea ni se
  imprimen costos a consola**. `hub/canal_ml.py` lee ese hub y gestiona el
  canal ML: altas, pausas/activaciones, stock y precio (detal ×
  `ML_PRECIO_FACTOR`, con guardia de rentabilidad descontando la comisión
  `ML_COMISION_PCT`; sin costo conocido no publica). El estado del canal
  (SKU→listing, sin costos) vive en `data/canal_ml.json` (generado, se
  commitea). El refresh token de ML **rota en cada uso**: se persiste cifrado
  en `hub/ml_token.enc` (AES-256+PBKDF2, mismo modelo que `costos.posgold.enc`;
  ese blob sí se commitea) y el workflow `sync-ml.yml` lo renueva y lo vuelve a
  subir. `sync-ml.yml` es dispatch-only hasta aprobar la app en
  developers.mercadolibre.com.co. Comandos: `--autorizar` (primera vez),
  `--prueba`, `--dry-run` (sin red ni token), `--sincronizar --publicar N`.
- **POSGold: panel Desarrollador de la cuenta (21-10-2026)**: la empresa
  tiene 8 endpoints API registrados, 5 de creación de PEDIDOS
  (`PedidoWebCreateAnonimo_v3/_v5`, `PedidoCreateExternal`,
  `PedidoCreateExternal_Anonimo`, `PedidoCreate_PluginWordPress`),
  `PedidoGestionApi/CrearPedido`, `CiudadApi/CiudadesGet` y el
  `GetProduct_V6` en uso — todos marcados "Inactivo" (verificado:
  `CiudadesGet` responde 404 con JWT válido; `GetProduct_V6` 200). Hay
  botón "Actualizar permisos" en `/apilog/Index`. La sección Webhooks
  existe pero está vacía. El token maestro (Empresa → Token) vive en
  `.env` como `POSGOLD_API_TOKEN` (local, gitignored).
  **Decisión de alcance (23-10-2026)**: la integración es SOLO
  sincronización de productos (fases 0+1 del hub); los pedidos de ML se
  gestionan en la plataforma de Mercado Libre y NO se empujan a POSGold —
  los endpoints de pedidos/webhooks Inactivos no bloquean nada y el
  correo a GoldPOS (`docs/correo_goldpos.md`) queda ARCHIVADO como plan B
  (solo relevante si se retoma la OMS). Plan en `docs/plan_hub.md` y
  `hub/README.md`.
- **Dropi (`DROPI/`, `gen_dropi.py`, `gen_descripciones.py`)**: la carga
  masiva `DROPI/carga-masiva-productos-con-variables.xlsx` (formato
  oficial de 20 columnas) es un ESPEJO del catálogo. La regenera
  `gen_dropi.py` (requiere `.env`; `--dry-run` diagnostica sin tocar)
  aplicando las mismas reglas de la tienda, en orden:
  1. Solo elegibles de `catalogo.js` (activo + existencias + foto +
     costo + rentabilidad, ya filtrados por el cron): el que salió del
     catálogo sale también de Dropi.
  2. PRECIO = **Precio 5 de POSGold** (IVA incluido, consultado EN VIVO
     con `GetProduct_V6` como el cron); sin Precio 5 no se carga
     (regla "sin precio").
  3. PRECIO SUGERIDO = Precio 2 real solo si queda por encima del
     Precio 5 (tachado honesto, nunca inventado).
  4. **Rentabilidad al Precio 5**: utilidad = Precio 5 sin IVA −
     `costopromedio` del Excel `PRODUCTOS*.xlsx` (unido por
     `productoid`, campo ajustable con `COSTO_CAMPO` en `.env`, igual
     que el cron); en/bajo el costo no se enlista y sin costo en el
     Excel tampoco (fail-safe, como el catálogo).
  5. **Nivel de existencias: solo enlistan productos con 50+ unidades**
     (STOCK < 50 no se carga; ajustable con `--min-stock`).
  6. **Conciliación con lo ya publicado en Dropi**: los SKUs (incluye
     `VARIATION SKU`) y nombres normalizados de los exports
     `DROPI/products_*.xlsx` y `DROPI/variaciones*.xlsx` (el más
     reciente de cada patrón) no se enlistan — no duplica lo que ya
     vive en la tienda Dropi; el nombre cubre exportados sin SKU.
  7. Sin imagen no se carga: conserva la URL viva de la fila (verifica
     todas con HEAD), rellena la faltante con el original JPEG de
     POSGold (`fulls[0]`) y usa la miniatura del repo de respaldo.
  8. **Descripciones de campaña para dropshippers**: TODAS las filas
     llevan texto de venta + el pie de la tienda (importadores
     directos, despachos antes/después de la 1:00 p. m. y WhatsApp de
     atención dropshippers **3106988465**). El texto va en **TEXTO
     PLANO** (decisión de la tienda: Dropi puede no renderizar HTML) —
     viñetas "•" y saltos de línea; cualquier HTML que llegue se
     convierte automáticamente. El copy por SKU vive en
     `data/descripciones_dropi.json` y manda sobre la curación; el
     pie lo agrega `gen_dropi.py` al escribir y lo corta al releer el
     Excel, nunca se duplica.
  9. Curación por SKU preservada (PRIVADO, CATEGORIA, DESCRIPCION,
     imágenes buenas); los SKUs nuevos heredan categoría del export
     07-10 (`DROPI_carga_masiva*ENRIQUECIDO*`) o del mapeo web→Dropi.

  Los copys los produce `gen_descripciones.py` con un modelo de visión
  por la API de NVIDIA (llave en la variable de entorno
  `NVIDIA_API_KEY`, NUNCA en el repo; modelo por defecto
  `nvidia/nemotron-3.5-super-vl-120b-a12b`, que lee la FOTO real de
  POSGold y tiene prohibido inventar medidas/edades/materiales —
  llama-3.2-11b-vision inventaba specs y se descartó), por lotes
  `--limit N` ordenados por stock (mayor rotación primero),
  incremental y reanudable: lo que ya está en el JSON no se pisa, las
  correcciones de la dueña mandan. Conciliación dura antes de
  escribir: aborta si queda UNA fila sin precio, sin imagen, con
  stock ≤ 0, rentabilidad ≤ 0 al Precio 5, HTML en la descripción o
  sin el pie de la tienda. Los Excel de DROPI no se commitean (salen
  en `.gitignore`). OJO: Dropi no borra lo ya publicado: los que
  salen del catálogo, quedan sin Precio 5, pierden rentabilidad, ya
  viven en Dropi o bajan de 50 unidades hay que pausarlos a mano en
  el panel de Dropi.

## Gotchas conocidos

- Playwright sobre `file://` no registra Resource Timing de imágenes ni descarga
  en frío: para medir rendimiento real, probar contra el sitio publicado.
- El servidor de fotos de POSGold tarda ~2 s por archivo: la vitrina NUNCA debe
  cargar originales (usa las miniaturas del repo; el original es solo para el
  lightbox, bajo demanda).
- Los carruseles muestran máximo 32 productos por fila: renderizar más avalancha
  peticiones (pasamos de 562 `<img>` a 72 y el arranque bajó de 12 s a ~2 s).
- El filtro de categorías del panel se guarda por navegador (localStorage);
  para que lo vean TODOS, la administración usa "Publicar para todos" (o se
  edita `data/configuracion.js`, nunca a mano: se publica desde el panel).
- El push del cron puede ser rechazado si el panel publicó algo a media
  corrida (el remoto se movió entre el checkout y el push del runner): la
  corrida avisa "AVISO git push" y la siguiente lo publica todo. No es error.
- QA de WebKit: tocar una foto del lightbox solo cuando la miniatura de la
  tarjeta ya cargó (nadie toca una foto que no ve); si el QA de WebKit móvil
  falla por tiempo, revisar primero si es la red, no el código.
- **El API de POSGold no trae costos** (verificado contra `GetProduct_V6`:
  34 campos, ninguno de costo; el login web tiene captcha): no intentar
  automatizar el refresco de costos. El plan B si POSGold algún día expone
  el campo está documentado en `.github/workflows/sync-top300.yml`.
  (Diferente: los endpoints de PEDIDO sí existen en la cuenta, solo están
  sin activar — ver la sección del hub arriba y `docs/correo_goldpos.md`.)
- **El Excel de costos es un contrato de formato**: el export de PRODUCTOS
  de POSGold trae 86 columnas y encabezados en minúscula
  (`productoid`, `costo`, `costopromedio`, `costoultimo`); el pipeline lee
  por NOMBRE de columna (el orden y las columnas extra no importan) y falla
  seguro: el panel rechaza el archivo antes de cifrar (columnas faltantes o
  <1.000 filas = export filtrado) y el cron aborta antes de tocar
  `catalogo.js` si los encabezados cambian. Un producto nuevo sin costo en
  el Excel queda oculto ("sin costo en Excel"): no es un bug, es la regla.
- Un producto nuevo en POSGold entra solo en la próxima corrida si cumple
  los filtros (activo + existencias + foto + precio + costo + rentabilidad);
  fotos, slug, categoría y precios son automáticos. Temporada navideña y
  destacados del héroe se curan a mano (panel / workflow).
