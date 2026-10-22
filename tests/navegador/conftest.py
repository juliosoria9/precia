import shutil
import subprocess
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "chromium: abre un Chromium real (Playwright) contra un servidor en 127.0.0.1"
    )


def _openssl() -> str | None:
    """`openssl` del PATH o, en Windows, el que trae Git for Windows junto a `git`."""
    encontrado = shutil.which("openssl")
    git = shutil.which("git")
    if encontrado is None and git is not None:
        raiz = Path(git).resolve().parent.parent
        for carpeta in ("usr", "mingw64"):
            ruta = raiz / carpeta / "bin" / "openssl.exe"
            if ruta.is_file():
                return str(ruta)
    return encontrado


@pytest.fixture(scope="session")
def certificado_tienda(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Certificado autofirmado (clave, certificado) para 127.0.0.1 y `tienda.example`.

    Se genera al vuelo una vez por sesión y no se guarda ninguna clave en el repositorio.
    """
    openssl = _openssl()
    if openssl is None:
        pytest.skip("openssl no está instalado")
    carpeta = tmp_path_factory.mktemp("certificado_tienda")
    clave, cert = carpeta / "clave.pem", carpeta / "cert.pem"
    orden = [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1"]
    orden += ["-subj", "/CN=tienda.example"]
    orden += ["-addext", "subjectAltName=DNS:tienda.example,IP:127.0.0.1"]
    orden += ["-keyout", str(clave), "-out", str(cert)]
    subprocess.run(orden, check=True, capture_output=True, timeout=60)
    return clave, cert
