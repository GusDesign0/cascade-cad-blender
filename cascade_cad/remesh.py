"""Non-destructive asynchronous quad remesh controller and viewport preview."""
from pathlib import Path
import importlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import bpy
from bpy.app.handlers import persistent
from . import remesh_setup
from .remesh_geometry import mesh_key

JOB = None


def host():
    return importlib.import_module(__package__)


def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()


def signature(obj):
    deps = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(deps)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=deps)
    try:
        return (mesh_key(mesh), tuple(x for row in obj.matrix_world for x in row),
                tuple(m.name if m else '' for m in mesh.materials))
    finally: evaluated.to_mesh_clear()


def unchanged(job):
    for obj, key in job['sources']:
        try:
            if signature(obj) != key: raise ValueError('A origem mudou durante o processamento. Gere uma nova previa.')
        except ReferenceError: raise ValueError('Um objeto de origem foi removido.')


@persistent
def cleanup(*args):
    global JOB
    job, JOB = JOB, None
    if job:
        process = job.get('process')
        if process and process.poll() is None:
            process.kill()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: pass
        handle = job.get('handle')
        if handle is not None: bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
        for mesh in job.get('meshes', []):
            try:
                if mesh.users == 0: bpy.data.meshes.remove(mesh)
            except ReferenceError: pass
        if job.get('log'): job['log'].close()
        shutil.rmtree(job['folder'], ignore_errors=True)
    host()._busy = False
    redraw()


def draw_preview():
    if not JOB or JOB['state'] != 'PREVIEW': return
    import gpu
    shader = JOB['shader']
    gpu.state.blend_set('ALPHA'); gpu.state.depth_test_set('LESS_EQUAL')
    try:
        shader.bind()
        shader.uniform_float('viewportSize', gpu.state.viewport_get()[2:])
        shader.uniform_float('lineWidth', 1.2)
        shader.uniform_float('color', (.05, .85, 1, .8))
        for obj, batch in JOB['batches']:
            with gpu.matrix.push_pop():
                gpu.matrix.multiply_matrix(obj.matrix_world)
                batch.draw(shader)
    except ReferenceError: pass
    finally:
        gpu.state.depth_test_set('NONE'); gpu.state.blend_set('NONE')


def preview(job, response):
    unchanged(job)
    names = [r['mesh'] for r in response['objects']]
    with bpy.data.libraries.load(str(job['folder']/'output.blend'), link=False) as (_, loaded):
        loaded.meshes = names
    job['meshes'] = loaded.meshes
    if len(loaded.meshes) != len(job['sources']) or any(m is None for m in loaded.meshes):
        raise ValueError('Previa incompleta.')
    if any(len(p.vertices) != 4 for m in loaded.meshes for p in m.polygons):
        raise ValueError('Resultado invalido: existem faces que nao sao quads.')
    job['response'] = response; job['state'] = 'PREVIEW'
    if not bpy.app.background:
        import gpu
        from gpu_extras.batch import batch_for_shader
        job['shader'] = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        job['batches'] = []
        total = sum(len(m.edges) for m in job['meshes'])
        step = max(1, (total+119999)//120000)
        job['wire_sampled'] = step > 1
        for (obj, _), mesh in zip(job['sources'], job['meshes']):
            coords = [v.co[:] for v in mesh.vertices]
            edges = [e.vertices[:] for e in mesh.edges][::step]
            job['batches'].append((obj, batch_for_shader(job['shader'], 'LINES', {'pos': coords}, indices=edges)))
        job['handle'] = bpy.types.SpaceView3D.draw_handler_add(draw_preview, (), 'WINDOW', 'POST_VIEW')
    redraw()


class RemeshSettings(bpy.types.PropertyGroup):
    method: bpy.props.EnumProperty(name='Metodo', items=[('AUTO','Adaptativo protegido','QuadWild; conversao fiel se a reducao falhar'),('NATIVE','QuadWild estrito','Rejeita qualquer resultado fora da tolerancia'),('EXACT','Superficie fiel','Conversao em quads sem suavizar; pode aumentar a malha')])
    density: bpy.props.FloatProperty(name='Densidade', default=1.4, min=.2, max=8, description='Mais densidade aumenta a quantidade de quads; nao e uma meta exata')
    tolerance_mm: bpy.props.FloatProperty(name='Tolerancia (mm)', default=.035, min=.001, max=10, precision=3)
    sharp_angle: bpy.props.FloatProperty(name='Angulo dos vincos', default=35, min=1, max=89)
    cad_guides: bpy.props.BoolProperty(name='Proteger limites CAD', default=True)
    material_guides: bpy.props.BoolProperty(name='Proteger limites de materiais', default=True)
    attempts: bpy.props.IntProperty(name='Tentativas adaptativas', default=2, min=1, max=3)
    solver_seconds: bpy.props.IntProperty(name='Limite do solver (s)', default=90, min=5, max=600)
    timeout: bpy.props.IntProperty(name='Tempo total maximo (s)', default=1800, min=30, max=21600)
    max_faces: bpy.props.IntProperty(name='Limite de quads por objeto', default=2000000, min=100, max=5000000)
    advanced: bpy.props.BoolProperty(name='Opcoes avancadas')
    status: bpy.props.StringProperty(default='Originais preservados; previa antes de aplicar')
    last_report: bpy.props.StringProperty(default='')


class RemeshGenerate(bpy.types.Operator):
    bl_idname = 'cascade.remesh_generate'
    bl_label = 'Gerar previa de quads'
    install_engine: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return not host()._busy and JOB is None and context.mode == 'OBJECT'

    def execute(self, context):
        global JOB
        s = context.scene.cascade_remesh
        try:
            sources = [o for o in context.selected_objects if o.type == 'MESH' and o.data.polygons]
            if not self.install_engine and not sources: raise ValueError('Selecione pelo menos uma malha fechada no Modo Objeto.')
            if self.install_engine and not bpy.app.online_access: raise ValueError('Ative o acesso a internet do Blender para baixar o motor opcional.')
            if not self.install_engine and s.method != 'EXACT' and not remesh_setup.available():
                raise ValueError('Instale o motor QuadWild no botao abaixo ou use Superficie fiel.')
            folder = Path(tempfile.mkdtemp(prefix='CascadeRemesh_'))
            JOB = {'folder': folder, 'sources': [], 'meshes': [], 'state': 'RUNNING', 'started': time.monotonic(), 'timeout': s.timeout}
            host()._busy = True
            settings = {name: getattr(s, name) for name in ('method','density','tolerance_mm','sharp_angle','cad_guides','material_guides','attempts','solver_seconds','max_faces')}
            for obj in sources: JOB['sources'].append((obj, signature(obj)))
            if not self.install_engine: bpy.data.libraries.write(str(folder/'input.blend'), set(sources), compress=True)
            request = {'settings': settings, 'objects': [{'name': o.name} for o in sources],
                       'unit_mm': 1000*context.scene.unit_settings.scale_length, 'frame': context.scene.frame_current,
                       'install_engine': self.install_engine}
            (folder/'request.json').write_text(json.dumps(request), encoding='utf-8')
            JOB['log'] = (folder/'worker.log').open('w', encoding='utf-8')
            env = os.environ.copy(); env['OMP_NUM_THREADS'] = '4'
            args = [bpy.app.binary_path, '-b', '-t', '4', '--factory-startup', '--python', str(Path(__file__).with_name('remesh_worker.py')), '--', str(folder)]
            JOB['process'] = subprocess.Popen(args, stdout=JOB['log'], stderr=subprocess.STDOUT, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            s.status = 'Calculando em processo separado...'
            if bpy.app.background:
                while JOB and JOB['process'].poll() is None:
                    if time.monotonic()-JOB['started'] > JOB['timeout']: raise TimeoutError('Tempo de processamento excedido.')
                    time.sleep(.1)
                return self.finish(context)
            self.timer = context.window_manager.event_timer_add(.4, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            cleanup(); s.status = str(exc); self.report({'ERROR'}, str(exc)); return {'CANCELLED'}

    def finish(self, context):
        s = context.scene.cascade_remesh
        try:
            path = JOB['folder']/'response.json'
            if not path.exists(): raise ValueError('O motor encerrou sem resultado. Os originais foram preservados.')
            response = json.loads(path.read_text(encoding='utf-8'))
            if not response.get('ok'): raise ValueError(response.get('error', 'Falha no motor.'))
            if response.get('installed'):
                cleanup(); s.status = 'Motor QuadWild instalado e verificado.'
            else:
                preview(JOB, response)
                s.status = 'Previa pronta. Confira o relatorio e aplique ou descarte.'
            return {'FINISHED'}
        except Exception as exc:
            cleanup(); s.status = str(exc); self.report({'ERROR'}, str(exc)); return {'CANCELLED'}

    def modal(self, context, event):
        if JOB is None:
            context.window_manager.event_timer_remove(self.timer); return {'CANCELLED'}
        if event.type == 'ESC' or time.monotonic()-JOB['started'] > JOB['timeout']:
            context.window_manager.event_timer_remove(self.timer); cleanup()
            context.scene.cascade_remesh.status = 'Processamento cancelado; originais preservados.'
            return {'CANCELLED'}
        if event.type == 'TIMER':
            try:
                progress = json.loads((JOB['folder']/'progress.json').read_text(encoding='utf-8'))
                context.scene.cascade_remesh.status = progress['stage'] + (' | %d/%d' % (progress['index'],progress['total']) if 'index' in progress else '')
            except (OSError, ValueError): pass
            redraw()
            if JOB['process'].poll() is not None:
                context.window_manager.event_timer_remove(self.timer); return self.finish(context)
        return {'PASS_THROUGH'}


class RemeshDiscard(bpy.types.Operator):
    bl_idname = 'cascade.remesh_discard'
    bl_label = 'Cancelar / descartar previa'
    @classmethod
    def poll(cls, context): return JOB is not None
    def execute(self, context):
        cleanup(); context.scene.cascade_remesh.status = 'Cancelado; nenhum original alterado.'
        return {'FINISHED'}


class RemeshApply(bpy.types.Operator):
    bl_idname = 'cascade.remesh_apply'
    bl_label = 'Aplicar quads e guardar originais'
    bl_options = {'REGISTER', 'UNDO'}
    @classmethod
    def poll(cls, context): return JOB is not None and JOB['state'] == 'PREVIEW'
    def execute(self, context):
        created = []; hidden = []
        try:
            unchanged(JOB)
            response = JOB['response']
            for (source, _), mesh, report in zip(JOB['sources'], JOB['meshes'], response['objects']):
                obj = source.copy(); obj.data = mesh; obj.name = source.name+' | Remesh'
                obj.modifiers.clear(); obj.constraints.clear(); obj.animation_data_clear()
                for key in list(obj.keys()):
                    if key.startswith('cad_'): del obj[key]
                for name in report['materials']:
                    mesh.materials.append(bpy.data.materials.get(name) if name else None)
                attr = mesh.attributes.get('cascade_material_index')
                if attr:
                    for p in mesh.polygons: p.material_index = min(attr.data[p.index].value, max(0,len(mesh.materials)-1))
                    mesh.attributes.remove(attr)
                for collection in source.users_collection: collection.objects.link(obj)
                if not obj.users_collection: context.collection.objects.link(obj)
                created.append(obj)
                obj.matrix_world = source.matrix_world.copy()
                obj.hide_render = False; obj.hide_set(False)
                obj['cascade_remesh_method'] = report['method']
                obj['cascade_remesh_source'] = source.name
                obj['cascade_remesh_error_mm'] = report['result']['sampled_max_mm']
            for source, _ in JOB['sources']:
                hidden.append((source,source.hide_get(),source.hide_render))
                source.hide_set(True); source.hide_render = True
            context.view_layer.update()
            for o in context.selected_objects: o.select_set(False)
            for obj in created: obj.select_set(True)
            context.view_layer.objects.active = created[0]
            context.scene.cascade_remesh.last_report = json.dumps(response, ensure_ascii=False)
            cleanup()
            context.scene.cascade_remesh.status = 'Quads aplicados. Originais guardados ocultos; Ctrl+Z desfaz.'
            return {'FINISHED'}
        except Exception as exc:
            for obj in created: bpy.data.objects.remove(obj,do_unlink=True)
            for obj, viewport, render in hidden: obj.hide_set(viewport); obj.hide_render = render
            cleanup(); self.report({'ERROR'},str(exc)); return {'CANCELLED'}


class RemeshReport(bpy.types.Operator):
    bl_idname = 'cascade.remesh_report'
    bl_label = 'Abrir relatorio completo'
    def execute(self, context):
        value = json.dumps(JOB['response'], ensure_ascii=False, indent=2) if JOB and JOB.get('response') else context.scene.cascade_remesh.last_report
        text = bpy.data.texts.get('Cascade - Relatorio Remesh.json') or bpy.data.texts.new('Cascade - Relatorio Remesh.json')
        text.clear(); text.write(value or '{}')
        self.report({'INFO'}, 'Relatorio disponivel no Editor de Texto: Cascade - Relatorio Remesh.json')
        return {'FINISHED'}


class RemeshPanel(bpy.types.Panel):
    bl_label = 'CASCADE / Remesh de quads'
    bl_idname = 'CAD_PT_remesh'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'CAD'
    def draw(self, context):
        layout = self.layout; s = context.scene.cascade_remesh
        layout.label(text=s.status, icon='INFO')
        if JOB:
            if JOB['state'] == 'PREVIEW':
                reports = JOB['response']['objects']
                faithful = sum(r['method'].startswith('EXACT') for r in reports)
                layout.label(text='%d objeto(s) | %d via modo fiel' % (len(reports),faithful))
                layout.label(text='%s quads | max. medido %.4f mm' % (format(sum(r['faces'] for r in reports),','), max(r['result']['sampled_max_mm'] for r in reports)))
                if JOB.get('wire_sampled'): layout.label(text='Previa de arestas amostrada (malha completa ao aplicar)')
                layout.operator('cascade.remesh_apply',icon='CHECKMARK')
                layout.operator('cascade.remesh_report')
            layout.operator('cascade.remesh_discard',icon='CANCEL'); return
        col = layout.column(); col.enabled = not host()._busy
        col.prop(s,'method')
        if s.method != 'EXACT': col.prop(s,'density')
        col.prop(s,'tolerance_mm')
        col.prop(s,'cad_guides'); col.prop(s,'material_guides')
        col.prop(s,'advanced',icon='PREFERENCES')
        if s.advanced:
            for name in ('sharp_angle','attempts','solver_seconds','timeout','max_faces'): col.prop(s,name)
        col.operator('cascade.remesh_generate',icon='MOD_REMESH')
        if not remesh_setup.available():
            col.operator('cascade.remesh_generate',text='Instalar motor QuadWild (Windows)').install_engine = True
        if s.last_report: layout.operator('cascade.remesh_report')
        layout.label(text='Previa > Aplicar | Esc cancela o calculo')


CLASSES = (RemeshSettings,RemeshGenerate,RemeshDiscard,RemeshApply,RemeshReport,RemeshPanel)


def register():
    for cls in CLASSES: bpy.utils.register_class(cls)
    bpy.types.Scene.cascade_remesh = bpy.props.PointerProperty(type=RemeshSettings)
    bpy.app.handlers.load_pre.append(cleanup)


def unregister():
    cleanup()
    if cleanup in bpy.app.handlers.load_pre: bpy.app.handlers.load_pre.remove(cleanup)
    del bpy.types.Scene.cascade_remesh
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
