import sys, os
sys.path.insert(0, "/workspace")

from datetime import datetime
from pathlib import Path
from facturabot.config import EmisorConfig
from facturabot.pdf import generar_pdf_factura

emisor = EmisorConfig(
    nombre="Carpintería Miranda & Hijos, C.A.",
    rif="J-40125873-2",
    direccion="Av. Principal con Calle El Cedro, Galpón 7, Zona Industrial Turumo",
    ciudad="Valencia, Carabobo, Zona Postal: 2001",
    telefono="+58 414-555-1234 / +58 241-555-9876",
    correo="ventas@carpinteriamiranda.com.ve",
    iva_porcentaje=16.0,
)

# productos = lista de (cantidad, descripcion, precio_unitario)
productos = [
    (1, "Cocina integral en melamina blanca 3.2 m lineales (gaveteros con cierre suave, tope de cuarzo)", 850.0),
    (1, "Closet vestier 2 puertas corredizas en MDF laqueado 2.4 x 2.2 m con iluminacion LED interna", 620.0),
    (1, "Mesa de comedor en macizo de teca barnizada 1.80 x 0.90 m", 380.0),
    (6, "Silla tipo Windsor en madera de pino pintada", 45.0),
    (2, "Librera modular 5 niveles en cedro 2.0 x 0.8 m", 140.0),
]

buffer, items, subtotal, iva, total = generar_pdf_factura(
    numero=123,
    cliente="Residencias Villa Real, C.A.",
    rif="J-30874521-6",
    telefono="+58 424-888-7766",
    domicilio="Urb. La Esmeralda, Torre B, Apto. 4-B, Av. Bolivar Norte, Valencia, Carabobo",
    condiciones="Forma de pago: Transferencia bancaria (Banesco). Tiempo de entrega: 25 dias habiles. Validez de la cotizacion: 15 dias.",
    productos=productos,
    moneda="USD",
    simbolo="$",
    emisor=emisor,
    logo_url=None,
    logo_cache_dir=Path("/tmp/.cache"),
    orden="OC-2026-0455",
)

destino = "/workspace/ejemplo_factura_carpinteria.pdf"
with open(destino, "wb") as f:
    f.write(buffer.getvalue())
print(f"OK -> {destino} ({os.path.getsize(destino)} bytes)")
print(f"Subtotal: ${subtotal:.2f} | IVA 16%: ${iva:.2f} | TOTAL: ${total:.2f}")
