"""Cancelable Blender subprocess. Native failures never enter the user's process."""
import json
import os
from pathlib import Path
import sys
import time
import traceback
import bpy

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from remesh_geometry import Reference,obj_mesh,subdivide_linear
from remesh_native import NativeQuadWild


def atomic(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,allow_nan=False),encoding='utf-8')
    for attempt in range(50):
        try:os.replace(temporary,path);return
        except PermissionError:
            if attempt==49:raise
            time.sleep(.01)


def run(folder):
    request=json.loads((folder/'request.json').read_text(encoding='utf-8'))
    settings=request['settings'];started=time.monotonic();reports=[];output=[]
    if request.get('install_engine'):
        import remesh_setup
        atomic(folder/'progress.json',{'stage':'Instalando motor verificado','fraction':.1})
        path=remesh_setup.install()
        atomic(folder/'response.json',{'ok':True,'installed':path});return
    with bpy.data.libraries.load(str(folder/'input.blend'),link=False) as (data,loaded):
        loaded.objects=data.objects
    for obj in loaded.objects:
        if obj is not None and obj.name not in bpy.context.scene.objects:
            bpy.context.scene.collection.objects.link(obj)
    bpy.context.scene.frame_set(request.get('frame',1))
    deps=bpy.context.evaluated_depsgraph_get();deps.update()
    for index,item in enumerate(request['objects']):
        obj=bpy.data.objects[item['name']];evaluated=obj.evaluated_get(deps);mesh=evaluated.to_mesh(preserve_all_data_layers=True,depsgraph=deps)
        ref=None;candidate=None;attempts=[];method='QUADWILD';native_error=None
        def progress(stage):
            atomic(folder/'progress.json',{'stage':stage,'object':item['name'],'index':index+1,
                'total':len(request['objects']),'fraction':index/len(request['objects']),
                'elapsed_s':round(time.monotonic()-started,1)})
        try:
            progress('Detectando vincos, ilhas e paineis CAD')
            ref=Reference(mesh,obj.matrix_world,request['unit_mm'],settings['sharp_angle'],settings['cad_guides'],settings['material_guides'])
            if ref.source_topology['nonmanifold'] or ref.source_topology['degenerate']:
                raise ValueError('A origem contem arestas abertas/nao manifold ou faces degeneradas. Corrija a malha antes do remesh.')
            if settings['method']!='EXACT' and ref.source_topology['components']==1:
                try:
                    path=folder/('object_%04d'%index);path.mkdir(exist_ok=True)
                    progress('Preparando guias protegidas')
                    export=ref.export_native(path)
                    engine=NativeQuadWild(path)
                    progress('QuadWild: calculando campo de direcoes');engine.field(settings['sharp_angle'])
                    progress('QuadWild: decompondo paineis');engine.trace()
                    for attempt in range(settings['attempts']):
                        density=(1/settings['density'])*(.7**attempt)
                        progress('Bi-MDF: gerando quads (%d/%d)'%(attempt+1,settings['attempts']))
                        candidate=obj_mesh(engine.quads(density,settings['solver_seconds']))
                        if len(candidate.polygons)>settings['max_faces']:
                            raise ValueError('Resultado excedeu o limite de quads configurado.')
                        progress('Medindo superficie e detalhes')
                        measured=ref.measure(candidate,settings['tolerance_mm']);attempts.append({'stage':'native','density':density,**measured})
                        if measured['accepted']:break
                        # Bounded, region-constrained projection is a candidate,
                        # never an excuse to skip the independent shape tests.
                        for _ in range(3):ref.project(candidate,settings['tolerance_mm'])
                        measured=ref.measure(candidate,settings['tolerance_mm']);attempts.append({'stage':'projected',**measured})
                        if measured['accepted']:break
                        if attempt==settings['attempts']-1 and len(candidate.polygons)*4<=settings['max_faces']:
                            old=candidate;candidate=subdivide_linear(candidate)
                            if old.users==0:bpy.data.meshes.remove(old)
                            for _ in range(3):ref.project(candidate,settings['tolerance_mm'])
                            measured=ref.measure(candidate,settings['tolerance_mm']);attempts.append({'stage':'refined',**measured})
                            if measured['accepted']:method='QUADWILD_REFINED';break
                        bpy.data.meshes.remove(candidate);candidate=None
                    if candidate is None or not measured['accepted']:
                        raise ValueError('O remesh adaptativo ultrapassou a tolerancia dos detalhes.')
                except Exception as exc:
                    native_error=str(exc)
                    if candidate is not None and candidate.users==0:bpy.data.meshes.remove(candidate)
                    candidate=None
                    if settings['method']=='NATIVE':raise
            if candidate is None:
                if settings['method']=='NATIVE':raise ValueError('QuadWild requer uma unica ilha fechada por objeto.')
                progress('Modo fiel: preservando a superficie original')
                method='EXACT' if settings['method']=='EXACT' else 'EXACT_FALLBACK'
                candidate=ref.exact_quads()
                if len(candidate.polygons)>settings['max_faces']:raise ValueError('A preservacao exata excedeu o limite de quads.')
                measured=ref.measure(candidate,settings['tolerance_mm']);attempts.append({'stage':'exact',**measured})
                if not measured['accepted']:raise ValueError('A conversao fiel nao passou na verificacao: '+', '.join(measured['reasons']))
            progress('Transferindo materiais e coordenadas UV')
            ref.transfer(candidate)
            candidate.name='CC_REMESH_%04d'%index
            materials=[m.name if m else None for m in mesh.materials]
            candidate.materials.clear()
            output.append(candidate)
            reports.append({'source':item['name'],'mesh':candidate.name,'method':method,
                'source_faces':len(mesh.polygons),'faces':len(candidate.polygons),'materials':materials,
                'result':measured,'attempts':attempts,'native_note':native_error})
        finally:
            if ref is not None:ref.close()
            evaluated.to_mesh_clear()
    progress('Salvando previa verificada')
    bpy.data.libraries.write(str(folder/'output.blend'),set(output),compress=True)
    atomic(folder/'response.json',{'ok':True,'objects':reports,'elapsed_s':round(time.monotonic()-started,2),
                                'engine':'QuadWild + Bi-MDF','settings':settings})


if __name__=='__main__':
    folder=Path(sys.argv[sys.argv.index('--')+1]).resolve()
    try:run(folder)
    except Exception as exc:
        atomic(folder/'response.json',{'ok':False,'error':str(exc),'traceback':traceback.format_exc()})
        print(traceback.format_exc(),flush=True)
