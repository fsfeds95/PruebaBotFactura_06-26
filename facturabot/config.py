"""Configuración del bot.

Todos los valores sensibles se leen desde variables de entorno (.env).
NUNCA pongas el token ni datos fiscales reales directamente en el código.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv es opcional; si no está, se usan solo las variables de entorno
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)


def _parse_ids(csv: str) -> frozenset[int]:
    """Convierte '123,456' en {123, 456}, ignorando valores inválidos."""
    ids = set()
    for parte in csv.split(","):
        parte = parte.strip()
        if not parte:
            continue
        try:
            ids.add(int(parte))
        except ValueError:
            logger.warning("ID de usuario ignorado (no numérico): %r", parte)
    return frozenset(ids)


@dataclass(frozen=True)
class EmisorConfig:
    """Datos fiscales del emisor de la factura (se configuran vía variables de entorno)."""

    nombre: str
    rif: str
    direccion: str
    ciudad: str
    telefono: str
    correo: str
    iva_porcentaje: float = 16.0
    leyenda_fiscal: str = (
        "Documento entregado originalmente de forma electrónica - "
        "Sistema Automatizado según Resolución SENIAT Nro. 93."
    )


@dataclass(frozen=True)
class Config:
    token: str
    db_path: Path
    monedas: dict
    moneda_por_defecto: str
    logo_url: str | None
    logo_cache_dir: Path
    allowed_user_ids: frozenset[int]
    emisor: EmisorConfig


def load_config() -> Config:
    """Lee la configuración desde variables de entorno.

    Lanza RuntimeError si falta algo crítico (token o lista de usuarios autorizados).
    """
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Falta la variable de entorno BOT_TOKEN. Revoca el token anterior en @BotFather "
            "(estaba expuesto en el repositorio) y configura uno nuevo en el archivo .env."
        )

    allowed_ids = _parse_ids(os.environ.get("ALLOWED_USER_IDS", ""))
    if not allowed_ids:
        raise RuntimeError(
            "Falta ALLOWED_USER_IDS. Indica los IDs de Telegram autorizados a usar el bot, "
            "separados por comas (ej: ALLOWED_USER_IDS=123456789)."
        )

    moneda_defecto = os.environ.get("MONEDA_POR_DEFECTO", "USD").strip()
    normalizada = {"bs": "Bs", "$": "USD", "usd": "USD", "eur": "EUR"}
    moneda_defecto = normalizada.get(moneda_defecto.lower(), moneda_defecto)

    emisor = EmisorConfig(
        nombre=os.environ.get("EMISOR_NOMBRE", "Brianta Pet Luengo"),
        rif=os.environ.get("EMISOR_RIF", ""),
        direccion=os.environ.get("EMISOR_DIRECCION", ""),
        ciudad=os.environ.get("EMISOR_CIUDAD", "Caracas"),
        telefono=os.environ.get("EMISOR_TELEFONO", ""),
        correo=os.environ.get("EMISOR_CORREO", ""),
        iva_porcentaje=float(os.environ.get("IVA_PORCENTAJE", "16")),
    )

    cache_dir = Path(os.environ.get("LOGO_CACHE_DIR", ".cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        token=token,
        db_path=Path(os.environ.get("DB_PATH", "facturas.db")),
        monedas={"Bs": "Bs.", "USD": "$", "EUR": "€"},
        moneda_por_defecto=moneda_defecto if moneda_defecto in ("Bs", "USD", "EUR") else "USD",
        logo_url=os.environ.get("LOGO_URL") or None,
        logo_cache_dir=cache_dir,
        allowed_user_ids=allowed_ids,
        emisor=emisor,
    )
