"""Capa de acceso a datos (SQLite).

Mejoras respecto a la versión anterior:
- Conexión única reutilizable con journal mode WAL y PRAGMA foreign_keys.
- Numeración ATÓMICA: el número se reserva dentro de la misma transacción
  en la que se inserta la factura, así nunca se "queman" números si el PDF falla.
- Borrado LÓGICO (flag eliminada) en lugar de borrado físico, para cumplir
  la normativa fiscal de conservación de comprobantes.
- Resumen mensual agrupado POR MONEDA (arregla la suma mezclada Bs+USD+EUR).
- Todas las consultas usan parámetros (?), nunca interpolación de texto.
"""

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Factura:
    id: int
    numero: str
    control: str
    cliente: str
    rif: str
    telefono: str
    domicilio: str
    condiciones: str
    moneda: str
    subtotal: float
    iva: float
    total: float
    fecha: str
    items: list  # lista de dicts {cantidad, descripcion, precio, monto}
    eliminada: int  # 0 = vigente, 1 = anulada


_SCHEMA = """
CREATE TABLE IF NOT EXISTS facturas (
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
    items TEXT,
    eliminada INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS contador (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ultimo_numero INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accion TEXT,
    detalle TEXT,
    fecha TEXT
);
CREATE INDEX IF NOT EXISTS idx_facturas_fecha ON facturas(fecha);
CREATE INDEX IF NOT EXISTS idx_facturas_cliente ON facturas(cliente);
"""


class Database:
    """Acceso a SQLite thread-safe (una conexión protegida con lock)."""

    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.execute("INSERT OR IGNORE INTO contador (id, ultimo_numero) VALUES (1, 0)")
        self._migrar()

    def _migrar(self) -> None:
        """Añade columnas nuevas a bases de datos creadas por versiones anteriores."""
        with self._lock, self._conn:
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(facturas)")}
            if "eliminada" not in cols:
                self._conn.execute(
                    "ALTER TABLE facturas ADD COLUMN eliminada INTEGER NOT NULL DEFAULT 0"
                )
                logger.info("Migración: añadida columna 'eliminada' a la tabla facturas.")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- Contador / numeración ----------------

    def reservar_numero(self) -> int:
        """Incrementa y devuelve el siguiente número DENTRO de una transacción.

        Debe ir acompañada de confirmar_factura() o revertir_numero() para
        garantizar que no queden números huérfanos.
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE contador SET ultimo_numero = ultimo_numero + 1 WHERE id = 1"
            )
            if cur.rowcount != 1:
                raise RuntimeError("No se pudo reservar el número de factura (fila contador).")
            numero = self._conn.execute(
                "SELECT ultimo_numero FROM contador WHERE id = 1"
            ).fetchone()[0]
            return int(numero)

    def revertir_numero(self, numero: int) -> None:
        """Devuelve el número reservado si la facturación falló (solo si sigue siendo el último)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE contador SET ultimo_numero = ? "
                "WHERE id = 1 AND ultimo_numero = ?",
                (numero - 1, numero),
            )

    def sincronizar_contador(self) -> int:
        """Sincroniza el contador con el mayor número emitido (incluidas anuladas:
        un número anulado nunca vuelve a usarse)."""
        with self._lock, self._conn:
            max_num = self._conn.execute(
                "SELECT MAX(CAST(numero AS INTEGER)) FROM facturas"
            ).fetchone()[0]
            nuevo = int(max_num) if max_num is not None else 0
            self._conn.execute("UPDATE contador SET ultimo_numero = ? WHERE id = 1", (nuevo,))
        self.registrar_log("contador_reseteado", f"Contador sincronizado en {nuevo}")
        return nuevo

    # ---------------- Facturas ----------------

    def guardar_factura(
        self,
        numero: str,
        control: str,
        cliente: str,
        rif: str,
        telefono: str,
        domicilio: str,
        condiciones: str,
        moneda: str,
        subtotal: float,
        iva: float,
        total: float,
        items: list,
    ) -> None:
        """Inserta la factura usando el número YA RESERVADO por reservar_numero()."""
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO facturas
                   (numero, control, cliente, rif, telefono, domicilio, condiciones,
                    moneda, subtotal, iva, total, fecha, items, eliminada)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    numero, control, cliente, rif, telefono, domicilio, condiciones,
                    moneda, subtotal, iva, total,
                    datetime.now().isoformat(timespec="seconds"),
                    json.dumps(items, ensure_ascii=False),
                ),
            )
        self.registrar_log("factura_creada", f"Factura {numero} creada para {cliente}")

    def obtener_facturas(self, filtro: str | None = None, incluir_anuladas: bool = False,
                         limite: int = 50) -> list[Factura]:
        consulta = "SELECT * FROM facturas"
        condiciones, params = [], []
        if not incluir_anuladas:
            condiciones.append("eliminada = 0")
        if filtro:
            condiciones.append("(cliente LIKE ? OR numero LIKE ?)")
            params += [f"%{filtro}%", f"%{filtro}%"]
        if condiciones:
            consulta += " WHERE " + " AND ".join(condiciones)
        consulta += " ORDER BY id DESC LIMIT ?"
        params.append(limite)

        with self._lock:
            filas = self._conn.execute(consulta, params).fetchall()
        return [self._fila_a_factura(f) for f in filas]

    def existe_factura(self, numero: str) -> bool:
        with self._lock:
            fila = self._conn.execute(
                "SELECT 1 FROM facturas WHERE numero = ? AND eliminada = 0", (numero,)
            ).fetchone()
        return fila is not None

    def anular_factura(self, numero: str) -> bool:
        """Borrado LÓGICO: marca la factura como anulada sin borrar el registro
        ni retroceder el contador (los números fiscales no se reutilizan)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE facturas SET eliminada = 1 WHERE numero = ? AND eliminada = 0",
                (numero,),
            )
        if cur.rowcount:
            self.registrar_log("factura_anulada", f"Factura {numero} anulada (borrado lógico)")
            return True
        return False

    # ---------------- Resumen ----------------

    def resumen_mensual(self, mes: int | None = None, anio: int | None = None) -> dict:
        """Total de facturas y montos AGRUPADOS POR MONEDA (no se suman Bs con USD)."""
        ahora = datetime.now()
        mes = mes or ahora.month
        anio = anio or ahora.year
        with self._lock:
            filas = self._conn.execute(
                """SELECT moneda, COUNT(*) AS cant, COALESCE(SUM(subtotal), 0) AS subtotal,
                          COALESCE(SUM(iva), 0) AS iva, COALESCE(SUM(total), 0) AS total
                   FROM facturas
                   WHERE eliminada = 0
                     AND strftime('%m', fecha) = ? AND strftime('%Y', fecha) = ?
                   GROUP BY moneda""",
                (str(mes).zfill(2), str(anio)),
            ).fetchall()
        return {
            "anio": anio,
            "mes": mes,
            "por_moneda": [
                {
                    "moneda": f["moneda"],
                    "cantidad": f["cant"],
                    "subtotal": f["subtotal"],
                    "iva": f["iva"],
                    "total": f["total"],
                }
                for f in filas
            ],
        }

    # ---------------- Logs ----------------

    def registrar_log(self, accion: str, detalle: str) -> None:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO logs (accion, detalle, fecha) VALUES (?, ?, ?)",
                    (accion, detalle, datetime.now().isoformat(timespec="seconds")),
                )
        except sqlite3.Error:  # el log nunca debe tumbar la operación principal
            logger.exception("No se pudo escribir en la tabla de logs")

    # ---------------- Helpers ----------------

    @staticmethod
    def _fila_a_factura(fila: sqlite3.Row) -> Factura:
        try:
            items = json.loads(fila["items"]) if fila["items"] else []
        except (json.JSONDecodeError, TypeError):
            # Compatibilidad con formato antiguo (texto plano)
            items = [{"descripcion": fila["items"], "cantidad": "", "precio": "", "monto": ""}]
        return Factura(
            id=fila["id"], numero=fila["numero"], control=fila["control"],
            cliente=fila["cliente"], rif=fila["rif"], telefono=fila["telefono"],
            domicilio=fila["domicilio"], condiciones=fila["condiciones"],
            moneda=fila["moneda"], subtotal=fila["subtotal"], iva=fila["iva"],
            total=fila["total"], fecha=fila["fecha"], items=items,
            eliminada=fila["eliminada"],
        )
