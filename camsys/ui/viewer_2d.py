"""
ui/viewer_2d.py — 2D-вьюер геометрии CAM-проекта.

Использует Qt Graphics View Framework — встроенная масштабируемая графика
с зумом, панорамой, выделением. Достаточно производительная для десятков
тысяч сегментов.

Координатная система:
    В CAM Y направлена ВВЕРХ (как в Альфакаме и CNC). 
    В Qt Y направлена ВНИЗ.
    Решение: применяем transform.scale(1, -1) к сцене, тогда CAM-координаты
    отображаются естественно (Y вверх).
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple
from PySide6 import QtCore, QtGui, QtWidgets

from camsys.core.project import Project, Layer, Geometry, Fiducial
from camsys.geometry.primitives import Line, Arc


# ─────────────────────────────────────────────────────────────────────────
#  ПРЕОБРАЗОВАНИЕ Polypath → QPainterPath
# ─────────────────────────────────────────────────────────────────────────

def polypath_to_qpainter(polypath) -> QtGui.QPainterPath:
    """Конвертирует наш Polypath в QPainterPath для отрисовки.
    
    Дуги дискретизируются в полилинии (1° на сегмент) чтобы избежать
    проблем с QPainterPath.arcTo при больших радиусах (где bounding box
    окружности тянется далеко за границы реальной дуги).
    """
    import math
    path = QtGui.QPainterPath()
    if not polypath or not polypath.segments:
        return path
    
    first = polypath.segments[0]
    path.moveTo(first.a[0], first.a[1])
    
    for seg in polypath.segments:
        if isinstance(seg, Line):
            path.lineTo(seg.b[0], seg.b[1])
        elif isinstance(seg, Arc):
            cx, cy = seg.center
            r = seg.radius
            
            # Углы начала и конца относительно центра
            sa = math.atan2(seg.a[1] - cy, seg.a[0] - cx)
            ea = math.atan2(seg.b[1] - cy, seg.b[0] - cx)
            
            # Угол развёртки с учётом направления
            sweep = ea - sa
            if seg.ccw:
                while sweep < 0:
                    sweep += 2 * math.pi
            else:
                while sweep > 0:
                    sweep -= 2 * math.pi
            
            # Шаг дискретизации: 1° для маленьких дуг, реже для больших радиусов
            # (так чтобы хорда не превышала ~0.5 мм визуально)
            step_rad = math.radians(2.0)
            if r > 50:
                step_rad = math.radians(0.5)
            
            n_steps = max(1, int(abs(sweep) / step_rad))
            for i in range(1, n_steps + 1):
                t = i / n_steps
                a = sa + sweep * t
                px = cx + r * math.cos(a)
                py = cy + r * math.sin(a)
                path.lineTo(px, py)
    
    return path


# ─────────────────────────────────────────────────────────────────────────
#  ГРАФИЧЕСКИЕ ЭЛЕМЕНТЫ
# ─────────────────────────────────────────────────────────────────────────

class GeometryItem(QtWidgets.QGraphicsPathItem):
    """Элемент сцены, отрисовывающий одну Geometry."""
    
    def __init__(self, geometry: Geometry, color: QtGui.QColor):
        super().__init__()
        self.geometry = geometry
        self.base_color = color
        
        path = polypath_to_qpainter(geometry.polypath)
        self.setPath(path)
        
        pen = QtGui.QPen(color)
        pen.setCosmetic(True)         # толщина не зависит от зума
        pen.setWidthF(1.5)
        self.setPen(pen)
        
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        
        # Считаем РЕАЛЬНЫЙ bbox по точкам сегментов (не доверяем path.boundingRect,
        # который для arcTo учитывает всю окружность). Это даёт корректный 
        # bbox для arcTo с большим радиусом и маленькой дугой.
        xs, ys = [], []
        for seg in geometry.polypath.segments:
            xs.append(seg.a[0]); ys.append(seg.a[1])
            xs.append(seg.b[0]); ys.append(seg.b[1])
        if xs:
            margin = 1.0
            self._real_bbox = QtCore.QRectF(
                min(xs) - margin, min(ys) - margin,
                max(xs) - min(xs) + 2*margin,
                max(ys) - min(ys) + 2*margin,
            )
        else:
            self._real_bbox = QtCore.QRectF()
    
    def boundingRect(self) -> QtCore.QRectF:
        """Возвращаем корректный bbox по точкам сегментов (не QPainterPath.boundingRect)."""
        return self._real_bbox
    
    def shape(self) -> QtGui.QPainterPath:
        """Для hit-testing используем сам path (с поправкой на толщину пера)."""
        return self.path()
    
    def paint(self, painter, option, widget=None):
        """Подсветка выделенных: ярче и толще."""
        if self.isSelected():
            sel_pen = QtGui.QPen(QtGui.QColor("#ffff00"))  # жёлтый
            sel_pen.setCosmetic(True)
            sel_pen.setWidthF(2.5)
            painter.setPen(sel_pen)
            painter.drawPath(self.path())
        else:
            super().paint(painter, option, widget)


class FiducialItem(QtWidgets.QGraphicsItem):
    """Маркер репера: красный круг с крестом."""
    
    RADIUS = 4.0  # пикселей экранных
    
    def __init__(self, fiducial: Fiducial):
        super().__init__()
        self.fiducial = fiducial
        self.setPos(fiducial.x, fiducial.y)
        self.setZValue(10)
    
    def boundingRect(self) -> QtCore.QRectF:
        r = self.RADIUS + 2
        return QtCore.QRectF(-r, -r, 2*r, 2*r)
    
    def paint(self, painter, option, widget=None):
        # Размер в пикселях экрана — нечувствительный к зуму
        scale = painter.transform().m11()
        r = self.RADIUS / abs(scale) if abs(scale) > 1e-6 else self.RADIUS
        
        pen = QtGui.QPen(QtGui.QColor("#ff3030"))
        pen.setCosmetic(True)
        pen.setWidthF(1.8)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        
        painter.drawEllipse(QtCore.QPointF(0, 0), r, r)
        # Крест внутри
        painter.drawLine(QtCore.QPointF(-r, 0), QtCore.QPointF(r, 0))
        painter.drawLine(QtCore.QPointF(0, -r), QtCore.QPointF(0, r))


# ─────────────────────────────────────────────────────────────────────────
#  СЦЕНА
# ─────────────────────────────────────────────────────────────────────────

class CamScene(QtWidgets.QGraphicsScene):
    """Сцена со всей геометрией проекта."""
    
    def __init__(self):
        super().__init__()
        self.setBackgroundBrush(QtGui.QColor("#0d0d0d"))  # тёмный как Альфакам
        
        # Соответствие Geometry.id → GeometryItem (для выделения по id)
        self._geom_items: Dict[str, GeometryItem] = {}
        # Layer.name → список GeometryItem (для управления видимостью)
        self._layer_items: Dict[str, List[QtWidgets.QGraphicsItem]] = {}
    
    def clear_all(self):
        self.clear()
        self._geom_items.clear()
        self._layer_items.clear()
    
    def load_project(self, project: Project):
        """Перезагружает сцену из проекта."""
        self.clear_all()
        
        for layer in project.layers.values():
            color = QtGui.QColor(layer.color or "#00ff00")
            items = []
            for geom in layer.geometries:
                if geom.polypath is None:
                    continue
                item = GeometryItem(geom, color)
                item.setVisible(layer.visible and geom.is_visible)
                self.addItem(item)
                self._geom_items[geom.id] = item
                items.append(item)
            self._layer_items[layer.name] = items
        
        # Реперы поверх
        for fid in project.fiducials:
            item = FiducialItem(fid)
            self.addItem(item)
        
        # Обновляем bounding rect сцены
        self.setSceneRect(self.itemsBoundingRect().adjusted(-50, -50, 50, 50))
    
    def set_layer_visible(self, layer_name: str, visible: bool):
        for item in self._layer_items.get(layer_name, []):
            item.setVisible(visible)
    
    def get_selected_geometry_ids(self) -> List[str]:
        return [
            it.geometry.id 
            for it in self.selectedItems() 
            if isinstance(it, GeometryItem)
        ]


# ─────────────────────────────────────────────────────────────────────────
#  ВЬЮ
# ─────────────────────────────────────────────────────────────────────────

class CamView(QtWidgets.QGraphicsView):
    """QGraphicsView с зумом колесом мыши и панорамой средней кнопкой."""
    
    geometriesSelected = QtCore.Signal(list)  # список Geometry.id
    
    def __init__(self, scene: CamScene):
        super().__init__(scene)
        self.setRenderHints(
            QtGui.QPainter.Antialiasing |
            QtGui.QPainter.SmoothPixmapTransform
        )
        # Y вверх
        self.scale(1, -1)
        
        # Перетаскивание средней кнопкой / Ctrl+ЛКМ
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self.setMouseTracking(True)
        
        # Для отображения координат курсора в строке состояния
        self._on_mouse_move = None
        
        scene.selectionChanged.connect(self._on_selection_changed)
    
    def _on_selection_changed(self):
        scene = self.scene()
        if isinstance(scene, CamScene):
            ids = scene.get_selected_geometry_ids()
            self.geometriesSelected.emit(ids)
    
    def wheelEvent(self, event: QtGui.QWheelEvent):
        """Колесо мыши — зум."""
        angle = event.angleDelta().y()
        factor = 1.15 if angle > 0 else 1/1.15
        self.scale(factor, factor)
    
    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MiddleButton:
            self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
            # Симулируем нажатие ЛКМ для drag-режима
            fake = QtGui.QMouseEvent(
                event.type(),
                event.position(),
                QtCore.Qt.LeftButton,
                QtCore.Qt.LeftButton,
                event.modifiers(),
            )
            super().mousePressEvent(fake)
        else:
            super().mousePressEvent(event)
    
    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MiddleButton:
            self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        super().mouseReleaseEvent(event)
    
    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        super().mouseMoveEvent(event)
        if self._on_mouse_move:
            pt = self.mapToScene(event.pos())
            self._on_mouse_move(pt.x(), pt.y())
    
    def fit_all(self):
        """Вписать всю сцену в видимую область."""
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(rect.adjusted(-10, -10, 10, 10), 
                           QtCore.Qt.KeepAspectRatio)
    
    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.key() == QtCore.Qt.Key_F:
            self.fit_all()
        else:
            super().keyPressEvent(event)


# ─────────────────────────────────────────────────────────────────────────
#  ВИЗУАЛИЗАЦИЯ ПУТЕЙ ФРЕЗЫ
# ─────────────────────────────────────────────────────────────────────────

class ToolpathItem(QtWidgets.QGraphicsPathItem):
    """Графический элемент для отображения пути фрезы.
    
    Цвет назначается ПО ОПЕРАЦИИ (по индексу) — все пути одного ножа 
    (INSIDE+OUTSIDE+leads+углы) рисуются одним цветом для удобства 
    визуального восприятия.
    
    Тип влияет только на стиль линии:
        - INSIDE/OUTSIDE/CORNER : сплошной
        - LEAD_IN/LEAD_OUT      : сплошной но чуть тоньше
        - RAPID                 : пунктир тонкий
    """
    
    # Палитра «как в Альфакаме» — насыщенные цвета по 16 операций
    PALETTE = [
        '#ff6666', '#66ff66', '#6699ff', '#ffcc33', '#ff66cc', '#66ffff',
        '#cc99ff', '#ffff66', '#ff9966', '#99ff99', '#9999ff', '#ffaa00',
        '#ff3399', '#33ffcc', '#9966ff', '#ccff66',
    ]
    
    def __init__(self, polypath, kind: str = 'CONTOUR', op_index: int = 0,
                 collision: bool = False):
        super().__init__()
        self.kind = kind
        self.op_index = op_index
        self.collision = collision
        # Сохраняем polypath для отрисовки стрелок направления
        self._polypath = polypath
        path = polypath_to_qpainter(polypath)
        self.setPath(path)
        
        if collision:
            # Lead пересекает путь соседнего ножа → красный, толще, видно сразу
            self._color = QtGui.QColor('#ff0033')
        else:
            color_str = self.PALETTE[op_index % len(self.PALETTE)]
            self._color = QtGui.QColor(color_str)
        pen = QtGui.QPen(self._color)
        
        # Стиль по типу
        if kind == 'RAPID':
            pen.setStyle(QtCore.Qt.DashLine)
            pen.setWidthF(0.8)
        elif kind in ('LEAD_IN', 'LEAD_OUT'):
            pen.setStyle(QtCore.Qt.SolidLine)
            pen.setWidthF(2.5 if collision else 1.2)
        else:
            pen.setStyle(QtCore.Qt.SolidLine)
            pen.setWidthF(1.5)
        
        pen.setCosmetic(True)
        self.setPen(pen)
        
        self.setZValue(6 if collision else 5)  # коллизии поверх
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, False)
    
    def boundingRect(self):
        return self.path().controlPointRect().adjusted(-5, -5, 5, 5)
    
    def paint(self, painter, option, widget=None):
        # Сначала линия пути
        super().paint(painter, option, widget)
        
        # Стрелки направления — только для контуров и лидов (не для RAPID)
        if self.kind == 'RAPID':
            return
        if not self._polypath or not self._polypath.segments:
            return
        
        # Точки стрелок: на 25% / 50% / 75% длины пути для контуров;
        # одна стрелка в середине для лидов.
        from camsys.geometry.path_offset import (
            polypath_total_length, point_and_tangent_at_distance)
        try:
            total = polypath_total_length(self._polypath)
        except Exception:
            return
        if total < 1e-6:
            return
        if self.kind == 'CONTOUR':
            fractions = [0.25, 0.5, 0.75]
        else:
            fractions = [0.5]
        
        # Размер стрелки в пикселях экрана (косметический, не зависит от зума)
        scale = painter.transform().m11()
        if scale < 1e-9:
            return
        arrow_px = 8.0    # длина стрелки в пикселях
        arrow_len = arrow_px / scale
        arrow_w = arrow_len * 0.55  # ширина основания
        
        painter.save()
        painter.setBrush(self._color)
        pen = QtGui.QPen(self._color)
        pen.setWidthF(0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        
        import math as _math
        for f in fractions:
            try:
                result = point_and_tangent_at_distance(self._polypath, total * f)
            except Exception:
                continue
            if result is None:
                continue
            pt, tan = result[0], result[1]
            # Треугольник: вершина в pt+tan*arrow_len, основание в pt
            tx, ty = tan
            # перпендикуляр
            nx, ny = -ty, tx
            tip = QtCore.QPointF(pt[0] + tx * arrow_len * 0.5,
                                 pt[1] + ty * arrow_len * 0.5)
            base_l = QtCore.QPointF(pt[0] - tx * arrow_len * 0.5 + nx * arrow_w * 0.5,
                                    pt[1] - ty * arrow_len * 0.5 + ny * arrow_w * 0.5)
            base_r = QtCore.QPointF(pt[0] - tx * arrow_len * 0.5 - nx * arrow_w * 0.5,
                                    pt[1] - ty * arrow_len * 0.5 - ny * arrow_w * 0.5)
            poly = QtGui.QPolygonF([tip, base_l, base_r])
            painter.drawPolygon(poly)
        painter.restore()


def _build_toolpath_geometry(project, op, tp, options_extras, cutting_params=None):
    """Прогоняет ту же логику что эмиттер для одного toolpath, и возвращает
    геометрию для визуализации: dict с ключами 'contour', 'lead_in', 'lead_out'.
    
    Не пишет в файл, не считает G-команды — только возвращает геометрию.
    
    Args:
        project, op, tp: контекст операции
        options_extras: extras для построения путей (tool_radius, tool_equidistant)
        cutting_params: если задан CuttingMacroParams — берём оттуда параметры
            lead-in/out (offset, angle, length) текущей панели UI, иначе из 
            tp.entry/tp.exit (дефолты операции).
    """
    from ..core.project import ContourSide, OperationKind, LeadStyle
    from ..geometry.direction import normalize_for_side, reverse_polypath
    from ..geometry.path_offset import (
        polypath_bbox, shift_start_to_corner, shift_start_along_contour,
        extract_subpath_around_indices, extract_subpath_around_point,
        distance_along_polypath,
    )
    from ..geometry.lead_inout import build_lead_in, build_lead_out
    from ..geometry.primitives import Polypath
    
    geom = project.get_geometry(tp.geometry_id)
    if geom is None or geom.polypath is None:
        return None
    
    # ── Извлечение polypath: фрагмент для CORNER_REWORK, полный для BLADE ──
    is_3d_corner = (op.kind == OperationKind.CORNER_REWORK 
                    and op.attributes.get('corner_is_3d')
                    and 'corner3d_point' in op.attributes)
    is_2d_corner = (op.kind == OperationKind.CORNER_REWORK
                    and 'corner_first_idx' in op.attributes
                    and not op.attributes.get('corner_is_3d'))
    
    if is_3d_corner:
        pt = op.attributes['corner3d_point']
        seg_hint = op.attributes['corner3d_segment_index']
        polypath = extract_subpath_around_point(
            geom.polypath, pt, seg_hint, pad_mm=1.5
        )
    elif is_2d_corner:
        first_idx = op.attributes['corner_first_idx']
        last_idx = op.attributes['corner_last_idx']
        polypath = extract_subpath_around_indices(
            geom.polypath, first_idx, last_idx, pad_mm=1.5
        )
    else:
        polypath = geom.polypath
    
    if not polypath.segments:
        return None
    
    # ── Извлечение параметров lead из cutting_params (приоритет над tp.entry) ──
    user_offset = tp.entry.start_offset
    lead_in_length_mult = tp.entry.line_length_x_tool_rad
    lead_in_radius_mult = tp.entry.arc_radius_x_tool_rad
    lead_in_angle = tp.entry.approach_angle
    lead_out_length_mult = tp.exit.line_length_x_tool_rad
    lead_out_radius_mult = tp.exit.arc_radius_x_tool_rad
    lead_out_angle = tp.exit.approach_angle
    
    if cutting_params is not None and not (is_3d_corner or is_2d_corner):
        # Привязка к ФИЗИЧЕСКОМУ резу (как в package_export): INSIDE (CW) режет
        # внешний → lead_outside; OUTSIDE (CCW) режет внутренний → lead_inside.
        if tp.side == ContourSide.OUTSIDE:
            src = cutting_params.lead_inside    # внутренний рез
        elif tp.side == ContourSide.INSIDE:
            src = cutting_params.lead_outside   # внешний рез
        else:
            src = None
        if src is not None:
            user_offset = src.offset
            lead_in_angle = src.angle
            lead_in_length_mult = src.length
            lead_in_radius_mult = src.length
            lead_out_angle = src.angle
            lead_out_length_mult = src.length
            lead_out_radius_mult = src.length
    
    # ── Нормализация направления и точка старта ──
    is_corner = is_3d_corner or is_2d_corner
    if is_corner:
        # Развернуть фрагмент под CCW (центр слева) для INSIDE/G41
        bb = polypath_bbox(geom.polypath)
        cx = (bb[0] + bb[2]) / 2.0
        cy = (bb[1] + bb[3]) / 2.0
        sp = polypath.segments[0].a
        tan = polypath.segments[0].tangent_at_start()
        cross = tan[0]*(cy - sp[1]) - tan[1]*(cx - sp[0])
        if cross < 0:  # центр справа = CW → разворачиваем под CCW
            polypath = reverse_polypath(polypath)
    elif geom.is_closed and tp.side in (ContourSide.OUTSIDE, ContourSide.INSIDE):
        side_name = "OUTSIDE" if tp.side == ContourSide.OUTSIDE else "INSIDE"
        polypath = normalize_for_side(polypath, side_name)
        
        # Точка старта в RT углу — единая для INSIDE и OUTSIDE
        polypath = shift_start_to_corner(polypath, "RT")
        # Если RT попал на дугу — сдвинуть на начало прямой
        from camsys.geometry.path_offset import shift_start_to_top_line
        polypath = shift_start_to_top_line(polypath)
        
        # СИММЕТРИЯ INSIDE/OUTSIDE: оба прохода на RT конец top line
        # Для CCW (INSIDE) top line идёт RIGHT→LEFT, start = TR ✓
        # Для CW (OUTSIDE) top line идёт LEFT→RIGHT, start = TL ✗ → сдвигаем 
        # на длину top line чтобы start стал TR концом. Тогда offset одинаково
        # сдвигает обе точки старта в одну сторону по верхней грани.
        seg0 = polypath.segments[0]
        from camsys.geometry.primitives import Line as _LineCls
        if isinstance(seg0, _LineCls) and seg0.b[0] > seg0.a[0]:
            import math as _m_sym
            top_len = _m_sym.hypot(seg0.b[0] - seg0.a[0], seg0.b[1] - seg0.a[1])
            polypath = shift_start_along_contour(polypath, top_len)
        
        # Инверсия знака offset: для INSIDE (CCW) сдвиг -5 без инверсии шёл
        # бы вниз по правой стороне. Инверсия делает оба прохода идущими от
        # RT в одну сторону (влево по верху).
        effective_offset = -user_offset if tp.side == ContourSide.INSIDE else user_offset
        if abs(effective_offset) > 1e-9:
            polypath = shift_start_along_contour(polypath, effective_offset)
        
        # ВАЖНО: overlap НЕ применяем здесь — он разомкнул бы контур
        # (closed=False), и offset_polypath_uniform не построил бы визуализацию
        # реза. Сохраняем замкнутый контур для offset, а удлинение применим
        # ПОСЛЕ построения offset visualization (см. ниже).
    
    # ── Lead-In и Lead-Out (если включены) ──
    tool_radius = options_extras.get('tool_radius', 0.6)
    tool_equidistant = options_extras.get('tool_equidistant', 1.2)
    tool_offset = tool_equidistant / 2.0  # как в .anc программе
    
    # Для CORNER операций используется ДРУГАЯ (более тонкая) фреза:
    # T3 (пятка 0.6мм для 2D углов) или T4 (3D-фреза, ~0.4мм).
    # Эквидистанта корнер-фрезы соответственно меньше.
    corner_tool_eq = options_extras.get('corner_tool_equidistant', 0.94)
    corner_tool_offset = corner_tool_eq / 2.0
    corner_tool_radius = options_extras.get('corner_tool_radius', 0.3)
    
    # Эквидистанта: фактический путь фрезы со смещением на tool_offset.
    # Для замкнутых BLADE контуров используем uniform-оффсет (один знак 
    # нормали по всему контуру — гарантирует согласованность в углах).
    # Для открытых CORNER фрагментов — geometric oriented by center.
    from ..geometry.path_offset import (offset_polypath_toward_center,
                                         offset_polypath_uniform,
                                         simplify_for_visualization,
                                         flatten_arcs_to_chords,
                                         join_polypath_corners,
                                         trim_self_intersections)
    
    bb = polypath_bbox(geom.polypath)
    center = ((bb[0]+bb[2])/2, (bb[1]+bb[3])/2)
    
    # ПРЕДОБРАБОТКА для визуализации (2 шага):
    # 1. simplify_for_visualization — мелкие biarc-сегменты и большие 
    #    R>50мм → Line. Реальные скругления R=0.15мм ОСТАЮТСЯ как Arc.
    # 2. merge_arc_clusters_to_arcs — соседние мелкие дуги с близкими 
    #    центрами объединяются в ОДНУ Arc. 16 микро-дуг биарк-аппроксимации
    #    скругления → одна логическая Arc от первой точки до последней.
    #    Это избавляет от «веера» при оффсете 16 независимых дуг.
    # ВАЖНО: только для визуализации, в .anc программу идёт исходник.
    # ПРЕДОБРАБОТКА для визуализации:
    # ВСЕ дуги (мелкие R<5мм скруглений ножа + биарк-«прямые» R>50мм) 
    # заменяются на Line. Реальные большие скругления R>5мм останутся 
    # как Arc.
    # 
    # Дуги мелких скруглений (R=0.15..0.7мм) при оффсете дают 
    # непредсказуемые артефакты («вееры», «крылья», петли). 
    # Замена на прямые → срез угла под 45° длиной ~1мм. Это визуально 
    # резко но устойчиво — никаких артефактов.
    # 
    # ВАЖНО: только для визуализации, в .anc программу идёт исходник.
    # Предобработка осевой для визуализации:
    # - если включено «Сглаживание под фрезу» — применяем smooth_for_offset
    #   (та же геометрия, что уйдёт в .anc): тугие места скруглены, offset чист.
    # - иначе — оставляем как есть. (Раньше здесь был flatten_arcs_to_chords,
    #   но после merge_segments_to_arcs в session.load_ai дуги стали чистыми,
    #   и flatten только портил вид скруглений — превращал их в ломаные.)
    smooth_on = bool(options_extras.get('smooth_offset_for_tool', False))
    if smooth_on and not (is_3d_corner or is_2d_corner) and geom.is_closed \
            and tp.side in (ContourSide.OUTSIDE, ContourSide.INSIDE):
        from ..geometry.path_offset import (smooth_for_offset, 
            simplify_geometry_via_shapely, has_real_3d_corners)
        _side = "OUTSIDE" if tp.side == ContourSide.OUTSIDE else "INSIDE"
        min_tool_r = options_extras.get('min_tool_radius', tool_offset * 0.9)
        
        # Адаптивно: если есть настоящие 3D углы — НЕ сглаживаем
        # (любое сглаживание их уничтожит)
        if not has_real_3d_corners(polypath, min_tool_radius_mm=min_tool_r):
            polypath_for_vis = simplify_geometry_via_shapely(polypath, tol_mm=0.1)
            polypath_for_vis = smooth_for_offset(polypath_for_vis, tool_offset, _side)
        else:
            polypath_for_vis = polypath
    else:
        polypath_for_vis = polypath
    
    # ── ВНУТРЕННЕЕ СГЛАЖИВАНИЕ для соответствия с .anc ──
    # Объединение коллинеарных линий (как в mtx_anderson.py перед эмиссией NC).
    # Tangent-repair арок здесь не применяется — он искажает геометрию 
    # (см. комментарий в post).
    if not (is_3d_corner or is_2d_corner):
        from ..geometry.path_offset import merge_collinear_lines
        polypath_for_vis = merge_collinear_lines(polypath_for_vis, angle_tol_deg=1.0)
    
    if is_3d_corner or is_2d_corner:
        # CORNER: открытый фрагмент → используем geometric (к центру ножа)
        polypath_offset = offset_polypath_toward_center(
            polypath_for_vis, corner_tool_offset, center
        )
        # После оффсета каждого сегмента в углу остаётся разрыв.
        # Стыкуем линии через их пересечение — получается острый угол на 
        # эквидистанте (физически фреза в этой точке заходит и выходит 
        # под прямым углом, как и должна на острие).
        polypath_offset = join_polypath_corners(polypath_offset, tol=0.01)
    elif tp.side == ContourSide.INSIDE:
        # ВНЕШНИЙ рез (INSIDE=CCW + G41 → НАРУЖУ от центра, «+»).
        polypath_offset = offset_polypath_uniform(
            polypath_for_vis, tool_offset, inward=False
        )
        # Удаляем самопересечения (петли в местах тугой кривизны)
        polypath_offset = trim_self_intersections(polypath_offset)
        # Стыкуем углы — после оффсета параллельные сегменты не соединены
        polypath_offset = join_polypath_corners(polypath_offset, tol=0.01)
    elif tp.side == ContourSide.OUTSIDE:
        # ВНУТРЕННИЙ рез (OUTSIDE=CW + G42 → ВНУТРЬ к центру, «−»).
        polypath_offset = offset_polypath_uniform(
            polypath_for_vis, tool_offset, inward=True
        )
        polypath_offset = trim_self_intersections(polypath_offset)
        polypath_offset = join_polypath_corners(polypath_offset, tol=0.01)
    else:
        polypath_offset = polypath
    
    # ── OVERLAP откладывается на ПОСЛЕ авто-подбора (см. ниже) ──
    # Если применить overlap здесь, polypath перестанет быть замкнутым 
    # (открытая ломаная с расширением), и shift_start_along_contour в 
    # auto_avoid сместит вместе со стартом и сам overlap-кусок.
    # Сохраним значение, применим в конце.
    pending_overlap = 0.0
    if (cutting_params is not None 
            and not (is_3d_corner or is_2d_corner)
            and tp.side in (ContourSide.OUTSIDE, ContourSide.INSIDE)
            and src is not None and getattr(src, 'overlap', 0.0) > 1e-9):
        pending_overlap = src.overlap
    
    lead_in_poly = None
    lead_out_poly = None
    
    # Lead-in/out строится от точки на ЭКВИДИСТАНТЕ (реальная траектория 
    # фрезы), а не на программной геометрии. Иначе заход «висит в воздухе»
    # на видимом смещении от пути фрезы.
    # Для CORNER операций используется ТОНКАЯ фреза → меньший tool_radius
    # → пропорционально меньшие lead-дуги и линии.
    effective_tool_radius = corner_tool_radius if (is_3d_corner or is_2d_corner) else tool_radius
    # Для lead-in line используем tool_offset (фактический боковой зазор) 
    # и формулу Alpha CAM: line_length = factor × tool_offset / sin(angle).
    # Это даёт корректную lateral clearance от контура.
    effective_tool_offset = corner_tool_offset if (is_3d_corner or is_2d_corner) else tool_offset
    import math as _m_view
    def _line_len_alpha_view(user_factor: float, angle_deg: float) -> float:
        ang_rad = _m_view.radians(max(5.0, min(175.0, angle_deg)))
        sin_a = _m_view.sin(ang_rad)
        if sin_a < 0.05: sin_a = 0.05
        return user_factor * effective_tool_offset / sin_a
    
    # Сторона захода для 2D/3D углов — выбирается по bbox центру 
    # (передаётся как forced_side в LeadGeometryRequest).
    # Для OUTSIDE/INSIDE — авто-подбор стороны через pick_lead_side_for_pass 
    # внутри plan_lead_in.
    forced_lead_side = None
    if is_3d_corner or is_2d_corner:
        if polypath_offset and polypath_offset.segments:
            bb = polypath_bbox(geom.polypath)
            cx = (bb[0]+bb[2])/2
            cy = (bb[1]+bb[3])/2
            sp_check = polypath_offset.segments[0].a
            tan_check = polypath_offset.segments[0].tangent_at_start()
            cross = tan_check[0]*(cy - sp_check[1]) - tan_check[1]*(cx - sp_check[0])
            forced_lead_side = "left" if cross > 0 else "right"
        else:
            forced_lead_side = "right"
    
    # ── LEAD-OUT откладывается на ПОСЛЕ автоподбора + overlap ──
    lead_out_to_build = (tp.exit.enabled 
                         and tp.exit.style in (LeadStyle.LINE_ARC_TANGENTIAL, LeadStyle.LINE)
                         and polypath_offset and polypath_offset.segments)
    
    # ── ПЛАНИРОВАНИЕ LEAD-IN ЕДИНОЙ ФУНКЦИЕЙ ──
    # plan_lead_in делает всё:
    #   1) построение line+arc на старте polypath'а
    #   2) проверка коллизий (line-only апроксимация + bbox prefilter)
    #   3) автоподбор позиции (сдвиги ±1..±8мм) если коллизия
    #   4) если не помогло — варианты угла + укорачивание
    # Тот же код использует и mtx_anderson.py → одинаковые результаты.
    lead_in_collision = False
    lead_out_collision = False
    
    auto_avoid_all = bool(options_extras.get('auto_avoid_all', True))
    
    # Кеш контуров для коллизий — строится ОДИН раз, переиспользуется для 
    # lead-in и lead-out.
    contours_lines_cache = []
    contours_bboxes_cache = []
    if project is not None and (tp.entry.enabled or lead_out_to_build):
        from ..geometry.lead_collision import build_contours_cache
        knife_layer = project.get_layer_by_name("Knife")
        if knife_layer is not None:
            contours_lines_cache, contours_bboxes_cache = build_contours_cache(
                knife_layer.geometries)
    
    if (tp.entry.enabled 
            and tp.entry.style in (LeadStyle.LINE_ARC_TANGENTIAL, LeadStyle.LINE)
            and polypath_offset and polypath_offset.segments):
        from ..geometry.lead_collision import LeadGeometryRequest, plan_lead_in
        
        req_in = LeadGeometryRequest(
            is_entry=True,
            pass_side=tp.side.name,
            angle_deg=lead_in_angle,
            line_length=_line_len_alpha_view(lead_in_length_mult, lead_in_angle),
            arc_radius=lead_in_radius_mult * effective_tool_offset,
            style=('line' if tp.entry.style == LeadStyle.LINE else 'line_arc'),
            forced_side=forced_lead_side,
        )
        
        # Опциональный exit_request — чтобы plan_lead_in при поиске сдвига 
        # учитывал коллизию ОБОИХ leads (in + out). Без этого может выйти:
        # lead-in OK, но lead-out на той же точке коллизирует → не сдвигается.
        exit_req = None
        if lead_out_to_build:
            exit_req = LeadGeometryRequest(
                is_entry=False,
                pass_side=tp.side.name,
                angle_deg=lead_out_angle,
                line_length=_line_len_alpha_view(lead_out_length_mult, lead_out_angle),
                arc_radius=lead_out_radius_mult * effective_tool_offset,
                style=('line' if tp.exit.style == LeadStyle.LINE else 'line_arc'),
                forced_side=None,
            )
        
        polypath_offset, lead_in_poly, lead_in_collision, _ = plan_lead_in(
            polypath_offset, req_in,
            contours_lines_cache, contours_bboxes_cache,
            geom.id, effective_tool_offset,
            auto_avoid=auto_avoid_all and project is not None,
            exit_request=exit_req,
            overlap=pending_overlap)
    
    # ── ПРИМЕНЕНИЕ OVERLAP ПОСЛЕ автоподбора ──
    # Теперь когда позиция старта окончательно подобрана, удлиняем 
    # программную осевую и offset на src.overlap мм.
    if pending_overlap > 1e-9:
        from ..geometry.path_offset import apply_overlap
        try:
            if polypath is not None and polypath.closed:
                polypath = apply_overlap(polypath, pending_overlap)
            if polypath_offset is not None and polypath_offset.closed:
                polypath_offset = apply_overlap(polypath_offset, pending_overlap)
        except Exception:
            pass
    
    # ── ПЛАНИРОВАНИЕ LEAD-OUT ИЗ ФИНАЛЬНОГО КОНЦА ──
    # Через ту же plan_lead_out функцию (без авто-сдвига — позиция жёстко 
    # определена концом polypath'а после применения overlap'а).
    if lead_out_to_build and polypath_offset and polypath_offset.segments:
        from ..geometry.lead_collision import LeadGeometryRequest, plan_lead_out
        
        # forced_side для exit'а вычисляется так же как для entry: bbox 
        # центр для углов, иначе авто.
        forced_exit_side = None
        if is_3d_corner or is_2d_corner:
            bb = polypath_bbox(geom.polypath)
            cx = (bb[0]+bb[2])/2
            cy = (bb[1]+bb[3])/2
            ep_check = polypath_offset.segments[-1].b
            tan_check = polypath_offset.segments[-1].tangent_at_end()
            cross = tan_check[0]*(cy - ep_check[1]) - tan_check[1]*(cx - ep_check[0])
            forced_exit_side = "left" if cross > 0 else "right"
        
        req_out = LeadGeometryRequest(
            is_entry=False,
            pass_side=tp.side.name,
            angle_deg=lead_out_angle,
            line_length=_line_len_alpha_view(lead_out_length_mult, lead_out_angle),
            arc_radius=lead_out_radius_mult * effective_tool_offset,
            style=('line' if tp.exit.style == LeadStyle.LINE else 'line_arc'),
            forced_side=forced_exit_side,
        )
        lead_out_poly, lead_out_collision, _ = plan_lead_out(
            polypath_offset, req_out,
            contours_lines_cache, contours_bboxes_cache,
            geom.id, effective_tool_offset)
    
    return {
        'contour': polypath_offset,
        'lead_in': lead_in_poly,
        'lead_out': lead_out_poly,
        'lead_in_collision': lead_in_collision,
        'lead_out_collision': lead_out_collision,
        'is_2d_corner': is_2d_corner,
        'is_3d_corner': is_3d_corner,
        'side': tp.side,
        'op_id': op.id,
    }


def add_toolpaths_to_scene(scene: 'CamScene', project, options_extras: dict = None,
                            cutting_params=None, show_filter: dict = None,
                            progress_callback=None):
    """Добавляет на сцену визуализацию путей фрезы для всех операций проекта.
    
    Args:
        scene: CamScene куда добавлять
        project: проект с операциями
        options_extras: tool_radius, tool_equidistant
        cutting_params: CuttingMacroParams — берёт оттуда lead_in/out параметры
        show_filter: dict с ключами для фильтрации видимости (по умолчанию 
            всё видно). Ключи:
                'blade': bool — показывать ли BLADE_FORMING (чистовые/черновые)
                'corner_2d': bool — показывать ли 2D углы
                'corner_3d': bool — показывать ли 3D углы
        progress_callback: callable(current, total) → bool. Вызывается для 
            каждой обработанной операции. Возвращает True для продолжения, 
            False для отмены. Может None (без прогресса).
    
    Возвращает список добавленных элементов.
    """
    from ..core.project import ContourSide, OperationKind
    
    if options_extras is None:
        options_extras = {'tool_radius': 0.4, 'tool_equidistant': 0.8}
    
    if show_filter is None:
        show_filter = {}
    show_blade = show_filter.get('blade', True)
    show_corner_2d = show_filter.get('corner_2d', True)
    show_corner_3d = show_filter.get('corner_3d', True)
    
    # Назначаем цвета по ТИПУ ПРОГРАММЫ:
    #   - BLADE_FORMING с program_number=1 = один цвет (чистовая 1_M)
    #   - BLADE_FORMING с program_number=2 = другой цвет (2_M)
    #   - CORNER_REWORK 2D = свой цвет (corner.anc)
    #   - CORNER_REWORK 3D = свой цвет (corner3D.anc)
    # Так число уникальных цветов = число генерируемых программ.
    def _color_index(op):
        if op.kind == OperationKind.CORNER_REWORK:
            if op.attributes.get('corner_is_3d'):
                return 1  # corner3D — фиолетовый
            return 2  # corner 2D — голубой
        # BLADE_FORMING: по program_number (1, 2, ...)
        prog = op.attributes.get('program_number', 0)
        return 3 + (prog or 0)
    
    # Множество geometry_id у ИСКЛЮЧЁННЫХ BLADE — их CORNER тоже скрываем
    excluded_geom_ids = set()
    for op in project.operations:
        if (op.kind == OperationKind.BLADE_FORMING 
                and op.attributes.get('excluded', False)):
            for gid in op.geometry_ids:
                excluded_geom_ids.add(gid)
    
    items = []
    # Считаем общее число операций для показа прогресса
    total_ops = sum(1 for op in project.operations 
                    if op.kind in (OperationKind.BLADE_FORMING, OperationKind.CORNER_REWORK)
                    and not op.attributes.get('excluded', False))
    processed = 0
    
    for op in project.operations:
        if op.kind not in (OperationKind.BLADE_FORMING, OperationKind.CORNER_REWORK):
            continue
        
        # Исключённые операции (снята галочка в таблице) — не показываем
        if op.attributes.get('excluded', False):
            continue
        
        # CORNER операции связаны с BLADE через geometry_id — если родительский 
        # BLADE исключён, скрываем и его CORNER операции
        if op.kind == OperationKind.CORNER_REWORK:
            if any(gid in excluded_geom_ids for gid in op.geometry_ids):
                continue
        
        # Фильтрация по типу программы
        if op.kind == OperationKind.BLADE_FORMING:
            if not show_blade:
                continue
        elif op.kind == OperationKind.CORNER_REWORK:
            is_3d = op.attributes.get('corner_is_3d', False)
            if is_3d and not show_corner_3d:
                continue
            if not is_3d and not show_corner_2d:
                continue
        
        op_idx = _color_index(op)
        
        for tp in op.toolpaths:
            try:
                geo = _build_toolpath_geometry(
                    project, op, tp, options_extras, cutting_params
                )
            except Exception:
                continue
            if geo is None:
                continue
            
            if geo['contour'] and geo['contour'].segments:
                item = ToolpathItem(geo['contour'], kind='CONTOUR', op_index=op_idx)
                scene.addItem(item)
                items.append(item)
            
            if geo['lead_in'] and geo['lead_in'].segments:
                item = ToolpathItem(geo['lead_in'], kind='LEAD_IN', op_index=op_idx,
                                    collision=geo.get('lead_in_collision', False))
                scene.addItem(item)
                items.append(item)
            
            if geo['lead_out'] and geo['lead_out'].segments:
                item = ToolpathItem(geo['lead_out'], kind='LEAD_OUT', op_index=op_idx,
                                    collision=geo.get('lead_out_collision', False))
                scene.addItem(item)
                items.append(item)
        
        # Прогресс после обработки всех toolpath'ов операции
        processed += 1
        if progress_callback is not None:
            if progress_callback(processed, total_ops) is False:
                # Юзер нажал Cancel — прерываем
                break
    
    return items
