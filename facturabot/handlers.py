"""Handlers del bot de Telegram.

Mejoras respecto a la versión anterior:
- Control de acceso por lista blanca de IDs (decorador @solo_autorizados).
- Envío de facturas centralizado en services.enviar_factura (sin duplicación).
- Mensajes con escape de Markdown para datos del usuario.
- Validación específica de errores (ValueError, etc.) en lugar de `except:` desnudos.
"""

import logging
import shlex
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from .config import Config
from .db import Database
from .services import emitir_factura, enviar_factura, escape_markdown

logger = logging.getLogger(__name__)

# Estados del conversation handler
(
    NOMBRE, RIF, TELEFONO, DOMICILIO, CONDICIONES,
    PRODUCTO_DESC, PRODUCTO_CANT, PRODUCTO_PRECIO, PRODUCTO_MENU,
    EDITAR_SELECCION, EDITAR_DESC, EDITAR_CANT, EDITAR_PRECIO,
) = range(13)


def solo_autorizados(func):
    """Decorador: rechaza a usuarios que no están en la lista blanca de IDs."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        cfg: Config = context.bot_data["cfg"]
        user_id = update.effective_user.id if update.effective_user else None
        if user_id not in cfg.allowed_user_ids:
            logger.warning("Acceso denegado al usuario id=%s", user_id)
            if update.effective_message:
                try:
                    await update.effective_message.reply_text(
                        "⛔ No estás autorizado a usar este bot."
                    )
                except TelegramError:
                    logger.exception("No se pudo enviar el aviso de acceso denegado")
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)

    return wrapper


# ---------- Helpers de UI ----------

def _moneda_actual(context) -> str:
    cfg: Config = context.bot_data["cfg"]
    return context.user_data.get("moneda", cfg.moneda_por_defecto)


def mostrar_lista_productos(productos, monedas, moneda):
    """Mensaje + teclado con la lista de productos y sus botones."""
    if not productos:
        mensaje = "📭 No hay productos agregados aún."
        keyboard = [[InlineKeyboardButton("➕ Agregar producto", callback_data="producto_agregar")]]
        return mensaje, InlineKeyboardMarkup(keyboard)

    mensaje = "📋 *LISTA DE PRODUCTOS:*\n\n"
    keyboard = []
    for idx, (cant, desc, precio) in enumerate(productos):
        mensaje += (
            f"*{idx + 1}.* {escape_markdown(desc)}\n"
            f"   🔢 Cantidad: {cant:g}\n"
            f"   💰 Precio: {precio:.2f} {moneda}\n"
            f"   📊 Subtotal: {cant * precio:.2f} {moneda}\n\n"
        )
        keyboard.append([
            InlineKeyboardButton(f"✏️ Editar #{idx + 1}", callback_data=f"editar_{idx}"),
            InlineKeyboardButton(f"❌ Eliminar #{idx + 1}", callback_data=f"eliminar_{idx}"),
        ])
    keyboard.append([
        InlineKeyboardButton("➕ Agregar producto", callback_data="producto_agregar"),
        InlineKeyboardButton("✅ Terminar y generar", callback_data="producto_terminar"),
    ])
    return mensaje, InlineKeyboardMarkup(keyboard)


def mostrar_edicion_producto(productos, idx, moneda):
    if not (0 <= idx < len(productos)):
        return None, None
    cant, desc, precio = productos[idx]
    keyboard = [
        [InlineKeyboardButton("📝 Editar descripción", callback_data="editar_desc")],
        [InlineKeyboardButton("🔢 Editar cantidad", callback_data="editar_cant")],
        [InlineKeyboardButton("💰 Editar precio", callback_data="editar_precio")],
        [InlineKeyboardButton("🔙 Volver a la lista", callback_data="volver_menu")],
    ]
    mensaje = (
        f"✏️ *Editando producto #{idx + 1}:*\n\n"
        f"📦 {escape_markdown(desc)}\n"
        f"🔢 Cantidad: {cant:g}\n"
        f"💰 Precio: {precio:.2f} {moneda}\n\n"
        "¿Qué deseas modificar?"
    )
    return mensaje, InlineKeyboardMarkup(keyboard)


async def _responder_menu(update_or_query, context, prefijo=""):
    """Reenvía la lista de productos (común a varios estados)."""
    cfg: Config = context.bot_data["cfg"]
    moneda = _moneda_actual(context)
    mensaje, markup = mostrar_lista_productos(
        context.user_data.get("productos", []), cfg.monedas, moneda
    )
    texto = f"{prefijo}\n\n🛒 *GESTIÓN DE PRODUCTOS*\n\n{mensaje}".strip()
    if hasattr(update_or_query, "edit_message_text"):  # CallbackQuery
        await update_or_query.edit_message_text(texto, reply_markup=markup,
                                                parse_mode=ParseMode.MARKDOWN)
    else:  # Message
        await update_or_query.reply_text(texto, reply_markup=markup,
                                         parse_mode=ParseMode.MARKDOWN)


async def _generar_y_enviar(destino_message, context):
    """Emite la factura desde user_data y la envía. Usada por /nueva y /factura."""
    cfg: Config = context.bot_data["cfg"]
    db: Database = context.bot_data["db"]
    data = context.user_data
    moneda = _moneda_actual(context)
    try:
        factura = emitir_factura(
            db, cfg,
            cliente=data["cliente"], rif=data["rif"], telefono=data["telefono"],
            domicilio=data["domicilio"], condiciones=data["condiciones"],
            productos=data["productos"], moneda=moneda,
        )
    except Exception as exc:  # noqa: BLE001 - se informa al usuario y se continúa
        logger.exception("Error al emitir factura")
        await destino_message.reply_text(f"❌ Error al generar la factura: {exc}")
        return False
    try:
        await enviar_factura(destino_message, factura)
    except TelegramError:
        await destino_message.reply_text(
            f"⚠️ La factura {factura.numero} se guardó, pero no se pudo enviar el PDF. "
            "Descárgala con /historial."
        )
    return True


# ---------- Comandos principales ----------

@solo_autorizados
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.bot_data["cfg"]
    moneda = _moneda_actual(context)
    await update.message.reply_text(
        "¡Hola! Soy tu FacturaBot 🧾\n\n"
        f"💰 Moneda actual: {moneda} {cfg.monedas[moneda]}\n\n"
        "Comandos disponibles:\n"
        "/nueva – Generar una factura paso a paso\n"
        "/historial – Ver facturas emitidas\n"
        "/resumen – Resumen mensual (por moneda)\n"
        "/eliminar NUMERO – Anular una factura (borrado lógico)\n"
        "/reset – Sincronizar contador\n"
        "/moneda – Cambiar moneda (Bs, USD, EUR)\n\n"
        "O usa /factura directo si ya conoces el formato."
    )


@solo_autorizados
async def nueva_factura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Vamos a crear una factura. Dame el *Nombre o Razón Social* del cliente:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return NOMBRE


@solo_autorizados
async def nombre(update, context):
    context.user_data["cliente"] = update.message.text
    await update.message.reply_text("📝 Ahora el *RIF / C.I.* del cliente:",
                                    parse_mode=ParseMode.MARKDOWN)
    return RIF


@solo_autorizados
async def rif(update, context):
    context.user_data["rif"] = update.message.text
    await update.message.reply_text("📝 Dame el *Teléfono* del cliente:",
                                    parse_mode=ParseMode.MARKDOWN)
    return TELEFONO


@solo_autorizados
async def telefono(update, context):
    context.user_data["telefono"] = update.message.text
    await update.message.reply_text("📝 Ahora el *Domicilio Fiscal* del cliente:",
                                    parse_mode=ParseMode.MARKDOWN)
    return DOMICILIO


@solo_autorizados
async def domicilio(update, context):
    context.user_data["domicilio"] = update.message.text
    await update.message.reply_text(
        "📝 Escribe las *Condiciones de Pago* (ej: Contado, Crédito 30 días):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return CONDICIONES


@solo_autorizados
async def condiciones(update, context):
    context.user_data["condiciones"] = update.message.text
    context.user_data["productos"] = []
    context.user_data["producto_actual"] = {}
    context.user_data["editando_idx"] = None
    await _responder_menu(update.message, context)
    return PRODUCTO_MENU


@solo_autorizados
async def producto_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "producto_agregar":
        context.user_data["producto_actual"] = {}
        await query.edit_message_text(
            "📦 Escribe la *Descripción o Nombre* del producto:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return PRODUCTO_DESC

    if data == "producto_terminar":
        if not context.user_data.get("productos"):
            await query.answer("❌ No hay productos. Agrega al menos uno.", show_alert=True)
            return PRODUCTO_MENU
        await query.edit_message_text("💰 Generando factura...")
        await _generar_y_enviar(query.message, context)
        context.user_data.clear()
        return ConversationHandler.END

    if data.startswith("eliminar_"):
        idx = int(data.split("_", 1)[1])
        productos = context.user_data["productos"]
        if 0 <= idx < len(productos):
            eliminado = productos.pop(idx)
            await _responder_menu(query, context,
                                  prefijo=f"✅ Producto eliminado: {eliminado[1]}")
        else:
            await query.answer("❌ Producto no encontrado.", show_alert=True)
        return PRODUCTO_MENU

    if data.startswith("editar_"):
        idx = int(data.split("_", 1)[1])
        productos = context.user_data["productos"]
        if 0 <= idx < len(productos):
            context.user_data["editando_idx"] = idx
            mensaje, markup = mostrar_edicion_producto(productos, idx, _moneda_actual(context))
            await query.edit_message_text(mensaje, reply_markup=markup,
                                          parse_mode=ParseMode.MARKDOWN)
            return EDITAR_SELECCION
        await query.answer("❌ Producto no encontrado.", show_alert=True)
        return PRODUCTO_MENU

    if data == "volver_menu":
        await _responder_menu(query, context)
        return PRODUCTO_MENU

    return PRODUCTO_MENU


@solo_autorizados
async def editar_seleccion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    idx = context.user_data.get("editando_idx")
    productos = context.user_data.get("productos", [])

    if idx is None or not (0 <= idx < len(productos)):
        await _responder_menu(query, context)
        return PRODUCTO_MENU

    if data == "volver_menu":
        await _responder_menu(query, context)
        return PRODUCTO_MENU
    if data == "editar_desc":
        await query.edit_message_text(
            f"✏️ Escribe la *nueva descripción* (actual: {escape_markdown(productos[idx][1])}):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return EDITAR_DESC
    if data == "editar_cant":
        await query.edit_message_text(
            f"🔢 Escribe la *nueva cantidad* (actual: {productos[idx][0]:g}):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return EDITAR_CANT
    if data == "editar_precio":
        await query.edit_message_text(
            f"💰 Escribe el *nuevo precio* (actual: {productos[idx][2]:.2f}):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return EDITAR_PRECIO
    return EDITAR_SELECCION


async def _volver_a_edicion(update, context):
    idx = context.user_data.get("editando_idx")
    productos = context.user_data.get("productos", [])
    mensaje, markup = mostrar_edicion_producto(productos, idx, _moneda_actual(context))
    if mensaje:
        await update.message.reply_text(mensaje, reply_markup=markup,
                                        parse_mode=ParseMode.MARKDOWN)
    return EDITAR_SELECCION


@solo_autorizados
async def editar_desc(update, context):
    idx = context.user_data.get("editando_idx")
    productos = context.user_data.get("productos", [])
    if 0 <= idx < len(productos):
        cant, _, precio = productos[idx]
        productos[idx] = (cant, update.message.text, precio)
        await update.message.reply_text("✅ Descripción actualizada.")
    return await _volver_a_edicion(update, context)


@solo_autorizados
async def editar_cant(update, context):
    try:
        nueva = float(update.message.text)
        if nueva <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Cantidad no válida. Escribe un número mayor que 0.")
        return EDITAR_CANT
    idx = context.user_data.get("editando_idx")
    productos = context.user_data.get("productos", [])
    if 0 <= idx < len(productos):
        _, desc, precio = productos[idx]
        productos[idx] = (nueva, desc, precio)
        await update.message.reply_text(f"✅ Cantidad actualizada a {nueva:g}.")
    return await _volver_a_edicion(update, context)


@solo_autorizados
async def editar_precio(update, context):
    try:
        nuevo = float(update.message.text)
        if nuevo <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Precio no válido. Escribe un número mayor que 0.")
        return EDITAR_PRECIO
    idx = context.user_data.get("editando_idx")
    productos = context.user_data.get("productos", [])
    if 0 <= idx < len(productos):
        cant, desc, _ = productos[idx]
        productos[idx] = (cant, desc, nuevo)
        await update.message.reply_text(f"✅ Precio actualizado a {nuevo:.2f}.")
    return await _volver_a_edicion(update, context)


@solo_autorizados
async def producto_desc(update, context):
    context.user_data.setdefault("producto_actual", {})["desc"] = update.message.text
    await update.message.reply_text("🔢 Escribe la *Cantidad* (solo números):",
                                    parse_mode=ParseMode.MARKDOWN)
    return PRODUCTO_CANT


@solo_autorizados
async def producto_cant(update, context):
    try:
        cantidad = float(update.message.text)
        if cantidad <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Cantidad no válida. Escribe un número (ej: 2, 3.5):")
        return PRODUCTO_CANT
    context.user_data["producto_actual"]["cant"] = cantidad
    await update.message.reply_text(
        "💰 Escribe el *Precio Unitario* (solo números, punto para decimales):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return PRODUCTO_PRECIO


@solo_autorizados
async def producto_precio(update, context):
    try:
        precio = float(update.message.text)
        if precio <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Precio no válido. Escribe un número (ej: 15.50, 100):")
        return PRODUCTO_PRECIO
    actual = context.user_data["producto_actual"]
    context.user_data["productos"].append((actual["cant"], actual["desc"], precio))
    context.user_data["producto_actual"] = {}
    await _responder_menu(update.message, context, prefijo="✅ Producto agregado.")
    return PRODUCTO_MENU


@solo_autorizados
async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Proceso cancelado. Usa /nueva para empezar de nuevo.")
    context.user_data.clear()
    return ConversationHandler.END


# ---------- Otros comandos ----------

@solo_autorizados
async def factura_directa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    ayuda = (
        "❌ Formato: /factura 'Cliente' 'RIF' 'Teléfono' 'Domicilio' 'Condiciones' "
        "'Producto1|Cantidad|Precio' ..."
    )
    if len(args) < 6:
        await update.message.reply_text(ayuda)
        return
    try:
        partes = shlex.split(" ".join(args))
    except ValueError:
        await update.message.reply_text("❌ Error en comillas. Usa comillas simples para cada campo.")
        return
    if len(partes) < 6:
        await update.message.reply_text("❌ Faltan datos.\n" + ayuda)
        return

    cliente, rif_, telefono_, domicilio_, condiciones_ = partes[:5]
    productos = []
    for raw in partes[5:]:
        campos = raw.split("|")
        if len(campos) != 3:
            await update.message.reply_text(f"❌ Producto mal formateado: '{raw}'")
            return
        desc, cant_str, prec_str = campos
        try:
            cant = float(cant_str.strip())
            precio = float(prec_str.strip())
            if cant <= 0 or precio <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(f"❌ Cantidad o precio inválido en: '{raw}'")
            return
        productos.append((cant, desc.strip(), precio))

    context.user_data.update(
        cliente=cliente, rif=rif_, telefono=telefono_,
        domicilio=domicilio_, condiciones=condiciones_, productos=productos,
    )
    ok = await _generar_y_enviar(update.message, context)
    if ok:
        context.bot_data["db"].registrar_log(
            "factura_directa", f"Factura emitida vía /factura para {cliente}"
        )
    context.user_data.clear()


@solo_autorizados
async def historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    filtro = " ".join(context.args) if context.args else None
    facturas = db.obtener_facturas(filtro)
    if not facturas:
        await update.message.reply_text("📭 No hay facturas vigentes.")
        return
    mensaje = "📋 *HISTORIAL (últimas 10)*\n\n"
    for f in facturas[:10]:
        mensaje += (
            f"🔹 N° {escape_markdown(f.numero)} – {escape_markdown(f.cliente)} – "
            f"{f.fecha[:10]} – {f.total:.2f} {escape_markdown(f.moneda)}\n"
        )
    if len(facturas) > 10:
        mensaje += "\n… más. Usa /historial 'nombre' para filtrar."
    await update.message.reply_text(mensaje, parse_mode=ParseMode.MARKDOWN)


@solo_autorizados
async def resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    cfg: Config = context.bot_data["cfg"]
    r = db.resumen_mensual()
    if not r["por_moneda"]:
        await update.message.reply_text("📊 Sin facturas este mes.")
        return
    nombres = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
               "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    mensaje = f"📊 *RESUMEN {nombres[r['mes'] - 1]} {r['anio']}*\n\n"
    for m in r["por_moneda"]:
        simbolo = cfg.monedas.get(m["moneda"], "")
        mensaje += (
            f"*{m['moneda']}*: {m['cantidad']} factura(s)\n"
            f"   Sub-total: {simbolo}{m['subtotal']:,.2f}\n"
            f"   IVA: {simbolo}{m['iva']:,.2f}\n"
            f"   Total: {simbolo}{m['total']:,.2f}\n\n"
        )
    await update.message.reply_text(mensaje, parse_mode=ParseMode.MARKDOWN)


@solo_autorizados
async def eliminar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usa: /eliminar NUMERO")
        return
    numero = context.args[0].lstrip("0") or "0"
    db: Database = context.bot_data["db"]
    if not db.existe_factura(numero) and not db.existe_factura(numero.zfill(6)):
        await update.message.reply_text(f"❌ No existe la factura N° {context.args[0]}.")
        return
    for candidato in (context.args[0], numero, numero.zfill(6)):
        if db.anular_factura(candidato):
            await update.message.reply_text(
                f"✅ Factura N° {candidato} ANULADA (borrado lógico).\n"
                "El registro se conserva por normativa fiscal y el número NO se reutiliza."
            )
            return
    await update.message.reply_text("❌ No se pudo anular la factura.")


@solo_autorizados
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    nuevo = db.sincronizar_contador()
    if nuevo == 0:
        await update.message.reply_text("🔄 Contador en 0 (no hay facturas emitidas).")
    else:
        await update.message.reply_text(
            f"🔄 Contador sincronizado. Última factura emitida: N° {str(nuevo).zfill(6)}.\n"
            f"La siguiente será la N° {str(nuevo + 1).zfill(6)}."
        )


@solo_autorizados
async def moneda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.bot_data["cfg"]
    if not context.args:
        actual = _moneda_actual(context)
        await update.message.reply_text(
            f"💰 Moneda actual: {actual} {cfg.monedas[actual]}\n"
            "Usa: /moneda Bs | /moneda USD | /moneda EUR"
        )
        return
    bruto = context.args[0]
    mapa = {"bs": "Bs", "$": "USD", "usd": "USD", "eur": "EUR"}
    mon = mapa.get(bruto.lower())
    if mon not in cfg.monedas:
        await update.message.reply_text("❌ Opciones: Bs, USD, EUR")
        return
    context.user_data["moneda"] = mon
    await update.message.reply_text(f"✅ Moneda cambiada a {mon} {cfg.monedas[mon]}.")
