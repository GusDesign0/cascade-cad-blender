import bpy
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cascade_cad as cad
from cascade_cad import live_fillet as live
cad.register()
bpy.context.preferences.view.show_splash=False
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'Teste_Fillet_020.blend'))
obj=next(o for o in bpy.data.objects if o.get('cad_brep'))
source=obj['cad_brep']; mesh=obj.data
window=bpy.context.window_manager.windows[0]
area=next(a for a in window.screen.areas if a.type=='VIEW_3D')
region=next(r for r in area.regions if r.type=='WINDOW')
checks=[]

def invoke():
    with bpy.context.temp_override(window=window,area=area,region=region):
        assert bpy.ops.cascade.fillet_live('INVOKE_DEFAULT')=={'RUNNING_MODAL'}
def cancel():
    live._active.request()
    assert live._active._pending_since is not None
    window.event_simulate(type='ESC',value='PRESS')
def verify_cancel():
    assert live._active is None and live._engine is None and not cad._busy
    assert obj.data==mesh and obj['cad_brep']==source and not obj.hide_get()
    checks.append('Escape while computation is pending kills worker and preserves original')
    invoke()
def wait_ready():
    tool=live._active
    if tool is None or tool._display_key!=tool.key(): return False
    assert live._engine.alive()
    checks.append('preview engine restarts after cancelled pending job')
def load_file():
    process=live._engine.process
    bpy.ops.wm.open_mainfile(filepath=str(ROOT/'Teste_Fillet_020.blend'))
    assert live._active is None and live._engine is None and not cad._busy
    assert process.poll() is not None
    checks.append('opening another file during preview removes handlers and shuts down worker')
    (ROOT/'validation_preview_lifecycle_020.json').write_text(json.dumps(checks,indent=2),encoding='utf8')
    print('PREVIEW_LIFECYCLE_VALIDATED',checks,flush=True)
    cad.unregister()
    bpy.ops.wm.quit_blender()

steps=[invoke,cancel,verify_cancel,wait_ready,load_file]
deadline=time.monotonic()+15
def tick():
    global deadline
    try:
        if time.monotonic()>deadline: raise TimeoutError(steps[0].__name__)
        result=steps[0]()
        if result is not False:
            steps.pop(0); deadline=time.monotonic()+15
        return 0.18
    except Exception:
        import traceback
        (ROOT/'work/preview_lifecycle_failure.txt').write_text(traceback.format_exc(),encoding='utf8')
        print(traceback.format_exc(),flush=True)
        cad.unregister(); bpy.ops.wm.quit_blender()
        return None
bpy.app.timers.register(tick,first_interval=3)

