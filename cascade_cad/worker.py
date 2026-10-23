"""Isolated Open CASCADE worker. JSON files only; never evaluates user code."""
import base64
import json
import math
import os
import sys
import tempfile
import time
import traceback
import zlib

import cadquery as cq


def pack(shape):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'shape.brep')
        shape.exportBrep(path)
        with open(path, 'rb') as stream:
            return base64.b64encode(zlib.compress(stream.read())).decode('ascii')


def unpack(data):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'shape.brep')
        with open(path, 'wb') as stream:
            stream.write(zlib.decompress(base64.b64decode(data)))
        return cq.Shape.importBrep(path)


def transformed(shape, matrix):
    if matrix is None:
        return shape
    # Matrices supplied by the add-on have translation in millimeters.
    from OCP.gp import gp_Trsf
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    trsf = gp_Trsf()
    trsf.SetValues(*[float(matrix[i][j]) for i in range(3) for j in range(4)])
    return cq.Shape.cast(BRepBuilderAPI_Transform(shape.wrapped, trsf, True).Shape())


def geometry(shape, tolerance, preview=False):
    vertices, triangles, face_ids, smooth = [], [], [], []
    faces = shape.Faces()
    for face_id, face in enumerate(faces):
        vs, ts = face.tessellate(tolerance, 0.12)
        start = len(vertices)
        vertices.extend([list(v.toTuple()) for v in vs])
        triangles.extend([[a + start, b + start, c + start] for a, b, c in ts])
        face_ids.extend([face_id] * len(ts))
        smooth.extend([face.geomType() != 'PLANE'] * len(ts))
    result = dict(vertices=vertices, triangles=triangles, face_ids=face_ids, smooth=smooth,
                  faces=len(faces), solids=len(shape.Solids()), volume=shape.Volume())
    if preview:
        return result
    edge_paths = []
    for edge in shape.Edges():
        count = max(2, min(96, int(edge.Length() / max(tolerance * 8, 0.5)) + 2))
        edge_paths.append([list(edge.positionAt(i / (count - 1)).toTuple()) for i in range(count)])
    return dict(**result, edges=edge_paths, edge_count=len(edge_paths), brep=pack(shape))


def run(req, source=None):
    op = req['op']
    p = req.get('params', {})
    if op == 'ping':
        import OCP
        return {'ok': True, 'cadquery': cq.__version__, 'ocp': OCP.__version__}
    if op == 'primitive':
        kind = p['kind']
        if kind == 'BOX':
            shape = cq.Workplane('XY').box(p['x'], p['y'], p['z'], centered=(True, True, False)).val()
        elif kind == 'CYLINDER':
            shape = cq.Workplane('XY').circle(p['radius']).extrude(p['z']).val()
        elif kind == 'SPHERE':
            shape = cq.Workplane('XY').sphere(p['radius']).val()
        else:
            raise ValueError('Primitiva desconhecida')
    elif op == 'profile':
        wp = cq.Workplane('XY')
        if p['kind'] == 'RECT':
            wp = wp.rect(p['x'], p['y'])
        elif p['kind'] == 'CIRCLE':
            wp = wp.circle(p['radius'])
        else:
            wp = wp.polyline(p['points']).close()
        shape = wp.extrude(p['depth']).val()
    elif op == 'import':
        shape = cq.importers.importStep(p['path']).val()
    else:
        shape = source.copy(mesh=False) if source is not None else unpack(req['brep'])
        if op == 'boolean':
            for operand in req['operands']:
                other = transformed(unpack(operand['brep']), operand['matrix'])
                shape = {'CUT': shape.cut, 'UNION': shape.fuse, 'INTERSECT': shape.intersect}[p['kind']](other)
            shape = shape.clean()
        elif op in ('fillet', 'chamfer'):
            edges = shape.Edges()
            chosen = [edges[i] for i in p['edges']] if p.get('edges') else edges
            if op == 'fillet':
                shape = shape.fillet(p['radius'], chosen)
            else:
                shape = shape.chamfer(p['radius'], None, chosen)
        elif op == 'shell':
            chosen = [shape.Faces()[i] for i in p['faces']]
            if not chosen:
                raise ValueError('Selecione uma face para abrir a casca')
            shape = shape.shell(chosen, p['thickness'])
        elif op == 'pushpull':
            face = shape.Faces()[p['face']]
            if face.geomType() != 'PLANE':
                raise ValueError('Push/pull desta versao aceita apenas faces planas')
            normal = face.normalAt()
            distance = p['distance']
            if abs(distance) < 1e-7:
                raise ValueError('A distancia deve ser diferente de zero')
            # An overlap of 0.001 mm prevents coincident-face union ambiguity.
            overlap = 0.001
            start = face.translate(normal.multiply(-overlap if distance > 0 else overlap))
            vector = normal.multiply(distance + (overlap if distance > 0 else -overlap))
            prism = cq.Solid.extrudeLinear(start.outerWire(), start.innerWires(), vector)
            shape = (shape.fuse(prism) if distance > 0 else shape.cut(prism)).clean()
        elif op == 'export':
            shape = transformed(shape, req.get('matrix'))
            cq.exporters.export(shape, p['path'], exportType='STEP')
            return {'ok': True, 'path': p['path']}
        elif op != 'refresh':
            raise ValueError('Operacao desconhecida: ' + op)
    if shape.isNull() or not shape.isValid() or not shape.Solids() or shape.Volume() < 1e-9:
        raise ValueError('A operacao nao produziu um solido CAD valido. Modelo anterior preservado.')
    return {'ok': True, **geometry(shape, req.get('tolerance', 0.12), req.get('preview', False))}


def live_server(folder):
    """A single warm OCCT process; a one-slot mailbox always retains newest input."""
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    folder = Path(folder)
    last_id = -1
    last_activity = time.monotonic()
    cached_brep, cached_shape = None, None
    while time.monotonic() - last_activity < 300:
        if not folder.exists():
            return
        try:
            request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            time.sleep(0.01)
            continue
        job_id = request['id']
        if job_id <= last_id:
            time.sleep(0.01)
            continue
        last_id = job_id
        started = time.monotonic()
        try:
            if request.get('brep') and request['brep'] != cached_brep:
                cached_shape = unpack(request['brep'])
                cached_brep = request['brep']
            response = run(request, cached_shape if request.get('brep') else None)
        except Exception as exc:
            response = {'ok': False, 'error': str(exc)}
        response.update(id=job_id, elapsed_ms=(time.monotonic()-started)*1000)
        temporary = folder / 'response.tmp'
        temporary.write_text(json.dumps(response, allow_nan=False), encoding='utf-8')
        from preview_engine import replace_mailbox
        replace_mailbox(temporary, folder / 'response.json')
        last_activity = time.monotonic()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--live':
        live_server(sys.argv[2])
        sys.exit(0)
    try:
        with open(sys.argv[1], encoding='utf-8') as stream:
            response = run(json.load(stream))
    except Exception as exc:
        response = {'ok': False, 'error': str(exc), 'traceback': traceback.format_exc()}
    with open(sys.argv[2], 'w', encoding='utf-8') as stream:
        json.dump(response, stream, allow_nan=False)

