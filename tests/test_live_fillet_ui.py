import bpy
import json
import math
import sys
import time
from pathlib import Path
from mathutils import Vector

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cascade_cad as cad
from cascade_cad import live_fillet as live
cad.register()
bpy.context.preferences.view.show_splash=False
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
obj=bpy.data.objects.new('CAD | Fillet interativo',bpy.data.meshes.new('CAD fixture'))
bpy.context.collection.objects.link(obj)
cad.set_mesh(obj,json.loads((ROOT/'work/fillet_fixture.json').read_text()),0.001)
obj.select_set(True)
bpy.context.view_layer.objects.active=obj
bpy.context.view_layer.update()
source=obj['cad_brep']
source_mesh=obj.data
original_selection=obj['cad_selected_edges']
bpy.context.scene.cascade_cad.fillet=2
bpy.context.scene.unit_settings.system='METRIC'
bpy.context.scene.unit_settings.length_unit='MILLIMETERS'
window=bpy.context.window_manager.windows[0]
area=next(a for a in window.screen.areas if a.type=='VIEW_3D')
region=next(r for r in area.regions if r.type=='WINDOW')
space=area.spaces.active
space.region_3d.view_distance=0.125
space.region_3d.view_location=Vector((0,0,0.01))
space.region_3d.view_rotation=Vector((0.12,-0.16,0.14)).to_track_quat('Z','Y')
space.region_3d.view_perspective='ORTHO'
space.clip_start=0.001
space.show_region_ui=True
space.shading.color_type='OBJECT'
space.overlay.show_floor=False
space.overlay.show_axis_x=False
space.overlay.show_axis_y=False
space.overlay.show_cursor=False
checks=[]
stats={'display_ids':set(),'latencies':[]}

def e(kind,value='PRESS',**kw):
    window.event_simulate(type=kind,value=value,**kw)
def invoke(op,**kwargs):
    with bpy.context.temp_override(window=window,area=area,region=region):
        return op('INVOKE_DEFAULT',**kwargs)
def active():
    return live._active
def ready():
    return active() is not None and active()._display_key==active().key() and not active()._dirty
def shot(name):
    bpy.ops.screen.screenshot(filepath=str(ROOT/name))
def numeric(text):
    # Start numeric input through the public event path.
    if active()._buffer:
        for _ in active()._buffer:
            e('BACK_SPACE')
    keys=['ZERO','ONE','TWO','THREE','FOUR','FIVE','SIX','SEVEN','EIGHT','NINE']
    for ch in text:
        e(keys[int(ch)] if ch.isdigit() else 'PERIOD',unicode=ch)
def preserve():
    assert obj.data==source_mesh and obj['cad_brep']==source
def mark(name):
    checks.append(name)

def setup():
    for r in area.regions:
        if r.type=='UI': r.active_panel_category='CAD'
    bpy.ops.ed.undo_push(message='CAD fixture')
    assert invoke(bpy.ops.cascade.pick,mode='EDGE')=={'RUNNING_MODAL'}
def select_all():
    e('MOUSEMOVE','NOTHING',x=region.x+450,y=region.y+400)
    e('A')
def start_with_b():
    e('B')
def check_initial():
    if not ready(): return False
    preserve()
    assert len(active()._selected)==12 and active()._batch is not None
    mark('B from edge selection opens live CAD preview without modifying mesh or BREP')
    shot('Fillet_020_Previa_Inicial.png')
def begin_drag():
    tool=active()
    xy=tool.handle_position()
    stats['initial_radius']=tool._value
    stats['drag_x']=int(region.x+xy.x)
    stats['drag_y']=int(region.y+xy.y)
    stats['drag_step']=0
    e('MOUSEMOVE','NOTHING',x=stats['drag_x'],y=stats['drag_y'])
    e('LEFTMOUSE',x=stats['drag_x'],y=stats['drag_y'])
def continuous_drag():
    tool=active()
    if tool._display_id>=0:
        stats['display_ids'].add(tool._display_id)
        stats['latencies'].append(tool._latency)
    preserve()
    i=stats['drag_step']
    if i<45:
        e('MOUSEMOVE','NOTHING',x=stats['drag_x']+i,y=stats['drag_y'])
        stats['drag_step']+=1
        return False
    e('LEFTMOUSE','RELEASE',x=stats['drag_x']+44,y=stats['drag_y'])
def check_drag():
    if not ready(): return False
    preserve()
    assert active()._value>stats['initial_radius']
    assert len(stats['display_ids'])>=3,stats['display_ids']
    mark('continuous mouse drag updates multiple actual CAD previews before mouse release')
    shot('Fillet_020_Arrastando.png')
def precision_start():
    tool=active(); xy=tool.handle_position()
    stats['precision_value']=tool._value
    stats['precision_sensitivity']=tool._sensitivity
    stats['px']=int(region.x+xy.x); stats['py']=int(region.y+xy.y)
    e('LEFTMOUSE',x=stats['px'],y=stats['py'])
def precision_move():
    e('MOUSEMOVE','NOTHING',x=stats['px']+20,y=stats['py'],shift=True)
    e('LEFTMOUSE','RELEASE',x=stats['px']+20,y=stats['py'])
def check_precision():
    if not ready(): return False
    assert math.isclose(active()._value-stats['precision_value'],2*stats['precision_sensitivity'],abs_tol=1e-5)
    mark('Shift precision reduces mouse sensitivity tenfold')
    numeric('2')
def snap_start():
    if not ready() or abs(active()._value-2)>1e-6: return False
    xy=active().handle_position()
    stats['sx']=int(region.x+xy.x); stats['sy']=int(region.y+xy.y)
    e('LEFTMOUSE',x=stats['sx'],y=stats['sy'])
def snap_move():
    e('MOUSEMOVE','NOTHING',x=stats['sx']+7,y=stats['sy'],ctrl=True)
    e('LEFTMOUSE','RELEASE',x=stats['sx']+7,y=stats['sy'])
def check_snap():
    if not ready(): return False
    assert math.isclose(active()._value*2,round(active()._value*2),abs_tol=1e-6)
    mark('Ctrl drag snaps radius to half millimeter increments')
    path=active()._paths[0]
    world=active()._matrix@Vector([v*0.001 for v in path[len(path)//2]])
    from bpy_extras import view3d_utils
    p=view3d_utils.location_3d_to_region_2d(region,space.region_3d,world)
    stats['edge_xy']=(int(p.x+region.x),int(p.y+region.y))
    e('LEFTMOUSE',x=stats['edge_xy'][0],y=stats['edge_xy'][1],ctrl=True)
    e('LEFTMOUSE','RELEASE',x=stats['edge_xy'][0],y=stats['edge_xy'][1],ctrl=True)
def toggle_back():
    assert len(active()._selected)==11
    e('LEFTMOUSE',x=stats['edge_xy'][0],y=stats['edge_xy'][1],ctrl=True)
    e('LEFTMOUSE','RELEASE',x=stats['edge_xy'][0],y=stats['edge_xy'][1],ctrl=True)
def check_selection():
    if len(active()._selected)!=12: return False
    mark('Ctrl click toggles original CAD edge selection during preview')
    numeric('3.5')
def check_numeric():
    if not ready() or abs(active()._value-3.5)>1e-6: return False
    preserve()
    mark('numeric radius 3.5 mm produces exact requested preview')
    stats['valid_batch']=active()._batch
    numeric('1000')
def check_invalid():
    if active()._error_key!=active().key(): return False
    preserve()
    assert active()._batch==stats['valid_batch']
    mark('invalid radius retains last valid preview and original CAD data')
    shot('Fillet_020_Raio_Invalido.png')
    e('RET')
def invalid_cannot_commit():
    assert active() is not None and active()._error
    preserve()
    mark('confirmation cannot commit stale preview when requested radius is invalid')
    numeric('2')
def recover():
    if not ready() or active()._error: return False
    mark('valid radius recovers after invalid attempt')
    e('C')
def check_chamfer():
    if not ready() or active()._value!=-2: return False
    preserve()
    mark('C toggles to a real negative-distance CAD chamfer preview')
    shot('Fillet_020_Chanfro.png')
    e('ESC')
def check_cancel():
    assert active() is None and not cad._busy and not obj.hide_get()
    preserve()
    assert len(json.loads(obj['cad_selected_edges']))==12
    mark('Escape restores original visibility, selection, mesh and embedded BREP')
    assert invoke(bpy.ops.cascade.fillet_live)=={'RUNNING_MODAL'}
def second_ready():
    if not ready(): return False
    numeric('3')
def commit_while_pending():
    # Confirmation must submit final geometry for 3, never commit earlier 2 mm preview.
    e('RET')
def committed():
    if active() is not None: return False
    assert not cad._busy and not obj.hide_get()
    assert obj['cad_brep']!=source and obj['cad_volume']<48000
    cad.check_cad(obj)
    assert bpy.context.scene.cascade_cad.fillet==3
    mark('Enter commits requested current radius using full CAD tessellation')
    stats['committed_brep']=obj['cad_brep']
    bpy.ops.ed.undo()
def check_undo():
    target=next(o for o in bpy.data.objects if o.get('cad_brep')==source)
    assert not target.hide_get()
    cad.check_cad(target)
    mark('single Blender undo restores source CAD solid after live tool')
    bpy.ops.ed.redo()
def check_redo():
    target=next(o for o in bpy.data.objects if o.get('cad_brep')==stats['committed_brep'])
    cad.check_cad(target)
    mark('Blender redo restores final fillet and valid selection mapping')
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'Exemplo_Fillet_Interativo_020.blend'))
    shot('Fillet_020_Confirmado.png')

steps=[lambda:e('ESC'),setup,select_all,start_with_b,check_initial,begin_drag,continuous_drag,check_drag,
       precision_start,precision_move,check_precision,snap_start,snap_move,check_snap,toggle_back,check_selection,check_numeric,check_invalid,invalid_cannot_commit,
       recover,check_chamfer,check_cancel,second_ready,commit_while_pending,committed,check_undo,check_redo]
deadline=time.monotonic()+20
def tick():
    global deadline
    try:
        if not steps:
            report={'blender':bpy.app.version_string,'checks':checks,'preview_updates_while_dragging':len(stats['display_ids']),
                    'calculation_ms':stats['latencies']}
            (ROOT/'validation_live_ui_020.json').write_text(json.dumps(report,indent=2),encoding='utf8')
            print('LIVE_FILLET_UI_VALIDATED',report,flush=True)
            cad.unregister()
            bpy.ops.wm.quit_blender()
            return None
        if time.monotonic()>deadline:
            raise TimeoutError(steps[0].__name__)
        result=steps[0]()
        if result is not False:
            steps.pop(0)
            deadline=time.monotonic()+20
            return 0.25
        return 0.035 if steps[0]==continuous_drag else 0.1
    except Exception:
        import traceback
        text=traceback.format_exc()
        tool=active()
        text+='\nACTIVE: '+str(tool is not None)
        if tool:
            text+='\n'+str({k:getattr(tool,k,None) for k in ('_value','_id','_dirty','_display_key','_error','_sent_key')})
            text+='\nENGINE: '+str(tool._engine.alive())
            text+='\n'+(tool._engine.path/'engine.log').read_text()
        shot('Fillet_020_Test_Failure.png')
        (ROOT/'work/live_ui_failure.txt').write_text(text,encoding='utf8')
        print(text,flush=True)
        cad.unregister()
        bpy.ops.wm.quit_blender()
        return None
bpy.app.timers.register(tick,first_interval=3)

