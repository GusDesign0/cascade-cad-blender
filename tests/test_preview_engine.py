import importlib.util
import json
import math
from pathlib import Path
import statistics
import time

ROOT=Path(__file__).resolve().parents[1]
CODE=ROOT/'cascade_cad'
(ROOT/'work').mkdir(exist_ok=True)
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj
w=module('worker',CODE/'worker.py')
engine_module=module('preview_engine',CODE/'preview_engine.py')
python=str(Path.home()/'AppData/Local/CascadeCAD/runtime/python.exe')
engine=engine_module.PreviewEngine(python,CODE/'worker.py')
checks=[]
timings=[]
box=w.run({'op':'primitive','params':{'kind':'BOX','x':60,'y':40,'z':20}})
(ROOT/'work/fillet_fixture.json').write_text(json.dumps(box),encoding='utf8')
def request(radius,preview=True,edges=None):
    return dict(op='fillet' if radius>=0 else 'chamfer',brep=box['brep'],params={'radius':abs(radius),'edges':edges or list(range(12))},preview=preview,tolerance=0.18 if preview else 0.12)
def wait(seq):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        if not engine.alive():
            raise RuntimeError((engine.path/'engine.log').read_text())
        result=engine.poll()
        if result and result['id']==seq:
            return result
        time.sleep(0.005)
    raise TimeoutError(seq)
try:
    for radius in (1,2,3,4,2,0.5):
        result=wait(engine.submit(request(radius)))
        assert result['ok'],result
        direct=w.run(request(radius))
        assert math.isclose(result['volume'],direct['volume'],rel_tol=1e-10)
        assert 'brep' not in result and 'edges' not in result
        timings.append(result['elapsed_ms'])
    checks.append('multiple radii match independent CAD operations without cumulative fillets')
    invalid=wait(engine.submit(request(1000)))
    assert not invalid['ok']
    recovered=wait(engine.submit(request(2)))
    assert recovered['ok']
    checks.append('invalid radius leaves engine alive and valid radius recovers')
    chamfer=wait(engine.submit(request(-2)))
    assert chamfer['ok'] and chamfer['volume']<48000
    checks.append('signed distance produces real CAD chamfer')
    ids=[]
    for radius in (1,5,2,4,3):
        ids.append(engine.submit(request(radius)))
    latest=wait(ids[-1])
    assert latest['ok'] and math.isclose(latest['volume'],w.run(request(3))['volume'],rel_tol=1e-10)
    checks.append('latest request wins when pointer updates arrive in a burst')
    committed=wait(engine.submit(request(3,preview=False)))
    assert committed['ok'] and committed['brep'] and committed['edges']
    assert w.unpack(committed['brep']).isValid()
    checks.append('final commit contains valid BREP and CAD selection mapping')
    one=wait(engine.submit(request(2,edges=[0])))
    assert one['ok'] and one['volume']>recovered['volume']
    checks.append('selection changes recalculate from untouched source')
    cutter=w.run({'op':'primitive','params':{'kind':'CYLINDER','radius':6,'z':30}})
    cut=w.run({'op':'boolean','brep':box['brep'],'params':{'kind':'CUT'},'operands':[{'brep':cutter['brep'],'matrix':[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}]})
    circles=[i for i,e in enumerate(w.unpack(cut['brep']).Edges()) if e.geomType()=='CIRCLE']
    job=request(1)
    job['brep']=cut['brep']; job['params']['edges']=circles
    cut_preview=wait(engine.submit(job))
    assert cut_preview['ok'],cut_preview
    assert math.isclose(cut_preview['volume'],w.run(job)['volume'],rel_tol=1e-10)
    checks.append('preview also fillets circular edges of a boolean solid with a hole')
    report={'checks':checks,'calculation_ms':timings,'median_ms':statistics.median(timings),'max_ms':max(timings)}
    (ROOT/'validation_preview_engine_020.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print('PREVIEW_ENGINE_VALIDATED',report)
finally:
    engine.close()

