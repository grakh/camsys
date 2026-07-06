"""
core/importer.py — импорт .ai в Project.

Шаги:
    1. Открыть .ai через io_.ai_parser
    2. Перечислить слои → создать Layer для каждого
    3. Для каждого пути в каждом слое:
         - распознать сегменты M/L/C/Z
         - кубические Безье → biarc-fit → Polypath из Line+Arc
         - создать Geometry
    4. В слое 'Knife' (или конфигурируемом) — найти открытые пути
         длиной ~1 точка → пометить как реперы (Fiducial)
    5. Вернуть готовый Project
"""

from __future__ import annotations
import os
from typing import Optional, List, Tuple

from .project import Project, Layer, Geometry, Fiducial
from ..io_.ai_parser import list_layers, get_layer_paths, Path as AIPath
from ..geometry.primitives import Bezier, Line, Polypath, vec_dist, EPS
from ..geometry.biarc import fit_bezier, DEFAULT_TOLERANCE


# Цветовая палитра по умолчанию для слоёв (как в Альфакаме — броские цвета)
DEFAULT_LAYER_COLORS = {
    "Knife":   "#00ff00",   # зелёный
    "REZ":     "#ff0000",   # красный
    "L-Test":  "#ffff00",   # жёлтый
    "info":    "#888888",   # серый
    "namber":  "#ff80ff",   # розовый
    "nember":  "#ff80ff",
    "RLL":     "#80c0ff",
    "Clamps":  "#ff8800",
    "Fixtures": "#ff8800",
}

# Размер открытого пути, ниже которого мы считаем его репером
FIDUCIAL_MAX_LENGTH = 5.0  # мм


def _ai_path_to_polypath(ai_path: AIPath,
                         biarc_tolerance: float = DEFAULT_TOLERANCE
                         ) -> Polypath:
    """Конвертирует AI-путь (с сегментами M/L/C/Z) в Polypath (Line+Arc).
    
    Кубические Безье прогоняются через biarc-фит до заданного допуска.
    """
    segments_out = []
    cur_point = None
    start_point = None
    closed = False
    
    for seg in ai_path.segments:
        if seg.op == 'M':
            cur_point = seg.points[0]
            start_point = cur_point
        elif seg.op == 'L':
            end = seg.points[0]
            if cur_point is not None and vec_dist(cur_point, end) > EPS:
                segments_out.append(Line(cur_point, end))
            cur_point = end
        elif seg.op == 'C':
            if cur_point is None:
                continue
            cp1, cp2, end = seg.points
            bez = Bezier(cur_point, cp1, cp2, end)
            fitted = fit_bezier(bez, tolerance=biarc_tolerance)
            segments_out.extend(fitted)
            cur_point = end
        elif seg.op == 'Z':
            # Замыкаем путь, если конец не совпадает с началом
            if (start_point is not None and cur_point is not None
                    and vec_dist(cur_point, start_point) > EPS):
                segments_out.append(Line(cur_point, start_point))
            closed = True
    
    return Polypath(segments=segments_out, closed=closed)


def _is_fiducial_circle(ai_path: AIPath) -> bool:
    """Эвристика: мелкая окружность на слое L-Test = реперная точка.
    
    Реперы в .ai макете — маленькие окружности на слое L-Test (обычно пара:
    внешняя ⌀~1мм + вложенная ⌀~0.25мм). Центр окружности = точка сверления
    репера для сведения координат на другом оборудовании.
    """
    bb = ai_path.bbox()
    w = bb[2] - bb[0]
    h = bb[3] - bb[1]
    if w <= 0 or h <= 0:
        return False
    # Мелкий объект, не микроскопический и не крупный
    if not (0.15 <= w <= 3.0 and 0.15 <= h <= 3.0):
        return False
    # Примерно круглый bbox (окружность): отношение сторон близко к 1
    ratio = w / h
    return 0.6 <= ratio <= 1.67


def _fiducial_center(ai_path: AIPath) -> tuple:
    """Центр реперной окружности = центр её bbox."""
    bb = ai_path.bbox()
    return ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)


def _is_fiducial_path(ai_path: AIPath) -> bool:
    """Старая эвристика (открытый путь ~1 точка). Оставлена для совместимости
    со старыми макетами, где реперы рисовались линиями, а не окружностями."""
    if any(s.op == 'Z' for s in ai_path.segments):
        return False
    if len(ai_path.segments) > 3:
        return False
    bb = ai_path.bbox()
    size = max(bb[2] - bb[0], bb[3] - bb[1])
    return size <= FIDUCIAL_MAX_LENGTH


def _is_clipping_path(ai_path: AIPath) -> bool:
    """Эвристика: путь-обёртка всей страницы (re-rectangle 700+ мм)."""
    bb = ai_path.bbox()
    return (bb[2] - bb[0]) > 600 and (bb[3] - bb[1]) > 400


def import_ai_to_project(ai_path: str,
                         project_name: Optional[str] = None,
                         knife_layer: str = "Knife",
                         fiducial_layer: str = "L-Test",
                         biarc_tolerance: float = DEFAULT_TOLERANCE,
                         ) -> Project:
    """Импорт .ai файла → готовый Project.
    
    Args:
        ai_path: путь к .ai
        project_name: имя проекта (по умолчанию = имя файла без расширения)
        knife_layer: имя слоя, в котором лежат ножи
        fiducial_layer: имя слоя с реперными окружностями (по умолч. L-Test)
        biarc_tolerance: точность биарк-фита, мм (по умолчанию 1 мкм)
    
    Returns:
        Project с заполненными слоями, геометрией и реперами.
    """
    if project_name is None:
        project_name = os.path.splitext(os.path.basename(ai_path))[0]
    
    project = Project(name=project_name, source_ai_path=ai_path)
    
    # Накопитель центров реперов (для дедупликации концентрических окружностей)
    fiducial_centers: List[tuple] = []
    
    def _add_fiducial_center(cx: float, cy: float, dedup_dist: float = 2.0):
        """Добавляет центр репера, если рядом ещё нет другого (дедуп пар)."""
        for (ex, ey) in fiducial_centers:
            if (cx - ex)**2 + (cy - ey)**2 < dedup_dist**2:
                return  # уже есть репер рядом — это вложенная окружность пары
        fiducial_centers.append((cx, cy))
    
    # Импорт всех слоёв
    layer_names = list_layers(ai_path)
    for layer_name in layer_names:
        color = DEFAULT_LAYER_COLORS.get(layer_name, "#ffffff")
        layer = project.add_layer(layer_name, color=color)
        
        try:
            ai_paths = get_layer_paths(ai_path, layer_name)
        except Exception:
            continue
        
        for idx, ap in enumerate(ai_paths):
            # Пропускаем клип-прямоугольники (обёртку страницы)
            if _is_clipping_path(ap):
                continue
            
            # ── РЕПЕРЫ: только мелкие окружности на слое L-Test ──
            # Центр окружности = точка сверления репера. Концентрические пары
            # (внешняя ⌀1мм + внутренняя ⌀0.25мм) дедуплицируются по близости
            # центров. Ищем строго на fiducial_layer (L-Test), нигде больше.
            if layer_name == fiducial_layer and _is_fiducial_circle(ap):
                cx, cy = _fiducial_center(ap)
                _add_fiducial_center(cx, cy)
                continue
            
            # Обычная геометрия
            polypath = _ai_path_to_polypath(ap, biarc_tolerance)
            if not polypath.segments:
                continue
            
            # Пропускаем вырожденные пути — мелкие открытые линии (артефакты
            # макета). Реальные ножи замкнутые и крупные.
            bb = ap.bbox()
            w_mm = bb[2] - bb[0]
            h_mm = bb[3] - bb[1]
            if not polypath.closed and max(w_mm, h_mm) < 3.0:
                continue
            
            geom = Geometry(
                name=f"{layer_name}_{idx}",
                polypath=polypath,
                source_layer=layer_name,
                source_index=idx,
                is_closed=polypath.closed,
            )
            layer.geometries.append(geom)
    
    # Превращаем накопленные центры в Fiducial объекты,
    # отсортированные по X (слева направо)
    fiducial_centers.sort(key=lambda c: c[0])
    for cx, cy in fiducial_centers:
        project.fiducials.append(Fiducial(
            x=cx, y=cy,
            name=f"FID{len(project.fiducials)+1}",
        ))
    
    return project
