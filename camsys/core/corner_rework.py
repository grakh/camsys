"""
core/corner_rework.py — создание операций обработки углов.

Алгоритм:
    1. Для каждой BLADE_FORMING операции находим её геометрию (контур ножа)
    2. Через detect_geometric_corners ищем малые дуги (R < threshold)
    3. Группируем близкие дуги через group_corner_arcs → CornerGroup
    4. Для каждой группы создаём операцию CORNER_REWORK с тонкой фрезой
    5. Операции сортируем по ножам: все углы нож1, потом нож2, ...
"""

from __future__ import annotations
from typing import List, Optional

from .project import (Project, Operation, ToolPath, ContourSide,
                       OperationKind, CutSettings, PassType, EntryExitConfig,
                       LeadStyle)
from ..geometry.corner_detect import (detect_geometric_corners,
                                       group_corner_arcs, CornerGroup)


def create_corner_rework_operations(project: Project,
                                    tool_number: int = 3,
                                    radius_threshold_mm: float = 0.7,
                                    group_proximity_mm: float = 2.0,
                                    feed_cut: float = 1500,
                                    feed_dive: float = 800,
                                    ) -> List[Operation]:
    """Создаёт операции обработки углов для всех ножей в проекте.
    
    Args:
        project: проект с уже созданными BLADE_FORMING операциями
        tool_number: T-номер тонкой фрезы (по умолчанию T3 = пятка 0.6мм)
        radius_threshold_mm: порог радиуса скругления для определения угла
        group_proximity_mm: радиус слияния близких дуг в один угол
        feed_cut: подача резания (мм/мин)
        feed_dive: подача врезания (мм/мин)
    
    Returns:
        Список созданных операций CORNER_REWORK (новых, добавленных в проект).
        Порядок: по ножам в исходном порядке project.operations.
    """
    new_ops: List[Operation] = []
    
    # Перебираем BLADE_FORMING операции в их текущем порядке
    blade_ops = [op for op in project.operations 
                 if op.kind == OperationKind.BLADE_FORMING]
    
    for blade_op in blade_ops:
        if not blade_op.geometry_ids:
            continue
        geom_id = blade_op.geometry_ids[0]
        geom = project.get_geometry(geom_id)
        if not geom or not geom.polypath:
            continue
        
        # Поиск углов на контуре
        small_arcs = detect_geometric_corners(geom.polypath,
                                              radius_threshold_mm=radius_threshold_mm)
        if not small_arcs:
            continue
        
        groups = group_corner_arcs(small_arcs,
                                   proximity_mm=group_proximity_mm)
        if not groups:
            continue
        
        # Создаём по одной операции для каждой группы углов
        for grp_idx, grp in enumerate(groups):
            settings = CutSettings(
                tool_number=tool_number,
                pass_type=PassType.SINGLE,
                feed_cut=int(feed_cut),
                feed_plunge=int(feed_dive),
                prog_z_depth=0.3,
            )
            
            op = Operation(
                name=f"{blade_op.name} corner #{grp_idx+1}",
                kind=OperationKind.CORNER_REWORK,
                geometry_ids=[geom_id],
                settings=settings,
            )
            # Метаданные о группе углов
            op.attributes['corner_first_idx'] = grp.first_idx
            op.attributes['corner_last_idx'] = grp.last_idx
            op.attributes['corner_center'] = grp.center
            op.attributes['corner_radius'] = grp.radius
            op.attributes['corner_apex'] = grp.apex
            op.attributes['corner_ccw'] = grp.ccw
            
            # Один toolpath для каждой группы. Используем OUTSIDE сторону
            # (компенсация G42 — фреза идёт снаружи дуги).
            tp = ToolPath(
                geometry_id=geom_id,
                side=ContourSide.OUTSIDE,
                # Lead-in/out — короткие, для обхода острия (как на скриншоте)
                entry=EntryExitConfig(
                    enabled=True,
                    style=LeadStyle.LINE_ARC_TANGENTIAL,
                    line_length_x_tool_rad=1.0,
                    arc_radius_x_tool_rad=1.0,
                    approach_angle=45.0,
                ),
                exit=EntryExitConfig(
                    enabled=True,
                    style=LeadStyle.LINE_ARC_TANGENTIAL,
                    line_length_x_tool_rad=1.0,
                    arc_radius_x_tool_rad=1.0,
                    approach_angle=45.0,
                ),
            )
            op.toolpaths = [tp]
            
            project.operations.append(op)
            new_ops.append(op)
    
    return new_ops
