import bpy
import json
import math
import os
import sys
from pathlib import Path
from mathutils import Matrix, Vector

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cascade_cad as cad
cad.register()
checks=[]

def op(name, **kwargs):
    result=bpy.ops.cascade.run(**kwargs)
    assert result == {'FINISHED'}, (name,result)
    checks.append({'name':name,'passed':True})
    return bpy.context.active_object

def select(*objs, active=None):
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        o.hide_set(False)
        o.select_set(True)
    bpy.context.view_layer.objects.active=active or objs[0]
    bpy.context.view_layer.update()

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
s=bpy.context.scene.cascade_cad
op('runtime ping',operation='ping')
s.width,s.length,s.depth=80,50,12
base=op('CAD block',operation='primitive',kind='BOX')
base.name='CAD | Base mecanica'
assert math.isclose(base.dimensions.x,0.08,abs_tol=1e-5)
base['cad_selected_edges']=json.dumps(list(range(base['cad_edge_count'])))
s.fillet=3
op('CAD fillet',operation='fillet')
original_volume=base['cad_volume']
s.radius,s.depth=5,25
cutter=op('CAD cylinder',operation='primitive',kind='CYLINDER')
cutter.location=(0.025,0,-0.005)
select(base,cutter,active=base)
op('CAD cut transformed cutter',operation='boolean',kind='CUT')
assert base['cad_volume'] < original_volume and cutter.hide_get()
cutter2=cutter.copy(); cutter2.data=cutter.data.copy()
bpy.context.collection.objects.link(cutter2)
cutter2.location.x=-0.025
select(base,cutter2,active=base)
op('CAD second cut',operation='boolean',kind='CUT')
s.radius,s.depth=12,18
boss=op('CAD boss',operation='primitive',kind='CYLINDER')
boss.location.z=0.010
select(base,boss,active=base)
op('CAD union',operation='boolean',kind='UNION')
s.radius,s.depth=5,40
hole=op('CAD center hole',operation='primitive',kind='CYLINDER')
hole.location.z=-0.005
select(base,hole,active=base)
op('CAD bore',operation='boolean',kind='CUT')
op('CAD STEP export',operation='export',payload=str(ROOT/'Exemplo_CascadeCAD.step'))
export_volume=base['cad_volume']
imported=op('CAD STEP import',operation='import',payload=str(ROOT/'Exemplo_CascadeCAD.step'))
assert math.isclose(imported['cad_volume'],export_volume,rel_tol=1e-7)
imported.hide_set(True)
select(base)
saved_brep=base['cad_brep']
saved_data=base.data
base['cad_selected_edges']='[0]'
s.fillet=10000
try:
    bpy.ops.cascade.run(operation='fillet')
except RuntimeError:
    pass
assert base['cad_brep']==saved_brep and base.data==saved_data
checks.append({'name':'failed fillet preserves model','passed':True})
base.data.vertices[0].co.x+=0.001
try:
    cad.check_cad(base)
except ValueError:
    checks.append({'name':'edited preview detected','passed':True})
else:
    raise AssertionError('Edited preview accepted')
op('restore original CAD preview',operation='refresh')
cad.check_cad(base)
assert bpy.ops.cascade.mesh_copy()=={'FINISHED'}
copy=bpy.context.active_object
assert not cad.is_cad(copy) and cad.is_cad(base)
bpy.data.objects.remove(copy,do_unlink=True)
checks.append({'name':'independent mesh conversion','passed':True})
select(base)
# Camera and render: this is an actual tessellation of the validated CAD solid.
scene=bpy.context.scene
scene.unit_settings.system='METRIC'
scene.unit_settings.length_unit='MILLIMETERS'
camera_data=bpy.data.cameras.new('Camera')
camera=bpy.data.objects.new('Camera',camera_data)
scene.collection.objects.link(camera)
camera.location=(0.14,-0.19,0.16)
direction=Vector((0,0,0.012))-camera.location
camera.rotation_euler=direction.to_track_quat('-Z','Y').to_euler()
camera_data.type='ORTHO'; camera_data.ortho_scale=0.13
camera_data.clip_start=0.001
scene.camera=camera
camera.hide_set(True)
scene.render.engine='BLENDER_WORKBENCH'
scene.display.shading.light='STUDIO'
scene.display.shading.studio_light='paint.sl'
scene.display.shading.color_type='OBJECT'
scene.display.shading.show_shadows=True
scene.display.shading.show_cavity=True
scene.display.shading.cavity_type='BOTH'
scene.display.shading.curvature_ridge_factor=1.3
scene.display.shading.curvature_valley_factor=1.1
scene.display.shading.background_type='WORLD'
scene.world.color=(0.035,0.045,0.06)
scene.render.resolution_x=1200; scene.render.resolution_y=900
scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'
scene.render.filepath=str(ROOT/'Previa_CascadeCAD.png')
for area in bpy.context.screen.areas:
    if area.type=='VIEW_3D':
        area.spaces.active.region_3d.view_distance=0.14
        area.spaces.active.region_3d.view_location=Vector((0,0,0.01))
        area.spaces.active.region_3d.view_rotation=camera.rotation_euler.to_quaternion()
        area.spaces.active.clip_start=0.001
        area.spaces.active.show_region_ui=True
        area.spaces.active.shading.color_type='OBJECT'
        area.spaces.active.overlay.show_floor=False
        area.spaces.active.overlay.show_axis_x=False
        area.spaces.active.overlay.show_axis_y=False
        area.spaces.active.region_3d.view_perspective='ORTHO'
s.fillet=2
base['cad_selected_edges']='[]'
saved_brep=base['cad_brep']
scene.cascade_cad.status='Open CASCADE | Exemplo editavel'
for obj in list(scene.objects):
    if obj!=base and obj!=camera:
        bpy.data.objects.remove(obj,do_unlink=True)
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'Exemplo_CascadeCAD.blend'))
bpy.ops.render.render(write_still=True)
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'Exemplo_CascadeCAD.blend'))
restored=bpy.data.objects.get('CAD | Base mecanica')
assert cad.is_cad(restored) and restored['cad_brep']==saved_brep
cad.check_cad(restored)
select(restored)
op('CAD operation after file reopen',operation='refresh')
checks.append({'name':'BREP persisted across blend reopen','passed':True})
cad.unregister()
cad.register()
checks.append({'name':'unregister and register','passed':True})
(ROOT/'validation_blender.json').write_text(json.dumps({'blender':bpy.app.version_string,'checks':checks},indent=2),encoding='utf8')
print('BLENDER_CAD_VALIDATED',len(checks))

