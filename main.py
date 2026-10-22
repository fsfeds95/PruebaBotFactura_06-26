"""Punto de entrada del FacturaBot.

Uso:
    python main.py            (requiere BOT_TOKEN y ALLOWED_USER_IDS en .env)
"""

import logging
import warnings

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

from facturabot import handlers as h
from facturabot.config import load_config
from facturabot.db import Database

# Suprimir la advertencia conocida de CallbackQueryHandler dentro de ConversationHandler
warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
# Silenciar logs excesivos de librerías
for noisy in ("httpx", "telegram", "PIL"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger("facturabot")


def build_application(cfg, db):
    """Crea la Application de PTB con todos los handlers registrados."""
    app = Application.builder().token(cfg.token).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["db"] = db

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("nueva", h.nueva_factura)],
        states={
            h.NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.nombre)],
            h.RIF: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.rif)],
            h.TELEFONO: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.telefono)],
            h.DOMICILIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.domicilio)],
            h.CONDICIONES: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.condiciones)],
            h.PRODUCTO_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.producto_desc)],
            h.PRODUCTO_CANT: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.producto_cant)],
            h.PRODUCTO_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.producto_precio)],
            h.PRODUCTO_MENU: [
                CallbackQueryHandler(h.producto_menu, pattern=r"^(producto_|editar_|eliminar_|volver_menu)")
            ],
            h.EDITAR_SELECCION: [
                CallbackQueryHandler(h.editar_seleccion, pattern=r"^(editar_|volver_menu)")
            ],
            h.EDITAR_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.editar_desc)],
            h.EDITAR_CANT: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.editar_cant)],
            h.EDITAR_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, h.editar_precio)],
        },
        fallbacks=[CommandHandler("cancel", h.cancelar)],
    )
    # El conversation handler va primero para que /nueva y /cancel tengan prioridad.
    app.add_handler(conv_handler)

    for nombre_cmd, funcion in (
        ("start", h.start),
        ("factura", h.factura_directa),
        ("historial", h.historial),
        ("resumen", h.resumen),
        ("eliminar", h.eliminar),
        ("reset", h.reset),
        ("moneda", h.moneda),
    ):
        app.add_handler(CommandHandler(nombre_cmd, funcion))

    return app


def main():
    try:
        cfg = load_config()
    except RuntimeError as exc:
        logger.critical("Configuración inválida: %s", exc)
        raise SystemExit(1) from exc

    db = Database(cfg.db_path)
    logger.info("🧾 FacturaBot iniciado. Usa /nueva para empezar.")

    app = build_application(cfg, db)
    try:
        # run_polling ya reintenta internamente ante errores de red/Telegram;
        # antes había una llamada recursiva a main() que podía causar
        # RecursionError si el token era inválido.
        app.run_polling()
    finally:
        db.close()


if __name__ == "__main__":
    main()
