import bpy,sys,json,math
from pathlib import Path
from mathutils import Matrix
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import cascade_cad as cad
from cascade_cad import remesh
cad.register();bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
checks=[]
def select(o):
 for obj in bpy.context.selected_objects:obj.select_set(False)
 o.select_set(True);bpy.context.view_layer.objects.active=o;bpy.context.view_layer.update()
def fail(op):
 try:result=op()
 except RuntimeError:result={'CANCELLED'}
 assert result=={'CANCELLED'}
s=bpy.context.scene.cascade_remesh;s.method='EXACT'
fixture=json.loads((ROOT/'tests/fixtures/fillet.json').read_text())
o=bpy.data.objects.new('Attributed',bpy.data.meshes.new('fixture'));bpy.context.collection.objects.link(o);cad.set_mesh(o,fixture,.001)
uv=o.data.uv_layers.new(name='DesignUV')
for loop in o.data.loops:uv.data[loop.index].uv=o.data.vertices[loop.vertex_index].co.xy*10
for name in ('Metal','Rubber'):o.data.materials.append(bpy.data.materials.new(name))
for p in o.data.polygons:p.material_index=p.index%2
parent=bpy.data.objects.new('Assembly',None);bpy.context.collection.objects.link(parent);parent.location=(.3,.1,-.2);parent.rotation_euler=(.2,.4,.6);o.parent=parent;o.scale=(1.2,.9,1.1)
select(o);key=remesh.signature(o);matrix=o.matrix_world.copy()
assert bpy.ops.cascade.remesh_generate()=={'FINISHED'}
assert bpy.ops.cascade.remesh_apply()=={'FINISHED'}
r=bpy.context.object
assert r.parent==parent and all(abs(a-b)<2e-6 for ar,br in zip(matrix,r.matrix_world) for a,b in zip(ar,br))
assert r.data.uv_layers.get('DesignUV') and len(r.data.uv_layers)==1
assert [m.name for m in r.data.materials]==['Metal','Rubber']
assert set(p.material_index for p in r.data.polygons)=={0,1}
assert remesh.signature(o)==key
checks.append('Parent, world transform, two materials and UV preserved; source fingerprint unchanged')
o.hide_set(False);select(o)
assert bpy.ops.cascade.remesh_generate()=={'FINISHED'}
o.location.x+=.001;bpy.context.view_layer.update();fail(bpy.ops.cascade.remesh_apply)
assert remesh.JOB is None and not o.hide_get();checks.append('Stale transform rejected without hiding source')
bpy.ops.mesh.primitive_plane_add();plane=bpy.context.object;key=remesh.signature(plane)
fail(bpy.ops.cascade.remesh_generate);assert remesh.signature(plane)==key and remesh.JOB is None
checks.append('Open mesh rejected; source kept')
select(o);s.max_faces=100
assert bpy.ops.cascade.remesh_generate()=={'FINISHED'}
remesh.cleanup();assert not cad._busy;checks.append('Cleanup releases process, meshes and CAD lock')
(ROOT/'validation_remesh_safety_030.json').write_text(json.dumps(checks,indent=2),encoding='utf-8');print('REMESH_SAFETY_PASS',checks,flush=True);cad.unregister()


