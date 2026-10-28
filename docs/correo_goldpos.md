# Correo a GoldPOS/POSGold — ARCHIVADO como plan B

Estado: **ARCHIVADO (23-10-2026)** — decisión de alcance de Juguetería
Taiwan: la integración es solo sincronización de productos (fases 0+1 del
hub); los pedidos de Mercado Libre se gestionan en la plataforma de ML y NO
se empujan a POSGold. Por eso **no se requiere respuesta de GoldPOS** ni
enviar este correo para el alcance vigente. Solo se retoma si la empresa
decide volver a la fase 3 (OMS) o a los webhooks. El borrador queda
intacto abajo como plan B.

Redactado y verificado (21-10-2026, tras inspeccionar el panel
Desarrollador de la cuenta). Pendiente si algún día se retoma: completar
los corchetes `[•]` y enviarlo. Estructura en dos capas: explicación en
palabras simples para quien lo lea primero (soporte/ventas) y anexo
técnico para desarrollo.

Datos de contacto para la firma:
- Gerencia — Juguetería Taiwan
- NIT: [completar] · Cel/WhatsApp: [completar] · Correo: [completar]
- Sitio: saldosjugueteriataiwan.com

---

> **Para:** soporte de GoldPOS / POSGold
> **Asunto:** Integración API — activación de endpoints de pedidos, webhooks y costos (Juguetería Taiwan)

```
Buen día,

Les escribe el equipo de JUGUETERÍA TAIWAN (Cali), clientes de POSGold.
Escribimos a solicitud de la gerencia. El correo tiene dos partes: primero
les contamos en palabras simples qué queremos lograr, y al final dejamos un
ANEXO TÉCNICO con los detalles exactos para su área de desarrollo, para que
puedan reenviarlo internamente sin traducir nada.

------------------------------------------------------------
1) LO QUE QUEREMOS LOGRAR (en palabras simples)
------------------------------------------------------------

Hoy vendemos en dos lugares: la tienda física (con POSGold en caja) y en
línea (nuestra página web, que ya se llena sola desde POSGold). Estamos
agregando un tercero: Mercado Libre.

El objetivo es que cuando un cliente compre por la página web o por
Mercado Libre, ese pedido quede registrado automáticamente en POSGold,
como si se hubiera digitado en caja. Es decir:

  - El inventario baja solo en el sistema.
  - El pedido queda listo para facturar y despachar.
  - La tienda física y la online nunca venden la misma unidad dos veces
    (ese es hoy nuestro principal dolor: se agota algo en tienda y la
    página sigue mostrándolo disponible, o al contrario).
  - Se elimina la doble digitación que hoy hace el personal.

------------------------------------------------------------
2) LO QUE YA FUNCIONA (para que no lo revisen de nuevo)
------------------------------------------------------------

Ya consumimos su API con éxito para el catálogo de la página:

  - LoginJWTToken (generación del token) — funciona.
  - GetProduct_V6 (consulta de productos de la bodega 2) — funciona;
    con ella sincronizamos 1.900+ productos, 4 veces al día, sin errores.
  - JWTValidateToken — documentada.

Nuestros datos: subdominio jugueteriataiwan.goldpos.com.co,
Empresa ID 1, Usuario ID 1, Bodega 2.

------------------------------------------------------------
3) LO QUE NECESITAMOS (en orden de importancia)
------------------------------------------------------------

PEDIDO 1 — Activar los endpoints de PEDIDOS que ya están registrados en
nuestra cuenta. En el panel (Desarrollador → Api Logs) aparecen 8
endpoints registrados; 5 son de creación de pedidos y todos figuran como
"Inactivo". Los probamos y efectivamente no responden (detalle en el
anexo). Pedimos que los activen para nuestra empresa, o que nos indiquen
cómo hacerlo desde el botón "Actualizar permisos" del panel.

  Por qué importa: con esto el pedido de la web o de Mercado Libre entra
  directo a POSGold. Es la pieza central de todo el proyecto.

PEDIDO 2 — Documentación del formato de datos (payload) de esos
endpoints de pedidos: qué campos son obligatorios, cómo se envían los
items, el cliente, la ciudad de envío y la forma de pago, y qué responde
el sistema cuando el pedido se crea bien (número de pedido, etc.).
El detalle exacto de cuáles necesitamos está en el anexo.

PEDIDO 3 — Webhooks (avisos automáticos). En el panel existe la sección
Desarrollador → WebHooks, pero en nuestra cuenta está vacía. Queremos
saber qué eventos pueden avisarnos (por ejemplo: "se vendió un producto",
"cambió el inventario", "se emitió una factura") y cómo registramos
nuestra dirección para recibirlos.

  Por qué importa: con avisos automáticos, cuando algo se venda en la
  tienda física, la página web y Mercado Libre lo sabrían en segundos,
  sin estar preguntando cada rato.

PEDIDO 4 — Costos de los productos por API. Hoy los costos los sacamos
de un archivo de Excel que se exporta a mano de POSGold. Queremos
eliminar ese paso manual: si la API entrega el costo del producto
(costo, costopromedio o costoultimo), el proceso queda 100% automático.

PEDIDO 5 — Consulta de ventas/facturas por rango de fechas, para
conciliar lo vendido en línea contra el POS.

PEDIDO 6 — Reglas de uso y pruebas: cuántas consultas por minuto
permiten, cada cuánto vence el token, si hay lista de IPs que debamos
registrar, y si tienen un ambiente de pruebas (o una bodega de prueba)
para ensayar sin tocar datos reales.

Si alguno de estos puntos tiene costo o licencia adicional, agradecemos
que nos lo indiquen de una vez para presupuestar.

Si prefieren resolverlo por llamada, con gusto nos acomodamos: podemos
compartir pantalla y mostrar lo que ya tenemos funcionando.

Muchas gracias por el tiempo. El sistema nos funciona muy bien y esta
integración es el siguiente paso natural.

Cordialmente,

[Nombre]
Gerencia — Juguetería Taiwan
NIT [•] | Cel/WhatsApp [•] | [correo]
saldosjugueteriataiwan.com

============================================================
ANEXO TÉCNICO (para el área de desarrollo)
============================================================

Contexto de integración:
- Base URL: https://jugueteriataiwan.goldpos.com.co
- Autenticación actual: POST /apiGold/Login_Api/LoginJWTToken
  (user + Password en query string; JWT vigente ~14 días; usamos un
  token fresco por corrida). Confirmamos también JWTValidateToken (POST).
- Consumo actual: GET /apiGold/ProductoApi/GetProduct_V6 con Bearer JWT.
  Parámetros en uso: empresaid=1, usuarioid=1, bodegaid=2,
  items_x_pagina=50000, pagina, ult_mov=2000-01-01, api=1, api2=1.
  Respuesta: {Status, Msj, Datos[], Conteo}; campos en uso: Productoid,
  Producto, Codigo, Disponible, Precio1..Precio10, Imagenes, Categoria,
  Activo, ProductoImpuestoPorcentaje.

A. ENDPOINTS REGISTRADOS Y SU ESTADO (panel Desarrollador → Api Logs,
   8 registros, todos en estado "Inactivo" salvo el que ya usamos):

   | Módulo          | Acción / Ruta                          | Método |
   |-----------------|----------------------------------------|--------|
   | PedidoApi       | PedidoWebCreateAnonimo_v3              | POST   |
   | PedidoApi       | PedidoWebCreateAnonimo_v5              | POST   |
   | PedidoApi       | PedidoCreateExternal                   | POST   |
   | PedidoApi       | PedidoCreateExternal_Anonimo           | POST   |
   | PedidoApi       | PedidoCreate_PluginWordPress           | POST   |
   | ProductoApi     | GetProduct_V6                          | GET    |
   | CiudadApi       | CiudadesGet                            | GET    |
   | PedidoGestionApi| CrearPedido                            | POST   |

   Evidencia de que "Inactivo" es real: con JWT fresco y válido,
   GET /apiGold/CiudadApi/CiudadesGet (con y sin empresaid/usuarioid)
   responde 404, mientras GetProduct_V6 responde 200 en la misma sesión.
   Concluimos que los endpoints deben activarse por empresa (¿desde el
   botón "Actualizar permisos" del panel o desde su lado?).

B. LO QUE SOLICITAMOS POR CADA ENDPOINT DE PEDIDO:
   - Ruta completa y método confirmados.
   - Contrato: campos requeridos y opcionales (cliente/tercero, items:
     productoid, cantidad, precio; bodega; sucursal; ciudad de envío;
     forma de pago; observaciones).
   - Respuesta de éxito (¿retorna numeración del pedido?, ¿id?).
   - Manejo de errores (códigos y mensajes).
   - Diferencias entre las versiones (v3 vs v5; External vs Anonimo;
     PedidoGestionApi/CrearPedido) y cuál recomiendan para:
     (a) pedidos desde nuestra web, y (b) pedidos desde un marketplace
     externo (Mercado Libre).
   - ¿El pedido queda como documento en Cola/Pedidos para facturar, o
     facturado directo? ¿Se puede indicar la condición?

C. WEBHOOKS (Desarrollador → WebHooks, tabla Codigo/Descripcion/Tipo/
   Ruta/Activo — vacía en nuestra cuenta):
   - Catálogo de eventos disponibles (venta, factura, actualización de
     inventario/kardex, pedido creado, etc.).
   - Formato del aviso (JSON de ejemplo) y mecanismo de reintento.
   - Cómo se registra la URL destino (¿desde el panel o por ustedes?).

D. COSTOS POR API:
   - ¿Existe endpoint o parámetro (en GetProduct_V6 u otro módulo) que
     exponga costo, costopromedio o costoultimo por producto?
   - Hoy los tomamos del export PRODUCTOS*.xlsx (86 columnas,
     encabezados en minúscula). Si la API los expone, retiramos esa
     dependencia manual.

E. CONSULTA DE VENTAS/FACTURAS:
   - ¿Existe endpoint para consultar facturas/ventas por rango de fechas
     (con items y totales)? Nos sirve para conciliar el canal online.

F. OPERACIÓN:
   - Rate limit por empresa/endpoint y política ante 429.
   - Vigencia del JWT y buen práctica recomendada (¿uno por corrida o
     token de larga vida?).
   - ¿Lista blanca de IPs del runner? (nuestra automatización corre en
     GitHub Actions; podemos informar rangos si lo requieren).
   - Ambiente de pruebas o bodega de prueba recomendada.

Quedamos atentos a cualquier pregunta. Gracias de nuevo.

[Nombre]
Gerencia — Juguetería Taiwan
NIT [•] | Cel/WhatsApp [•] | [correo]
```

---

## Después de enviar

- Si activan los endpoints: probar `CiudadesGet` y un pedido de prueba
  contra una bodega de prueba antes de conectar Mercado Libre.
- Si entregan el catálogo de webhooks: la fase 2 del hub
  (`docs/plan_hub.md`) pasa de "backend con polling" a "backend que
  recibe eventos", mucho más simple.
- Si exponen costos: retirar la dependencia del Excel según lo
  documentado en `.github/workflows/sync-top300.yml`.
