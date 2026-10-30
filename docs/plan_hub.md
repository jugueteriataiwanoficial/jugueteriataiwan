# Plan del hub — nuestro propio "VTEX" para Juguetería Taiwan

Decisión registrada: en lugar de comprar VTEX (o una suscripción tipo
Multivende), construimos la capa mínima equivalente sobre lo que ya existe en
este repo. Estado y detalle operativo del código: `hub/README.md`.

## Decisión de alcance — Juguetería Taiwan (23-10-2026)

La empresa definió el alcance de la integración POSGold ↔ Mercado Libre:

- **Dentro de alcance:** solo la **sincronización de productos** — el mismo
  espejo que ya hace el catálogo de la vitrina (inventario, precios, fotos,
  stock) llevado al canal ML: altas, pausas/activaciones y precios con la
  guardia de rentabilidad. Fases 0 y 1.
- **Fuera de alcance:** la **gestión de pedidos desde POSGold**. Los pedidos
  de Mercado Libre se gestionan directamente en la plataforma de ML (donde
  hoy se trabaja), sin empujarlos al POS. Las fases 2, 3 y 4 quedan en pausa
  hasta una nueva decisión de la empresa.

Consecuencia: **no se requiere respuesta de GoldPOS** para el alcance
vigente. Todo lo que la sincronización necesita ya funciona
(`LoginJWTToken` + `GetProduct_V6`) o ya está resuelto (costos por Excel
cifrado, contrato validado). El correo de [`correo_goldpos.md`](correo_goldpos.md)
queda **archivado como plan B**: solo vuelve a ser relevante si la empresa
decide retomar la fase 3 (OMS) o los webhooks.

## Por qué

Un VTEX es esencialmente: catálogo maestro + inventario + motor de precios +
OMS + conectores de canal. Este repo ya tenía la mayoría de esas piezas
resolviendo un solo canal (la vitrina); el hub las generaliza para que
cualquier canal nuevo sea "un adaptador más".

| Componente de un VTEX | Qué ya teníamos | Qué aporta el hub |
|---|---|---|
| Storefront | `index.html` (vitrina + carrito + checkout WhatsApp) | nada (no se toca) |
| Catálogo/PIM | `catalogo.js` + `mapa_imagenes.json` | catálogo maestro único con costos |
| Inventario | POSGold, pull 4×/día, solo lo vendible | inventario completo (activos y agotados) |
| Motor de precios | `precioDetal()` en el frente | precios por canal (comisiones, guardias) |
| OMS (órdenes) | — (los pedidos morían en WhatsApp) | fase 3 |
| Conector de marketplace | — | adaptador ML (fase 1) |

## Arquitectura

```
POSGold (ERP: fuente única de verdad)
   │
   ├──► sync_posgold.py (4×/día) ──► catalogo.js ──► vitrina (canal_web)
   │
   └──► construir_hub.py ──► hub/productos.json (catálogo maestro, con costos)
                                   │
                                   └──► canal_ml.py ──► Mercado Libre (canal_ml)
                                             │
                                             └──► data/canal_ml.json (estado)
```

Reglas del diseño:

1. **POSGold es la única fuente de verdad.** Nadie inventa productos ni
   precios: el hub normaliza lo que reporta POSGold.
2. **Cada canal lee del hub, nunca de POSGold.** Agregar un marketplace nuevo
   (Falabella, Rappi, etc.) = escribir `canal_X.py` con las mismas reglas.
3. **Los costos nunca salen del entorno privado.** El catálogo maestro trae
   costos y vive gitignored; los canales solo publican precios derivados.
4. **El repo público sigue siendo seguro.** `data/canal_ml.json` (estado de
   canal) y `hub/ml_token.enc` (token cifrado) viajan al repo sin secretos.

## Fases

| Fase | Qué es | Estado |
|---|---|---|
| 0 | Catálogo maestro `hub/productos.json` (todo el inventario + costos) | ✅ Construida y probada (6.249 productos, 5.937 con costo) |
| 1 | Adaptador ML batch: altas, pausas, stock, precios en GitHub Actions | ✅ Construida y en producción: campaña de altas completa (29-10-2026) — 678 publicaciones (658 activas), 0 elegibles pendientes; solo quedan 10 activaciones congeladas por moderación de ML (panel manual) y las nuevas esperan moderación en la corrida siguiente |
| 2 | Backend mínimo (webhooks ML, renovación de token en caliente, stock en vivo) | ⏸️ **Fuera de alcance** (decisión 23-10-2026): los pedidos se gestionan en la plataforma de ML; polling batch 4×/día basta. El hallazgo de Webhooks de POSGold queda como reserva para una fase 2 futura |
| 3 | OMS: orden ML → reserva de inventario → factura/cotización en POSGold → guía Mercado Envíos | ⏸️ **Fuera de alcance** (decisión 23-10-2026): los pedidos de ML NO se empujan a POSGold. Si algún día se retoma, POSGold ya tiene los 5 endpoints de pedidos registrados (Inactivos); el correo de solicitud queda archivado en `correo_goldpos.md` |
| 4 | Panel unificado: stock/órdenes de todos los canales desde el panel admin existente | ⏸️ **Fuera de alcance** por ahora (decisión 23-10-2026) |

## Hallazgo clave dentro de la cuenta POSGold (21-10-2026)

Inspección del panel **Desarrollador** de la cuenta (Api Logs, WebHooks,
Token, Peticion Log) con sesión iniciada:

- **8 endpoints registrados** para la empresa; 5 de creación de pedidos
  (`PedidoWebCreateAnonimo_v3/_v5`, `PedidoCreateExternal`,
  `PedidoCreateExternal_Anonimo`, `PedidoCreate_PluginWordPress`),
  `PedidoGestionApi/CrearPedido`, `CiudadApi/CiudadesGet` y el
  `GetProduct_V6` en uso. Todos marcados **"Inactivo"**.
- Verificación: `CiudadesGet` responde **404** con JWT fresco válido,
  mientras `GetProduct_V6` responde 200 en la misma sesión → el flag es
  real. Existe un botón **"Actualizar permisos"** en esa página.
- **WebHooks**: la sección existe (tabla Codigo/Descripcion/Tipo/Ruta/
  Activo) pero está **vacía** — sin catálogo de eventos publicado.
- **Token maestro** de la empresa en Empresa → Token (172 caracteres):
  guardado en `.env` como `POSGOLD_API_TOKEN` (local, gitignored, jamás
  al repo). El cron sigue usando `POSGOLD_TOKEN_USER/PASS` → JWT.
- **Peticion Log**: auditor de peticiones API por fecha/URL (útil para
  debug de la integración; hoy en 0 registros).
- Botón "Postman" del panel → solo enlaza al workspace público
  ([posgold-desarrollo/posgold-api](https://www.postman.com/posgold-desarrollo/posgold-api)),
  que documenta únicamente `LoginJWTToken`, `GetProduct_V6` y
  `JWTValidateToken` (rastros en `.firecrawl/`; `info taiwan.txt`).

Consecuencia: la fase 3 ya no depende de que GoldPOS **construya** nada —
solo de que **active y documente** lo que ya está registrado. El correo
definitivo (dos capas: común + anexo técnico) está listo en
[`docs/correo_goldpos.md`](correo_goldpos.md). Con la decisión de alcance
del 23-10-2026 (fases 2–4 fuera de alcance), este hallazgo queda como
**reserva estratégica**: no se espera respuesta de GoldPOS y el correo no
se envía salvo que la empresa retome la OMS.

## Decisiones abiertas

1. **API completa de POSGold.** Ver "Hallazgo clave" arriba. Resumen de lo
   pendiente a GoldPOS (correo archivado en `docs/correo_goldpos.md`):
   activación y documentación de los endpoints de pedido ya registrados,
   catálogo de webhooks, costos vía API y consulta de ventas/facturas.
   **Para el alcance decidido (23-10-2026: solo sincronización de
   productos) nada de esto se necesita**: la sincronización ya funciona y
   los costos van por el Excel cifrado. El correo queda como plan B para
   una eventual fase 3. Los sondeos de endpoints no registrados dan 404
   (ver `Integracion.txt`): adivinar no funciona.
2. **Multivende vs. in-house.** Contacto anotado en `Mercado_Libre.txt`
   (Valentina Atehortúa). Con un solo canal no justifica la suscripción; se
   reevalúa si se agregan 3+ marketplaces.
3. **Hosting del backend (fase 2).** Cloud Run / Fly.io / Cloudflare Worker
   con capa gratuita basta para el volumen de la tienda. El requisito duro es
   tener URL pública para los webhooks de ML. (Fase 2 fuera de alcance por
   la decisión del 23-10-2026; se reevalúa solo si se retoma.)

## Riesgos conocidos

- **Rate limit de ML (HTTP 429):** el adaptador reintenta con backoff; el
  catálogo (~300 publicables) cabe holgado en corridas batch.
- **Moderación de ML:** fotos y atributos obligatorios por categoría
  (BRAND, MODEL) pueden rechazar altas item por item; la corrida continúa
  y deja el error en `est["validaciones"]`. Resuelto en el código tras la
  campaña (29-10-2026): atributos obligatorios con valor por defecto
  honrado (`POWER_SUPPLY_TYPE`, `IS_FACTORY_KIT` — ver
  `_valor_defecto_req`), títulos de una palabra completados
  (`completar_titulo_ml`) y las categorías que exigen GTIN sin EAN en el
  hub van a `ignorados`. Lo que no se puede resolver por API son las
  publicaciones que ML congela por cuenta propia
  (`item.status.not_modifiable`, 10 al cierre de la campaña): hay que
  liberarlas una a una desde el panel de ML.
- **Sobreventa de inventario:** ML y la tienda física comparten el mismo stock
  real. Con la decisión de alcance (23-10-2026) no habrá fase 3 de descuento
  en POSGold a corto plazo, así que el techo `ML_STOCK_MAXIMO` y el stock
  mínimo son la mitigación permanente (la misma ventana de desfase que ya
  convive con la vitrina 4×/día).
- **Token de ML:** rota en cada uso; el blob cifrado commiteado es la fuente
  fresca (`hub/README.md` → Tokens).
- **Repo público:** nada de tokens/costos en claro; los secretos van en
  secrets de Actions y `.env` (gitignored).

## Enlaces

- [Guía de productos de ML](https://developers.mercadolibre.com.co/es_ar/guia-para-producto) (nota original en `Mercado_Libre.txt`)
- [API docs de ML](https://developers.mercadolibre.com.co/es_ar/api-docs-es)
- [API reference de VTEX](https://developers.vtex.com/docs/api-reference) (referencia del "producto completo"; solo como inspiración de dominios: catálogo/OMS/pricing)
- [Postman POSGold](https://www.postman.com/posgold-desarrollo/posgold-api)
