"""Build distributable Blender extension and complete package using only stdlib."""
from pathlib import Path
import hashlib
import tomllib
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'cascade_cad'
DIST=ROOT/'dist'
DIST.mkdir(exist_ok=True)
version=tomllib.loads((SOURCE/'blender_manifest.toml').read_text(encoding='utf8'))['version']
extension=DIST/f'CascadeCAD-{version}.zip'
sources=[p for p in SOURCE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc']
with zipfile.ZipFile(extension,'w',zipfile.ZIP_DEFLATED,compresslevel=7) as archive:
    for path in sorted(sources): archive.write(path,path.relative_to(SOURCE).as_posix())
complete=DIST/f'CascadeCAD-{version}-completo.zip'
files=[p for p in ROOT.rglob('*') if p.is_file() and not any(part in ('.git','dist','work','__pycache__') for part in p.relative_to(ROOT).parts) and p.suffix not in ('.pyc','.blend1')]
with zipfile.ZipFile(complete,'w',zipfile.ZIP_DEFLATED,compresslevel=7) as archive:
    for path in sorted(files): archive.write(path,path.relative_to(ROOT).as_posix())
    archive.write(extension,extension.name)
for path in (extension,complete):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
    print(path.name,path.stat().st_size)
checksums=DIST/'SHA256SUMS.txt'
checksums.write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n' for path in (extension,complete)),encoding='utf8')

