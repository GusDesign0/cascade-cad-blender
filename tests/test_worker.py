import importlib.util
import json
import math
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cadworker', ROOT/'cascade_cad/worker.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
checks = []

def run(name, request, expected=None):
    result = w.run(request)
    assert result['ok'], result
    if 'brep' in result:
        shape = w.unpack(result['brep'])
        assert shape.isValid() and shape.Solids()
        assert len(result['face_ids']) == len(result['triangles'])
        assert all(0 <= i < len(result['vertices']) for tri in result['triangles'] for i in tri)
        if expected is not None:
            assert math.isclose(result['volume'], expected, rel_tol=1e-7, abs_tol=1e-4), (name, result['volume'],expected)
    checks.append({'name': name, 'volume_mm3': result.get('volume'), 'passed': True})
    return result

box = run('box', {'op':'primitive','params':{'kind':'BOX','x':60,'y':40,'z':20}}, 48000)
cyl = run('cylinder', {'op':'primitive','params':{'kind':'CYLINDER','radius':6,'z':30}},math.pi*36*30)
identity = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
cut = run('boolean cut', {'op':'boolean','brep':box['brep'],'operands':[{'brep':cyl['brep'],'matrix':identity}],
                         'params':{'kind':'CUT'}},48000-math.pi*36*20)
run('boolean union', {'op':'boolean','brep':box['brep'],'operands':[{'brep':cyl['brep'],'matrix':identity}],
                         'params':{'kind':'UNION'}},48000+math.pi*36*10)
run('boolean intersect', {'op':'boolean','brep':box['brep'],'operands':[{'brep':cyl['brep'],'matrix':identity}],
                         'params':{'kind':'INTERSECT'}},math.pi*36*20)
fillet = run('fillet all', {'op':'fillet','brep':box['brep'],'params':{'radius':2,'edges':[]}})
run('chamfer one edge', {'op':'chamfer','brep':box['brep'],'params':{'radius':1,'edges':[0]}})
shape=w.unpack(box['brep'])
top = max(range(len(shape.Faces())), key=lambda i:shape.Faces()[i].Center().z)
run('push face', {'op':'pushpull','brep':box['brep'],'params':{'face':top,'distance':5}},60000)
run('pull face', {'op':'pushpull','brep':box['brep'],'params':{'face':top,'distance':-5}},36000)
run('shell', {'op':'shell','brep':box['brep'],'params':{'faces':[top],'thickness':-2}})
run('rectangle extrusion', {'op':'profile','params':{'kind':'RECT','x':20,'y':10,'depth':7}},1400)
run('circle extrusion negative', {'op':'profile','params':{'kind':'CIRCLE','radius':5,'depth':-10}},math.pi*250)
run('polygon extrusion', {'op':'profile','params':{'kind':'POLY','points':[[0,0],[10,0],[10,10],[0,10]],'depth':5}},500)
run('sphere', {'op':'primitive','params':{'kind':'SPHERE','radius':5}},4/3*math.pi*125)
# Translated and rotated cutter crossing only part of a block.
matrix = [[0,-1,0,25],[1,0,0,0],[0,0,1,0],[0,0,0,1]]
run('transformed cutter', {'op':'boolean','brep':box['brep'],'operands':[{'brep':cyl['brep'],'matrix':matrix}], 'params':{'kind':'CUT'}})
scaled = [[2,0,0,0],[0,2,0,0],[0,0,2,0],[0,0,0,1]]
run('uniform scaled cutter', {'op':'boolean','brep':box['brep'],'operands':[{'brep':cyl['brep'],'matrix':scaled}], 'params':{'kind':'CUT'}})
with tempfile.TemporaryDirectory() as td:
    path = str(pathlib.Path(td)/'roundtrip.step')
    run('STEP export', {'op':'export','brep':fillet['brep'],'matrix':identity,'params':{'path':path}})
    run('STEP import volume roundtrip', {'op':'import','params':{'path':path}},fillet['volume'])
try:
    w.run({'op':'fillet','brep':box['brep'],'params':{'radius':10000,'edges':[]}})
except Exception:
    checks.append({'name':'invalid fillet rejected', 'passed': True})
else:
    raise AssertionError('Impossible fillet accepted')
report = ROOT/'validation_worker.json'
report.write_text(json.dumps(checks,indent=2),encoding='utf8')
print('WORKER_VALIDATED',len(checks))

