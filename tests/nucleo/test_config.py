from pathlib import Path

import pytest

from precia.navegador.proxy import ModoProxy
from precia.nucleo.config import Config, leer_env


def test_leer_env_admite_comentarios_comillas_y_espacios(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comentario\n"
        "\n"
        "A=1\n"
        'B = "con espacios"\n'
        "C='simple'\n"
        "export D=exportada\n"
        "E=valor # comentario al final\n"
        "LINEA_ROTA\n",
        encoding="utf-8",
    )
    assert leer_env(env) == {
        "A": "1",
        "B": "con espacios",
        "C": "simple",
        "D": "exportada",
        "E": "valor",
    }


def test_leer_env_inexistente_devuelve_vacio(tmp_path: Path) -> None:
    assert leer_env(tmp_path / "no-existe.env") == {}


def test_la_variable_de_entorno_manda_sobre_el_fichero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=del-fichero\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "del-entorno")
    assert Config.cargar(raiz=tmp_path).clave_api == "del-entorno"


def test_config_por_defecto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PRECIA_DEMO", raising=False)
    config = Config.cargar(raiz=tmp_path)
    assert config.clave_api is None
    assert config.ruta_bd == tmp_path / "datos" / "precia.sqlite3"
    assert config.ruta_cache == tmp_path / "cache"
    assert not config.demo


def test_la_clave_no_aparece_en_repr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secreta-de-prueba")
    config = Config.cargar(raiz=tmp_path)
    assert "sk-secreta-de-prueba" not in repr(config)
    assert "sk-secreta-de-prueba" not in str(config)


def test_modo_demo_por_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECIA_DEMO", "1")
    assert Config.cargar(raiz=tmp_path).demo


# --- Proxy ----------------------------------------------------------------------------------------

VARIABLES_PROXY = (
    "PRECIA_PROXY",
    "EVOMI_API_KEY",
    "EVOMI_PRODUCTO",
    "PRECIA_PROXY_PAIS",
    "PRECIA_PROXY_MODO",
)


@pytest.fixture
def sin_proxy_en_el_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    for nombre in VARIABLES_PROXY:
        monkeypatch.delenv(nombre, raising=False)


@pytest.mark.usefixtures("sin_proxy_en_el_entorno")
def test_sin_proxy_por_defecto(tmp_path: Path) -> None:
    config = Config.cargar(raiz=tmp_path)
    assert config.proxy_url is None and config.clave_evomi is None
    assert config.evomi_producto == "rpc"
    assert config.proxy_pais == "ES"
    assert config.proxy_modo is ModoProxy.BLOQUEO
    assert not config.hay_proxy


@pytest.mark.usefixtures("sin_proxy_en_el_entorno")
def test_proxy_desde_el_fichero(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "EVOMI_API_KEY=clave-evomi-secreta\n"
        "EVOMI_PRODUCTO=MP\n"
        "PRECIA_PROXY_PAIS=fr\n"
        "PRECIA_PROXY_MODO=siempre\n",
        encoding="utf-8",
    )
    config = Config.cargar(raiz=tmp_path)
    assert config.clave_evomi == "clave-evomi-secreta"
    assert config.evomi_producto == "mp"
    assert config.proxy_pais == "FR"
    assert config.proxy_modo is ModoProxy.SIEMPRE
    assert config.hay_proxy


@pytest.mark.usefixtures("sin_proxy_en_el_entorno")
def test_modo_desconocido_es_bloqueo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECIA_PROXY_MODO", "a veces")
    assert Config.cargar(raiz=tmp_path).proxy_modo is ModoProxy.BLOQUEO


@pytest.mark.usefixtures("sin_proxy_en_el_entorno")
def test_precia_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECIA_PROXY", "http://usu:secreta@proxy.example:8080")
    config = Config.cargar(raiz=tmp_path)
    assert config.proxy_url == "http://usu:secreta@proxy.example:8080"
    assert config.hay_proxy


@pytest.mark.usefixtures("sin_proxy_en_el_entorno")
def test_los_secretos_del_proxy_no_aparecen_en_repr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRECIA_PROXY", "http://usu:secreta-del-proxy@proxy.example:8080")
    monkeypatch.setenv("EVOMI_API_KEY", "clave-evomi-secreta")
    config = Config.cargar(raiz=tmp_path)
    for texto in (repr(config), str(config)):
        assert "secreta-del-proxy" not in texto and "clave-evomi-secreta" not in texto
