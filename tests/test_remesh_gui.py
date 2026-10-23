import bpy,sys,time,json
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
(ROOT/'work').mkdir(exist_ok=True)
import cascade_cad as cad
from cascade_cad import remesh
cad.register();bpy.context.preferences.view.show_splash=False
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
o=bpy.data.objects.new('CAD Remesh GUI',bpy.data.meshes.new('Fixture'));bpy.context.collection.objects.link(o)
cad.set_mesh(o,json.loads((ROOT/'tests/fixtures/fillet.json').read_text()),.001)
o.select_set(True);bpy.context.view_layer.objects.active=o
bpy.context.scene.cascade_remesh.method='EXACT'
window=bpy.context.window_manager.windows[0];area=next(a for a in window.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW');space=area.spaces.active
space.region_3d.view_distance=.14;space.region_3d.view_location=Vector((0,0,.01));space.region_3d.view_rotation=Vector((.12,-.16,.14)).to_track_quat('Z','Y');space.clip_start=.001;space.show_region_ui=True;space.shading.color_type='OBJECT';space.overlay.show_floor=False
checks=[];state={}
class TestMenu(bpy.types.Menu):
 bl_idname='CAD_MT_remesh_test';bl_label='Apply test'
 def draw(self,context):self.layout.operator('cascade.remesh_apply')
bpy.utils.register_class(TestMenu)
def invoke():
 with bpy.context.temp_override(window=window,area=area,region=region):assert bpy.ops.cascade.remesh_generate('INVOKE_DEFAULT')=={'RUNNING_MODAL'}
def cancel():
 state['process']=remesh.JOB['process'];window.event_simulate(type='ESC',value='PRESS')
def check_cancel():
 assert remesh.JOB is None and state['process'].poll() is not None and not o.hide_get();checks.append('Escape cancels worker without touching source');invoke()
def ready():
 if remesh.JOB and remesh.JOB['state']=='RUNNING':return False
 assert remesh.JOB and remesh.JOB['state']=='PREVIEW';assert len(remesh.JOB['batches'])==1
 checks.append('GPU preview loaded while original remains visible')
 with bpy.context.temp_override(window=window,area=area,region=region):bpy.ops.wm.call_menu(name='CAD_MT_quick')
 window.event_simulate(type='ESC',value='PRESS')
def apply():
 with bpy.context.temp_override(window=window,area=area,region=region):
  bpy.ops.ed.undo_push(message='Before remesh application')
  bpy.ops.wm.call_menu(name='CAD_MT_remesh_test')
def enter():window.event_simulate(type='RET',value='PRESS')
def applied():
 assert remesh.JOB is None and o.hide_get();checks.append('Apply creates free quad mesh and hides original')
def undo():
 with bpy.context.temp_override(window=window,area=area,region=region):assert bpy.ops.ed.undo()=={'FINISHED'}
def check_undo():
 src=bpy.data.objects['CAD Remesh GUI'];assert not src.hide_get() and not bpy.data.objects.get('CAD Remesh GUI | Remesh');checks.append('Undo restores original visibility and removes result')
 with bpy.context.temp_override(window=window,area=area,region=region):assert bpy.ops.ed.redo()=={'FINISHED'}
def check_redo():
 assert bpy.data.objects['CAD Remesh GUI'].hide_get() and bpy.data.objects.get('CAD Remesh GUI | Remesh');checks.append('Redo restores quads')
 bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'work/remesh_gui_undo.blend'))
 src=bpy.data.objects['CAD Remesh GUI'];src.hide_set(False);src.select_set(True);bpy.context.view_layer.objects.active=src;invoke()
 process=remesh.JOB['process'];bpy.ops.wm.open_mainfile(filepath=str(ROOT/'work/remesh_gui_undo.blend'))
 assert remesh.JOB is None and process.poll() is not None;checks.append('Opening another file cancels pending subprocess and preview');done()
def done():
 (ROOT/'validation_remesh_gui_030.json').write_text(json.dumps(checks,indent=2),encoding='utf-8');print('REMESH_GUI_PASS',checks,flush=True);cad.unregister();bpy.ops.wm.quit_blender()
steps=[invoke,cancel,check_cancel,ready,apply,enter,applied,undo,check_undo,check_redo,done];deadline=time.monotonic()+40
def tick():
 global deadline
 try:
  if time.monotonic()>deadline:raise TimeoutError(steps[0].__name__)
  if steps[0]() is not False:steps.pop(0);deadline=time.monotonic()+40
  return .4
 except Exception:
  import traceback
  (ROOT/'work/remesh_gui_failure.txt').write_text(traceback.format_exc());print(traceback.format_exc(),flush=True);cad.unregister();bpy.ops.wm.quit_blender()
bpy.app.timers.register(tick,first_interval=3)

