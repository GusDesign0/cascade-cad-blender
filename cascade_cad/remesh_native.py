"""QuadWild + Bi-MDF, isolated in the remesh worker process.

C ABI adapted from ksami/QRemeshify (GPL-3.0-or-later), see cascade_quadwild/.
"""
import ctypes as c
from pathlib import Path
from cascade_quadwild.data import Parameters, create_default_QRParameters, QRParameters
import remesh_setup


class NativeQuadWild:
    def __init__(self, folder):
        if not remesh_setup.available(verify=True):
            raise RuntimeError('Instale o motor QuadWild pelo painel Remesh.')
        root = remesh_setup.engine_path()
        self.folder = Path(folder)
        self.quadwild = c.CDLL(str(root / 'lib_quadwild.dll'))
        self.patches = c.CDLL(str(root / 'lib_quadpatches.dll'))
        self.quadwild.remeshAndField2.argtypes = [c.POINTER(Parameters), c.c_char_p, c.c_char_p, c.c_char_p]
        self.quadwild.remeshAndField2.restype = None
        self.quadwild.trace2.argtypes = [c.c_char_p]
        self.quadwild.trace2.restype = c.c_bool
        self.patches.quadPatches.argtypes = [c.c_char_p, c.POINTER(QRParameters), c.c_float, c.c_int, c.c_bool]
        self.patches.quadPatches.restype = c.c_int

    def field(self, angle):
        params = Parameters(False, angle, .01, 1., True, False)
        self.quadwild.remeshAndField2(c.byref(params), str(self.folder / 'source.obj').encode(),
                                   str(self.folder / 'source_rem.sharp').encode(), str(self.folder / 'source_rem.rosy').encode())

    def trace(self):
        if not self.quadwild.trace2(str(self.folder / 'source_rem').encode()):
            raise RuntimeError('O motor nao conseguiu decompor os paineis. Original preservado.')

    def quads(self, density, seconds=90):
        params = create_default_QRParameters()
        config = Path(__file__).resolve().parent / 'cascade_quadwild/config'
        params.flow_config_filename = str(config / 'main_config/flow_virtual_simple.json').encode()
        params.satsuma_config_filename = str(config / 'satsuma/default.json').encode()
        params.timeLimit = seconds
        # Geometry is projected and tested by Cascade with its original reference,
        # instead of allowing unconstrained native Laplacian smoothing.
        params.finalSmoothing = False
        params.resultSmoothingIterations = 0
        params.resultSmoothingLaplacianIterations = 0
        result = self.patches.quadPatches(str(self.folder / 'source_rem_p0.obj').encode(), c.byref(params), density, 0, False)
        path = self.folder / 'source_rem_p0_0_quadrangulation.obj'
        if result != 0 or not path.is_file():
            raise RuntimeError('QuadWild nao produziu uma malha valida (codigo %s).' % result)
        return path
