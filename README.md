# FacturaBot 🧾

Bot de Telegram para emitir facturas en PDF (estilo venezolano: RIF, IVA 16%,
nº de control) y almacenarlas en SQLite.

## ⚠️ Seguridad

- El **token nunca va en el código**: se lee de `BOT_TOKEN` (`.env`).
- Si tu token llegó a estar commiteado, **revócalo con @BotFather (`/revoke`)**
  y configura el nuevo. Un token filtrado permite a cualquiera controlar tu bot.
- Solo los usuarios cuya ID está en `ALLOWED_USER_IDS` pueden usar el bot.

## Estructura del proyecto

```
main.py                 # punto de entrada (arranque y registro de handlers)
app.py                  # servidor Flask modo webhook (Render / Vercel)
api/index.py            # función serverless de Vercel
web/index.html          # landing con botones "Deploy to Render" / "Deploy to Vercel"
render.yaml             # Blueprint de despliegue en Render (1 clic)
vercel.json             # configuración de la función serverless en Vercel
facturabot/
  config.py             # configuración desde variables de entorno (.env)
  db.py                 # SQLite: numeración atómica, borrado lógico, resumen por moneda
  pdf.py                # generación del PDF (ReportLab), logo cacheado, totales exactos
  services.py           # lógica de emisión compartida (reserva nº → PDF → guardar → enviar)
  handlers.py           # comandos y conversación del bot (+ control de acceso)
  webhook.py            # runtime webhook (procesamiento de updates + setWebhook)
tests/                  # pruebas unitarias (cálculos, contador, anulación, resumen)
```

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # edita BOT_TOKEN, ALLOWED_USER_IDS y datos fiscales
python main.py
```

## Despliegue en la nube (botones 🚀)

La página `https://<tu-despliegue>/` muestra los botones **Deploy to Render** y
**Deploy to Vercel**. Antes de usarlos, reemplaza `TU_USUARIO` por tu usuario
de GitHub en `web/index.html` (enlaces de los botones).

### Render (recomendado — BD persistente)

1. Sube el repo a GitHub.
2. En Render: *New → Blueprint* (lee `render.yaml`) o usa el botón de la landing.
3. En el panel, añade como **Secret**: `BOT_TOKEN`, `ALLOWED_USER_IDS`
   (`WEBHOOK_SECRET` se genera solo gracias a `generateValue: true`).
4. *(Opcional pero recomendado)* Añade un **Disk** (p. ej. 1 GB) montado en
   `/opt/render/project/src/data` — coincide con `DB_PATH=data/facturas.db`.
5. Al terminar el deploy, registra el webhook (desde tu PC o Render Shell):
   ```bash
   python -m facturabot.webhook   # detecta RENDER_EXTERNAL_URL automáticamente
   ```

### Vercel (solo pruebas — sin disco persistente)

1. Botón **Deploy to Vercel** de la landing (o *Import Project* en vercel.com).
2. Configura env vars: `BOT_TOKEN`, `ALLOWED_USER_IDS`, `WEBHOOK_SECRET` y
   `DB_PATH=/tmp/facturas.db` (⚠️ efímero: las facturas se pierden al reiniciar;
   para producción conecta una BD externa tipo Neon/Turso).
3. Registra el webhook desde tu PC:
   ```bash
   VERCEL_URL=tu-proyecto.vercel.app python -m facturabot.webhook
   ```

> El modo polling tradicional (`python main.py`) sigue disponible para ejecutar
> el bot localmente o en un VPS.

## Comandos del bot

| Comando | Descripción |
|---|---|
| `/nueva` | Factura paso a paso (cliente → productos con botones → PDF) |
| `/factura 'Cliente' 'RIF' 'Tel' 'Dom' 'Cond' 'Prod\|Cant\|Precio'` | Emisión directa |
| `/historial [filtro]` | Últimas facturas vigentes |
| `/resumen` | Totales del mes **agrupados por moneda** |
| `/eliminar NUM` | **Anula** la factura (borrado lógico, no se reutiliza el número) |
| `/reset` | Sincroniza el contador con la BD |
| `/moneda Bs\|USD\|EUR` | Moneda para futuras facturas |
| `/cancel` | Cancela la conversación actual |

## Notas fiscales

- La anulación es **lógica**: el registro queda marcado (`eliminada = 1`) para
  cumplir la conservación de comprobantes; los números emitidos no se reutilizan.
- El PDF incluye leyenda de sistema automatizado (Resolución SENIAT 93), nº de
  control y desglose de sub-total/IVA/total. Ajusta los datos del emisor en `.env`.

## Pruebas

```bash
python -m unittest discover -s tests -v
```
