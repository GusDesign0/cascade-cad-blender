"""Cascade CAD: Blender interaction, isolated Open CASCADE geometry.
Copyright 2026 Cascade CAD contributors. GPL-3.0-or-later.
"""
bl_info = {'name': 'Cascade CAD', 'author': 'Cascade CAD contributors',
           'version': (0, 2, 0), 'blender': (4, 2, 0), 'category': 'Mesh',
           'location': 'View3D > Sidebar > CAD',
           'description': 'Solid modeling using a local Open CASCADE worker'}

import bpy
import blf
import gpu
import hashlib
import json
import math
import os
import subprocess
import shutil
import tempfile
import time
from pathlib import Path
from mathutils import Vector, Matrix
from bpy.props import StringProperty, FloatProperty, EnumProperty, IntProperty
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ImportHelper, ExportHelper
from gpu_extras.batch import batch_for_shader
from .live_fillet import CADFilletLive, warmup as warmup_preview, shutdown as shutdown_preview, before_load

ADDON = __package__ or 'cascade_cad'
ROOT = Path(__file__).resolve().parent
_busy = False
_keys = []
_selection_handle = None


def mm_scale(context):
    return 0.001 / max(context.scene.unit_settings.scale_length, 1e-12)


def fingerprint(mesh):
    h = hashlib.sha256()
    for v in mesh.vertices:
        h.update((','.join(format(x, '.8g') for x in v.co) + ';').encode())
    for poly in mesh.polygons:
        h.update((','.join(map(str, poly.vertices)) + ';').encode())
    return h.hexdigest()


def is_cad(obj):
    return obj is not None and obj.type == 'MESH' and bool(obj.get('cad_brep'))


def check_cad(obj):
    if not is_cad(obj):
        raise ValueError('Selecione um solido Cascade CAD')
    if obj.mode != 'OBJECT':
        raise ValueError('Volte ao Object Mode. Use as selecoes CAD do painel.')
    if obj.modifiers:
        raise ValueError('Remova os modificadores da previa CAD ou use uma copia de malha')
    if fingerprint(obj.data) != obj.get('cad_fingerprint'):
        raise ValueError('A malha foi editada. Restaure a previa CAD pelo painel.')
    scales = obj.matrix_world.to_scale()
    if min(scales) <= 0 or max(scales) - min(scales) > 1e-5 or obj.matrix_world.determinant() <= 0:
        raise ValueError('Use escala positiva e uniforme nos objetos CAD')
    cols = [obj.matrix_world.to_3x3().col[i].normalized() for i in range(3)]
    if any(abs(cols[i].dot(cols[j])) > 1e-5 for i in range(3) for j in range(i)):
        raise ValueError('Transformacoes com cisalhamento nao sao suportadas')


def matrix_mm(matrix, scale):
    out = [list(row) for row in matrix]
    for i in range(3):
        out[i][3] /= scale
    return out


def runtime_path(context):
    env = os.environ.get('CASCADE_CAD_PYTHON', '')
    pref = context.preferences.addons.get(ADDON)
    configured = pref.preferences.python_path if pref else ''
    candidates = [env, bpy.path.abspath(configured) if configured else '',
                  str(ROOT.parent / 'CascadeCAD_Runtime' / 'Scripts' / 'python.exe'),
                  str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) /
                      'CascadeCAD' / 'runtime' / 'python.exe')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ValueError('Motor ausente. Execute Instalar_Motor_CAD.ps1 e escolha o Python nas preferencias.')


def set_mesh(obj, result, scale):
    mesh = bpy.data.meshes.new('CAD | Superficie')
    mesh.from_pydata([[v * scale for v in pt] for pt in result['vertices']], [], result['triangles'])
    mesh.update()
    attr = mesh.attributes.new('cad_face', 'INT', 'FACE')
    attr.data.foreach_set('value', result['face_ids'])
    if result.get('smooth'):
        mesh.polygons.foreach_set('use_smooth', result['smooth'])
    old = obj.data
    if old:
        for mat in old.materials:
            mesh.materials.append(mat)
    obj.data = mesh
    if old and old.users == 0:
        bpy.data.meshes.remove(old)
    obj['cad_brep'] = result['brep']
    obj['cad_edges'] = json.dumps(result['edges'], separators=(',', ':'))
    obj['cad_mm_scale'] = scale
    obj['cad_faces'] = result['faces']
    obj['cad_edge_count'] = result['edge_count']
    obj['cad_solids'] = result['solids']
    obj['cad_volume'] = result['volume']
    obj['cad_selected_edges'] = '[]'
    obj['cad_selected_faces'] = '[]'
    obj['cad_fingerprint'] = fingerprint(mesh)
    obj.color = (0.16, 0.43, 0.58, 1)


class CADPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON
    python_path: StringProperty(name='Python do motor CAD', subtype='FILE_PATH',
                               description='Python separado com CadQuery 2.6.1 e OCP instalados')

    def draw(self, context):
        self.layout.prop(self, 'python_path')
        self.layout.operator('cascade.run', text='Verificar Open CASCADE', icon='CHECKMARK').operation = 'ping'
        self.layout.label(text='Use o instalador do pacote. O motor funciona localmente e offline apos instalar.')


class CADSettings(bpy.types.PropertyGroup):
    plane: EnumProperty(name='Plano', items=[('XY', 'XY', ''), ('XZ', 'XZ', ''), ('YZ', 'YZ', ''),
                                           ('VIEW', 'Vista', 'Plano paralelo a vista, passando pelo cursor 3D')])
    width: FloatProperty(name='Largura (mm)', default=60, min=0.01, max=100000)
    length: FloatProperty(name='Comprimento (mm)', default=40, min=0.01, max=100000)
    depth: FloatProperty(name='Altura / extrusao (mm)', default=20, min=0.01, max=100000)
    radius: FloatProperty(name='Raio (mm)', default=10, min=0.001, max=100000)
    fillet: FloatProperty(name='Raio / chanfro (mm)', default=2, min=0.001, max=100000)
    distance: FloatProperty(name='Push/pull (mm)', default=5, min=-100000, max=100000)
    thickness: FloatProperty(name='Espessura (mm)', default=-2, min=-100000, max=100000)
    snap: FloatProperty(name='Grade (mm)', default=1, min=0.01, max=10000)
    tolerance: FloatProperty(name='Tesselacao (mm)', default=0.12, min=0.005, max=5)
    status: StringProperty(default='Pronto | Open CASCADE local')


def plane_matrix(context):
    p = context.scene.cascade_cad.plane
    if p == 'XY':
        rotation = Matrix.Identity(4)
    elif p == 'XZ':
        rotation = Matrix.Rotation(math.pi / 2, 4, 'X')
    elif p == 'YZ':
        rotation = Matrix.Rotation(math.pi / 2, 4, 'Y')
    else:
        view = context.region_data or context.space_data.region_3d
        rotation = view.view_rotation.to_matrix().to_4x4()
    rotation.translation = context.scene.cursor.location
    return rotation


class CADRun(bpy.types.Operator):
    bl_idname = 'cascade.run'
    bl_label = 'Operacao CAD'
    bl_options = {'REGISTER', 'UNDO'}
    operation: StringProperty(default='primitive')
    kind: StringProperty(default='BOX')
    payload: StringProperty(default='')
    _timer = None
    _process = None
    _temp = None

    def prepare(self, context):
        s = context.scene.cascade_cad
        op = self.operation
        if op != 'ping' and context.mode != 'OBJECT':
            raise ValueError('As ferramentas CAD funcionam em Object Mode')
        req = {'op': op, 'tolerance': s.tolerance, 'params': {}}
        self._target = None
        self._cutters = []
        self._scale = mm_scale(context)
        self._placement = plane_matrix(context) if context.area and context.area.type == 'VIEW_3D' else Matrix.Translation(context.scene.cursor.location)
        if op == 'primitive':
            req['params'] = dict(kind=self.kind, x=s.width, y=s.length, z=s.depth, radius=s.radius)
        elif op == 'profile':
            data = json.loads(self.payload)
            req['params'] = data['params']
            self._placement = Matrix(data['matrix'])
        elif op == 'import':
            req['params'] = {'path': self.payload}
            self._placement = Matrix.Identity(4)
        elif op != 'ping':
            obj = context.active_object
            if op == 'refresh' and is_cad(obj):
                if obj.mode != 'OBJECT':
                    raise ValueError('Volte ao Object Mode antes de restaurar')
                if obj.modifiers:
                    raise ValueError('Remova os modificadores antes de restaurar a previa')
            else:
                check_cad(obj)
            self._target = obj
            self._scale = obj['cad_mm_scale']
            req['brep'] = obj['cad_brep']
            faces = json.loads(obj.get('cad_selected_faces', '[]'))
            edges = json.loads(obj.get('cad_selected_edges', '[]'))
            if op == 'boolean':
                operands = [o for o in context.selected_objects if o != obj]
                if not operands:
                    raise ValueError('Selecione alvo e cortador; o objeto ativo e o alvo')
                req['operands'] = []
                for other in operands:
                    check_cad(other)
                    if abs(other['cad_mm_scale'] - self._scale) > 1e-10:
                        raise ValueError('Os solidos usam escalas de unidade diferentes')
                    relative = obj.matrix_world.inverted() @ other.matrix_world
                    req['operands'].append({'brep': other['cad_brep'], 'matrix': matrix_mm(relative, self._scale)})
                req['params'] = {'kind': self.kind}
                self._cutters = operands
            elif op in ('fillet', 'chamfer'):
                if not edges:
                    raise ValueError('Use Selecionar arestas CAD antes de arredondar')
                req['params'] = dict(radius=s.fillet, edges=edges)
            elif op == 'pushpull':
                if len(faces) != 1:
                    raise ValueError('Selecione exatamente uma face CAD')
                req['params'] = dict(face=faces[0], distance=s.distance)
            elif op == 'shell':
                if not faces:
                    raise ValueError('Selecione as faces que serao abertas')
                if abs(s.thickness) < 0.001:
                    raise ValueError('Informe uma espessura diferente de zero')
                req['params'] = dict(faces=faces, thickness=s.thickness)
            elif op == 'export':
                req['params'] = {'path': self.payload}
                req['matrix'] = matrix_mm(obj.matrix_world, self._scale)
        return req

    def apply(self, context, result):
        if not result.get('ok'):
            raise ValueError(result.get('error', 'Falha no motor CAD'))
        if self.operation == 'ping':
            context.scene.cascade_cad.status = 'CadQuery ' + result['cadquery'] + ' | OCP ' + result['ocp']
            self.report({'INFO'}, context.scene.cascade_cad.status)
            return
        if self.operation == 'export':
            destination = Path(self.payload)
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.step', delete=False) as staged:
                stage_path = Path(staged.name)
            try:
                shutil.copyfile(result['path'], stage_path)
                os.replace(stage_path, destination)
            finally:
                if stage_path.exists():
                    stage_path.unlink()
            self.report({'INFO'}, 'STEP exportado: ' + str(destination))
            return
        obj = self._target
        if obj is None:
            obj = bpy.data.objects.new('CAD | ' + (self.kind if self.operation == 'primitive' else 'Solido'),
                                       bpy.data.meshes.new('CAD'))
            context.collection.objects.link(obj)
            obj.matrix_world = self._placement
        set_mesh(obj, result, self._scale)
        for cutter in self._cutters:
            cutter.hide_set(True)
        for item in context.selected_objects:
            item.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        context.scene.cascade_cad.status = f"{result['solids']} solido(s) | {result['faces']} faces CAD"
        self.report({'INFO'}, 'CAD atualizado')

    def cleanup(self, context):
        global _busy
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self._process and self._process.poll() is None:
            self._process.kill()
            self._process.wait()
        if self._temp:
            self._temp.cleanup()
            self._temp = None
        _busy = False
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()

    def execute(self, context):
        global _busy
        if _busy:
            self.report({'WARNING'}, 'Aguarde a operacao CAD em andamento')
            return {'CANCELLED'}
        try:
            request = self.prepare(context)
            python = runtime_path(context)
            self._temp = tempfile.TemporaryDirectory(prefix='cascade_cad_')
            input_path = Path(self._temp.name) / 'request.json'
            self._output = Path(self._temp.name) / 'response.json'
            if self.operation == 'export':
                request['params']['path'] = str(Path(self._temp.name) / 'export.step')
            input_path.write_text(json.dumps(request), encoding='utf-8')
            self._process = subprocess.Popen([python, str(ROOT / 'worker.py'), str(input_path), str(self._output)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            _busy = True
            self._start = time.monotonic()
            if bpy.app.background:
                self._process.wait(timeout=180)
                self.apply(context, json.loads(self._output.read_text(encoding='utf-8')))
                self.cleanup(context)
                return {'FINISHED'}
            self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
            context.window_manager.modal_handler_add(self)
            context.scene.cascade_cad.status = 'Calculando com Open CASCADE... Esc cancela'
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self.cleanup(context)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def invoke(self, context, event):
        return self.execute(context)

    def modal(self, context, event):
        if event.type == 'ESC' or time.monotonic() - self._start > 180:
            context.scene.cascade_cad.status = 'Operacao cancelada; modelo preservado'
            self.cleanup(context)
            return {'CANCELLED'}
        if event.type == 'TIMER' and self._process.poll() is not None:
            try:
                if not self._output.exists():
                    raise ValueError('O motor encerrou sem resposta. Verifique o Python nas preferencias.')
                self.apply(context, json.loads(self._output.read_text(encoding='utf-8')))
                self.cleanup(context)
                return {'FINISHED'}
            except Exception as exc:
                context.scene.cascade_cad.status = 'Falha CAD; modelo anterior preservado'
                self.report({'ERROR'}, str(exc))
                self.cleanup(context)
                return {'CANCELLED'}
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'MOUSEMOVE', 'NDOF_MOTION'}:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}


def draw_lines(paths, color, width=2):
    coords = []
    for path in paths:
        for a, b in zip(path, path[1:]):
            coords.extend([a, b])
    if not coords:
        return
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    shader.bind()
    shader.uniform_float('viewportSize', gpu.state.viewport_get()[2:])
    shader.uniform_float('lineWidth', width)
    shader.uniform_float('color', color)
    batch_for_shader(shader, 'LINES', {'pos': coords}).draw(shader)


def hud(lines):
    font = 0
    blf.size(font, 16)
    for i, line in enumerate(lines):
        blf.position(font, 28, 100 - i * 23, 0)
        blf.color(font, 0.86, 0.95, 1, 1)
        blf.draw(font, line)


def draw_selection():
    context = bpy.context
    obj = context.active_object
    if _busy or not is_cad(obj) or obj.mode != 'OBJECT' or obj.hide_get():
        return
    try:
        chosen = json.loads(obj.get('cad_selected_edges', '[]'))
        if chosen:
            paths = json.loads(obj['cad_edges'])
            scale = obj['cad_mm_scale']
            matrix = obj.matrix_world
            gpu.state.depth_test_set('NONE')
            draw_lines([[matrix @ Vector([x * scale for x in p]) for p in paths[i]]
                        for i in chosen if i < len(paths)], (1, 0.55, 0.12, 1), 3)
        faces = set(json.loads(obj.get('cad_selected_faces', '[]')))
        if faces:
            attr = obj.data.attributes.get('cad_face')
            if not attr:
                return
            coords = []
            for poly in obj.data.polygons:
                if attr.data[poly.index].value in faces:
                    coords.extend([obj.matrix_world @ obj.data.vertices[v].co for v in poly.vertices])
            if coords:
                gpu.state.depth_test_set('LESS_EQUAL')
                gpu.state.blend_set('ALPHA')
                shader = gpu.shader.from_builtin('UNIFORM_COLOR')
                shader.bind()
                shader.uniform_float('color', (1, 0.55, 0.12, 0.35))
                batch_for_shader(shader, 'TRIS', {'pos': coords}).draw(shader)
    finally:
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('NONE')


class CADDraw(bpy.types.Operator):
    bl_idname = 'cascade.draw'
    bl_label = 'Desenhar perfil CAD'
    bl_description = 'Desenhe no plano escolhido; grade em mm, Alt desativa snap; Enter confirma'
    kind: EnumProperty(items=[('RECT', 'Retangulo', ''), ('CIRCLE', 'Circulo', ''), ('POLY', 'Polilinha', '')])

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D' and context.mode == 'OBJECT' and not _busy

    def invoke(self, context, event):
        self._area = context.area
        self._region = next(r for r in context.area.regions if r.type == 'WINDOW')
        self._rv3d = context.space_data.region_3d
        self._matrix = plane_matrix(context)
        self._inverse = self._matrix.inverted()
        self._scale = mm_scale(context)
        self._points = []
        self._hover = (0, 0)
        self._depth = context.scene.cascade_cad.depth
        self._buffer = ''
        self._stage = 'DRAW'
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_overlay, (), 'WINDOW', 'POST_VIEW')
        self._hud = bpy.types.SpaceView3D.draw_handler_add(self.draw_hud, (), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        self._area.header_text_set('Cascade CAD | Clique para desenhar | Enter confirma | Esc cancela')
        return {'RUNNING_MODAL'}

    def point(self, context, event):
        xy = (event.mouse_x - self._region.x, event.mouse_y - self._region.y)
        origin = view3d_utils.region_2d_to_origin_3d(self._region, self._rv3d, xy)
        direction = view3d_utils.region_2d_to_vector_3d(self._region, self._rv3d, xy)
        normal = self._matrix.to_3x3() @ Vector((0, 0, 1))
        denom = direction.dot(normal)
        if abs(denom) < 1e-6:
            return None
        t = (self._matrix.translation - origin).dot(normal) / denom
        local = self._inverse @ (origin + direction * t)
        x, y = local.x / self._scale, local.y / self._scale
        if not event.alt:
            grid = context.scene.cascade_cad.snap
            x, y = round(x / grid) * grid, round(y / grid) * grid
        if event.shift and self._points and self.kind == 'POLY':
            a = self._points[-1]
            if abs(x - a[0]) > abs(y - a[1]):
                y = a[1]
            else:
                x = a[0]
        return x, y

    def outline(self):
        if not self._points:
            return []
        a = self._points[0]
        b = self._hover if len(self._points) == 1 else self._points[-1]
        if self.kind == 'RECT':
            return [a, (b[0], a[1]), b, (a[0], b[1]), a]
        if self.kind == 'CIRCLE':
            r = math.dist(a, b)
            return [(a[0] + r * math.cos(i * math.tau / 64), a[1] + r * math.sin(i * math.tau / 64))
                    for i in range(65)]
        pts = self._points[:] if self._stage == 'DEPTH' else self._points + [self._hover]
        return pts + [pts[0]]

    def draw_overlay(self):
        if bpy.context.area != self._area:
            return
        outline = self.outline()
        if not outline:
            return
        def world(p, z):
            return self._matrix @ Vector((p[0] * self._scale, p[1] * self._scale, z * self._scale))
        base = [world(p, 0) for p in outline]
        paths = [base]
        if self._stage == 'DEPTH':
            top = [world(p, self._depth) for p in outline]
            paths.append(top)
            stride = 8 if self.kind == 'CIRCLE' else 1
            paths.extend([[a, b] for a, b in zip(base[::stride], top[::stride])])
        gpu.state.depth_test_set('NONE')
        draw_lines(paths, (0.12, 0.8, 1, 1))

    def draw_hud(self):
        if bpy.context.area != self._area:
            return
        if self._stage == 'DEPTH':
            hud(['CASCADE CAD / Extrusao', f'Altura: {self._depth:.3f} mm',
                 'Digite mm ou use a roda | Enter cria o solido | Esc cancela'])
        else:
            hud(['CASCADE CAD / ' + self.kind,
                 f'X {self._hover[0]:.2f} mm   Y {self._hover[1]:.2f} mm',
                 'Clique define pontos | Enter extruda | Alt sem grade | Shift ortogonal'])

    def cleanup(self):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        bpy.types.SpaceView3D.draw_handler_remove(self._hud, 'WINDOW')
        self._area.header_text_set(None)
        self._area.tag_redraw()

    def finish(self, context):
        pts = self._points
        if self.kind == 'POLY':
            if len(pts) < 3:
                raise ValueError('A polilinha precisa de pelo menos 3 pontos')
            params = dict(kind='POLY', points=pts, depth=self._depth)
            placement = self._matrix
        else:
            if len(pts) < 2 or math.dist(pts[0], pts[1]) < 0.01:
                raise ValueError('Defina um perfil com tamanho maior que zero')
            a, b = pts
            center = a if self.kind == 'CIRCLE' else ((a[0]+b[0])/2, (a[1]+b[1])/2)
            placement = self._matrix @ Matrix.Translation(Vector((center[0]*self._scale, center[1]*self._scale, 0)))
            params = dict(kind=self.kind, x=abs(b[0]-a[0]), y=abs(b[1]-a[1]),
                          radius=math.dist(a,b), depth=self._depth)
            if self.kind == 'RECT' and min(params['x'], params['y']) < 0.01:
                raise ValueError('O retangulo precisa de largura e altura')
        if abs(self._depth) < 0.001:
            raise ValueError('A extrusao deve ser diferente de zero')
        data = json.dumps({'params': params, 'matrix': [list(row) for row in placement]})
        self.cleanup()
        bpy.ops.cascade.run('INVOKE_DEFAULT', operation='profile', payload=data)

    def modal(self, context, event):
        self._area.tag_redraw()
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self.cleanup()
            return {'CANCELLED'}
        if self._stage == 'DEPTH':
            if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                self._depth += context.scene.cascade_cad.snap * (1 if event.type == 'WHEELUPMOUSE' else -1)
                return {'RUNNING_MODAL'}
            if event.value == 'PRESS':
                if event.type == 'BACK_SPACE':
                    self._buffer = self._buffer[:-1]
                elif event.ascii and event.ascii in '0123456789.-,':
                    self._buffer += event.ascii.replace(',', '.')
                if self._buffer:
                    try:
                        self._depth = float(self._buffer)
                    except ValueError:
                        pass
                if event.type in {'RET', 'NUMPAD_ENTER', 'LEFTMOUSE'}:
                    try:
                        self.finish(context)
                        return {'FINISHED'}
                    except Exception as exc:
                        self.report({'WARNING'}, str(exc))
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            p = self.point(context, event)
            if p is not None:
                self._hover = p
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            p = self.point(context, event)
            if p is None:
                self.report({'WARNING'}, 'Plano visto de lado; mude a vista ou o plano')
            else:
                self._hover = p
                self._points.append(p)
                if self.kind != 'POLY' and len(self._points) == 2:
                    self._stage = 'DEPTH'
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            if self.kind == 'POLY' and len(self._points) >= 3:
                self._stage = 'DEPTH'
        if event.type == 'BACK_SPACE' and event.value == 'PRESS' and self._points:
            self._points.pop()
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'NDOF_MOTION'}:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}


class CADPick(bpy.types.Operator):
    bl_idname = 'cascade.pick'
    bl_label = 'Selecionar elementos CAD'
    bl_description = 'Clique alterna selecao; Enter confirma; Esc restaura a selecao anterior'
    mode: EnumProperty(items=[('EDGE', 'Arestas', ''), ('FACE', 'Faces', '')])

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D' and is_cad(context.active_object) and context.mode == 'OBJECT' and not _busy

    def invoke(self, context, event):
        try:
            check_cad(context.active_object)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if self.mode == 'EDGE':
            warmup_preview(context)
        self._area = context.area
        self._region = next(r for r in context.area.regions if r.type == 'WINDOW')
        self._rv3d = context.space_data.region_3d
        self._obj = context.active_object
        self._paths = json.loads(self._obj['cad_edges'])
        self._scale = self._obj['cad_mm_scale']
        self._key = 'cad_selected_edges' if self.mode == 'EDGE' else 'cad_selected_faces'
        self._selected = set(json.loads(self._obj.get(self._key, '[]')))
        self._hover = -1
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_overlay, (), 'WINDOW', 'POST_VIEW')
        self._hud = bpy.types.SpaceView3D.draw_handler_add(self.draw_hud, (), 'WINDOW', 'POST_PIXEL')
        self._area.header_text_set('CAD | Clique seleciona | A todas | Enter confirma | B abre fillet | Esc cancela')
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def world(self, pt):
        return self._obj.matrix_world @ Vector([x * self._scale for x in pt])

    def draw_overlay(self):
        if bpy.context.area != self._area:
            return
        gpu.state.depth_test_set('NONE')
        if self.mode == 'EDGE':
            draw_lines([[self.world(p) for p in path] for path in self._paths], (0.16, 0.3, 0.38, 0.6), 1)
            draw_lines([[self.world(p) for p in self._paths[i]] for i in self._selected], (1, 0.55, 0.12, 1), 3)
            if self._hover >= 0:
                draw_lines([[self.world(p) for p in self._paths[self._hover]]], (0.1, 0.95, 1, 1), 4)
        else:
            attr = self._obj.data.attributes.get('cad_face')
            coords, colors = [], []
            for poly in self._obj.data.polygons:
                idx = attr.data[poly.index].value
                if idx in self._selected or idx == self._hover:
                    coords.extend([self._obj.matrix_world @ self._obj.data.vertices[v].co for v in poly.vertices])
                    colors.extend([(0.1, 0.8, 1, 0.5) if idx == self._hover else (1, 0.5, 0.1, 0.5)] * 3)
            if coords:
                gpu.state.blend_set('ALPHA')
                shader = gpu.shader.from_builtin('SMOOTH_COLOR')
                shader.bind()
                batch_for_shader(shader, 'TRIS', {'pos': coords, 'color': colors}).draw(shader)
                gpu.state.blend_set('NONE')

    def draw_hud(self):
        if bpy.context.area == self._area:
            hud(['CASCADE CAD / ' + ('Arestas' if self.mode == 'EDGE' else 'Faces'),
                 f'{len(self._selected)} selecionada(s) | Clique alterna | Enter confirma',
                 'A todas | Alt+A limpa | B abre fillet | Esc cancela'])

    def hover(self, event):
        xy = Vector((event.mouse_x - self._region.x, event.mouse_y - self._region.y))
        if self.mode == 'FACE':
            origin = view3d_utils.region_2d_to_origin_3d(self._region, self._rv3d, xy)
            direction = view3d_utils.region_2d_to_vector_3d(self._region, self._rv3d, xy)
            inv = self._obj.matrix_world.inverted()
            hit, loc, normal, index = self._obj.ray_cast(inv @ origin, (inv.to_3x3() @ direction).normalized())
            return self._obj.data.attributes['cad_face'].data[index].value if hit else -1
        best, chosen = 14.0, -1
        for i, path in enumerate(self._paths):
            points = [view3d_utils.location_3d_to_region_2d(self._region, self._rv3d, self.world(pt)) for pt in path]
            for a, b in zip(points, points[1:]):
                if a is None or b is None:
                    continue
                ab = b-a
                t = max(0, min(1, (xy-a).dot(ab) / max(ab.length_squared, 1e-12)))
                dist = (xy - (a+ab*t)).length
                if dist < best:
                    best, chosen = dist, i
        return chosen

    def cleanup(self):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        bpy.types.SpaceView3D.draw_handler_remove(self._hud, 'WINDOW')
        self._area.header_text_set(None)
        self._area.tag_redraw()

    def modal(self, context, event):
        self._area.tag_redraw()
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self.cleanup()
            return {'CANCELLED'}
        if event.type in {'RET', 'NUMPAD_ENTER', 'B'} and event.value == 'PRESS':
            self._obj[self._key] = json.dumps(sorted(self._selected))
            self.cleanup()
            if event.type == 'B' and self.mode == 'EDGE' and self._selected:
                bpy.ops.cascade.fillet_live('INVOKE_DEFAULT')
            return {'FINISHED'}
        if event.type == 'MOUSEMOVE':
            self._hover = self.hover(event)
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self._hover = self.hover(event)
            if self._hover >= 0:
                if self._hover in self._selected:
                    self._selected.remove(self._hover)
                else:
                    self._selected.add(self._hover)
        if event.type == 'A' and event.value == 'PRESS':
            count = len(self._paths) if self.mode == 'EDGE' else self._obj['cad_faces']
            self._selected = set() if event.alt else set(range(count))
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'NDOF_MOTION'}:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}


class CADImport(bpy.types.Operator, ImportHelper):
    bl_idname = 'cascade.import_step'
    bl_label = 'Importar STEP'
    filename_ext = '.step'
    filter_glob: StringProperty(default='*.step;*.stp', options={'HIDDEN'})

    def execute(self, context):
        bpy.ops.cascade.run('INVOKE_DEFAULT', operation='import', payload=self.filepath)
        return {'FINISHED'}


class CADExport(bpy.types.Operator, ExportHelper):
    bl_idname = 'cascade.export_step'
    bl_label = 'Exportar STEP'
    filename_ext = '.step'
    filter_glob: StringProperty(default='*.step;*.stp', options={'HIDDEN'})

    def execute(self, context):
        bpy.ops.cascade.run('INVOKE_DEFAULT', operation='export', payload=self.filepath)
        return {'FINISHED'}


class CADMeshCopy(bpy.types.Operator):
    bl_idname = 'cascade.mesh_copy'
    bl_label = 'Criar copia de malha'
    bl_description = 'Cria uma copia independente para editar, esculpir ou adicionar modificadores'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not is_cad(obj):
            return {'CANCELLED'}
        copy = obj.copy()
        copy.data = obj.data.copy()
        for key in list(copy.keys()):
            if key.startswith('cad_'):
                del copy[key]
        copy.name = obj.name + ' | Malha'
        context.collection.objects.link(copy)
        obj.select_set(False)
        obj.hide_set(True)
        copy.select_set(True)
        context.view_layer.objects.active = copy
        return {'FINISHED'}


class CADPanel(bpy.types.Panel):
    bl_label = 'CASCADE / Solid Modeling'
    bl_idname = 'CAD_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'CAD'

    def draw(self, context):
        layout = self.layout
        s = context.scene.cascade_cad
        layout.label(text=s.status, icon='INFO')
        layout.enabled = not _busy
        col = layout.column(align=True)
        col.prop(s, 'plane')
        col.label(text='Origem: cursor 3D | Medidas em mm')
        row = col.row(align=True)
        for kind, name in [('RECT', 'Retangulo'), ('CIRCLE', 'Circulo'), ('POLY', 'Polilinha')]:
            row.operator('cascade.draw', text=name).kind = kind
        col.prop(s, 'snap')
        box = layout.box()
        box.label(text='Solidos por medidas', icon='MESH_CUBE')
        box.prop(s, 'width')
        box.prop(s, 'length')
        box.prop(s, 'depth')
        box.prop(s, 'radius')
        row = box.row(align=True)
        for kind, name in [('BOX', 'Bloco'), ('CYLINDER', 'Cilindro'), ('SPHERE', 'Esfera')]:
            op = row.operator('cascade.run', text=name)
            op.operation, op.kind = 'primitive', kind
        obj = context.active_object
        box = layout.box()
        box.label(text='Edicao direta CAD', icon='EDITMODE_HLT')
        box.enabled = is_cad(obj)
        row = box.row(align=True)
        row.operator('cascade.pick', text='Selecionar arestas').mode = 'EDGE'
        row.operator('cascade.pick', text='Selecionar faces').mode = 'FACE'
        if is_cad(obj):
            ne = len(json.loads(obj.get('cad_selected_edges', '[]')))
            nf = len(json.loads(obj.get('cad_selected_faces', '[]')))
            box.label(text=f'{ne} aresta(s) | {nf} face(s) selecionada(s)')
        box.prop(s, 'fillet')
        box.operator('cascade.fillet_live', text='Fillet / Chanfro: arrastar (B)', icon='MOD_BEVEL')
        row = box.row(align=True)
        row.operator('cascade.run', text='Aplicar raio').operation = 'fillet'
        row.operator('cascade.run', text='Chanfro fixo').operation = 'chamfer'
        box.prop(s, 'distance')
        box.operator('cascade.run', text='Push / Pull face plana', icon='FACESEL').operation = 'pushpull'
        box.prop(s, 'thickness')
        box.operator('cascade.run', text='Shell / Abrir casca').operation = 'shell'
        box = layout.box()
        box.label(text='Booleanas | objeto ativo = alvo')
        row = box.row(align=True)
        for kind, name in [('CUT', 'Cortar'), ('UNION', 'Unir'), ('INTERSECT', 'Intersecao')]:
            op = row.operator('cascade.run', text=name)
            op.operation, op.kind = 'boolean', kind
        row = layout.row(align=True)
        row.operator('cascade.import_step', text='Importar STEP', icon='IMPORT')
        row.operator('cascade.export_step', text='Exportar STEP', icon='EXPORT')
        layout.prop(s, 'tolerance')
        row = layout.row(align=True)
        row.operator('cascade.run', text='Atualizar previa').operation = 'refresh'
        row.operator('cascade.mesh_copy', text='Malha livre')
        if is_cad(obj):
            layout.label(text=f"Volume local: {obj.get('cad_volume', 0):,.2f} mm3")
        layout.operator('cascade.run', text='Verificar motor', icon='CHECKMARK').operation = 'ping'
        layout.label(text='Shift + Q: menu CAD | Esc: cancelar')


class CADMenu(bpy.types.Menu):
    bl_idname = 'CAD_MT_quick'
    bl_label = 'Cascade CAD'

    def draw(self, context):
        col = self.layout
        for kind, name in [('RECT', 'Desenhar retangulo'), ('CIRCLE', 'Desenhar circulo'), ('POLY', 'Desenhar polilinha')]:
            col.operator('cascade.draw', text=name).kind = kind
        col.separator()
        col.operator('cascade.pick', text='Selecionar arestas CAD').mode = 'EDGE'
        col.operator('cascade.pick', text='Selecionar faces CAD').mode = 'FACE'
        col.operator('cascade.fillet_live', text='Fillet / Chanfro interativo (B)')
        col.operator('cascade.run', text='Push / Pull').operation = 'pushpull'
        col.separator()
        for kind, name in [('CUT', 'Cortar'), ('UNION', 'Unir'), ('INTERSECT', 'Intersecao')]:
            op = col.operator('cascade.run', text=name)
            op.operation, op.kind = 'boolean', kind


CLASSES = (CADPreferences, CADSettings, CADRun, CADDraw, CADPick, CADFilletLive, CADImport, CADExport, CADMeshCopy, CADPanel, CADMenu)


def register():
    global _selection_handle
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.cascade_cad = bpy.props.PointerProperty(type=CADSettings)
    bpy.app.handlers.load_pre.append(before_load)
    if not bpy.app.background:
        _selection_handle = bpy.types.SpaceView3D.draw_handler_add(draw_selection, (), 'WINDOW', 'POST_VIEW')
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
        kmi = km.keymap_items.new('wm.call_menu', 'Q', 'PRESS', shift=True)
        kmi.properties.name = 'CAD_MT_quick'
        _keys.append((km, kmi))
        kmi = km.keymap_items.new('cascade.fillet_live', 'B', 'PRESS')
        _keys.append((km, kmi))


def unregister():
    global _selection_handle
    shutdown_preview()
    if before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(before_load)
    if _selection_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_selection_handle, 'WINDOW')
        _selection_handle = None
    for km, kmi in _keys:
        km.keymap_items.remove(kmi)
    _keys.clear()
    del bpy.types.Scene.cascade_cad
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == '__main__':
    register()

