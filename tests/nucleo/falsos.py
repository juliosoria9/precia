"""Dobles de prueba compartidos por los tests de `nucleo`."""

from dataclasses import dataclass, field

from precia.nucleo.modelos import EstadoTienda, ResultadoBusqueda


@dataclass
class SeguimientoFalso:
    """`Seguimiento` que apunta los estados de cada tienda, los resultados parciales (C1) y el
    detalle de en qué está cada una (A10)."""

    estados: list[tuple[str, EstadoTienda]] = field(default_factory=list)
    parciales: list[ResultadoBusqueda] = field(default_factory=list)
    detalles: list[tuple[str, str]] = field(default_factory=list)

    def tienda(self, dominio: str, estado: EstadoTienda) -> None:
        self.estados.append((dominio, estado))

    def parcial(self, resultado: ResultadoBusqueda) -> None:
        self.parciales.append(resultado)

    def detalle(self, dominio: str, texto: str) -> None:
        self.detalles.append((dominio, texto))

    def detalles_de(self, dominio: str) -> list[str]:
        """Los detalles del progreso fino de una tienda (A10), en orden."""
        return [texto for d, texto in self.detalles if d == dominio]

    def de(self, dominio: str) -> list[EstadoTienda]:
        """Los estados de una tienda, en orden."""
        return [estado for d, estado in self.estados if d == dominio]
