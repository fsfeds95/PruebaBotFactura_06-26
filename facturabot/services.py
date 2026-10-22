"""Lógica de negocio compartida entre handlers (elimina la duplicación del envío de facturas).

Flujo seguro de emisión:
1. Se reserva el número en la BD (transacción atómica).
2. Se genera el PDF; si falla, se revierte el número reservado.
3. Se guarda la factura y se envía por Telegram; si el envío falla, se avisa
   pero la factura queda registrada (nunca se pierde lo ya emitido).
"""

import logging
from dataclasses import dataclass

from .config import Config
from .db import Database
from .pdf import generar_pdf_factura

logger = logging.getLogger(__name__)


@dataclass
class FacturaEmitida:
    buffer: object
    numero: str
    control: str
    cliente: str
    moneda: str
    subtotal: float
    iva: float
    total: float


def emitir_factura(
    db: Database,
    cfg: Config,
    cliente: str,
    rif: str,
    telefono: str,
    domicilio: str,
    condiciones: str,
    productos,
    moneda: str,
) -> FacturaEmitida:
    """Reserva número → genera PDF → persiste. Revierte el número si el PDF falla."""
    simbolo = cfg.monedas.get(moneda, cfg.monedas[cfg.moneda_por_defecto])
    numero = db.reservar_numero()
    try:
        buffer, items, subtotal, iva, total = generar_pdf_factura(
            numero=numero,
            cliente=cliente,
            rif=rif,
            telefono=telefono,
            domicilio=domicilio,
            condiciones=condiciones,
            productos=productos,
            moneda=moneda,
            simbolo=simbolo,
            emisor=cfg.emisor,
            logo_url=cfg.logo_url,
            logo_cache_dir=cfg.logo_cache_dir,
        )
    except Exception:
        db.revertir_numero(numero)  # no "quemamos" números si el PDF falló
        logger.exception("Error al generar el PDF de la factura %s", numero)
        raise

    numero_str = str(numero).zfill(6)
    db.guardar_factura(
        numero=numero_str,
        control=numero_str,
        cliente=cliente,
        rif=rif,
        telefono=telefono,
        domicilio=domicilio,
        condiciones=condiciones,
        moneda=moneda,
        subtotal=subtotal,
        iva=iva,
        total=total,
        items=items,
    )
    return FacturaEmitida(
        buffer=buffer, numero=numero_str, control=numero_str, cliente=cliente,
        moneda=moneda, subtotal=subtotal, iva=iva, total=total,
    )


async def enviar_factura(destino, factura: FacturaEmitida):
    """Envío único reutilizable (query.message o update.message).

    `destino` es el objeto message sobre el que se responde. Escapa el nombre del
    cliente para MarkdownV2/Markdown y maneja errores de Telegram sin tumbar el bot.
    """
    from telegram.error import TelegramError

    cliente_seguro = escape_markdown(factura.cliente)
    filename = f"factura_{factura.numero}_{sanear_filename(factura.cliente)}.pdf"
    caption = (
        f"✅ *Factura generada con éxito*\n\n"
        f"👤 *Cliente:* {cliente_seguro}\n"
        f"📄 *Número:* {factura.numero}\n"
        f"💰 *Total:* {factura.total:.2f} {factura.moneda}\n"
        f"🧾 *Moneda:* {factura.moneda}"
    )
    try:
        await destino.reply_document(
            document=factura.buffer, filename=filename, caption=caption, parse_mode="Markdown"
        )
    except TelegramError:
        logger.exception("No se pudo enviar la factura %s por Telegram", factura.numero)
        raise


def escape_markdown(texto: str) -> str:
    """Escapa caracteres especiales de Markdown de Telegram."""
    especiales = ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]
    resultado = str(texto)
    for car in especiales:
        resultado = resultado.replace(car, f"\\{car}")
    return resultado


def sanear_filename(texto: str) -> str:
    """Nombre de archivo seguro: solo alfanuméricos, guiones y guiones bajos."""
    limpio = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(texto))
    return limpio[:40] or "cliente"
