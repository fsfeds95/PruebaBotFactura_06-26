import sqlite3
import io
import shlex
import requests
import warnings
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.warnings import PTBUserWarning
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.colors import red, black
from reportlab.lib.utils import ImageReader

# Suprimir la advertencia de CallbackQueryHandler en ConversationHandler
warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

# =========== CONFIGURACIÓN ===========
TOKEN = "8946336368:AAGU5PN2kbkn8gSXGfDHjltRr7eptR4K8xU"
DB_NAME = "facturas_carpinteria_miranda.db"
MONEDAS = {"Bs": "Bs.", "USD": "$", "EUR": "€"}
MONEDA_POR_DEFECTO = "USD"
URL_LOGO = "https://i.ibb.co/ZpQTHPDj/IMG-20260625-WA0160.jpg"

# Diccionario de meses en español
MESES_ESP = {
    "January": "Enero", "February": "Febrero", "March": "Marzo",
    "April": "Abril", "May": "Mayo", "June": "Junio",
    "July": "Julio", "August": "Agosto", "September": "Septiembre",
    "October": "Octubre", "November": "Noviembre", "December": "Diciembre"
}

# Estados para el conversation handler
(NOMBRE, RIF, TELEFONO, DOMICILIO, CONDICIONES, PRODUCTO_DESC, PRODUCTO_CANT, PRODUCTO_PRECIO, PRODUCTO_MENU, EDITAR_SELECCION, EDITAR_DESC, EDITAR_CANT, EDITAR_PRECIO) = range(13)

# =========== BASE DE DATOS ===========
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS facturas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero TEXT UNIQUE,
        control TEXT,
        cliente TEXT,
        rif TEXT,
        telefono TEXT,
        domicilio TEXT,
        condiciones TEXT,
        moneda TEXT,
        subtotal REAL,
        iva REAL,
        total REAL,
        fecha TEXT,
        items TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS contador (
        ultimo_numero INTEGER DEFAULT 0
    )''')
    c.execute("INSERT OR IGNORE INTO contador (ultimo_numero) VALUES (0)")
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        accion TEXT,
        detalle TEXT,
        fecha TEXT
    )''')
    conn.commit()
    conn.close()

def obtener_siguiente_numero():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT ultimo_numero FROM contador")
    ultimo = c.fetchone()[0]
    nuevo = ultimo + 1
    c.execute("UPDATE contador SET ultimo_numero = ?", (nuevo,))
    conn.commit()
    conn.close()
    return nuevo

def guardar_factura(numero, control, cliente, rif, telefono, domicilio, condiciones, moneda, subtotal, iva, total, items):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''INSERT INTO facturas
        (numero, control, cliente, rif, telefono, domicilio, condiciones, moneda, subtotal, iva, total, fecha, items)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (numero, control, cliente, rif, telefono, domicilio, condiciones, moneda, subtotal, iva, total, datetime.now().isoformat(), items))
    conn.commit()
    conn.close()
    registrar_log("factura_creada", f"Factura {numero} creada para {cliente}")

def registrar_log(accion, detalle):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO logs (accion, detalle, fecha) VALUES (?, ?, ?)",
              (accion, detalle, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def obtener_facturas(filtro=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if filtro:
        c.execute("SELECT * FROM facturas WHERE cliente LIKE ? OR numero LIKE ?", (f'%{filtro}%', f'%{filtro}%'))
    else:
        c.execute("SELECT * FROM facturas ORDER BY id DESC")
    facturas = c.fetchall()
    conn.close()
    return facturas

# NUEVA VERSIÓN: Sincroniza el contador si se elimina la última factura
def eliminar_factura(numero):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM facturas WHERE numero = ?", (numero,))

    try:
        num_int = int(numero)
        c.execute("SELECT ultimo_numero FROM contador")
        ultimo = c.fetchone()[0]
        if num_int == ultimo:
            nuevo_ultimo = max(0, ultimo - 1)
            c.execute("UPDATE contador SET ultimo_numero = ?", (nuevo_ultimo,))
    except ValueError:
        pass

    conn.commit()
    conn.close()
    registrar_log("factura_eliminada", f"Factura {numero} eliminada")

# NUEVA VERSIÓN: Sincroniza el contador con el número de factura real más alto
def resetear_contador():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT MAX(CAST(numero AS INTEGER)) FROM facturas")
    max_num = c.fetchone()[0]

    if max_num is None:
        nuevo_limite = 0
    else:
        nuevo_limite = max_num

    c.execute("UPDATE contador SET ultimo_numero = ?", (nuevo_limite,))
    conn.commit()
    conn.close()
    registrar_log("contador_reseteado", f"Contador de facturas sincronizado en {nuevo_limite}")
    return nuevo_limite

def resumen_mensual(mes=None, anio=None):
    if not mes:
        mes = datetime.now().month
    if not anio:
        anio = datetime.now().year
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''SELECT COUNT(*), SUM(total) FROM facturas
                 WHERE strftime('%m', fecha) = ? AND strftime('%Y', fecha) = ?''',
              (str(mes).zfill(2), str(anio)))
    resultado = c.fetchone()
    conn.close()
    return resultado[0] if resultado[0] else 0, resultado[1] if resultado[1] else 0.0

# =========== GENERADOR DE PDF ===========
def generar_pdf_factura(cliente, rif, telefono, domicilio, condiciones, productos, moneda="USD", iva_porcentaje=16):
    numero = obtener_siguiente_numero()
    numero_str = str(numero).zfill(6)
    control_str = str(numero).zfill(6)

    ahora = datetime.now()
    dia = ahora.day
    mes_ingles = ahora.strftime("%B")
    mes = MESES_ESP.get(mes_ingles, mes_ingles)
    anio = ahora.year
    ciudad = "Caracas"

    subtotal = 0.0
    items = []
    for cantidad, descripcion, precio in productos:
        monto = cantidad * precio
        subtotal += monto
        items.append((cantidad, descripcion, precio, monto))

    iva = subtotal * (iva_porcentaje / 100)
    total = subtotal + iva

    simbolo = MONEDAS.get(moneda, MONEDAS[MONEDA_POR_DEFECTO])

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margen_x = 20 * mm
    margen_y = 20 * mm
    y = height - margen_y

    # =========== CABECERA CON LOGO ===========
    logo_cargado = False
    try:
        response = requests.get(URL_LOGO, timeout=10)
        if response.status_code == 200:
            logo_buffer = io.BytesIO(response.content)
            logo = ImageReader(logo_buffer)
            c.drawImage(logo, margen_x, y - 15 * mm, width=40*mm, height=25*mm, mask='auto')
            logo_cargado = True
    except:
        pass

    if logo_cargado:
        centro_texto = width / 2 + 30
    else:
        centro_texto = width / 2

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(centro_texto, y, "Brianta Pet Luengo")
    y -= 6 * mm

    c.setFont("Helvetica", 10)
    c.drawCentredString(centro_texto, y, "Calle Gramoven, Casa Nro. 6, Barrio 19 de Abril")
    y -= 4 * mm
    c.drawCentredString(centro_texto, y, "Caracas, Distrito Capital, Zona Postal: 1010")
    y -= 4 * mm
    c.drawCentredString(centro_texto, y, "Correo: Briantasca@gmail.com | Telf./WhatsApp: 0412-290.05.40")
    y -= 6 * mm

    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(width - margen_x, y, "RIF.: 13533596-3")
    y -= 6 * mm

    # Fecha
    c.setFont("Helvetica", 9)
    x_fecha = width - 70 * mm
    ancho_celda = 14 * mm
    c.rect(x_fecha, y - 8 * mm, ancho_celda * 4, 6 * mm)
    c.drawString(x_fecha + 2 * mm, y - 6 * mm, "Lugar")
    c.drawString(x_fecha + ancho_celda + 2 * mm, y - 6 * mm, "Día")
    c.drawString(x_fecha + ancho_celda * 2 + 2 * mm, y - 6 * mm, "Mes")
    c.drawString(x_fecha + ancho_celda * 3 + 2 * mm, y - 6 * mm, "Año")
    y -= 6 * mm
    c.rect(x_fecha, y - 8 * mm, ancho_celda * 4, 6 * mm)
    c.drawString(x_fecha + 2 * mm, y - 6 * mm, ciudad)
    c.drawString(x_fecha + ancho_celda + 2 * mm, y - 6 * mm, f"{dia:02d}")
    c.drawString(x_fecha + ancho_celda * 2 + 2 * mm, y - 6 * mm, mes)
    c.drawString(x_fecha + ancho_celda * 3 + 2 * mm, y - 6 * mm, str(anio))
    y -= 12 * mm

    # ======== TÍTULO CON NÚMEROS EN ROJO ========
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margen_x, y, "FACTURA Nº ")

    c.setFillColor(red)
    c.drawString(margen_x + 90, y, numero_str)

    c.setFillColor(black)

    c.setFont("Helvetica", 12)
    c.drawRightString(width - margen_x - 40, y, "Nº de Control 00 - ")

    c.setFillColor(red)
    c.drawRightString(width - margen_x, y, control_str)

    c.setFillColor(black)
    y -= 8 * mm

    # =========== CLIENTE ===========
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margen_x, y, "Nombre o Razón Social:")
    c.setFont("Helvetica", 10)
    c.drawString(margen_x + 41.5 * mm, y, cliente)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margen_x + 90 * mm, y, "RIF./C.I.:")
    c.setFont("Helvetica", 10)
    c.drawString(margen_x + 105 * mm, y, rif)
    y -= 5 * mm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margen_x, y, "Domicilio Fiscal:")
    c.setFont("Helvetica", 10)
    c.drawString(margen_x + 29 * mm, y, domicilio)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margen_x + 90 * mm, y, "Teléfono:")
    c.setFont("Helvetica", 10)
    c.drawString(margen_x + 107 * mm, y, telefono)
    y -= 5 * mm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margen_x, y, "Condiciones de Pago:")
    c.setFont("Helvetica", 10)
    c.drawString(margen_x + 38 * mm, y, condiciones)
    y -= 8 * mm

    # =========== TABLA DE PRODUCTOS ===========
    c.setFont("Helvetica-Bold", 10)
    x_tabla = margen_x
    ancho_cols = [15 * mm, 101 * mm, 30 * mm, 30 * mm]
    y_tabla = y
    c.line(x_tabla, y_tabla, x_tabla + sum(ancho_cols), y_tabla)
    y_tabla -= 5 * mm
    c.drawString(x_tabla + 2 * mm, y_tabla, "Cant.")
    c.drawString(x_tabla + ancho_cols[0] + 2 * mm, y_tabla, "Descripción")
    c.drawString(x_tabla + ancho_cols[0] + ancho_cols[1] + 2 * mm, y_tabla, "P. Unit.")
    c.drawString(x_tabla + ancho_cols[0] + ancho_cols[1] + ancho_cols[2] + 2 * mm, y_tabla, "Monto")
    y_tabla -= 2 * mm
    c.line(x_tabla, y_tabla, x_tabla + sum(ancho_cols), y_tabla)
    y_tabla -= 5 * mm

    c.setFont("Helvetica", 9)
    for cantidad, descripcion, precio, monto in items:
        c.drawString(x_tabla + 2 * mm, y_tabla, str(cantidad))
        c.drawString(x_tabla + ancho_cols[0] + 2 * mm, y_tabla, descripcion[:30])
        c.drawRightString(x_tabla + ancho_cols[0] + ancho_cols[1] + 12 * mm, y_tabla, f"{simbolo}{precio:.2f}")
        c.drawRightString(x_tabla + ancho_cols[0] + ancho_cols[1] + ancho_cols[2] + 12 * mm, y_tabla, f"{simbolo}{monto:.2f}")
        y_tabla -= 5 * mm
        c.line(x_tabla, y_tabla + 5, x_tabla + sum(ancho_cols), y_tabla +5)
        y_tabla -= 3 * mm

    y = y_tabla - 8 * mm

    # =========== TOTALES ===========
    x_totales = width - 80 * mm
    y_totales = y
    c.rect(x_totales + 17, y_totales - 18.5 * mm, 60 * mm, 20 * mm)
    y_totales -= 5 * mm

    c.setFont("Helvetica", 10)
    c.drawString(x_totales + 10 * mm, y_totales, "Sub-Total")
    c.drawRightString(x_totales + 62 * mm, y_totales, f"{simbolo}{subtotal:.2f}")
    y_totales -= 5 * mm

    c.drawString(x_totales + 10 * mm, y_totales, f"IVA {iva_porcentaje}%")
    c.drawRightString(x_totales + 62 * mm, y_totales, f"{simbolo}{iva:.2f}")
    y_totales -= 5 * mm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x_totales + 10 * mm, y_totales, "TOTAL")
    c.drawRightString(x_totales + 62 * mm, y_totales, f"{simbolo}{total:.2f}")

    # =========== FORMA DE PAGO ===========
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margen_x, y, "Forma de Pago:")
    c.setFont("Helvetica", 9)
    opciones = ["Efectivo Bs.", "Efectivo USD.", "Tarjeta de Debito.", "Pago Movil", "Otros:"]
    y_pago = y - 5 * mm
    for opcion in opciones:
        c.drawString(margen_x + 1 * mm, y_pago, "☐ " + opcion)
        y_pago -= 4 * mm

    # =========== PIE ===========
    c.setFont("Helvetica", 8)
    c.drawCentredString(width / 2, 10 * mm, "Factura generada electrónicamente - Válida como comprobante fiscal")

    c.save()
    buffer.seek(0)

    items_str = "\n".join([f"{cant}x {desc} - {simbolo}{precio:.2f} c/u = {simbolo}{monto:.2f}" for cant, desc, precio, monto in items])
    guardar_factura(numero_str, control_str, cliente, rif, telefono, domicilio, condiciones, moneda, subtotal, iva, total, items_str)

    return buffer, numero_str, control_str, subtotal, iva, total

# =========== FUNCIONES AUXILIARES PARA PRODUCTOS ===========
def mostrar_lista_productos(productos):
    """Genera un mensaje con la lista de productos y botones para editar/eliminar"""
    if not productos:
        mensaje = "📭 No hay productos agregados aún."
        keyboard = [
            [InlineKeyboardButton("➕ Agregar producto", callback_data="producto_agregar")]
        ]
        return mensaje, InlineKeyboardMarkup(keyboard)

    mensaje = "📋 *LISTA DE PRODUCTOS:*\n\n"
    keyboard = []

    for idx, (cant, desc, precio) in enumerate(productos):
        mensaje += f"*{idx+1}.* {desc}\n"
        mensaje += f"   🔢 Cantidad: {cant}\n"
        mensaje += f"   💰 Precio: {precio:.2f}\n"
        mensaje += f"   📊 Subtotal: {cant * precio:.2f}\n\n"

        # Botones para editar y eliminar cada producto
        keyboard.append([
            InlineKeyboardButton(f"✏️ Editar #{idx+1}", callback_data=f"editar_{idx}"),
            InlineKeyboardButton(f"❌ Eliminar #{idx+1}", callback_data=f"eliminar_{idx}")
        ])

    # Botones de acción general
    keyboard.append([
        InlineKeyboardButton("➕ Agregar producto", callback_data="producto_agregar"),
        InlineKeyboardButton("✅ Terminar y generar", callback_data="producto_terminar")
    ])

    return mensaje, InlineKeyboardMarkup(keyboard)

def mostrar_edicion_producto(productos, idx):
    """Muestra la pantalla de edición de un producto específico"""
    if 0 <= idx < len(productos):
        cant, desc, precio = productos[idx]
        keyboard = [
            [InlineKeyboardButton("📝 Editar descripción", callback_data="editar_desc")],
            [InlineKeyboardButton("🔢 Editar cantidad", callback_data="editar_cant")],
            [InlineKeyboardButton("💰 Editar precio", callback_data="editar_precio")],
            [InlineKeyboardButton("🔙 Volver a la lista", callback_data="volver_menu")]
        ]
        mensaje = (
            f"✏️ *Editando producto #{idx+1}:*\n\n"
            f"📦 {desc}\n"
            f"🔢 Cantidad: {cant}\n"
            f"💰 Precio: {precio:.2f}\n\n"
            f"¿Qué deseas modificar?"
        )
        return mensaje, InlineKeyboardMarkup(keyboard)
    return None, None

# =========== HANDLERS DEL BOT ===========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    moneda_actual = context.user_data.get("moneda", MONEDA_POR_DEFECTO)
    await update.message.reply_text(
        f"¡Hola, mi amor! Soy tu FacturaBot 💋\n\n"
        f"💰 Moneda actual: {moneda_actual} {MONEDAS[moneda_actual]}\n\n"
        "Comandos disponibles:\n"
        "/nueva - Generar una factura paso a paso\n"
        "/historial - Ver facturas emitidas\n"
        "/resumen - Resumen mensual\n"
        "/eliminar NUMERO - Eliminar una factura\n"
        "/reset - Reiniciar contador\n"
        "/moneda - Cambiar moneda (Bs, USD, EUR)\n\n"
        "O usa /factura directo si ya sabes el formato."
    )

# =========== CONVERSACIÓN PARA NUEVA FACTURA ===========
async def nueva_factura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 Vamos a crear una factura. Dame el **Nombre o Razón Social** del cliente:")
    return NOMBRE

async def nombre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['cliente'] = update.message.text
    await update.message.reply_text("📝 Ahora el **RIF / C.I.** del cliente:")
    return RIF

async def rif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['rif'] = update.message.text
    await update.message.reply_text("📝 Dame el **Teléfono** del cliente:")
    return TELEFONO

async def telefono(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['telefono'] = update.message.text
    await update.message.reply_text("📝 Ahora el **Domicilio Fiscal** del cliente:")
    return DOMICILIO

async def domicilio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['domicilio'] = update.message.text
    await update.message.reply_text("📝 Escribe las **Condiciones de Pago** (ej: Contado, Crédito 30 días):")
    return CONDICIONES

async def condiciones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['condiciones'] = update.message.text
    context.user_data['productos'] = []
    context.user_data['producto_actual'] = {}
    context.user_data['editando_idx'] = None

    # Mostrar menú inicial con botones
    mensaje, reply_markup = mostrar_lista_productos(context.user_data['productos'])
    await update.message.reply_text(
        f"🛒 *GESTIÓN DE PRODUCTOS*\n\n{mensaje}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return PRODUCTO_MENU

async def producto_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # ===== AGREGAR PRODUCTO =====
    if data == "producto_agregar":
        await query.edit_message_text(
            "📦 Escribe la **Descripción o Nombre** del producto:"
        )
        return PRODUCTO_DESC

    # ===== TERMINAR Y GENERAR FACTURA =====
    elif data == "producto_terminar":
        if not context.user_data['productos']:
            await query.answer("❌ No hay productos. Agrega al menos uno.", show_alert=True)
            return PRODUCTO_MENU

        moneda = context.user_data.get("moneda", MONEDA_POR_DEFECTO)
        cliente = context.user_data['cliente']
        rif = context.user_data['rif']
        telefono = context.user_data['telefono']
        domicilio = context.user_data['domicilio']
        condiciones = context.user_data['condiciones']
        productos = context.user_data['productos']

        await query.edit_message_text(f"💰 Generando factura en {moneda} {MONEDAS[moneda]}...")

        try:
            pdf_buffer, numero, control, subtotal, iva, total = generar_pdf_factura(
                cliente, rif, telefono, domicilio, condiciones, productos, moneda
            )

            cliente_limpio = cliente.replace(" ", "_")
            await query.message.reply_document(
                document=pdf_buffer,
                filename=f"factura_{numero}_{cliente_limpio}.pdf",
                caption=(
                    f"✅ *Factura generada con éxito* 💋\n\n"
                    f"👤 *Cliente:* {cliente}\n"
                    f"📄 *Número de factura:* {numero}\n"
                    f"💰 *Moneda:* {moneda}"
                ),
                parse_mode='Markdown'
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Error al generar la factura: {str(e)}")

        context.user_data.clear()
        return ConversationHandler.END

    # ===== ELIMINAR PRODUCTO =====
    elif data.startswith("eliminar_"):
        idx = int(data.split("_")[1])
        productos = context.user_data['productos']
        if 0 <= idx < len(productos):
            producto_eliminado = productos.pop(idx)
            mensaje, reply_markup = mostrar_lista_productos(productos)
            await query.edit_message_text(
                f"✅ Producto eliminado: {producto_eliminado[1]}\n\n"
                f"🛒 *GESTIÓN DE PRODUCTOS*\n\n{mensaje}",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await query.answer("❌ Producto no encontrado.", show_alert=True)
        return PRODUCTO_MENU

    # ===== EDITAR PRODUCTO - SELECCIONAR QUÉ EDITAR =====
    elif data.startswith("editar_"):
        idx = int(data.split("_")[1])
        context.user_data['editando_idx'] = idx
        productos = context.user_data['productos']
        if 0 <= idx < len(productos):
            mensaje, reply_markup = mostrar_edicion_producto(productos, idx)
            await query.edit_message_text(
                mensaje,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return EDITAR_SELECCION
        else:
            await query.answer("❌ Producto no encontrado.", show_alert=True)
            return PRODUCTO_MENU

    # ===== VOLVER AL MENÚ =====
    elif data == "volver_menu":
        mensaje, reply_markup = mostrar_lista_productos(context.user_data['productos'])
        await query.edit_message_text(
            f"🛒 *GESTIÓN DE PRODUCTOS*\n\n{mensaje}",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return PRODUCTO_MENU

    return PRODUCTO_MENU

# ===== EDITAR SELECCIÓN =====
async def editar_seleccion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    idx = context.user_data['editando_idx']
    productos = context.user_data['productos']

    if data == "volver_menu":
        mensaje, reply_markup = mostrar_lista_productos(productos)
        await query.edit_message_text(
            f"🛒 *GESTIÓN DE PRODUCTOS*\n\n{mensaje}",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return PRODUCTO_MENU

    elif data == "editar_desc":
        await query.edit_message_text(
            f"✏️ Escribe la **nueva descripción** para el producto:\n"
            f"(Actual: {productos[idx][1]})"
        )
        return EDITAR_DESC

    elif data == "editar_cant":
        await query.edit_message_text(
            f"🔢 Escribe la **nueva cantidad** para el producto:\n"
            f"(Actual: {productos[idx][0]})"
        )
        return EDITAR_CANT

    elif data == "editar_precio":
        await query.edit_message_text(
            f"💰 Escribe el **nuevo precio** para el producto:\n"
            f"(Actual: {productos[idx][2]:.2f})"
        )
        return EDITAR_PRECIO

    return EDITAR_SELECCION

# ===== GUARDAR EDICIONES =====
async def editar_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = context.user_data['editando_idx']
    productos = context.user_data['productos']
    if 0 <= idx < len(productos):
        cant, _, precio = productos[idx]
        productos[idx] = (cant, update.message.text, precio)
        await update.message.reply_text(f"✅ Descripción actualizada correctamente.")

    mensaje, reply_markup = mostrar_edicion_producto(productos, idx)
    await update.message.reply_text(
        mensaje,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return EDITAR_SELECCION

async def editar_cant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        nueva_cant = float(update.message.text)
        if nueva_cant <= 0:
            await update.message.reply_text("❌ La cantidad debe ser mayor a 0.")
            return EDITAR_CANT

        idx = context.user_data['editando_idx']
        productos = context.user_data['productos']
        if 0 <= idx < len(productos):
            _, desc, precio = productos[idx]
            productos[idx] = (nueva_cant, desc, precio)
            await update.message.reply_text(f"✅ Cantidad actualizada a {nueva_cant}.")
    except ValueError:
        await update.message.reply_text("❌ Cantidad no válida. Escribe un número.")
        return EDITAR_CANT

    mensaje, reply_markup = mostrar_edicion_producto(productos, idx)
    await update.message.reply_text(
        mensaje,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return EDITAR_SELECCION

async def editar_precio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        nuevo_precio = float(update.message.text)
        if nuevo_precio <= 0:
            await update.message.reply_text("❌ El precio debe ser mayor a 0.")
            return EDITAR_PRECIO

        idx = context.user_data['editando_idx']
        productos = context.user_data['productos']
        if 0 <= idx < len(productos):
            cant, desc, _ = productos[idx]
            productos[idx] = (cant, desc, nuevo_precio)
            await update.message.reply_text(f"✅ Precio actualizado a {nuevo_precio:.2f}.")
    except ValueError:
        await update.message.reply_text("❌ Precio no válido. Escribe un número.")
        return EDITAR_PRECIO

    mensaje, reply_markup = mostrar_edicion_producto(productos, idx)
    await update.message.reply_text(
        mensaje,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return EDITAR_SELECCION

# =========== AGREGAR PRODUCTO (FLUJO ORIGINAL) ===========
async def producto_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['producto_actual']['desc'] = update.message.text
    await update.message.reply_text("🔢 Ahora escribe la **Cantidad** del producto (solo números):")
    return PRODUCTO_CANT

async def producto_cant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cantidad = float(update.message.text)
        if cantidad <= 0:
            await update.message.reply_text("❌ La cantidad debe ser mayor a 0. Escribe un número válido:")
            return PRODUCTO_CANT
        context.user_data['producto_actual']['cant'] = cantidad
        await update.message.reply_text("💰 Ahora escribe el **Precio Unitario** del producto (solo números, usa punto para decimales):")
        return PRODUCTO_PRECIO
    except ValueError:
        await update.message.reply_text("❌ Cantidad no válida. Escribe un número (ej: 2, 3.5):")
        return PRODUCTO_CANT

async def producto_precio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        precio = float(update.message.text)
        if precio <= 0:
            await update.message.reply_text("❌ El precio debe ser mayor a 0. Escribe un número válido:")
            return PRODUCTO_PRECIO

        desc = context.user_data['producto_actual']['desc']
        cant = context.user_data['producto_actual']['cant']
        context.user_data['productos'].append((cant, desc, precio))
        context.user_data['producto_actual'] = {}

        mensaje, reply_markup = mostrar_lista_productos(context.user_data['productos'])
        await update.message.reply_text(
            f"✅ Producto agregado correctamente.\n\n"
            f"🛒 *GESTIÓN DE PRODUCTOS*\n\n{mensaje}",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return PRODUCTO_MENU

    except ValueError:
        await update.message.reply_text("❌ Precio no válido. Escribe un número (ej: 15.50, 100):")
        return PRODUCTO_PRECIO

# =========== CANCELAR ===========
async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Proceso cancelado. Usa /nueva para empezar de nuevo.")
    context.user_data.clear()
    return ConversationHandler.END

# =========== OTROS COMANDOS ===========
async def factura_directa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 6:
        await update.message.reply_text(
            "❌ Formato: /factura 'Cliente' 'RIF' 'Teléfono' 'Domicilio' 'Condiciones' 'Producto1|Cantidad|Precio' ..."
        )
        return
    try:
        partes = shlex.split(" ".join(args))
    except:
        await update.message.reply_text("❌ Error en comillas. Usa comillas simples para cada campo.")
        return
    if len(partes) < 6:
        await update.message.reply_text("❌ Faltan datos.")
        return
    cliente, rif, telefono, domicilio, condiciones = partes[:5]
    productos_raw = partes[5:]
    productos = []
    for raw in productos_raw:
        if '|' not in raw:
            await update.message.reply_text(f"❌ Producto mal formateado: '{raw}'")
            return
        desc, cant_str, prec_str = raw.split('|')
        try:
            cant = float(cant_str.strip())
            precio = float(prec_str.strip())
        except ValueError:
            await update.message.reply_text(f"❌ Cantidad o precio inválido en: '{raw}'")
            return
        productos.append((cant, desc.strip(), precio))
    if not productos:
        await update.message.reply_text("❌ Agrega al menos un producto.")
        return
    moneda = context.user_data.get("moneda", MONEDA_POR_DEFECTO)
    try:
        pdf_buffer, numero, control, subtotal, iva, total = generar_pdf_factura(
            cliente, rif, telefono, domicilio, condiciones, productos, moneda
        )

        cliente_limpio = cliente.replace(" ", "_")
        await update.message.reply_document(
            document=pdf_buffer,
            filename=f"factura_{numero}_{cliente_limpio}.pdf",
            caption=(
                f"✅ *Factura generada con éxito* 💋\n\n"
                f"👤 *Cliente:* {cliente}\n"
                f"📄 *Número de factura:* {numero}\n"
                f"💰 *Moneda:* {moneda}"
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    filtro = " ".join(context.args) if context.args else None
    facturas = obtener_facturas(filtro)
    if not facturas:
        await update.message.reply_text("📭 No hay facturas.")
        return
    mensaje = "📋 *HISTORIAL*\n\n"
    for f in facturas[:10]:
        mensaje += f"🔹 N° {f[1]} - {f[3]} - {f[11][:10]} - {f[9]} {f[8]:.2f}\n"
    if len(facturas) > 10:
        mensaje += "\n... más. Usa /historial 'nombre' para filtrar."
    await update.message.reply_text(mensaje)

async def resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_facturas, total_monto = resumen_mensual()
    await update.message.reply_text(
        f"📊 *RESUMEN DEL MES*\n\nFacturas: {total_facturas}\nMonto total: {total_monto:.2f}"
    )

async def eliminar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usa: /eliminar NUMERO")
        return
    numero = context.args[0]
    try:
        eliminar_factura(numero)
        await update.message.reply_text(f"✅ Factura N° {numero} eliminada.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

# NUEVA VERSIÓN: Handler del comando /reset con respuesta detallada
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nuevo_inicio = resetear_contador()
    if nuevo_inicio == 0:
        await update.message.reply_text("🔄 Contador reiniciado a 0 (No hay facturas en la base de datos).")
    else:
        await update.message.reply_text(
            f"🔄 Contador sincronizado con la base de datos.\n"
            f"La última factura real es la N° {str(nuevo_inicio).zfill(6)}.\n"
            f"La siguiente factura a generar será la N° {str(nuevo_inicio + 1).zfill(6)}."
        )

async def moneda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        moneda_actual = context.user_data.get("moneda", MONEDA_POR_DEFECTO)
        await update.message.reply_text(
            f"💰 Moneda actual: {moneda_actual} {MONEDAS[moneda_actual]}\n"
            "Usa: /moneda Bs | /moneda USD | /moneda EUR"
        )
        return
    mon = context.args[0].upper()
    if mon not in MONEDAS:
        await update.message.reply_text("❌ Opciones: Bs, USD, EUR")
        return
    context.user_data["moneda"] = mon
    await update.message.reply_text(f"✅ Moneda cambiada a {mon} {MONEDAS[mon]} para todas las facturas futuras.")

# =========== MAIN ===========
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    # Conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("nueva", nueva_factura)],
        states={
            NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, nombre)],
            RIF: [MessageHandler(filters.TEXT & ~filters.COMMAND, rif)],
            TELEFONO: [MessageHandler(filters.TEXT & ~filters.COMMAND, telefono)],
            DOMICILIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, domicilio)],
            CONDICIONES: [MessageHandler(filters.TEXT & ~filters.COMMAND, condiciones)],
            PRODUCTO_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, producto_desc)],
            PRODUCTO_CANT: [MessageHandler(filters.TEXT & ~filters.COMMAND, producto_cant)],
            PRODUCTO_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, producto_precio)],
            PRODUCTO_MENU: [CallbackQueryHandler(producto_menu, pattern="^(producto_|editar_|eliminar_)")],
            EDITAR_SELECCION: [CallbackQueryHandler(editar_seleccion, pattern="^(editar_|volver_menu)")],
            EDITAR_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_desc)],
            EDITAR_CANT: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_cant)],
            EDITAR_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_precio)]
        },
        fallbacks=[CommandHandler("cancel", cancelar)]
    )
    app.add_handler(conv_handler)

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("factura", factura_directa))
    app.add_handler(CommandHandler("historial", historial))
    app.add_handler(CommandHandler("resumen", resumen))
    app.add_handler(CommandHandler("eliminar", eliminar))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("moneda", moneda))

    print("🔥 FacturaBot activo, mi rey. Usa /nueva para empezar.")

    try:
        app.run_polling()
    except Exception as e:
        print(f"❌ Error en el bot: {e}")
        print("🔄 Reiniciando en 5 segundos...")
        time.sleep(5)
        main()

if __name__ == "__main__":
    main()
