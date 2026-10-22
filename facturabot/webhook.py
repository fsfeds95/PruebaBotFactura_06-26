"""Servidor HTTP para recibir el webhook de Telegram.

Este modo es necesario para desplegar en plataformas que solo ejecutan
servicios web con request/response (Vercel como función serverless, Render
como Web Service). En modo webhook el bot NO hace polling: Telegram envía
cada actualización a ``POST /webhook`` y aquí se procesa con la misma
Application y handlers del bot.

Uso:
    app = create_app()                     # gunicorn/Flask/app.py
    register_webhook("https://mi-app...")  # una vez, p.ej. desde bootstrap

Variables de entorno:
    BOT_TOKEN        token del bot (obligatorio)
    WEBHOOK_SECRET   secreto que Telegram reenvía en un header; protege el
                     endpoint contra peticiones falsas (recomendado)
    DB_PATH          ruta SQLite. En Vercel usa /tmp (volátil); en Render
                     monta un Disk y apunta DB_PATH al disco.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
from pathlib import Path

import requests
from flask import Flask, jsonify, request
from telegram import Update

from .config import Config, load_config
from .db import Database

logger = logging.getLogger(__name__)

# Página estática con los botones "Deploy to Render" / "Deploy to Vercel"
_INDEX_HTML_PATH = Path(__file__).resolve().parent.parent / "web" / "index.html"


class BotRuntime:
    """Mantiene viva la Application de PTB dentro del proceso web.

    Un solo event loop en un hilo dedicado: cada update llega por la cola y
    se procesa con ``Application.process_update``, esperando a que terminen
    las coroutines que envían las respuestas antes de responder a Telegram
    (crítico en serverless: si la función retorna, se congelan las tareas).
    """

    def __init__(self, cfg: Config):
        from main import build_application  # noqa: PLC0415 (main importa este módulo)

        self._db = Database(cfg.db_path)
        self.application = build_application(cfg, self._db)
        self._loop = asyncio.new_event_loop()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        async def _boot():
            # initialize() hace get_me() contra Telegram; si el token es inválido
            # o no hay red, NO debe tumbar el servicio web (render/Vercel),
            # solo se loguea y los updates fallarán por su cuenta.
            try:
                await self.application.initialize()
                await self.application.start()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "La Application no pudo inicializarse (¿BOT_TOKEN válido?): %s", exc
                )

        fut = asyncio.run_coroutine_threadsafe(_boot(), self._loop)
        try:
            fut.result(timeout=30)
        except TimeoutError:
            logger.error("Timeout inicializando la Application del webhook")

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
            for t in pending:
                t.cancel()
            self._loop.close()

    def process(self, update: Update, timeout: float = 25.0) -> None:
        """Procesa un update y espera a que se envíen todas las respuestas."""

        async def _process():
            await self.application.process_update(update)
            pending = [
                t for t in asyncio.all_tasks(self._loop) if t is not asyncio.current_task()
            ]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        future = asyncio.run_coroutine_threadsafe(_process(), self._loop)
        future.result(timeout=timeout)


_runtime: BotRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> BotRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = BotRuntime(load_config())
        return _runtime


def create_app() -> Flask:
    """Crea la aplicación Flask: página de despliegue + endpoint webhook."""
    app = Flask(__name__)

    @app.get("/")
    def index():
        """Landing page con los botones Deploy to Render / Deploy to Vercel."""
        if _INDEX_HTML_PATH.exists():
            return _INDEX_HTML_PATH.read_text(encoding="utf-8")
        return jsonify(bot="FacturaBot", modo="webhook"), 200

    @app.get("/health")
    def health():
        return jsonify(status="ok"), 200

    @app.post("/webhook")
    def webhook():
        # Proteger el endpoint: Telegram reenvía el secret_token registrado
        esperado = os.environ.get("WEBHOOK_SECRET", "")
        recibido = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if esperado and recibido != esperado:
            return jsonify(error="no autorizado"), 403

        data = request.get_json(silent=True)
        if not data:
            return jsonify(ignored=True), 200

        try:
            runtime = get_runtime()
            update = Update.de_json(data, runtime.application.bot)
            runtime.process(update)
        except Exception:
            # Loguear y devolver 200: con 500 Telegram reintenta indefinidamente
            logger.exception("Error procesando update %s", (data or {}).get("update_id", "?"))
            return jsonify(ok=False, logged=True), 200
        return jsonify(ok=True), 200

    return app


def register_webhook(base_url: str | None = None) -> bool:
    """Registra la URL del webhook ante la API de Telegram (una sola vez).

    Determina la URL pública automáticamente de RENDER_EXTERNAL_URL o
    VERCEL_URL si no se pasa ninguna.
    """
    base_url = (
        base_url
        or os.environ.get("RENDER_EXTERNAL_URL")
        or (f"https://{os.environ['VERCEL_URL']}" if os.environ.get("VERCEL_URL") else None)
    )
    if not base_url:
        logger.warning(
            "No se encontró URL pública (RENDER_EXTERNAL_URL / VERCEL_URL). "
            "Pásala manualmente: register_webhook('https://tu-dominio/webhook')"
        )
        return False

    token = os.environ["BOT_TOKEN"]
    secret = os.environ.get("WEBHOOK_SECRET") or secrets.token_hex(16)
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": f"{base_url.rstrip('/')}/webhook",
            "secret_token": secret,
            "drop_pending_updates": False,
            "allowed_updates": ["message", "callback_query"],
        },
        timeout=15,
    )
    info = resp.json()
    if info.get("ok"):
        logger.info("✅ Webhook registrado en %s/webhook", base_url.rstrip("/"))
    else:
        logger.error("Fallo al registrar webhook: %s", info)
    return bool(info.get("ok"))


def delete_webhook() -> bool:
    """Quita el webhook (para volver al modo polling)."""
    token = os.environ["BOT_TOKEN"]
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/deleteWebhook",
        json={"drop_pending_updates": False},
        timeout=15,
    )
    return bool(resp.json().get("ok"))


if __name__ == "__main__":  # python -m facturabot.webhook -> registrar webhook
    logging.basicConfig(level=logging.INFO)
    ok = register_webhook()
    raise SystemExit(0 if ok else 1)
