"""Launch using Blender --python Abrir_Demo.py. No saved preferences are changed."""
import bpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
if not hasattr(bpy.types, 'CAD_PT_main'):
    import cascade_cad
    cascade_cad.register()
bpy.ops.wm.open_mainfile(filepath=str(ROOT / 'Teste_Fillet_020.blend'))

def show_panel():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.show_region_ui = True
                for region in area.regions:
                    if region.type == 'UI':
                        region.active_panel_category = 'CAD'
    return None
bpy.app.timers.register(show_panel, first_interval=0.5)

