# Fixtures de extracción

Casi todas son páginas sintéticas escritas para los tests. Las de esta tabla vienen de páginas reales y por eso se apuntan aquí, como en `../reales/README.md`.

## Fichas recortadas de Oscaro

Las 5 fichas de pastillas de freno que cita la referencia OEM 1K0698151 en su pestaña oculta «Referencias del fabricante (OEM)» de Oscaro. Descargadas en la sesión S03 (2026-10-22) para el caso de referencia del emparejado por OEM (tanda B5 del diagnóstico de eficiencia). **No** están tal cual, por eso no van en `reales/`: son un **recorte** hecho con `scratchpad/b5/recortar_oscaro.py` de la sesión, que conserva la meta, el título, el JSON-LD del producto (con `mpn`), la cabecera con el precio y el `tabpanel` oculto de referencias OEM. Cada una lleva al principio un comentario con la URL, el estado y la hora de descarga.

Las usan `tests/emparejado/test_oscaro.py`, `tests/extraccion/test_ficha.py` y `tests/nucleo/test_orquestador.py`.

**Pendiente de que Julio confirme** que pueden quedarse en el repositorio (la aprobación para guardar páginas reales como fixture es de S01 y no cubre Oscaro).

| Fichero | URL | Estado | Descargada (UTC) | Caracteres |
|---|---|---|---|---|
| `oscaro_ficha_brembo_p85075.html` | https://www.oscaro.es/juego-de-4-pastillas-de-freno-brembo-p-85-075-1988684-402-p | 200 | 2026-10-22T13:39 | 48,515 |
| `oscaro_ficha_jpgroup_1163601110.html` | https://www.oscaro.es/juego-de-4-pastillas-de-freno-jp-group-as-1163601110-4477871-402-p | 200 | 2026-10-22T13:39 | 30,787 |
| `oscaro_ficha_kawe_810083.html` | https://www.oscaro.es/juego-de-4-pastillas-de-freno-kawe-810083-7579908-402-p | 200 | 2026-10-22T13:39 | 24,304 |
| `oscaro_ficha_textar_2358701.html` | https://www.oscaro.es/juego-de-4-pastillas-de-freno-textar-2358701-1873626-402-p | 200 | 2026-10-22T13:39 | 54,874 |
| `oscaro_ficha_valeo_301635.html` | https://www.oscaro.es/juego-de-4-pastillas-de-freno-valeo-301635-6042329-402-p | 200 | 2026-10-22T13:39 | 30,287 |
