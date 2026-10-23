"""Run in a real Blender window with --enable-event-simulate."""
import bpy
import json
import sys
import time
from pathlib import Path
from mathutils import Vector
from bpy_extras import view3d_utils

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cascade_cad as cad
cad.register()
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'Exemplo_CascadeCAD.blend'))
checks=[]
state={}

def area_context():
    window=bpy.context.window_manager.windows[0]
    area=next(a for a in window.screen.areas if a.type=='VIEW_3D')
    region=next(r for r in area.regions if r.type=='WINDOW')
    return window,area,region

def event(kind,value='PRESS',**kw):
    window,area,region=area_context()
    window.event_simulate(type=kind,value=value,**kw)

def click(x,y):
    event('MOUSEMOVE','NOTHING',x=x,y=y)
    event('LEFTMOUSE',x=x,y=y)
    event('LEFTMOUSE','RELEASE',x=x,y=y)

def screen_point(world):
    window,area,region=area_context()
    p=view3d_utils.location_3d_to_region_2d(region,area.spaces.active.region_3d,Vector(world))
    return int(p.x+region.x),int(p.y+region.y)

def invoke(op,**kwargs):
    window,area,region=area_context()
    with bpy.context.temp_override(window=window,area=area,region=region):
        return op('INVOKE_DEFAULT',**kwargs)

def start():
    window,area,region=area_context()
    area.spaces.active.show_region_ui=True
    for reg in area.regions:
        if reg.type=='UI':
            reg.active_panel_category='CAD'
    bpy.context.scene.cascade_cad.plane='XY'
    bpy.context.scene.cascade_cad.status='Open CASCADE | Painel de modelagem'
    return 1

def shot():
    bpy.ops.screen.screenshot(filepath=str(ROOT/'Interface_CascadeCAD.png'))
    checks.append({'name':'real Blender sidebar and CAD preview drawn','passed':True})
    state['brep']=bpy.context.active_object['cad_brep']
    assert invoke(bpy.ops.cascade.pick,mode='EDGE')=={'RUNNING_MODAL'}

def edge_click():
    obj=bpy.context.active_object
    paths=json.loads(obj['cad_edges'])
    p=paths[0][len(paths[0])//2]
    world=obj.matrix_world@Vector([v*obj['cad_mm_scale'] for v in p])
    click(*screen_point(world))

def edge_shot():
    bpy.ops.screen.screenshot(filepath=str(ROOT/'Selecao_Arestas_CAD.png'))
    event('RET')

def face_start():
    assert len(json.loads(bpy.context.active_object['cad_selected_edges']))==1
    checks.append({'name':'viewport edge selection via simulated mouse','passed':True})
    assert invoke(bpy.ops.cascade.pick,mode='FACE')=={'RUNNING_MODAL'}

def face_click():
    click(*screen_point((0.008,0,0.028)))
    event('RET')

def draw_start():
    assert len(json.loads(bpy.context.active_object['cad_selected_faces']))==1
    checks.append({'name':'viewport BREP face selection via ray cast','passed':True})
    state['old_objects']=len(bpy.data.objects)
    assert invoke(bpy.ops.cascade.draw,kind='RECT')=={'RUNNING_MODAL'}

def draw_a():
    click(*screen_point((-0.020,-0.015,0)))

def draw_b():
    click(*screen_point((0.020,0.015,0)))

def draw_depth():
    bpy.ops.screen.screenshot(filepath=str(ROOT/'Desenho_Extrusao_CAD.png'))
    event('EIGHT',unicode='8')
    event('RET')

def wait_geometry():
    if cad._busy:
        return 0.5
    assert len(bpy.data.objects)==state['old_objects']+1
    obj=bpy.context.active_object
    assert cad.is_cad(obj)
    assert abs(obj['cad_volume']-40*30*8)<1e-3, obj['cad_volume']
    checks.append({'name':'draw rectangle, numeric extrusion, async OCCT commit','passed':True})
    state['new_brep']=obj['cad_brep']
    state['new_name']=obj.name
    with bpy.context.temp_override(window=area_context()[0],area=area_context()[1],region=area_context()[2]):
        bpy.ops.ed.undo()

def redo():
    assert not any(o.get('cad_brep')==state['new_brep'] for o in bpy.data.objects)
    with bpy.context.temp_override(window=area_context()[0],area=area_context()[1],region=area_context()[2]):
        bpy.ops.ed.redo()

def verify_redo():
    assert any(o.get('cad_brep')==state['new_brep'] for o in bpy.data.objects)
    checks.append({'name':'Blender undo and redo restores embedded CAD solid','passed':True})

steps=[start,shot,edge_click,edge_shot,face_start,face_click,draw_start,draw_a,draw_b,draw_depth,wait_geometry,redo,verify_redo]
def tick():
    try:
        if not steps:
            (ROOT/'validation_ui.json').write_text(json.dumps(checks,indent=2),encoding='utf8')
            print('UI_CAD_VALIDATED',len(checks),flush=True)
            bpy.ops.wm.quit_blender()
            return None
        action=steps[0]
        delay=action()
        if action==wait_geometry and delay is not None:
            return delay
        steps.pop(0)
        return 1.5
    except Exception:
        import traceback
        (ROOT/'work/ui_failure.txt').write_text(traceback.format_exc(),encoding='utf8')
        print(traceback.format_exc(),flush=True)
        bpy.ops.wm.quit_blender()
        return None
bpy.app.timers.register(tick,first_interval=3,persistent=True)

