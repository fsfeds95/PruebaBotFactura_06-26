"""Entrypoint serverless de Vercel (función Python).

Vercel ejecuta este archivo para cada petición HTTP. Aquí se importa la app
Flask del webhook y se expone como handler WSGI sin servidor.

⚠️ Importante en Vercel:
  - El sistema de archivos es efímero: usa DB_PATH=/tmp/facturas.db
    (los datos se pierden cuando la instancia se apaga; sirve para pruebas
    o complementa con una BD externa tipo Neon/Turso/Postgres).
  - Después del primer deploy, registra el webhook ante Telegram desde tu PC:
      BOT_TOKEN=... python -m facturabot.webhook
    (detecta VERCEL_URL automáticamente) o manualmente con curl a setWebhook.
"""

import logging
import os
import sys
from pathlib import Path

# Asegurar que el proyecto raíz esté en el path (Vercel incluye solo /api por defecto)
RAIZ = str(Path(__file__).resolve().parent.parent)
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

logging.basicConfig(level=logging.INFO)

# En serverless no hay disco persistente: fijar /tmp si no se indicó otra cosa
os.environ.setdefault("DB_PATH", "/tmp/facturas.db")

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from facturabot.webhook import create_app  # noqa: E402

app = create_app()


def handler(event, context):
    """Adaptador para runtime custom de Vercel (si se configurara lambda)."""
    return app(event, context) if isinstance(event, dict) else None


# Vercel detecta automáticamente objetos WSGI llamados `app` en funciones Python
# (requirements.txt con `vercel[python]`-style: usa el server WSGI integrado).
