"""Interactive, non-destructive CAD previews. GPL-3.0-or-later."""
import importlib
import atexit
import json
import math
import time

import bpy
import blf
import gpu
from bpy.props import EnumProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .preview_engine import PreviewEngine

_engine = None
_active = None


def host():
    return importlib.import_module(__package__)


def ensure_engine(context):
    global _engine
    if _engine is None or not _engine.alive():
        if _engine is not None:
            _engine.close()
        h = host()
        _engine = PreviewEngine(h.runtime_path(context), h.ROOT / 'worker.py')
    return _engine


def warmup(context):
    try:
        ensure_engine(context)
    except (OSError, ValueError):
        pass  # The actual tool reports a missing runtime when invoked.


def close_engine():
    global _engine
    if _engine is not None:
        _engine.close()
        _engine = None


def shutdown(*args):
    if _active is not None:
        _active.cleanup(bpy.context)
    close_engine()


@bpy.app.handlers.persistent
def before_load(*args):
    shutdown()


atexit.register(close_engine)


def edge_at(paths, matrix, scale, region, view, xy):
    best, chosen = 14.0, -1
    for i, path in enumerate(paths):
        points = [view3d_utils.location_3d_to_region_2d(region, view, matrix @ Vector([v*scale for v in p])) for p in path]
        for a, b in zip(points, points[1:]):
            if a is None or b is None:
                continue
            ab = b-a
            t = max(0, min(1, (xy-a).dot(ab)/max(ab.length_squared, 1e-12)))
            distance = (xy-(a+ab*t)).length
            if distance < best:
                best, chosen = distance, i
    return chosen


class CADFilletLive(bpy.types.Operator):
    bl_idname = 'cascade.fillet_live'
    bl_label = 'Fillet / Chanfro interativo'
    bl_description = 'Arraste a alca amarela com previa CAD; Enter ou botao direito confirma, Esc cancela'
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}
    initial_mode: EnumProperty(items=[('FILLET', 'Fillet', ''), ('CHAMFER', 'Chanfro', '')])

    @classmethod
    def poll(cls, context):
        h = host()
        obj = context.active_object
        return (context.area is not None and context.area.type == 'VIEW_3D' and context.mode == 'OBJECT'
                and not h._busy and h.is_cad(obj) and bool(json.loads(obj.get('cad_selected_edges', '[]'))))

    def invoke(self, context, event):
        global _active
        h = host()
        try:
            h.check_cad(context.active_object)
            self._engine = ensure_engine(context)
            self._obj = context.active_object
            self._area = context.area
            self._region = next(r for r in self._area.regions if r.type == 'WINDOW')
            self._view = context.space_data.region_3d
            self._scene = context.scene
            self._matrix = self._obj.matrix_world.copy()
            self._scale = self._obj['cad_mm_scale']
            self._source = self._obj['cad_brep']
            self._paths = json.loads(self._obj['cad_edges'])
            self._selected = set(json.loads(self._obj.get('cad_selected_edges', '[]')))
            self._selection_snapshot = list(context.selected_objects)
            self._old_status = context.scene.cascade_cad.status
            self._value = context.scene.cascade_cad.fillet * (-1 if self.initial_mode == 'CHAMFER' else 1)
            self._raw_value = self._value
            self._buffer = ''
            self._dragging = False
            self._last_x = 0
            self._id = None
            self._sent_key = None
            self._display_key = None
            self._display_id = -1
            self._requests = {}
            self._error_key = None
            self._error = ''
            self._latency = None
            self._batch = None
            self._shader = None
            self._timer = None
            self._draw3d = None
            self._draw2d = None
            self._dirty = True
            self._changed_at = time.monotonic()-1
            self._last_submit_at = 0
            self._pending_since = None
            self._committing = False
            self._hidden = False
            self._closed = False
            center = sum((self._matrix @ v for v in [Vector(c) for c in self._obj.bound_box]), Vector())/8
            screen = view3d_utils.location_3d_to_region_2d(self._region, self._view, center)
            if screen is None:
                screen = Vector((self._region.width/2, self._region.height/2))
            self._anchor = Vector((max(90, min(self._region.width-180, screen.x+80)),
                                   max(180, min(self._region.height-180, screen.y+65))))
            a = view3d_utils.region_2d_to_location_3d(self._region, self._view, screen, center)
            b = view3d_utils.region_2d_to_location_3d(self._region, self._view, screen+Vector((1,0)), center)
            self._sensitivity = max(0.0001, min(100, (b-a).length/self._scale/self._matrix.to_scale().x))
            self._draw3d = bpy.types.SpaceView3D.draw_handler_add(self.draw_preview, (), 'WINDOW', 'POST_VIEW')
            self._draw2d = bpy.types.SpaceView3D.draw_handler_add(self.draw_hud, (), 'WINDOW', 'POST_PIXEL')
            self._timer = context.window_manager.event_timer_add(0.03, window=context.window)
            context.window_manager.modal_handler_add(self)
            h._busy = True
            _active = self
            self._area.header_text_set('CAD | Arraste a alca amarela | Digite mm | Enter / direito confirma | Esc cancela')
            self._area.tag_redraw()
            return {'RUNNING_MODAL'}
        except Exception as exc:
            if hasattr(self, '_closed'):
                self.cleanup(context)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def key(self):
        return round(self._value, 6), tuple(sorted(self._selected))

    def handle_position(self):
        displacement = max(-160, min(160, self._value/self._sensitivity))
        return self._anchor+Vector((displacement, 0))

    def changed(self, value):
        self._value = max(-100000, min(100000, value))
        self._dirty = True
        self._changed_at = time.monotonic()
        self._error = ''
        self._error_key = None

    def request(self, final=False):
        if not self._selected:
            self._error = 'Selecione pelo menos uma aresta com Ctrl+clique'
            self._error_key = self.key()
            self._dirty = False
            return
        key = self.key()
        request = dict(op='fillet' if self._value >= 0 else 'chamfer', brep=self._source,
                       params={'radius': abs(key[0]), 'edges': list(key[1])}, preview=not final,
                       tolerance=self._scene.cascade_cad.tolerance if final else max(0.18, self._scene.cascade_cad.tolerance))
        if abs(self._value) < 0.001:
            request['op'] = 'refresh'
        self._id = self._engine.submit(request)
        self._requests[self._id] = (key, final)
        self._requests = {i: item for i, item in self._requests.items() if i >= self._id-64}
        self._last_submit_at = time.monotonic()
        self._sent_key = key
        self._sent_final = final
        if self._pending_since is None:
            self._pending_since = time.monotonic()
        self._dirty = False

    def build_preview(self, result):
        positions = [self._matrix @ Vector([v*self._scale for v in p]) for p in result['vertices']]
        normals = [Vector() for p in positions]
        for a,b,c in result['triangles']:
            normal = (positions[b]-positions[a]).cross(positions[c]-positions[a])
            for i in (a,b,c):
                normals[i] += normal
        light = Vector((0.3,-0.45,0.84)).normalized()
        colors = []
        for normal in normals:
            illumination = max(0, normal.normalized().dot(light))
            colors.append((0.13+0.20*illumination, 0.30+0.32*illumination, 0.39+0.32*illumination, 1))
        self._shader = gpu.shader.from_builtin('SMOOTH_COLOR')
        self._batch = batch_for_shader(self._shader, 'TRIS', {'pos':positions, 'color':colors}, indices=result['triangles'])
        self._obj.hide_set(True)
        self._hidden = True

    def receive(self, context):
        result = self._engine.poll()
        if result is None:
            if not self._engine.alive():
                raise RuntimeError('O motor CAD encerrou. Esc restaura o original; reabra para tentar novamente.')
            return None
        item = self._requests.get(result['id'])
        if item is None:
            return None
        result_key, is_final = item
        current = result['id'] == self._id and result_key == self.key()
        self._pending_since = None if current else time.monotonic()
        self._latency = result['elapsed_ms']
        if not result.get('ok'):
            if not current:
                return None
            self._error = 'Raio inviavel: reduza o valor ou mude a selecao'
            self._error_key = self.key()
            self._committing = False
            self._scene.cascade_cad.status = 'Raio invalido | ultima previa valida preservada'
            return None
        if is_final:
            if not current:
                return None
            # Only this point changes Blender mesh data and embedded BREP.
            host().set_mesh(self._obj, result, self._scale)
            self._scene.cascade_cad.fillet = abs(self._value)
            self.cleanup(context, committed=True)
            self.report({'INFO'}, f"{'Fillet' if self._value>=0 else 'Chanfro'} CAD: {abs(self._value):.3f} mm")
            return {'FINISHED'}
        if result['id'] <= self._display_id or result_key[1] != self.key()[1]:
            return None
        # Show progressively newer solutions even while the pointer keeps moving.
        # Final confirmation is stricter and accepts only the exact requested key.
        self.build_preview(result)
        self._display_key = result_key
        self._display_id = result['id']
        if current:
            self._error = ''
            self._error_key = None
        self._scene.cascade_cad.status = f'Previa CAD {result_key[0]:+.3f} mm | {self._latency:.0f} ms'
        return None

    def confirm(self, context):
        if self._committing:
            return None
        if abs(self._value)<0.001:
            self.cleanup(context)
            return {'CANCELLED'}
        if self._error_key == self.key() or not self._selected:
            self.report({'WARNING'}, 'Escolha um raio e uma selecao validos antes de confirmar')
            return None
        self.request(final=True)
        self._committing = True
        return None

    def cleanup(self, context, committed=False):
        global _active
        if getattr(self, '_closed', False):
            return
        self._closed = True
        for attr in ('_draw3d', '_draw2d'):
            handle = getattr(self, attr, None)
            if handle is not None:
                bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
                setattr(self, attr, None)
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        try:
            self._obj.hide_set(False)
            for obj in self._selection_snapshot:
                if obj.name in context.view_layer.objects and not obj.hide_get():
                    obj.select_set(True)
            self._obj.select_set(True)
            context.view_layer.objects.active = self._obj
            self._scene.cascade_cad.status = (f"CAD confirmado: {self._value:+.3f} mm" if committed else 'Fillet cancelado | original preservado')
        except ReferenceError:
            pass
        self._area.header_text_set(None)
        self._area.tag_redraw()
        self._batch = None
        host()._busy = False
        _active = None
        if not committed and self._pending_since is not None:
            close_engine()

    def draw_preview(self):
        if bpy.context.area != self._area:
            return
        h = host()
        try:
            if self._batch is not None:
                gpu.state.depth_test_set('LESS_EQUAL')
                gpu.state.depth_mask_set(True)
                self._shader.bind()
                self._batch.draw(self._shader)
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set('ALPHA')
            paths = [[self._matrix @ Vector([v*self._scale for v in p]) for p in self._paths[i]] for i in self._selected]
            h.draw_lines(paths, (1,0.64,0.1,0.42), 1.5)
        finally:
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set('NONE')

    def draw_hud(self):
        if bpy.context.area != self._area:
            return
        center = self.handle_position()
        circle = [(center.x+9*math.cos(i*math.tau/40), center.y+9*math.sin(i*math.tau/40), 0) for i in range(41)]
        host().draw_lines([circle, [(self._anchor.x-165,self._anchor.y,0),(self._anchor.x+165,self._anchor.y,0)]],(1,0.76,0.08,1),2)
        font=0
        y=self._region.height-125
        mode='FILLET' if self._value>=0 else 'CHANFRO'
        pending = (f'Atualizando... previa {self._display_key[0]:+.3f} mm' if self._display_key else 'Iniciando previa CAD...')
        error = (f'Raio inviavel | ultima previa valida: {self._display_key[0]:+.3f} mm'
                 if self._error and self._display_key else self._error)
        status = ('Confirmando CAD...' if self._committing else error or
                  (pending if self._display_key != self.key() else f'Previa CAD | {self._latency:.0f} ms'))
        lines=[f'{mode}  {self._value:+.3f} mm',status,
               'Arraste a alca | Shift: preciso | Ctrl: passo 0,5 mm',
               'Digite mm | C: chanfro | D: fillet | Ctrl+clique: arestas',
               'Enter / botao direito: confirmar | Esc: cancelar']
        shader=gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind(); shader.uniform_float('color',(0.02,0.035,0.05,0.94))
        gpu.state.blend_set('ALPHA')
        batch_for_shader(shader,'TRIS',{'pos':[(62,y+31,0),(665,y+31,0),(665,y-107,0),(62,y-107,0)]},indices=[(0,1,2),(0,2,3)]).draw(shader)
        gpu.state.blend_set('NONE')
        for i,line in enumerate(lines):
            blf.size(font,21 if i==0 else 14)
            blf.position(font,76,y-i*25,0)
            blf.color(font,1,0.38,0.2,1) if self._error else blf.color(font,0.92,0.95,1,1)
            blf.draw(font,line)
        blf.size(font,16)
        usable = min([r.x-self._region.x for r in self._area.regions if r.type=='UI' and r.width>5] or [self._region.width])
        blf.position(font,min(center.x+17,usable-165),center.y+12,0)
        blf.color(font,1,0.82,0.15,1)
        blf.draw(font,f'{self._value:+.3f} mm')

    def modal(self, context, event):
        self._area.tag_redraw()
        try:
            if event.type=='ESC' and event.value=='PRESS':
                self.cleanup(context)
                return {'CANCELLED'}
            if event.type=='TIMER':
                result=self.receive(context)
                if result:
                    return result
                if self._dirty and not self._committing and time.monotonic()-self._last_submit_at>=0.05:
                    self.request()
                if self._pending_since is not None and time.monotonic()-self._pending_since>20:
                    raise TimeoutError('Tempo de calculo excedido. Modelo original preservado.')
                return {'RUNNING_MODAL'}
            if self._committing:
                return {'RUNNING_MODAL'}
            if event.type in {'RET','NUMPAD_ENTER','RIGHTMOUSE'} and event.value=='PRESS':
                return self.confirm(context) or {'RUNNING_MODAL'}
            if event.type=='LEFTMOUSE' and event.value=='PRESS':
                xy=Vector((event.mouse_x-self._region.x,event.mouse_y-self._region.y))
                if event.ctrl:
                    edge=edge_at(self._paths,self._matrix,self._scale,self._region,self._view,xy)
                    if edge>=0:
                        self._selected.symmetric_difference_update({edge})
                        self.changed(self._value)
                elif (xy-self.handle_position()).length<24:
                    self._dragging=True
                    self._last_x=event.mouse_x
                    self._raw_value=self._value
                    self._buffer=''
                return {'RUNNING_MODAL'}
            if event.type=='LEFTMOUSE' and event.value=='RELEASE':
                self._dragging=False
                return {'RUNNING_MODAL'}
            if event.type=='MOUSEMOVE' and self._dragging:
                delta=(event.mouse_x-self._last_x)*self._sensitivity*(0.1 if event.shift else 1)
                self._last_x=event.mouse_x
                self._raw_value+=delta
                value=round(self._raw_value/0.5)*0.5 if event.ctrl else self._raw_value
                self.changed(value)
                return {'RUNNING_MODAL'}
            if event.value=='PRESS':
                if event.type in {'C','D'}:
                    self._buffer=''
                    self.changed(-abs(self._value) if event.type=='C' else abs(self._value))
                elif event.type=='BACK_SPACE':
                    self._buffer=self._buffer[:-1]
                    try:
                        self.changed(float(self._buffer or '0'))
                    except ValueError:
                        pass
                elif event.ascii and event.ascii in '0123456789.,-+':
                    self._buffer+=event.ascii.replace(',','.')
                    try:
                        self.changed(float(self._buffer))
                    except ValueError:
                        pass
            if event.type in {'MIDDLEMOUSE','WHEELUPMOUSE','WHEELDOWNMOUSE','NDOF_MOTION'}:
                return {'PASS_THROUGH'}
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self.cleanup(context)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
