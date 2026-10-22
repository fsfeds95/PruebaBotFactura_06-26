"""Servidor HTTP para FacturaBot (modo webhook).

Necesario para desplegar en plataformas que solo soportan servicios web:
  - Render (Web Service)
  - Vercel (función serverless, ver api/webhook.py + vercel.json)

Telegram envía cada actualización a POST /webhook; el bot responde usando la
misma Application y handlers del modo polling.

Uso local:
    BOT_TOKEN=... ALLOWED_USER_IDS=... python app.py
    # y luego: python -m facturabot.webhook  (registra el webhook con ngrok u otra URL pública)
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
for noisy in ("httpx", "telegram", "PIL"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from facturabot.webhook import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))  # Render inyecta PORT=10000
    app.run(host="0.0.0.0", port=port)
