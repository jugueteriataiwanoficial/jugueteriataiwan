# Integración Mercado Libre – Juguetería Taiwan

Guía y registro de configuración de la app en el DevCenter. Última actualización: 28/10/2026.

> **Seguridad:** este documento NO contiene la Clave secreta ni tokens, a propósito. Un archivo de documentación se copia, se sube a repos o se comparte con facilidad. La clave va **solo** en el `.env` local y en los Secrets de GitHub.

---

## 1. Datos de la app

| Campo | Valor |
|---|---|
| App ID / Client ID | `206449781003470` (no es secreto) |
| Client Secret | Ver en DevCenter → tu app → ícono del ojo. Se pega solo en `.env` |
| Refresh Token | Aún no existe. Se genera con `python hub/canal_ml.py --autorizar` |
| Panel de la app | https://developers.mercadolibre.com.co/devcenter/edit-app/206449781003470 |
| País / sitio | Colombia (MCO) |
| Estado | Aplicación no certificada (suficiente para uso propio) |

**Sobre el "token":** hoy todavía no hay ninguno. Lo que tienes es el Client ID y el Client Secret. El Refresh Token aparece después de autorizar (Paso 4) y el script lo guarda solo en `.env` y en `hub/ml_token.enc`.

---

## 2. Detalle de lo diligenciado

### Información básica (visto en pantalla)

| Campo | Valor |
|---|---|
| Nombre | JUGUETERIA TAIWAN |
| Nombre corto | JUGUETERIATAIWAN |
| Descripción | Herramienta interna para publicar y sincronizar el inventario de nuestra tienda *(no se alcanzó a ver en la captura; verifica que no esté vacía)* |
| Propósito de la solución | Negocios |
| Número de usuarios | 1 a 10 usuarios |
| Logo | Cargado (logo Taiwan) |

### Configuración y scopes (indicado en los pasos; confirma que quedó así)

| Campo | Valor |
|---|---|
| Redirect URI | `https://oauth.pstmn.io/v1/callback` (la del DevCenter fue rechazada como "dirección no válida") |
| Flujo Authorization Code | Marcado |
| Flujo Refresh Token | Marcado |
| Flujo Client Credentials | Desmarcado |
| PKCE | Sin marcar |
| Negocios | Mercado Libre |
| Permiso: Usuarios | Lectura y escritura |
| Permiso: Publicación y sincronización | Lectura y escritura |

---

## 3. Lo que NO se diligenció (a propósito)

| Campo | Estado | Por qué |
|---|---|---|
| Negocios → VIS | Sin marcar | Es de inmuebles/vehículos, no aplica |
| Comunicaciones pre y post venta | Sin acceso | El sincronizador no lee mensajes |
| Publicidad de un producto | Sin acceso | No se gestionan campañas |
| Facturación de una venta | Sin acceso | No se factura desde el script |
| Métricas del negocio | Sin acceso | No se necesitan por ahora |
| Promociones, cupones y descuentos | Sin acceso | No se usan por ahora |
| Venta y envíos de un producto | Sin acceso | Activar solo lectura si luego quieres leer pedidos |
| Tópicos (Orders, Messages, Prices, Items, Catalog, Shipments, etc.) | Sin tocar | Son para webhooks; el sync no los usa |
| Notificaciones callbacks URL | Vacío | No hay servidor que reciba notificaciones |
| Configuración de seguridad (70%) | Pendiente | Recomendada, pero no bloquea el uso |
| Certificación de la app | No aplica | Solo para apps de terceros |

---

## 4. Credenciales en `.env`

Archivo: `C:\Users\User\JUGUETERIA_TAIWAN\.env`

```
ML_CLIENT_ID=206449781003470
ML_CLIENT_SECRET=<ver en DevCenter o en .env; nunca en documentación>
```

`ML_TOKEN_KEY` ya existe. Si el script lee la redirect desde variable de entorno, agrega también:

```
ML_REDIRECT_URI=https://oauth.pstmn.io/v1/callback
```

---

## 5. Alinear la redirect URI del script

El script trae por defecto la URL del DevCenter y debe usar la registrada. En PowerShell:

```powershell
Select-String -Path hub\canal_ml.py -Pattern "redirect|devcenter"
```

- Si lee una variable (`ML_REDIRECT_URI` o similar): se agrega al `.env`.
- Si está fija en el código (cerca de las líneas 491-533 de `hub/canal_ml.py`): reemplazar por `https://oauth.pstmn.io/v1/callback`.

Debe coincidir carácter por carácter con la registrada en ML.

---

## 6. Autorizar (una sola vez)

```powershell
python hub/canal_ml.py --autorizar
```

1. Abre el enlace que imprime el script e inicia sesión con la **cuenta vendedora**.
2. Acepta los permisos.
3. Postman mostrará una página vacía o con error: es normal.
4. Copia `code=TG-...` (o la URL completa) de la barra de direcciones y pégalo en la consola.
5. Se guarda `ML_REFRESH_TOKEN` en `.env` y `hub/ml_token.enc`.

El code expira en ~10 minutos y sirve una sola vez.

## 7. Validar

```powershell
python hub/canal_ml.py --prueba
```

Esperado: `OK: token valido. vendedor: ... site: MCO`.

## 8. Plan sin publicar

```powershell
python hub/construir_hub.py
python hub/canal_ml.py --dry-run
```

Deja `reportes/plan_ml_*.json`.

## 9. Publicación de prueba

```powershell
python hub/canal_ml.py --sincronizar --publicar 2
```

Revisa en ML fotos, categoría y precio (Precio 2 × 1,15, redondeado a $100). Si rechaza atributos (BRAND/MODEL), se amplía `crear_publicacion()`.

## 10. GitHub Actions

1. `git add hub/ml_token.enc` → commit `hub: token ML cifrado` → `git push origin main`.
2. En el repo: Settings → Secrets and variables → Actions: `ML_CLIENT_ID`, `ML_CLIENT_SECRET`, `ML_TOKEN_KEY` (y `ML_REDIRECT_URI` si aplica). `ML_REFRESH_TOKEN` solo si no commiteaste `ml_token.enc`.
3. Actions → sync-ml → Run workflow con `publicar = 0`; si queda verde, repetir con `publicar = 2`.
4. Cuando todo esté conforme, añadir `schedule:` a `sync-ml.yml`.

---

## 11. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `redirect_uri mismatch` | URL del script distinta a la registrada | Repetir sección 5 |
| `invalid_grant` | Code expirado o ya usado | Repetir `--autorizar` |
| No entrega refresh token | Falta marcar Refresh Token en la app | Editar la app y autorizar de nuevo |
| 401 a mitad del proceso | Refresh token rotado o perdido | Repetir `--autorizar` |
| 403 al publicar | Permiso sin escritura en Publicación y sincronización | Editar la app y poner Lectura y escritura |

## 12. Reglas de oro

- `client_secret` y tokens nunca van al repo, a WhatsApp, a capturas ni a chats. Solo `.env` y Secrets de GitHub.
- Si una clave se expuso (por ejemplo en una captura), se regenera desde el menú ⋮ junto a **Client Secret** en el DevCenter y se actualiza en `.env` y en GitHub.
