"""Generador de PDF de facturas (ReportLab).

Mejoras respecto a la versión anterior:
- Constantes de layout en mm (arregla la mezcla píxeles/mm del cuadro de totales
  y de los títulos con los números en rojo).
- Ajuste de línea (word-wrap) para descripciones y domicilios largos.
- Casilla Unicode ☐ sustituida por una caja dibujada (Helvetica no soporta ese glifo).
- Logo descargado UNA VEZ y cacheado en disco/memoria; si falla, se omite sin romper nada.
- Cálculos con redondeo decimal correcto (banker's safe) y desglose IVA.
- La función NO toca la base de datos ni el contador: recibe el número ya reservado
  (separación de responsabilidades; hace main.py más simple y testeable).
"""

import io
import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import requests
from reportlab.lib.colors import black, red
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)

# ---------- Layout (todo en mm para consistencia) ----------
MARGEN_X = 20 * mm
MARGEN_Y = 20 * mm
ANCHO_TABLA = 176 * mm
COL_CANT = 15 * mm
COL_DESC = 101 * mm
COL_PUNIT = 30 * mm
COL_MONTO = 30 * mm
ALTO_FILA = 5 * mm
CUADRO_TOTALES_ANCHO = 60 * mm
FUENTE = "Helvetica"
FUENTE_BOLD = "Helvetica-Bold"

MESES_ESP = {
    "January": "Enero", "February": "Febrero", "March": "Marzo",
    "April": "Abril", "May": "Mayo", "June": "Junio",
    "July": "Julio", "August": "Agosto", "September": "Septiembre",
    "October": "Octubre", "November": "Noviembre", "December": "Diciembre",
}


def money(value: float) -> Decimal:
    """Redondea a 2 decimales de forma determinista (evita 0.1+0.2 = 0.30000000004)."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calcular_totales(productos, iva_porcentaje: float):
    """Devuelve (items, subtotal, iva, total) con montos redondeados a 2 decimales."""
    items = []
    subtotal = Decimal("0.00")
    for cantidad, descripcion, precio in productos:
        monto = money(cantidad * precio)
        subtotal += monto
        items.append({"cantidad": cantidad, "descripcion": descripcion,
                      "precio": float(money(precio)), "monto": float(monto)})
    iva = money(float(subtotal) * iva_porcentaje / 100)
    total = money(float(subtotal + iva))
    return items, float(subtotal), float(iva), float(total)


_logo_cache: dict[str, ImageReader | None] = {}


def get_logo(url: str | None, cache_dir: Path):
    """Descarga el logo una sola vez y lo cachea en memoria y en disco.

    Devuelve un ImageReader o None si no está disponible (nunca lanza excepción).
    """
    if not url:
        return None
    if url in _logo_cache:
        return _logo_cache[url]

    imagen = None
    try:
        cache_path = cache_dir / f"logo_{abs(hash(url)) & 0xFFFFFFFF:08x}.img"
        contenido = None
        if cache_path.exists():
            contenido = cache_path.read_bytes()
        else:
            respuesta = requests.get(url, timeout=10)
            respuesta.raise_for_status()
            contenido = respuesta.content
            cache_path.write_bytes(contenido)
        imagen = ImageReader(io.BytesIO(contenido))
    except (requests.RequestException, OSError) as exc:
        logger.warning("No se pudo cargar el logo (%s); se genera el PDF sin logo.", exc)
    _logo_cache[url] = imagen
    return imagen


def _wrap(c: canvas.Canvas, texto: str, fuente: str, tam: int, ancho_mm: float) -> list[str]:
    """Divide un texto en líneas que quepan en ancho_mm."""
    palabras = str(texto).split()
    lineas, actual = [], ""
    for palabra in palabras:
        candidato = f"{actual} {palabra}".strip()
        if c.stringWidth(candidato, fuente, tam) <= ancho_mm * mm or not actual:
            actual = candidato
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas or [""]


def generar_pdf_factura(
    numero: int,
    cliente: str,
    rif: str,
    telefono: str,
    domicilio: str,
    condiciones: str,
    productos,
    moneda: str,
    simbolo: str,
    emisor,
    logo_url: str | None = None,
    logo_cache_dir: Path = Path(".cache"),
    orden: str | None = None,
):
    """Genera el PDF de la factura y devuelve (buffer, items, subtotal, iva, total).

    El número debe venir YA RESERVADO por la capa de datos (db.reservar_numero()).
    """
    numero_str = str(numero).zfill(6)
    control_str = numero_str

    ahora = datetime.now()
    mes = MESES_ESP.get(ahora.strftime("%B"), ahora.strftime("%B"))

    items, subtotal, iva, total = calcular_totales(productos, emisor.iva_porcentaje)

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - MARGEN_Y

    def fmt(valor: float) -> str:
        return f"{simbolo}{money(valor):,}".replace(",", ".")

    # ---------- Cabecera con logo ----------
    logo = get_logo(logo_url, logo_cache_dir)
    centro_texto = width / 2 + (25 * mm if logo else 0)
    if logo:
        c.drawImage(logo, MARGEN_X, y - 25 * mm, width=40 * mm, height=25 * mm, mask="auto")

    c.setFont(FUENTE_BOLD, 16)
    c.drawCentredString(centro_texto, y, emisor.nombre)
    y -= 6 * mm

    c.setFont(FUENTE, 9)
    linea_direccion = emisor.direccion
    contacto = " | ".join(x for x in [emisor.correo, f"Telf.: {emisor.telefono}"] if x)
    c.drawCentredString(centro_texto, y, linea_direccion)
    y -= 4 * mm
    c.drawCentredString(centro_texto, y, emisor.ciudad)
    y -= 4 * mm
    if contacto:
        c.drawCentredString(centro_texto, y, contacto)
        y -= 2 * mm

    c.setFont(FUENTE_BOLD, 12)
    c.drawRightString(width - MARGEN_X, y, f"RIF.: {emisor.rif}" if emisor.rif else "")
    y -= 8 * mm

    # ---------- Caja de fecha ----------
    x_fecha = width - 70 * mm
    ancho_celda = 14 * mm
    etiquetas = ["Lugar", "Día", "Mes", "Año"]
    valores = [emisor.ciudad, f"{ahora.day:02d}", mes, str(ahora.year)]
    for fila_etiquetas, fila_valores in ((etiquetas, None), (None, valores)):
        c.rect(x_fecha, y - 8 * mm, ancho_celda * 4, 6 * mm)
        c.setFont(FUENTE, 8)
        for i in range(4):
            texto = (fila_etiquetas or fila_valores)[i]
            c.drawString(x_fecha + ancho_celda * i + 2 * mm, y - 6 * mm, texto)
        y -= 6 * mm
    y -= 6 * mm

    # ---------- Título con números en rojo ----------
    c.setFont(FUENTE_BOLD, 14)
    c.setFillColor(black)
    etiqueta_num = "FACTURA Nº "
    c.drawString(MARGEN_X, y, etiqueta_num)
    c.setFillColor(red)
    c.drawString(MARGEN_X + c.stringWidth(etiqueta_num, FUENTE_BOLD, 14), y, numero_str)
    c.setFillColor(black)

    c.setFont(FUENTE, 11)
    etiqueta_ctrl = "Nº de Control 00 - "
    c.drawRightString(width - MARGEN_X - 14 * mm, y, etiqueta_ctrl)
    c.setFillColor(red)
    c.drawRightString(width - MARGEN_X, y, control_str)
    c.setFillColor(black)
    if orden:
        c.setFont(FUENTE, 9)
        c.drawCentredString(width / 2, y - 5 * mm, f"Nº de Orden: {orden}")
    y -= 10 * mm

    # ---------- Datos del cliente (con word-wrap) ----------
    def campo_izq(etiqueta: str, valor: str, ancho_valor_mm: float,
                  etiqueta_derecha: str = "", valor_derecha: str = ""):
        """Escribe etiqueta/valor a la izquierda (con wrap) y opcionalmente a la derecha."""
        nonlocal y
        c.setFont(FUENTE_BOLD, 10)
        c.drawString(MARGEN_X, y, etiqueta)
        c.setFont(FUENTE, 10)
        lineas = _wrap(c, valor, FUENTE, 10, ancho_valor_mm)[:2]
        for j, linea in enumerate(lineas):
            c.drawString(MARGEN_X + c.stringWidth(etiqueta, FUENTE_BOLD, 10) + 2 * mm,
                         y - j * 4 * mm, linea)
        if etiqueta_derecha:
            c.setFont(FUENTE_BOLD, 10)
            c.drawString(MARGEN_X + 95 * mm, y, etiqueta_derecha)
            c.setFont(FUENTE, 10)
            c.drawString(MARGEN_X + 112 * mm, y, valor_derecha)
        y -= 4 * mm * max(1, len(lineas)) + 1.5 * mm

    campo_izq("Nombre o Razón Social:", cliente, 60 * mm, "RIF./C.I.:", rif)
    campo_izq("Domicilio Fiscal:", domicilio, 60 * mm, "Teléfono:", telefono)
    campo_izq("Condiciones de Pago:", condiciones, 150 * mm)
    y -= 3 * mm

    # ---------- Tabla de productos ----------
    x_tabla = MARGEN_X
    anchos = [COL_CANT, COL_DESC, COL_PUNIT, COL_MONTO]
    c.setFont(FUENTE_BOLD, 10)
    c.line(x_tabla, y, x_tabla + ANCHO_TABLA, y)
    y -= ALTO_FILA
    for encabezado, offset in zip(("Cant.", "Descripción", "P. Unit.", "Monto"),
                                  (0, COL_CANT, COL_CANT + COL_DESC, COL_CANT + COL_DESC + COL_PUNIT)):
        c.drawString(x_tabla + offset + 2 * mm, y, encabezado)
    y -= 2 * mm
    c.line(x_tabla, y, x_tabla + ANCHO_TABLA, y)
    y -= ALTO_FILA

    c.setFont(FUENTE, 9)
    for item in items:
        lineas = _wrap(c, item["descripcion"], FUENTE, 9, COL_DESC - 4 * mm)
        alto_bloque = ALTO_FILA * len(lineas)
        c.drawString(x_tabla + 2 * mm, y - (len(lineas) - 1) * 3.5 * mm,
                     f"{item['cantidad']:g}")
        for j, linea in enumerate(lineas):
            c.drawString(x_tabla + COL_CANT + 2 * mm, y - j * 3.8 * mm, linea)
        c.drawRightString(x_tabla + COL_CANT + COL_DESC + COL_PUNIT - 2 * mm, y,
                          fmt(item["precio"]))
        c.drawRightString(x_tabla + ANCHO_TABLA - 2 * mm, y, fmt(item["monto"]))
        y -= alto_bloque
        c.line(x_tabla, y, x_tabla + ANCHO_TABLA, y)
        y -= 2 * mm

    y -= 6 * mm

    # ---------- Cuadro de totales (en mm puros) ----------
    x_totales = width - 80 * mm
    alto_cuadro = 20 * mm
    if y - alto_cuadro < 40 * mm:  # salto de página si no cabe
        c.showPage()
        y = height - MARGEN_Y
    c.rect(x_totales, y - alto_cuadro, CUADRO_TOTALES_ANCHO, alto_cuadro)
    yy = y - 5 * mm
    c.setFont(FUENTE, 10)
    c.drawString(x_totales + 4 * mm, yy, "Sub-Total")
    c.drawRightString(x_totales + CUADRO_TOTALES_ANCHO - 4 * mm, yy, fmt(subtotal))
    yy -= 5 * mm
    c.drawString(x_totales + 4 * mm, yy, f"IVA {emisor.iva_porcentaje:g}%")
    c.drawRightString(x_totales + CUADRO_TOTALES_ANCHO - 4 * mm, yy, fmt(iva))
    yy -= 5 * mm
    c.setFont(FUENTE_BOLD, 11)
    c.drawString(x_totales + 4 * mm, yy, "TOTAL")
    c.drawRightString(x_totales + CUADRO_TOTALES_ANCHO - 4 * mm, yy, fmt(total))

    # ---------- Forma de pago (cajas dibujadas, no glifos Unicode) ----------
    c.setFont(FUENTE_BOLD, 10)
    c.drawString(MARGEN_X, y, "Forma de Pago:")
    opciones = ["Efectivo Bs.", "Efectivo USD.", "Tarjeta de Débito",
                "Pago Móvil", "Transferencia", "Otros: ____________"]
    y_pago = y - 5 * mm
    c.setFont(FUENTE, 9)
    for i, opcion in enumerate(opciones):
        col = MARGEN_X + (i % 3) * 60 * mm
        if i % 3 == 0 and i > 0:
            y_pago -= 4 * mm
        c.rect(col, y_pago - 0.5 * mm, 3 * mm, 3 * mm)  # casilla
        c.drawString(col + 4.5 * mm, y_pago, opcion)
    y -= 20 * mm

    # ---------- Pie ----------
    c.setFont(FUENTE, 8)
    c.drawCentredString(width / 2, 14 * mm, emisor.leyenda_fiscal)
    c.drawCentredString(width / 2, 10 * mm,
                        "Factura generada electrónicamente - Válida como comprobante fiscal")

    c.save()
    buffer.seek(0)
    return buffer, items, subtotal, iva, total
