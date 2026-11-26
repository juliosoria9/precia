# Casos etiquetados del banco de evaluación

`recambios.json` guarda la «verdad» de 25 fichas de producto que ya están en el repositorio como fixtures HTML (`tests/fixtures/`). El banco de evaluación (hito H8) pasa cada fixture por el extractor y compara lo que sale con lo que dice este fichero.

**Estado: etiquetas pendientes de revisión por Julio.** Las puso una IA (Claude Code, sesión S06, 2026-11-25) leyendo el HTML guardado, sin ejecutar el extractor de PrecIA (`precia.extraccion`, `precia.perfiles`, `precia.nucleo`), para que el banco no mida al extractor contra sí mismo. Julio tiene que validarlas a mano, una a una, antes de usar las cifras en la memoria.

## Formato

- `version`, `descripcion` y `casos` (lista).
- Cada caso: `id` (único), `fixture` (ruta relativa a `tests/fixtures/`, con `/`), `url` (la de la página, tomada del README de los fixtures), `tienda`, `sector`, `real` (`false` en las 2 páginas sintéticas), `moneda` (solo si no es el euro), `esperado`, `evidencia` y `nota` (opcional).
- `esperado` tiene como mucho cuatro claves: `precio`, `marca`, `referencia_oem` y `estado`. Cada una vale:
  - una cadena: el valor correcto;
  - `null`: se ha mirado y el fixture no da ese dato (el extractor no debería inventarlo);
  - clave ausente: hay duda, y ese campo no se evalúa en ese caso.
- `evidencia`: por cada campo con valor, un trozo literal del fixture (120 caracteres como mucho) donde se ve.

## Criterio de etiquetado

- `precio`: el precio de venta vigente, con punto y dos decimales (`"29.86"`). No vale el tachado, el recomendado, el «desde», el precio por kilo o litro ni los gastos de envío. Con variantes, el de la variante seleccionada o la de la URL. En euros salvo que el caso lleve `moneda`.
- `marca`: la marca o el fabricante tal como lo escribe la página (`"BREMBO"`, `"Adata"`), sin normalizar mayúsculas. No vale el vendedor de un marketplace.
- `referencia_oem`: la referencia del fabricante, número de pieza, OEM o MPN, tal como aparece. No valen el SKU, el id interno de la tienda ni un EAN, aunque la página los declare como `mpn`.
- `estado`: `"Nuevo"`, `"Usado"` o `"Reacondicionado"`, solo si la página lo dice o lo declara (`itemCondition`, «Material de segunda mano»).

Muchos fixtures son recortes de la página real: la etiqueta describe lo que hay en el fichero guardado, no lo que tuviera la página completa. Lo raro de cada caso está en su `nota`.

## Comprobación

Al crearlos se comprobó, con un script de solo librería estándar, que cada fixture existe, que cada evidencia aparece literalmente en su fichero, que los `id` son únicos y que cada precio casa con `^\d+\.\d{2}$`. Eso garantiza que el valor está en la página, no que sea el correcto: de ahí la revisión pendiente.
