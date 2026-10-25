"""URL de entrada de una tienda: la que pega el usuario si PrecIA no encuentra el buscador.

Nota 16 §A (ADR-018). Hay dos formas:

- **A1**, plantilla de búsqueda: lleva `{consulta}` una vez, en la ruta o en la consulta
  (`https://tienda.es/buscar?q={consulta}`). El descubrimiento la prueba la primera.
- **A2**, listado o categoría: sin `{consulta}` y con `{pagina}` como mucho una vez
  (`https://tienda.es/catalogue/page-{pagina}.html`). Da un perfil `listado`.

La escribe una persona, pero acaba en descargas: aquí se comprueba todo lo que no depende del
DNS para que no pueda apuntar a otro servidor (otro host, un subdominio, usuario, puerto, `\\`
que los navegadores leen como `/`, `%` en el host, un IDN que imita al de la tienda) ni llevar
marcadores que cambien el host en cada búsqueda. El anti-SSRF (`navegador.url.validar_url`,
que resuelve el DNS) se aplica además, como siempre, al descargar.

Solo se admite ASCII imprimible: los acentos van codificados (`%C3%AD`), como los copia el
navegador, y un IDN va en punycode (`xn--...`). Así un carácter invisible o una letra de otro
alfabeto que se parece a una latina no pueden colarse. Los mensajes de `EntradaNoValida` se
enseñan en la web y nunca citan la URL.
"""

from urllib.parse import urlsplit

from precia.extraccion.listado import dominio_normalizado
from precia.perfiles.validador import consulta_fuera_del_host

MAX_ENTRADA = 500
"""Caracteres de la URL de entrada (lo mismo que admite el almacén)."""
CONSULTA = "{consulta}"
PAGINA = "{pagina}"
_PUERTOS = {"http": 80, "https": 443}
_PROHIBIDOS = frozenset("\"'`<>\\")
"""Comillas, `<`, `>` y barra invertida: no hacen falta en una URL y sirven para colar cosas."""


class EntradaNoValida(ValueError):
    """La URL de entrada no se puede usar. El mensaje es para el usuario."""


def normalizar_entrada(dominio: str, texto: str) -> str | None:
    """`texto` como URL de entrada de la tienda `dominio`, o `None` si está vacío.

    Devuelve la URL con el esquema y el host en minúsculas, sin el puerto por defecto y con la
    ruta `/` si no traía ninguna; el resto se queda tal cual. Lanza `EntradaNoValida` si no
    cumple las reglas del docstring del módulo.
    """
    texto = texto.strip()
    if not texto:
        return None
    if len(texto) > MAX_ENTRADA:
        raise EntradaNoValida(f"La URL de entrada no puede pasar de {MAX_ENTRADA} caracteres.")
    if not texto.isascii():
        raise EntradaNoValida(
            "La URL de entrada solo puede llevar caracteres ASCII: cópiala de la barra de "
            "direcciones del navegador (los acentos van como %C3%AD y un dominio con tildes, en "
            "punycode «xn--…»)."
        )
    if not texto.isprintable() or any(c.isspace() or c in _PROHIBIDOS for c in texto):
        raise EntradaNoValida(
            "La URL de entrada no puede llevar espacios, comillas, «<», «>» ni barras invertidas."
        )
    if "#" in texto:
        raise EntradaNoValida(
            "Quita de la URL de entrada la parte desde «#»: no llega a la tienda."
        )
    _comprobar_marcadores(texto)

    tienda = dominio_normalizado(dominio) or dominio.strip().lower()
    otro_host = EntradaNoValida(
        f"La URL de entrada tiene que ser de la tienda: {tienda} o www.{tienda}, sin usuario."
    )
    try:
        partes = urlsplit(texto)
    except ValueError as error:  # p. ej. corchetes de IPv6 sin cerrar («https://[tienda.es/»)
        raise otro_host from error
    esquema = partes.scheme.lower()
    if esquema not in _PUERTOS or not texto[len(partes.scheme) :].startswith("://"):
        raise EntradaNoValida(
            "Escribe la URL de entrada completa, empezando por https:// (o http://)."
        )
    if not consulta_fuera_del_host(texto) or "{" in partes.netloc or "}" in partes.netloc:
        raise EntradaNoValida(
            "{consulta} y {pagina} van en la ruta o en los parámetros, nunca en el host."
        )
    if "@" in partes.netloc or "%" in partes.netloc:
        raise otro_host
    host = (partes.hostname or "").lower()
    if host not in (tienda, f"www.{tienda}"):
        raise otro_host
    try:
        puerto = partes.port
    except ValueError as error:
        raise EntradaNoValida("El puerto de la URL de entrada no es válido.") from error
    if puerto not in (None, _PUERTOS[esquema]):
        raise EntradaNoValida(
            "La URL de entrada no puede llevar un puerto: solo el de siempre (443 o 80)."
        )
    consulta = f"?{partes.query}" if partes.query else ""
    return f"{esquema}://{host}{partes.path or '/'}{consulta}"


def _comprobar_marcadores(texto: str) -> None:
    """`{consulta}` o `{pagina}` como mucho una vez, no los dos, y ninguna otra llave."""
    consultas = texto.count(CONSULTA)
    paginas = texto.count(PAGINA)
    if consultas > 1:
        raise EntradaNoValida("{consulta} solo puede salir una vez en la URL de entrada.")
    if paginas > 1:
        raise EntradaNoValida("{pagina} solo puede salir una vez en la URL de entrada.")
    if consultas and paginas:
        raise EntradaNoValida(
            "La URL de entrada lleva {consulta} (búsqueda) o {pagina} (listado), no las dos."
        )
    resto = texto.replace(CONSULTA, "").replace(PAGINA, "")
    if "{" in resto or "}" in resto:
        raise EntradaNoValida(
            "La URL de entrada solo admite las llaves {consulta} o {pagina}, escritas así."
        )
