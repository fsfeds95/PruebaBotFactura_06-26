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
facturabot/
  config.py             # configuración desde variables de entorno (.env)
  db.py                 # SQLite: numeración atómica, borrado lógico, resumen por moneda
  pdf.py                # generación del PDF (ReportLab), logo cacheado, totales exactos
  services.py           # lógica de emisión compartida (reserva nº → PDF → guardar → enviar)
  handlers.py           # comandos y conversación del bot (+ control de acceso)
tests/                  # pruebas unitarias (cálculos, contador, anulación, resumen)
```

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # edita BOT_TOKEN, ALLOWED_USER_IDS y datos fiscales
python main.py
```

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
