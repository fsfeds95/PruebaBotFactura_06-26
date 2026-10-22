"""Pruebas unitarias de la capa de datos y los cálculos de factura."""

import tempfile
import unittest
from pathlib import Path

from facturabot.db import Database
from facturabot.pdf import calcular_totales, money


class TestCalculos(unittest.TestCase):
    def test_money_redondeo(self):
        self.assertEqual(money(0.1 + 0.2), money(0.3))
        self.assertEqual(str(money(1.005)), "1.01")

    def test_iva_16(self):
        productos = [(2, "Silla", 10.0), (1, "Mesa", 100.0)]
        items, subtotal, iva, total = calcular_totales(productos, 16)
        self.assertEqual(subtotal, 120.0)
        self.assertEqual(iva, 19.2)
        self.assertEqual(total, 139.2)
        self.assertEqual(len(items), 2)


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _guardar(self, numero_int=None, moneda="USD", total=100.0):
        n = numero_int or self.db.reservar_numero()
        num = str(n).zfill(6)
        self.db.guardar_factura(num, num, "Cliente", "RIF", "Telf", "Domicilio",
                                "Contado", moneda, total / 1.16, total * 0.16 / 1.16,
                                total, [{"cantidad": 1, "descripcion": "X",
                                         "precio": total, "monto": total}])
        return num

    def test_numeracion_atomica_y_reversion(self):
        n1 = self.db.reservar_numero()
        n2 = self.db.reservar_numero()
        self.assertEqual((n1, n2), (1, 2))
        self.db.revertir_numero(n2)  # falla el PDF → se devuelve el número
        n3 = self.db.reservar_numero()
        self.assertEqual(n3, 2)

    def test_anulacion_es_logica_y_no_reutiliza_numero(self):
        num = self._guardar()
        self.assertTrue(self.db.anular_factura(num))
        self.assertFalse(self.db.anular_factura(num))  # ya estaba anulada
        # No aparece en el historial vigente...
        self.assertEqual([f.numero for f in self.db.obtener_facturas()], [])
        # ...pero el registro se conserva y el número no se reutiliza
        todas = self.db.obtener_facturas(incluir_anuladas=True)
        self.assertEqual(len(todas), 1)
        self.assertEqual(todas[0].eliminada, 1)
        siguiente = self.db.reservar_numero()
        self.assertEqual(siguiente, 2)

    def test_resumen_agrupa_por_moneda(self):
        self._guardar(moneda="USD", total=110.0)
        self._guardar(moneda="Bs", total=4000.0)
        r = self.db.resumen_mensual()
        por = {m["moneda"]: m["total"] for m in r["por_moneda"]}
        self.assertEqual(por["USD"], 110.0)
        self.assertEqual(por["Bs"], 4000.0)  # no se mezclan monedas

    def test_sincronizar_contador(self):
        for _ in range(3):
            self._guardar()
        nuevo = self.db.sincronizar_contador()
        self.assertEqual(nuevo, 3)
        self.assertEqual(self.db.reservar_numero(), 4)

    def test_filtro_inyeccion_parametros(self):
        self._guardar()
        # El patrón LIKE llega como parámetro; una cadena maliciosa no rompe nada
        self.assertEqual(self.db.obtener_facturas("'; DROP TABLE facturas; --"), [])
        self.assertEqual(len(self.db.obtener_facturas(incluir_anuladas=True)), 1)


if __name__ == "__main__":
    unittest.main()
