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
python qa_ml.py             # adaptador ML offline: precios, atributos, payloads, estado (sin red ni costos)
python hub/ml_diagnostico.py --payloads   # arma payloads ML con datos publicos, sin publicar ni rotar token
python sync_posgold.py --dry-run   # sync de prueba sin tocar catalogo.js
python sync_posgold.py --verificar-todas   # auditar/regenerar miniaturas
python generar_enlace_config.py    # enlace de config (llave+tokens) para un navegador
python gen_dropi.py          # actualiza la carga masiva DROPI con Precio 5 de POSGold (--dry-run para probar)
python auditar_skus_dropi.py # login manual Dropi y vuelca TODOS los SKU (activos+archivados) a reportes/dropi_skus.json
python auditar_skus_dropi.py --solo-revisar   # audita partes/principal contra el snapshot, sin navegador
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
  canal ML: altas, pausas/activaciones, stock y precio (Precio 5 ya incluye ganancia + IVA).
  ML cobra 15% comisión + 5% pasarela = 20% total; Juguetería Taiwan asume envío gratis ($23.000).
  Fórmula: **ML = (Precio 5 + 23000) / 0.80**, redondeado a $100.
  Guardia de rentabilidad descuenta comisión + pasarela (20%); sin costo conocido no publica). El estado del canal
  (SKU→listing, sin costos) vive en `data/canal_ml.json` (generado, se
  commitea). El refresh token de ML **rota en cada uso**: se persiste cifrado
  en `hub/ml_token.enc` (AES-256+PBKDF2, mismo modelo que `costos.posgold.enc`;
  ese blob sí se commitea) y el workflow `sync-ml.yml` lo renueva y lo vuelve a
  subir. `sync-ml.yml` es dispatch-only hasta aprobar la app en
  developers.mercadolibre.com.co. Comandos: `--autorizar` (primera vez),
  `--prueba`, `--dry-run` (sin red ni token), `--sincronizar --publicar N`.
  **Campaña de altas completa (29-10-2026)**: 678 publicaciones en
  `data/canal_ml.json` (658 activas, 0 elegibles pendientes, `qa_ml.py`
  70/70), subidas en tandas de `--publicar 50`. El ciclo de cada tanda:
  alta OK → `paused` → el sync siguiente la activa (la moderación de ML
  despeja entre corridas). Al cierre quedaban 3 puntas: (a) 9 recién
  creadas esperando moderación (se activan solas), (b) 10 congeladas por
  ML con `item.status.not_modifiable` — solo manual en el panel: 01389,
  01476, 01731, 02110, 02280, 02418, 02419, 02896, 02973 y 03119 (cada
  corrida lo reintenta en vano hasta liberarlas allá), (c) 2 `ignorados`
  (001309 preexistente; 02039 Cubo Rubik Soma Madera porque su categoría
  UP exige GTIN y no trae EAN válido). Dos arreglos del adaptador nacidos
  de los rechazos: `_valor_defecto_req` llena `IS_FACTORY_KIT` = No
  (242084) y `POWER_SUPPLY_TYPE` desde el nombre del producto
  (pilas/luz/sonido/electrónico/gira → Batería 4491927; si no, Operación
  manual 49605648), y `completar_titulo_ml` completa los títulos de una
  sola palabra con el nombre de la categoría + la marca hasta 40
  (ML rechazaba "Tatuco", "Piratas", "Monopolio" y "Cazafantasmas" con
  `item.title.minimum_length`). GOTCHA: `sp.sin_acentos()` devuelve
  MAYÚSCULAS sin tildes — comparar nombres de valores ML normalizando
  ambos lados con ella (comparar en minúscula nunca matchea).
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
      (regla "sin precio"). **Cerrado al siguiente múltiplo de $1.000**
      (regla 28-10-2026: $10.500 → $11.000), siempre hacia ARRIBA:
      jamás por debajo del Precio 5 rentable.
    3. PRECIO SUGERIDO = **PRECIO + $10.000 como mínimo** (regla de la
       tienda 26-10-2026, tras el primer cargue que Dropi rechazó con
       celdas vacías): TODA fila lo lleva; el Precio 2 real solo se
       conserva si ya supera ese mínimo. Ninguna celda va en blanco.
       **También cerrado al siguiente $1.000** y nunca por debajo del
       precio redondeado + $10.000.
  4. **Rentabilidad al Precio 5**: utilidad = Precio 5 sin IVA −
     `costopromedio` del Excel `PRODUCTOS*.xlsx` (unido por
     `productoid`, campo ajustable con `COSTO_CAMPO` en `.env`, igual
     que el cron); en/bajo el costo no se enlista y sin costo en el
     Excel tampoco (fail-safe, como el catálogo).
  5. **Nivel de existencias: solo enlistan productos con 50+ unidades**
     (STOCK < 50 no se carga; ajustable con `--min-stock`).
   6. **Conciliación con lo ya publicado en Dropi**: los SKUs (incluye
      `VARIATION SKU`) y nombres normalizados de **TODOS** los exports
      `DROPI/products_*.xlsx` y `DROPI/variaciones*.xlsx` no se
      enlistan — no duplica lo que ya vive en la tienda Dropi; el
      nombre cubre exportados sin SKU. Se absorben TODOS los archivos
      de cada patrón (28-10: el listado publicado se partió en 2
      páginas y llegó en 2 exports; leer solo el más reciente dejaría
      ciega la conciliación y duplicaría lo publicado). Para los
      listados ARCHIVADOS (el export no los trae y el panel no muestra
      su SKU), `auditar_skus_dropi.py` abre Chromium con login manual
      y pagina el endpoint interno del panel para volcar TODOS los
      SKU de la cuenta a `reportes/dropi_skus.json` (gitignored);
      `gen_dropi.py` absorbe ese snapshot y bloquea esos SKU. Los
      nombres del snapshot solo bloquean si el listado está ACTIVO:
      un archivado puede volver a enlistarse con otro SKU.
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
   10. **NOMBRE máximo 39** (Dropi rechaza los de 40+ con "supera el
      máximo permitido y será truncado", cargue de etapas 26-10
      13:34): recorte por palabra completa medido en bytes UTF-8
      (cubre la Ñ/acentos), aplicado en `recortar_nombre()` y exigido
      en la conciliación pre y post escritura. El recorte puede dejar
      2 SKUs con el mismo nombre corto (39 pares en el principal):
       Dropi lo permite (el nombre no es único allá).
   11. **Piso de precio (27-10-2026)**: el Precio 5 mínimo para
      enlistar es **$2.000** — por debajo, empacar y despachar una
      unidad cuesta más de lo que deja y nadie lo revende al detal.
      Excepciones por categoría (`PRECIO_MINIMO_CATEGORIA`, que PISA
      el piso general, arriba o abajo): **PIÑATERIA y CACHARRO piden
      $5.000** (suministros de fiesta y cacharería barata no son
      producto de dropshipping) y **SLIME baja a $500** (el gancho
      viral de TikTok se conserva aunque sea barato). El piso
       general se ajusta con `--min-precio`; exigido en la
       conciliación pre y post escritura. Con la regla 13 las
       excepciones de PIÑATERIA/CACHARRO/SLIME ya no aplican en la
       práctica (esas categorías salen por alcance); el piso
       general de $2.000 sí sigue rigiendo las 3 categorías
       cargadas.
   12. **Partes en serie de hasta 50 filas (28-10-2026)**: el cargue
       masivo se sube en tandas — `gen_dropi.py` escribe, además del
       principal, la serie `DROPI/carga-masiva-parte-01-de-NN.xlsx`,
       `-02-`, ... que cubre el **100 %** de la carga (partición
       disjunta del principal, en su mismo orden). Se suben **EN
       ORDEN** y no hace falta re-exportar entre partes (son
       disjuntas por construcción). Al terminar la serie —o si UNA
       falla—: re-exportar `products_*.xlsx` (TODAS las páginas,
       paginación al máximo) y correr `gen_dropi.py` de nuevo; las
       partes de generaciones anteriores se borran solas en cada
        regeneración. Ajustable con `--filas-parte` (0 = solo el
        principal). Nació tras el cargue exitoso de la prueba de 50
        filas del 28-10, después de 4 intentos fallidos con archivos
        grandes.
   13. **Alcance por categoría (28-10-2026, decisión de la dueña)**:
       la carga solo lleva **JUGUETERIA, JUEGO DE MESA y DIDACTICO**
       (`CATEGORIAS_CARGA`); las demás categorías quedan fuera del
       alcance del marketplace (salen con el motivo "categoría fuera
       del alcance"). Lo ya publicado en Dropi de otras categorías
       no se toca: pausarlo es manual en el panel. El mini de
       bloqueados también respeta el alcance.

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

  **Estado del canal (26-10-2026) — primer cargue rechazado y
  corregido; subida pendiente**: `DROPI/carga-masiva-productos-con-
  variables.xlsx` quedó completa y verificada (1.144 filas, 0
  entran/0 salen, todas las URLs de imagen vivas): todas con Precio 5
  rentable, 50+ unidades, copy de campaña y pie. Los copys cerraron
  al 100 % (1.144 SKUs en `data/descripciones_dropi.json`, lote final
  del 25-10) y el 26-10 se volcaron al Excel con `gen_dropi.py`.
  **El primer intento de cargue (26-10 11:06) fue rechazado por Dropi
  con "Encabezado no reconocido en la estructura del archivo"**
  (reporte `DROPI/error-importando-productos*.xlsx`): el Excel escrito
  por openpyxl 3.1.5 local viaja con **inline strings** (sin
  `xl/sharedStrings.xml`) y el parser de Dropi no los lee. Corregido:
  `gen_dropi.py` escribe ahora con **xlsxwriter** (shared strings,
  `strings_to_urls` desactivado para que IMAGENES no se vuelvan
  hipervínculos) y ABORTA si el archivo escrito no trae
   `sharedStrings.xml`. Segunda corrección del mismo día, regla de la
   duena: **PRECIO SUGERIDO = PRECIO + $10.000 mínimo en TODAS las
   filas** (99 iban vacías y 1.045 quedaban por debajo del mínimo;
   solo 82 conservan el Precio 2 real porque ya lo superan).
   **Cargue de etapas 26-10 13:34 — 95 filas marcadas por NOMBRE de
   40+ caracteres** ("supera el máximo permitido y será truncado";
   reporte `error-importando-productos26-10-2026 13_34.xlsx`, 37
   juguetería + 33 juego de mesa + 25 didáctico; el reintento de
   13:36 marcó solo 7). Corregido: regla 10 (nombre ≤ 39 bytes) y
   archivos regenerados.    **El export `products_20261026_154402.xlsx`
   reveló que NADA entró de esos intentos** (solo viven los 10
   productos históricos): el cargue de Dropi es todo-o-nada — si
   UNA fila falla, no se importa nada — así que no hay duplicados
   y el archivo corregido se puede subir limpio. Ese export ya está
   absorbido por la conciliación (siguen siendo 1.144 filas, 409
   en el derivado de etapas). **Tercer intento (26-10 16:15, archivo
   de etapas corregido): 408/409 validadas — único rechazo el SKU
   04521** por "Ya existe un producto con este SKU" (listado viejo
   ID 2031517 "DIDACTICO ENCAJABLE DE FIGURAS x 4", creado a mano y
   NO visible en los exports `products_*.xlsx` — lección: el export
   puede no traer todo lo que vive en Dropi; los conflictos por SKU
   solo los revela el intento de cargue). Decisión de la dueña:
   Dropi no deja borrar — se ARCHIVÓ el listado viejo; si archivar
   no libera el SKU, hay respaldo listo con el 04521 excluido
   (`...-didactico-sin-04521.xlsx`, 408 filas). El export
   `products_20261026_170509.xlsx` confirmó que NADA entró del
   intento 16:15 (todo-o-nada) y ya está absorbido por la
   conciliación: la carga queda en 1.144 filas (409 en la etapa).
   **Cuarto intento (26-10 17:22, el de 408 filas): mismo patrón,
   nuevo SKU — `04539`** chocó con OTRO listado viejo invisible
   (ID 2030269 "🧩 ROMPECABEZAS ENCAJABLE CUERPO HUMANO", tampoco
   en los exports). Lección: los conflictos por SKU se revelan
   UNO a la vez (la validación de nombre reporta en bloque —95 de
   una vez—, pero el "ya existe" corta la importación en la
   primera fila que choca); hay listados viejos que viven en el
    panel pero no en los exports. Flujo: archivar el listado viejo
    en el panel y reintentar; conviene revisar en el panel la
    sección de archivados para identificarlos todos de una vez.
   **El listado pegado del panel (`DROPI/Productos_Archivados.xlsx`,
   26-10 17:47 — tabla copiada de la página: 73 productos con ID y
   nombre pero SIN SKU) reveló la causa de fondo: el export
   `products_*.xlsx` trae SOLO la primera página del listado (los
   10 primeros alfabéticos)** — la conciliación estaba ciega a los
   otros 63+ productos viejos creados a mano (juego de mesa,
   pixel/Minecraft, RC, 2025-2026), varios de los cuales cruzan por
   nombre con nuestra carga (PERRO SUPER HEROE, JUGUETE CARRO, UNO
   NO MERCY, ¿POR QUÉ SIEMPRE YO?) y chocarán por SKU. Y aparte hay
   productos invisibles HASTA en el panel (2031517 y 2030269: solo
    aparecen en los reportes de error del cargue). **Flujo corregido
    — aplicado 26-10 20:30**: el export `products_20261026_202956.xlsx`
    (paginación al máximo: 99 filas, 73 productos, 93 SKUs+VSKUs, 72
    nombres) quedó absorbido por la conciliación: 31 filas excluidas
    por ya-en-Dropi (26 choques nuevos contra los viejos: PERRO SUPER
    HEROE 6603888891759, burbujeros 05675-78, Funko 03147/03148/
    802023021011, LEGO magnético 6723456789179/86/93, 266038889007
    90/820, FOX-23xx, 04533, 01011, 01012, 01850, 03100,
    6925070401851, 6603888921388...) y los 2 bloqueadores (04521,
    04539) por la lista `SKU_BLOQUEADOS_DROPI` del script. La carga
    queda en **1.116 filas (400 en la etapa, 15 privados)** y
    `DROPI/carga-masiva-mini-04521-04539.xlsx` prueba los
    bloqueadores aparte: si el mini entra, quitar el SKU de la lista
    y correr de nuevo. Los ya publicados conservan su listado viejo
    (actualizarlos es otro paso, manual o por "Actualizar").
     `gen_dropi.py` AVISA si el export products_*.xlsx trae ≤15
     productos: el export saca la página visible — siempre exportar
     con la paginación al máximo o la conciliación vuelve a quedar
     ciega y duplicaría lo publicado.
  **Piso de precio aplicado (27-10-2026)**: regla 11 — la carga
  quedó en **1.003 filas (385 en la etapa, 15 privados en la
  etapa, 126 privados en el principal)**: salieron 112 por el piso
  (55 PIÑATERIA y 30 CACHARRO bajo $5.000, 27 de otras categorías
  bajo $2.000; los 75 SLIME se conservan todos, incluso los de
  $800) y 1 más por existencias caídas bajo 50 desde el 26-10. El
  export `products_20261027_225110.xlsx` (94 SKUs) quedó absorbido:
  apareció UN listado nuevo en Dropi desde el 26-10 (7702202506184
   YOYO CHINO) y ya no se duplica. Nada de la carga se ha subido
   todavía, así que el filtro no dejó nada huérfano en Dropi.
  **Cargue de prueba exitoso + serie de partes (28-10-2026)**: la
  prueba de 50 filas (`carga-masiva-prueba-50.xlsx`, 17 didáctico +
  17 juego de mesa + 16 juguetería, todas PÚBLICAS) **entró limpia
  por el panel** — primer cargue exitoso tras 4 intentos fallidos.
  Los exports de esa noche confirmaron los 50 publicados y revelaron
  que **el listado se partió en 2 páginas** (`products_20261028_
  002608.xlsx`: 108 filas/página 1; `products_20261028_002908.xlsx`:
  42 filas/página 2 — la conciliación ahora absorbe TODOS los
  exports: 144 SKUs y 120 nombres; los 50 de la prueba quedaron
  excluidos, "ya en Dropi" pasó de 32 a 83). Regla 12 aplicada: el
  principal quedó en **952 filas (125 privados; uno más cayó por
  existencias)** y la serie nueva cubre el 100 %: **20 partes de
  hasta 50 filas** (`carga-masiva-parte-01-de-20.xlsx` ... `-20-`,
  19×50+2). **La etapa de 3 categorías y la prueba-50 quedaron
  retiradas** (todo su contenido ya vive en las partes o en Dropi;
   subirlas de nuevo fallaría por SKUs ya publicados): el flujo de
   cargue son LAS PARTES, en orden.
  **Partes 01-02 publicadas; parte-03 rechazada por un bloqueador
  nuevo (28-10 01:51)**: las partes 01 y 02 entraron limpias (50+50,
  confirmadas por los exports: 244 SKUs publicados) y la parte-03
  fue rechazada en el todo-o-nada por UN solo SKU — **03371**
  ("BOLSO PELUCHE CAPIBARA MANZANA 3") chocó con el listado viejo
  invisible **ID 1840409 "BOLSO CAPIBARA ECONOMICO"** (reporte
  `error-importando-productos28-10-2026 01_51.xlsx`, fila 28).
  Tercera vez del mismo patrón: los choques por SKU se revelan UNO
  a la vez y hay listados viejos que no viven ni en los exports.
  Corregido: 03371 entró a `SKU_BLOQUEADOS_DROPI`; los 3 exports
  de esa noche (el listado ya se parte en 3 páginas: 100+108+42
  filas) quedaron absorbidos y la serie se regeneró — el principal
  quedó en **850 filas** y la serie nueva es **17 partes**
  (`carga-masiva-parte-01-de-17.xlsx` ... `-17-`; la numeración
  anterior murió con la regeneración, sus archivos se borraron
  solos). El mini de bloqueados pasó a llamarse
  **`carga-masiva-mini-bloqueados.xlsx`** y ahora lo genera
  `gen_dropi.py` en cada corrida con datos frescos (desde la regla
  13 lleva los 2 bloqueados del alcance: 04521 y 04539; 03371
  quedó fuera por categoría). Conviene archivar el listado viejo
   ID 1840409 en el panel (como se hizo con 2031517 y
   2030269) y probar el mini: si entra, quitar esos SKUs de la
   lista y correr de nuevo.
  **Alcance por categoría + precios cerrados aplicados (28-10-2026,
  reglas de la dueña)**: la carga solo lleva **JUGUETERIA, JUEGO DE
  MESA y DIDACTICO** (regla 13) y **todos los precios van cerrados
  al siguiente $1.000, siempre hacia arriba** ($10.500 → $11.000;
  jamás por debajo del Precio 5 rentable; el sugerido también).
  Aplicado: salieron **616 productos por categoría fuera del
  alcance** y el principal quedó en **333 filas (13 privados;
  153 juguetería + 115 juego de mesa + 65 didáctico)** con la
  serie nueva de **7 partes** (`carga-masiva-parte-01-de-07.xlsx`
  ... `-07-`; la numeración anterior murió con la regeneración).
  El mini de bloqueados quedó solo con **04521 y 04539** (los dos
  didácticos; 03371 es BOLSO Y PELUCHES y salió del alcance). OJO:
  de lo ya publicado en Dropi, **125 productos del catálogo
  quedaron fuera del alcance nuevo** (subieron con la prueba-50 y
  las partes 01-02 de la serie vieja, que cubrían todas las
  categorías): viven allá y Dropi no borra — pausarlos es manual
  en el panel; otros 67 publicados sí son del alcance.
  **Partes 01-03 publicadas; parte-04 rechazada por otro bloqueador
  (28-10 09:19)**: las partes 01, 02 y 03 entraron limpias
  (50+50+50; los 4 exports de la mañana confirman **394 SKUs
  publicados**, en 100+100+108+92 filas) y la parte-04 cayó en el
  todo-o-nada por **02587** ("SPOT IT DOBBLE VARIOS MOTIVOS"), que
  choca con el listado viejo **ID 1832792 "Blue Orange Spot It
  Dobble 123"** (reporte `error-importando-productos28-10-2026
  09_19.xlsx`, fila 24). Ese listado YA estaba archivado en el panel
  y aun así bloquea: archivar no libera el SKU (misma lección de
  1840409/03371). Corregido: 02587 entró a `SKU_BLOQUEADOS_DROPI`
  (cuarto bloqueador) y la serie se regeneró: el principal quedó en
  **182 filas** y la serie nueva es de **4 partes**
  (`carga-masiva-parte-01-de-04.xlsx` ... `-04-`, 50+50+50+32; la
  numeración de 7 murió con la regeneración). El mini quedó con
  **3 SKUs del alcance (02587, 04521 y 04539)**; 03371 sigue fuera
  por categoría. De los 394 publicados, **342 siguen en el catálogo:
  217 del alcance y 125 de otras categorías que hay que pausar a
  mano**; los otros 52 no cruzan el catálogo actual.
  **Decisión confirmada: las filas curadas suben PRIVADO=SI** (ocultas
  del marketplace, activables después
  desde el panel de Dropi): histórico 83 de COLECCIÓN/licencias
  (Funko, anime: espeja la categoría `colecciones` que la vitrina
  mantiene oculta), 34 de deduplicación por nombre (de cada grupo
  repetido queda UNO público) y 23 de curación discrecional de la
  dueña. Los conteos del principal bajaron con cada filtro: 126
  privados tras el piso de precio (27-10), 125 tras la prueba del
  28-10 y **13 con el alcance de 3 categorías (28-10)** — la
  mayoría de esas marcas eran de categorías que hoy quedan fuera
  (COLECCIÓN, BOLSO Y PELUCHES, etc.). `gen_dropi.py`
  PRESERVA esa marca por SKU y jamás la pone: los SKUs nuevos entran
  siempre públicos; lo ya publicado en Dropi (10 productos de agosto,
  PRIVADO=NO) no cambia con la carga — ocultarlo allá es manual.
  Convenciones de la carpeta: respaldo `.respaldo-YYYY-MM-DD.xlsx`
  antes de escribir a mano (hay 23-10 y 26-10) y `QA_*.xlsx` =
  muestras de revisión de la dueña (las del 24-10: 24 privados; el
  "previo-privados" es copia idéntica de respaldo). El reporte de
   errores de Dropi de un intento fallido queda como
   `DROPI/error-importando-productosFECHA.xlsx`. Las cargas por
   etapas por categoría (`carga-masiva-productos-con-variables-
   <categorias>.xlsx`) y la prueba-50 quedaron RETIRADAS el 28-10,
   reemplazadas por la serie de partes: todo su contenido ya vive en
   las partes o publicado en Dropi. El principal sigue siendo el
   espejo completo; los demás archivos de carga son derivados que se
   regeneran filtrando, nunca editándose a mano.

  **Modelo operativo — dos opciones en el panel de Dropi**: "Carga
  masiva" (crear) y "Actualizar" (lo publicado). Altas = Excel
  INCREMENTAL tras re-exportar (solo lo no publicado: lo que
   `gen_dropi.py` ya genera); actualizaciones = archivo con los ya
   publicados y su Precio 5/stock frescos por "Actualizar"; bajas =
   pausar a mano (Dropi no borra). Nota de curación: al volverse el
   Excel incremental, la marca PRIVADO de los ya publicados deja de
   vivir en el archivo de trabajo (queda en Dropi y en los respaldos);
   si un SKU publicado se borra allá y re-entra al catálogo, re-
   entraría PÚBLICO — marcarlo de nuevo o persistir la curación
     (abajo). Pendiente, en orden: (1) correr
   `python auditar_skus_dropi.py` (login manual; vuelca activos +
   archivados a `reportes/dropi_skus.json`) y luego `python
   gen_dropi.py` para regenerar las partes excluyendo TODOS los SKU
   de la cuenta (las partes actuales se generaron antes del snapshot
   y pueden chocar con un archivado invisible), (2) **subir las
   partes EN ORDEN** (`carga-masiva-parte-01-de-04.xlsx`, luego la
   02, 03 y 04) por el panel con "Carga masiva" — son disjuntas por
   construcción, no hace falta re-exportar entre partes (sin API
   pública de carga; la sesión de observación de app.dropi.co quedó
   en `reportes/observacion_dropi/`), (3) **al terminar la serie**
   (o si UNA parte falla): re-exportar `products_*.xlsx` a `DROPI/`
   — **TODAS las páginas, con la paginación al máximo** — y correr
   `gen_dropi.py` para que la conciliación absorba lo publicado y
     regenere la serie de lo que falte, (4) probar el mini-archivo
     `carga-masiva-mini-bloqueados.xlsx` (los 3 SKUs bloqueadores
     del alcance: 02587, 04521 y 04539; 03371 salió con la regla 13):
     si entra, quitarlos de
    `SKU_BLOQUEADOS_DROPI` y correr de
    nuevo, (5) probar "Actualizar" con 2-3 SKUs antes de conectarla
   al pipeline: si sobrescribe TODAS las columnas puede pisar
   PRIVADO (voltearía los privados a públicos) y si hace upsert
   quizá una sola opción baste, (6) con esa prueba, agregar a
   `gen_dropi.py` un modo `--actualizar` (publicados con Precio
   5/stock frescos, preservando PRIVADO; idealmente migrando la
   curación a un `data/curacion_dropi.json` por SKU que sobreviva a
   las regeneraciones, como las descripciones), (7) pausar a mano en
   Dropi lo que salga del catálogo, pierda el Precio 5 o la
   rentabilidad, o baje de 50 unidades.

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
