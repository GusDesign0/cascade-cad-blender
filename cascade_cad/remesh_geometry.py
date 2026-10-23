"""Feature constraints, attribute transfer and independent remesh quality checks.

Runs inside Blender. No remeshing function mutates the source Mesh datablock.
All geometric calculations use a centered, normalized WORLD-space reference.
"""
from array import array
from collections import defaultdict
import hashlib
import math
import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree


def mesh_key(mesh):
    h = hashlib.sha256()
    for sequence, prop, kind, size in [(mesh.vertices, 'co', 'f', 3), (mesh.loops, 'vertex_index', 'i', 1),
                                       (mesh.polygons, 'loop_total', 'i', 1), (mesh.polygons, 'material_index', 'i', 1)]:
        data = array(kind, [0]) * (len(sequence) * size)
        sequence.foreach_get(prop, data)
        h.update(data.tobytes())
    for name in ('cad_face', 'sharp_edge', '.sculpt_face_set'):
        attr = mesh.attributes.get(name)
        if attr:
            data = array('i', [0]) * len(attr.data)
            attr.data.foreach_get('value', data)
            h.update(name.encode()); h.update(data.tobytes())
    h.update(bytes(e.use_seam for e in mesh.edges))
    for uv in mesh.uv_layers:
        data = array('f', [0]) * (len(uv.data) * 2)
        uv.data.foreach_get('uv', data)
        h.update(uv.name.encode()); h.update(data.tobytes())
    return h.hexdigest()


def topology(mesh):
    bm = bmesh.new(); bm.from_mesh(mesh)
    unseen = set(bm.verts); components = 0
    while unseen:
        stack = [unseen.pop()]; components += 1
        while stack:
            for edge in stack.pop().link_edges:
                for vert in edge.verts:
                    if vert in unseen:
                        unseen.remove(vert); stack.append(vert)
    result = {'vertices': len(bm.verts), 'edges': len(bm.edges), 'faces': len(bm.faces),
              'components': components, 'euler': len(bm.verts)-len(bm.edges)+len(bm.faces),
              'nonmanifold': sum(not e.is_manifold for e in bm.edges),
              'degenerate': sum(f.calc_area() < 1e-12 for f in bm.faces),
              'quads': sum(len(f.verts) == 4 for f in bm.faces)}
    bm.free()
    return result


def tree(mesh):
    mesh.calc_loop_triangles()
    return BVHTree.FromPolygons([v.co.copy() for v in mesh.vertices],
                               [tuple(t.vertices) for t in mesh.loop_triangles], all_triangles=True)


def obj_mesh(path):
    vertices, faces = [], []
    with open(path, encoding='utf-8', errors='replace') as stream:
        for line in stream:
            tokens = line.split()
            if not tokens: continue
            if tokens[0] == 'v': vertices.append(tuple(float(x) for x in tokens[1:4]))
            elif tokens[0] == 'f':
                face = [int(x.split('/')[0]) for x in tokens[1:]]
                faces.append([v-1 if v > 0 else len(vertices)+v for v in face])
    if not faces or any(len(f) != 4 for f in faces):
        raise ValueError('O motor retornou faces que nao sao quads.')
    mesh = bpy.data.meshes.new('Cascade | Quads')
    mesh.from_pydata(vertices, [], faces); mesh.update()
    return mesh


def subdivide_linear(mesh):
    """Conforming quad refinement, not smoothing and not a native-remesh substitute."""
    # modifier_apply can free the old datablock: keep the caller-owned mesh safe.
    obj = bpy.data.objects.new('_cascade_refine', mesh.copy())
    bpy.context.scene.collection.objects.link(obj)
    bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    modifier = obj.modifiers.new('Linear quad patches', 'SUBSURF')
    modifier.subdivision_type = 'SIMPLE'; modifier.levels = 1
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    result = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    return result


def segment_point(p, a, b):
    d = b-a
    return a + d * min(1., max(0., (p-a).dot(d) / max(d.length_squared, 1e-25)))


class SegmentIndex:
    def __init__(self, segments):
        self.segments = segments
        self.centers = KDTree(max(1, len(segments)))
        self.ends = KDTree(max(1, 2*len(segments)))
        for i, (a, b) in enumerate(segments):
            self.centers.insert((a+b)*.5, i)
            self.ends.insert(a, i); self.ends.insert(b, i)
        self.centers.balance(); self.ends.balance()

    def nearest(self, p):
        if not self.segments: return None, math.inf
        candidates = {i for _, i, _ in self.centers.find_n(p, min(8, len(self.segments)))}
        candidates.update(i for _, i, _ in self.ends.find_n(p, min(8, len(self.segments)*2)))
        best, distance = None, math.inf
        for i in candidates:
            a, b = self.segments[i]; point = segment_point(p, a, b); d = (p-point).length
            if d < distance: best, distance = point, d
        return best, distance


class Reference:
    def __init__(self, source_mesh, world_matrix, unit_mm, angle=35, use_cad=True, use_material=True):
        self.local_matrix = world_matrix.inverted()
        positions = [world_matrix @ v.co for v in source_mesh.vertices]
        low = Vector(tuple(min(p[i] for p in positions) for i in range(3)))
        high = Vector(tuple(max(p[i] for p in positions) for i in range(3)))
        self.center = (low+high)*.5; self.factor = (high-low).length / 100
        if self.factor < 1e-12: raise ValueError('Objeto sem dimensoes suficientes para remesh.')
        self.mm = self.factor * unit_mm
        self.mesh = source_mesh.copy()
        self.mesh.name = '_cascade_reference'
        for v, co in zip(self.mesh.vertices, positions): v.co = (co-self.center)/self.factor
        bm = bmesh.new(); bm.from_mesh(self.mesh)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
        bm.normal_update()
        cad = bm.faces.layers.int.get('cad_face') if use_cad else None
        face_set = bm.faces.layers.int.get('.sculpt_face_set')
        cad_ok = cad is not None and len({f[cad] for f in bm.faces}) > 1
        angle = math.radians(angle)
        guides = set()
        for e in bm.edges:
            different = len(e.link_faces) == 2 and (
                cad_ok and e.link_faces[0][cad] != e.link_faces[1][cad] or
                use_material and e.link_faces[0].material_index != e.link_faces[1].material_index or
                face_set is not None and e.link_faces[0][face_set] != e.link_faces[1][face_set])
            if e.is_boundary or not e.smooth or e.seam or different or e.calc_face_angle(0) > angle:
                guides.add(e)
        # Smooth connected charts are an unambiguous label even without BREP data.
        regions = bm.faces.layers.int.new('_cascade_region')
        unseen = set(bm.faces); label = 0
        while unseen:
            stack = [unseen.pop()]
            while stack:
                f = stack.pop(); f[regions] = label
                for e in f.edges:
                    if e in guides: continue
                    for other in e.link_faces:
                        if other in unseen: unseen.remove(other); stack.append(other)
            label += 1
        self.groups = defaultdict(list); self.guide_segments = []
        for e in guides:
            a, b = (v.co.copy() for v in e.verts)
            pair = tuple(sorted({f[regions] for f in e.link_faces}))
            self.groups[pair].append((a,b)); self.guide_segments.append((a,b))
            e.smooth = False
        bm.to_mesh(self.mesh)
        self.source_topology = topology(self.mesh)
        self.bvh = tree(self.mesh)
        self.triangles = list(self.mesh.loop_triangles)
        self.region_attr = self.mesh.attributes['_cascade_region']
        self.region_trees = {}; self.segment_trees = {}
        self.input_bm = bm

    def export_native(self, folder):
        bm = self.input_bm.copy()
        protected = {v for e in bm.edges if not e.smooth or e.seam or e.is_boundary for v in e.verts}
        # Remove only coplanar subdivisions. Feature chains and their endpoints stay.
        bmesh.ops.dissolve_limit(bm, angle_limit=1e-5, use_dissolve_boundaries=False,
            verts=[v for v in bm.verts if v not in protected],
            edges=[e for e in bm.edges if e.smooth and not e.seam and not e.is_boundary],
            delimit={'NORMAL', 'MATERIAL', 'SEAM', 'SHARP', 'UV'})
        bmesh.ops.triangulate(bm, faces=list(bm.faces), quad_method='SHORT_EDGE', ngon_method='BEAUTY')
        bm.verts.index_update(); bm.faces.index_update(); bm.edges.index_update(); bm.normal_update()
        with (folder/'source.obj').open('w', encoding='ascii') as stream:
            for v in bm.verts: stream.write('v %.10g %.10g %.10g\n' % tuple(v.co))
            for f in bm.faces: stream.write('f ' + ' '.join(str(v.index+1) for v in f.verts) + '\n')
        sharp = []
        for e in bm.edges:
            if e.smooth or not e.link_faces: continue
            f = e.link_faces[0]
            # The C API expects the edge index in the face's cyclic vertex order.
            for i, loop in enumerate(f.loops):
                if loop.edge == e:
                    sharp.append('%d,%d,%d' % (int(e.is_convex), f.index, i)); break
        (folder/'source_rem.sharp').write_text(str(len(sharp))+'\n'+'\n'.join(sharp)+'\n', encoding='ascii')
        result = {'input_triangles': len(bm.faces), 'guides': len(sharp)}
        bm.free(); return result

    def region(self, p):
        hit = self.bvh.find_nearest(p)
        if hit[0] is None: return -1
        return self.region_attr.data[self.triangles[hit[2]].polygon_index].value

    def labels(self, mesh):
        return [self.region(p.center) for p in mesh.polygons]

    def region_tree(self, label):
        if label not in self.region_trees:
            faces = [tuple(t.vertices) for t in self.triangles
                     if self.region_attr.data[t.polygon_index].value == label]
            self.region_trees[label] = BVHTree.FromPolygons([v.co.copy() for v in self.mesh.vertices], faces, all_triangles=True)
        return self.region_trees[label]

    def project(self, mesh, tolerance_mm):
        labels = self.labels(mesh)
        membership = [set() for _ in mesh.vertices]
        for p, label in zip(mesh.polygons, labels):
            for v in p.vertices: membership[v].add(label)
        cap = tolerance_mm*8/self.mm
        lengths = [math.inf]*len(mesh.vertices)
        for e in mesh.edges:
            a,b=e.vertices; length=(mesh.vertices[a].co-mesh.vertices[b].co).length
            lengths[a]=min(lengths[a],length);lengths[b]=min(lengths[b],length)
        proposed=[]
        for v, charts in zip(mesh.vertices, membership):
            closest, distance = None, math.inf
            if len(charts) > 1:
                for pair, segments in self.groups.items():
                    if not set(pair).issubset(charts): continue
                    if pair not in self.segment_trees: self.segment_trees[pair] = SegmentIndex(segments)
                    point, d = self.segment_trees[pair].nearest(v.co)
                    if d < distance: closest, distance = point, d
            elif charts:
                target = self.region_tree(next(iter(charts)))
                if target: closest, _, _, distance = target.find_nearest(v.co)
            # A bounded relaxation cannot collapse both ends of an edge onto a
            # nearby crease. Unbounded shrinkwrap caused precisely that failure.
            if closest is not None and distance <= cap:
                delta=closest-v.co; limit=min(cap,lengths[v.index]*.18)
                if delta.length>limit:delta=delta.normalized()*limit
                proposed.append(v.co+delta)
            else:proposed.append(v.co.copy())
        original=[v.co.copy() for v in mesh.vertices]
        for v,point in zip(mesh.vertices,proposed):v.co=point
        mesh.update()
        bm=bmesh.new();bm.from_mesh(mesh)
        invalid=any(f.calc_area()<1e-12 for f in bm.faces);bm.free()
        if invalid:
            for v,point in zip(mesh.vertices,original):v.co=point
            mesh.update()

    def exact_quads(self):
        if all(len(p.vertices)==4 for p in self.mesh.polygons):
            return self.mesh.copy()
        # Use exactly Blender's surface triangulation. Joining nearly coplanar
        # triangles first can subtly change a nonplanar quad's diagonal surface.
        # Each source triangle becomes three planar quads, with shared midpoints.
        self.mesh.calc_loop_triangles()
        mesh = bpy.data.meshes.new('_cascade_exact_triangles')
        mesh.from_pydata([v.co[:] for v in self.mesh.vertices], [],
                         [tuple(t.vertices) for t in self.mesh.loop_triangles])
        mesh.update()
        result = subdivide_linear(mesh)
        bpy.data.meshes.remove(mesh)
        return result

    def measure(self, mesh, tolerance_mm, sample_limit=12000):
        info = topology(mesh); out = tree(mesh); distances = []
        def sample(source, target):
            source.calc_loop_triangles()
            step = max(1, math.ceil(len(source.vertices)/sample_limit))
            for i in range(0, len(source.vertices), step):
                hit = target.find_nearest(source.vertices[i].co)
                if hit[0] is None: return False
                distances.append(hit[3]*self.mm)
            step = max(1, math.ceil(len(source.loop_triangles)/sample_limit))
            for i in range(0, len(source.loop_triangles), step):
                triangle = source.loop_triangles[i]
                p = sum((source.vertices[v].co for v in triangle.vertices), Vector())/3
                hit = target.find_nearest(p)
                if hit[0] is None: return False
                distances.append(hit[3]*self.mm)
            return True
        valid = sample(self.mesh, out) and sample(mesh, self.bvh)
        guide_error = 0.
        # Test *every* guide segment, including tiny CAD fillets, independently of
        # the surface sample cap. These checks cannot be hidden by a low average.
        for a,b in self.guide_segments:
            for point in (a,b,(a+b)*.5):
                hit = out.find_nearest(point)
                guide_error = max(guide_error, hit[3]*self.mm if hit[0] is not None else math.inf)
        maximum = max(distances, default=math.inf)
        reasons = []
        if info['quads'] != info['faces']: reasons.append('faces que nao sao quads')
        if info['components'] != self.source_topology['components']: reasons.append('numero de ilhas alterado')
        if info['euler'] != self.source_topology['euler']: reasons.append('furo ou topologia alterados')
        if info['nonmanifold'] > self.source_topology['nonmanifold']: reasons.append('arestas abertas ou nao manifold')
        if info['degenerate']: reasons.append('faces degeneradas')
        if not valid or maximum > tolerance_mm: reasons.append('erro de superficie acima da tolerancia')
        if guide_error > tolerance_mm: reasons.append('perda de linhas CAD ou vincos')
        return {'accepted': not reasons, 'reasons': reasons, 'topology': info,
                'sampled_max_mm': maximum, 'sampled_mean_mm': sum(distances)/max(1,len(distances)),
                'guide_surface_max_mm': guide_error, 'samples': len(distances),
                'guide_segments_tested': len(self.guide_segments), 'tolerance_mm': tolerance_mm}

    def transfer(self, mesh):
        """Transfer material regions and UV corners, then return original local units."""
        self.mesh.calc_loop_triangles()
        material = mesh.attributes.new('cascade_material_index', 'INT', 'FACE')
        hits = []
        for p in mesh.polygons:
            hit = self.bvh.find_nearest(p.center); tri = self.triangles[hit[2]]
            material.data[p.index].value = self.mesh.polygons[tri.polygon_index].material_index
            hits.append(tri.polygon_index)
        for uv in self.mesh.uv_layers:
            if mesh.uv_layers.get(uv.name): continue
            output = mesh.uv_layers.new(name=uv.name)
            for p in mesh.polygons:
                for loop in p.loop_indices:
                    co = mesh.vertices[mesh.loops[loop].vertex_index].co
                    hit = self.bvh.find_nearest(co*.999999+p.center*.000001)
                    tri = self.triangles[hit[2]]
                    a,b,c = (self.mesh.vertices[v].co for v in tri.vertices)
                    v0,v1,v2 = b-a,c-a,hit[0]-a
                    d00,d01,d11 = v0.dot(v0),v0.dot(v1),v1.dot(v1)
                    denom = max(d00*d11-d01*d01, 1e-25)
                    u=(d11*v2.dot(v0)-d01*v2.dot(v1))/denom
                    v=(d00*v2.dot(v1)-d01*v2.dot(v0))/denom
                    ta,tb,tc = (uv.data[i].uv for i in tri.loops)
                    output.data[loop].uv=ta*(1-u-v)+tb*u+tc*v
        for name in ('cad_face', '.sculpt_face_set'):
            source = self.mesh.attributes.get(name)
            if source and not mesh.attributes.get(name):
                target = mesh.attributes.new(name, 'INT', 'FACE')
                for i,face in enumerate(hits): target.data[i].value=source.data[face].value
        for v in mesh.vertices: v.co=self.local_matrix @ (v.co*self.factor+self.center)
        for p in mesh.polygons:p.use_smooth=True
        for attr in list(mesh.attributes):
            if attr.name.startswith('_cascade_'):mesh.attributes.remove(attr)
        mesh.update(); mesh.set_sharp_from_angle(angle=math.radians(40))
        return mesh

    def close(self):
        self.input_bm.free()
        bpy.data.meshes.remove(self.mesh)
