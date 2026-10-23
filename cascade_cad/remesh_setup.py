"""Pinned, separately installed native remesher. No Blender imports."""
import hashlib
import json
import os
from pathlib import Path
import platform
import tempfile
import urllib.request
import zipfile

VERSION = '1.1.0'
URL = 'https://github.com/ksami/QRemeshify/releases/download/1.1.0/QRemeshify-1.1.0-windows.zip'
ZIP_SHA256 = 'e7e1460371e5dc989ad34690b8c4cf7ddc2c0cc754e25c9aafba751f5dbcbfd9'
FILES = {
    'lib_quadwild.dll': 'f658fb002f9b9f10214a479ba67b80fd08204b5444096fa67f5b67a33a38061f',
    'lib_quadpatches.dll': 'cc03cca4f1f69043bb137581b08d06438f6da3643f1a470a5674857af374a946',
}


def engine_path():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'CascadeCAD' / 'remesh' / ('quadwild-' + VERSION)


def available(verify=False):
    root = engine_path()
    if not (root / 'engine.json').is_file():
        return False
    for name, digest in FILES.items():
        path = root / name
        if not path.is_file() or verify and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return False
    return True


def install(archive=None):
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('O motor nativo desta versao esta disponivel para Windows x64.')
    root = engine_path()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cascade_remesh_setup_') as temp:
        path = Path(archive) if archive else Path(temp) / 'engine.zip'
        if archive is None:
            with urllib.request.urlopen(URL, timeout=90) as response, path.open('wb') as stream:
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
        if hashlib.sha256(path.read_bytes()).hexdigest() != ZIP_SHA256:
            raise RuntimeError('O SHA-256 do motor nao corresponde a versao verificada. Nada foi instalado.')
        with zipfile.ZipFile(path) as source:
            for name, digest in FILES.items():
                matches = [n for n in source.namelist() if n.endswith('/lib/' + name)]
                if len(matches) != 1:
                    raise RuntimeError('Arquivo de motor inesperado: ' + name)
                data = source.read(matches[0])
                if hashlib.sha256(data).hexdigest() != digest:
                    raise RuntimeError('Biblioteca corrompida: ' + name)
                staged = root / (name + '.tmp')
                staged.write_bytes(data)
                os.replace(staged, root / name)
    record = {'version': VERSION, 'url': URL, 'sha256': ZIP_SHA256, 'libraries': FILES,
              'license': 'GPL-3.0', 'source': 'https://github.com/ksami/QRemeshify',
              'algorithm': 'https://github.com/cgg-bern/quadwild-bimdf'}
    (root / 'engine.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    return str(root)


if __name__ == '__main__':
    import sys
    print(install(sys.argv[1] if len(sys.argv) > 1 else None))
