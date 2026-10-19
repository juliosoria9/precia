from pathlib import Path

import pytest

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
