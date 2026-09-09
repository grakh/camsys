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
        
        # GeometryItem никогда не выделяется — visualization-only. Юзерская 
        # логика селекта работает через ToolpathItem в режиме «Выделенные».
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, False)
        
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
        """Рендер исходной геометрии — БЕЗ подсветки выделения.
        
        Юзерская подсветка (жёлтая) работает ТОЛЬКО через ToolpathItem 
        в режиме «Выделенные». GeometryItem — visualization-only слой, 
        никогда не подсвечивается.
        """
        # Убираем Qt-дефолтную рамку выделения (если она сработала)
        option.state &= ~QtWidgets.QStyle.State_Selected
        super().paint(painter, option, widget)


class FiducialItem(QtWidgets.QGraphicsItem):
    """Маркер репера: красный круг с крестом. Кликается через ПКМ 
    для включения/отключения (связка с FIDUCIAL_DRILL операцией)."""
    
    RADIUS = 4.0  # пикселей экранных
    
    def __init__(self, fiducial: Fiducial, op_id: str = ""):
        super().__init__()
        self.fiducial = fiducial
        self.op_id = op_id  # id соответствующей FIDUCIAL_DRILL операции
        self._excluded = False  # визуальное состояние (для перерисовки)
        self.setPos(fiducial.x, fiducial.y)
        self.setZValue(10)
    
    def boundingRect(self) -> QtCore.QRectF:
        r = self.RADIUS + 4  # +2мм для комфортного клика ПКМ
        return QtCore.QRectF(-r, -r, 2*r, 2*r)
    
    def shape(self):
        """Расширенная область захвата клика — 2мм в сцене."""
        path = QtGui.QPainterPath()
        path.addEllipse(QtCore.QPointF(0, 0), 2.0, 2.0)
        return path
    
    def set_excluded(self, excluded: bool):
        """Меняет визуальный статус — отключённый серым, вкл. — красным."""
        if self._excluded != excluded:
            self._excluded = excluded
            self.update()
    
    def paint(self, painter, option, widget=None):
        # Размер в пикселях экрана — нечувствительный к зуму
        scale = painter.transform().m11()
        r = self.RADIUS / abs(scale) if abs(scale) > 1e-6 else self.RADIUS
        
        # Цвет: серый для отключённого, красный для активного
        color = QtGui.QColor("#666666") if self._excluded else QtGui.QColor("#ff3030")
        pen = QtGui.QPen(color)
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
    
    # Сигнал: юзер кликнул на toolpath-элемент. Аргумент — id операции.
    # Используется в main_window для показа полей переопределения lead'а.
    # Пустая строка = клик мимо → снятие выделения.
    toolpath_clicked = QtCore.Signal(str)
    
    # Сигнал: правый клик на toolpath — юзер хочет переключить excluded 
    # (включить/исключить нож из экспорта). Аргумент — id операции.
    toolpath_right_clicked = QtCore.Signal(str)
    
    # Сигнал: юзер кликнул подпись региона на сцене. Аргумент — order_key
    # (композитный ключ типа "121254" или "121254#1"). main_window
    # переключает видимость путей этого заказа (D3).
    stitch_label_clicked = QtCore.Signal(str)
    
    def __init__(self):
        super().__init__()
        self.setBackgroundBrush(QtGui.QColor("#0d0d0d"))  # тёмный как Альфакам
        
        # Соответствие Geometry.id → GeometryItem (для выделения по id)
        self._geom_items: Dict[str, GeometryItem] = {}
        # Пункты сцены по AI-слоям и виртуальный «Регионы» для подписей заказов
        self._layer_items: Dict[str, List[QtWidgets.QGraphicsItem]] = {}
        self._stitch_label_items: List[QtWidgets.QGraphicsItem] = []
        # id выделенного toolpath (для переопределения lead'а)
        self._selected_op_id: str = ""
        # Флаг: разрешено ли выделение toolpath'ов кликом. Ставится главным 
        # окном по состоянию галки «Авто-подбор для всех элементов» — если 
        # ВЫКЛ, юзер может кликать по элементам и переопределять lead'ы.
        # По умолчанию ВЫКЛ — селект работает только когда явно включено.
        self._toolpath_selection_enabled: bool = False
    
    def set_toolpath_selection_enabled(self, enabled: bool):
        """Включить/выключить возможность выделения ножей по клику.
        
        Работает через собственную логику scene.mousePressEvent — ищет 
        ToolpathItem под курсором и подсвечивает его жёлтым (не через Qt-
        селект). GeometryItem'ы не выделяются НИКАК — они visualization-only.
        
        enabled=True (режим «Выделенные») → клик по ножу подсвечивает его.
        enabled=False → селект выключен, старое выделение снимается.
        """
        self._toolpath_selection_enabled = enabled
        if not enabled:
            # Снимаем наше toolpath-выделение
            self._selected_op_id = ""
            self._refresh_selection_highlight()
    
    def mousePressEvent(self, event):
        """Определяем клик по ToolpathItem — эмитим сигнал.
        
        ToolpathItem создаётся ТОЛЬКО для операций слоя Knife (по построению
        в add_toolpaths_to_scene). GeometryItem'ы не-Knife слоёв имеют 
        ItemIsSelectable=False (устанавливается в load_project), поэтому Qt 
        не выделяет их кликом. Дополнительной фильтрации по layer не нужно.
        
        Наш toolpath-select работает только когда `_toolpath_selection_enabled`
        (галка «Авто-подбор» ВЫКЛ). Иначе всё идёт через super() — Qt-логика 
        выделения GeometryItem'ов слоя Knife работает как обычно.
        """
        if event.button() == QtCore.Qt.LeftButton:
            pos = event.scenePos()
            hit_op_id = ""

            # ── ФАЗА 0: Подписи регионов сшивки (виртуальный слой) ──
            # Приоритетно, потому что подписи всегда наверху (z=999+).
            for it in self.items(pos):
                if it in self._stitch_label_items:
                    key = it.data(0)
                    if isinstance(key, str) and key:
                        self.stitch_label_clicked.emit(key)
                        event.accept()
                        return
                # Только верхний элемент — если это не подпись, идём дальше
                break
            
            # ── ФАЗА 1: РЕПЕРЫ (FiducialItem) ──
            # Реперы селектим ВСЕГДА (не только в режиме «Выделенные»), 
            # т.к. их не редактируют через lead-поля — просто клик = выделено.
            for it in self.items(pos):
                if isinstance(it, FiducialItem) and it.op_id:
                    hit_op_id = it.op_id
                    break
            
            # ── ФАЗА 2+3: Toolpath (только если Выделенные режим) ──
            if not hit_op_id and self._toolpath_selection_enabled:
                # ── ФАЗА 2: Приоритетный поиск УГЛА ──
                # Углы имеют z=8, blade z=5 → в items(pos) углы уже сверху. Но 
                # если click вне узкой shape() угла и попадает только в широкую 
                # blade — угол пропустим. Поэтому ищем в РАСШИРЕННОЙ 5мм зоне 
                # вокруг клика: любой найденный угол побеждает.
                search_rect = QtCore.QRectF(pos.x()-2.5, pos.y()-2.5, 5.0, 5.0)
                for it in self.items(search_rect):
                    if (isinstance(it, ToolpathItem) and it.op_id and it.selectable 
                            and it.zValue() >= 7):  # z>=7 → угол
                        hit_op_id = it.op_id
                        break
                
                # ── ФАЗА 3: Fallback на blade (точное попадание) ──
                # Если угол не нашли — обычный поиск по точке (найдёт blade).
                if not hit_op_id:
                    for it in self.items(pos):
                        if isinstance(it, ToolpathItem) and it.op_id and it.selectable:
                            hit_op_id = it.op_id
                            break
            
            if hit_op_id and hit_op_id != self._selected_op_id:
                self._selected_op_id = hit_op_id
                self._refresh_selection_highlight()
                self.toolpath_clicked.emit(hit_op_id)
                event.accept()
                return
        
        elif event.button() == QtCore.Qt.RightButton:
            # Правый клик на toolpath — переключить excluded (быстрая 
            # альтернатива галочке в operations-таблице). Логика поиска 
            # такая же как для левой кнопки (сначала угол, потом blade).
            pos = event.scenePos()
            hit_op_id = ""
            
            search_rect = QtCore.QRectF(pos.x()-2.5, pos.y()-2.5, 5.0, 5.0)
            for it in self.items(search_rect):
                if (isinstance(it, ToolpathItem) and it.op_id and it.selectable 
                        and it.zValue() >= 7):
                    hit_op_id = it.op_id
                    break
            
            if not hit_op_id:
                for it in self.items(pos):
                    if isinstance(it, ToolpathItem) and it.op_id and it.selectable:
                        hit_op_id = it.op_id
                        break
            
            # Также проверяем клик по реперу (FiducialItem)
            if not hit_op_id:
                for it in self.items(pos):
                    if isinstance(it, FiducialItem) and it.op_id:
                        hit_op_id = it.op_id
                        break
            
            if hit_op_id:
                self.toolpath_right_clicked.emit(hit_op_id)
                event.accept()
                return
        
        super().mousePressEvent(event)
    
    def keyPressEvent(self, event):
        """Escape — снять выделение toolpath'а."""
        if event.key() == QtCore.Qt.Key_Escape and self._selected_op_id:
            self.clear_selection()
            self.toolpath_clicked.emit("")
        else:
            super().keyPressEvent(event)
    
    def _refresh_selection_highlight(self):
        """Помечает выделенный ToolpathItem жирной обводкой."""
        for item in self.items():
            if isinstance(item, ToolpathItem):
                item.set_selected_highlight(item.op_id == self._selected_op_id
                                              and self._selected_op_id != "")
    
    def refresh_fiducial_state(self, project):
        """Обновляет визуальное состояние FiducialItem'ов по excluded flag'у.
        
        Вызывается когда юзер меняет excluded у FIDUCIAL_DRILL операции 
        (галка в таблице, ПКМ на канвасе). Без этого визуал репера остаётся 
        активно-красным даже когда галка снята.
        """
        from ..core.project import OperationKind
        # Строим отображение op_id → excluded
        fid_excluded = {
            op.id: op.attributes.get('excluded', False)
            for op in project.operations
            if op.kind == OperationKind.FIDUCIAL_DRILL
        }
        for item in self.items():
            if isinstance(item, FiducialItem) and item.op_id in fid_excluded:
                item.set_excluded(fid_excluded[item.op_id])
    
    def clear_selection(self):
        """Снять выделение (все ToolpathItem'ы возвращаются к обычному виду)."""
        self._selected_op_id = ""
        self._refresh_selection_highlight()
    
    def clear_all(self):
        self.clear()
        self._geom_items.clear()
        self._layer_items.clear()
        self._stitch_label_items.clear()

    def add_stitch_labels(self, stitch_info, order_states=None):
        """Рисует подписи номеров заказов у нижней границы каждого региона.

        Args:
            stitch_info: StitchInfo с регионами.
            order_states: dict {order_key: {'has_paths': bool, 'is_shown': bool}}
                — состояние заказа в сессии. `has_paths=True` = пути
                уже посчитаны и лежат в кэше `_order_toolpath_items` (тогда
                подпись зелёная и с ✓). `is_shown=True` = пути этого заказа
                сейчас видны на сцене (тогда фон подсвечен ярче). Оба флага
                независимы: юзер может ✓ но скрытый (или наоборот).

        Подписи получают data(0)=order_key — при клике сцена эмитит
        `stitch_label_clicked(order_key)`.
        """
        for item in self._stitch_label_items:
            self.removeItem(item)
        self._stitch_label_items.clear()

        if stitch_info is None or not stitch_info.regions:
            return

        if order_states is None:
            order_states = {}

        from collections import defaultdict
        from ..io_.stitch import order_key_to_filename
        counts = defaultdict(int)
        for r in stitch_info.regions:
            if r.order_number:
                counts[r.order_number] += 1
        seen = defaultdict(int)

        for i, r in enumerate(stitch_info.regions):
            on = r.order_number or f"регион {i + 1}"
            # Композитный ключ для копий (совпадает с dropdown itemData)
            if r.order_number and counts[r.order_number] > 1:
                copy_idx = seen[r.order_number]
                seen[r.order_number] += 1
                label_base = f"{on} (копия {copy_idx + 1})"
                order_key = f"{on}#{copy_idx}"
            else:
                label_base = on
                order_key = on

            st = order_states.get(order_key, {})
            has_paths = bool(st.get('has_paths', False))
            is_shown = bool(st.get('is_shown', False))

            label_text = label_base + ("  ✓" if has_paths else "")

            # Позиция — центр X региона, у нижней границы (там где L-test)
            x0, y0, x1, y1 = r.bbox
            cx = (x0 + x1) / 2.0
            cy = y0

            font = QtGui.QFont()
            font.setPointSizeF(9.0)
            font.setBold(True)
            text_item = QtWidgets.QGraphicsSimpleTextItem(label_text)
            text_item.setFont(font)
            # Цвет:
            #   зелёный = has_paths (пути посчитаны в сессии)
            #   белый   = has_paths=False
            #   is_shown добавляет яркую жёлтую рамку/цвет
            if is_shown:
                colour = QtGui.QColor("#ffff00")
            elif has_paths:
                colour = QtGui.QColor("#00ff88")
            else:
                colour = QtGui.QColor("#ffffff")
            text_item.setBrush(QtGui.QBrush(colour))
            text_item.setTransform(QtGui.QTransform().scale(1, -1))
            br = text_item.boundingRect()
            text_item.setPos(cx - br.width() / 2.0, cy + br.height() + 2.0)
            text_item.setZValue(1000)
            # Кладём order_key на элемент, чтобы поймать клик и понять кого
            text_item.setData(0, order_key)

            pad = 2.0
            bg = QtWidgets.QGraphicsRectItem(
                cx - br.width() / 2.0 - pad,
                cy + 2.0 - pad,
                br.width() + 2 * pad,
                br.height() + 2 * pad,
            )
            # Фон: тёмный (обычный) или тёмно-жёлтый (когда показан)
            if is_shown:
                bg.setBrush(QtGui.QBrush(QtGui.QColor(80, 60, 0, 220)))
                bg.setPen(QtGui.QPen(QtGui.QColor("#ffff00"), 0))
            else:
                bg.setBrush(QtGui.QBrush(QtGui.QColor(0, 0, 0, 180)))
                bg.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            bg.setZValue(999)
            bg.setData(0, order_key)  # клик по фону тоже должен работать

            self.addItem(bg)
            self.addItem(text_item)
            self._stitch_label_items.extend([bg, text_item])

    def set_stitch_labels_visible(self, visible: bool):
        """Показать/скрыть подписи заказов (виртуальный слой «Регионы»)."""
        for item in self._stitch_label_items:
            item.setVisible(visible)
    
    def load_project(self, project: Project):
        """Перезагружает сцену из проекта."""
        self.clear_all()
        
        for layer in project.layers.values():
            color = QtGui.QColor(layer.color or "#00ff00")
            items = []
            # Только слой Knife может быть выделяемым. На остальных 
            # (Reg-марки, Trim-линии, лист-бордер) ItemIsSelectable=False 
            # всегда — их вообще не выделяем никогда.
            is_knife = (layer.name == "Knife")
            for geom in layer.geometries:
                if geom.polypath is None:
                    continue
                item = GeometryItem(geom, color)
                # ItemIsSelectable=False всегда (задано в __init__). Юзерский 
                # селект работает через ToolpathItem — не через геометрии.
                item.setVisible(layer.visible and geom.is_visible)
                self.addItem(item)
                self._geom_items[geom.id] = item
                items.append(item)
            self._layer_items[layer.name] = items
        
        # Реперы поверх — с привязкой к своим FIDUCIAL_DRILL операциям
        # чтобы ПКМ на репере мог включать/отключать соответствующую op.
        from ..core.project import OperationKind
        fid_op_map = {
            op.attributes.get('fiducial_id'): op
            for op in project.operations
            if op.kind == OperationKind.FIDUCIAL_DRILL 
            and op.attributes.get('fiducial_id')
        }
        for fid in project.fiducials:
            op = fid_op_map.get(fid.id)
            op_id = op.id if op else ""
            excluded = op.attributes.get('excluded', False) if op else False
            # Фильтр по региону сшивки
            stitch_filtered = (
                op.attributes.get('stitch_filtered_out', False) if op else False)
            item = FiducialItem(fid, op_id=op_id)
            item.set_excluded(excluded)
            item.setVisible(not stitch_filtered)
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
        """Колесо мыши — зум с центром под курсором.

        Компенсируем сдвиг вручную: замеряем сцену-точку под курсором до и
        после scale, разницу возвращаем translate'ом. Для этого якорь
        трансформации должен быть NoAnchor — иначе AnchorUnderMouse тоже
        пытается держать точку и в паре со scale(1,-1) картинка «уплывает»
        к началу координат (два механизма конфликтуют).
        """
        angle = event.angleDelta().y()
        if angle == 0:
            return
        factor = 1.15 if angle > 0 else 1 / 1.15
        # QWheelEvent.position() — QPointF в координатах вьюпорта
        try:
            pos_view = event.position().toPoint()
        except AttributeError:  # старые Qt: pos()
            pos_view = event.pos()
        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        old_scene = self.mapToScene(pos_view)
        self.scale(factor, factor)
        new_scene = self.mapToScene(pos_view)
        delta = new_scene - old_scene
        self.translate(delta.x(), delta.y())
    
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
                 collision: bool = False, op_id: str = "",
                 selectable: bool = False):
        super().__init__()
        self.kind = kind
        self.op_index = op_index
        self.collision = collision
        self.op_id = op_id  # id операции для клика/переопределения
        # Флаг: реагирует ли этот item на клик для селекта. По умолчанию 
        # False — селект работает только на определённых toolpath'ах 
        # (внутренний контур blade — задаётся в add_toolpaths_to_scene).
        self.selectable = selectable
        # Флаг подсветки выделения (устанавливается CamScene при клике)
        self._highlighted = False
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
        self._base_pen = pen  # запоминаем оригинал для восстановления
        self.setPen(pen)
        
        self.setZValue(6 if collision else 5)  # коллизии поверх
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, False)
    
    def set_selected_highlight(self, highlighted: bool):
        """Показать/убрать подсветку выделенного элемента.
        
        Выделенный элемент рисуется жирной жёлтой обводкой поверх обычного 
        цвета. Z-value НЕ меняем — иначе выделенный blade блокировал бы 
        клик по углам через свою широкую shape() зону.
        """
        if self._highlighted == highlighted:
            return
        self._highlighted = highlighted
        if highlighted:
            pen = QtGui.QPen(QtGui.QColor('#ffff00'))  # ярко-жёлтый
            pen.setWidthF(3.0)
            pen.setCosmetic(True)
            self.setPen(pen)
        else:
            self.setPen(self._base_pen)
        self.update()
    
    def boundingRect(self):
        return self.path().controlPointRect().adjusted(-5, -5, 5, 5)
    
    def shape(self):
        """Область захвата клика.
        
        Для selectable=True — расширяем чтобы юзеру было легче попасть:
        - Для CORNER — 2мм (углы маленькие; шире мешало бы селекту соседних 
          углов, но 1мм было тонковато)
        - Для BLADE — 3мм (крупные, редко близко)
        Для остальных toolpath'ов — дефолтная тонкая.
        """
        if not self.selectable:
            return super().shape()
        # Эвристика по длине path: короткие = углы, длинные = blade
        path_len = self._estimate_path_length()
        width = 2.0 if path_len < 15.0 else 3.0
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(width)
        stroker.setCapStyle(QtCore.Qt.RoundCap)
        stroker.setJoinStyle(QtCore.Qt.RoundJoin)
        return stroker.createStroke(self.path())
    
    def _estimate_path_length(self) -> float:
        """Быстрая оценка длины полипаса (сумма длин Line + Arc)."""
        if not self._polypath or not self._polypath.segments:
            return 0.0
        total = 0.0
        for seg in self._polypath.segments:
            try:
                total += seg.length()
            except Exception:
                pass
        return total
    
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


def _end_curvature_radius(polypath, at_start: bool) -> float:
    """Радиус кривизны на конце фрагмента (мм). Линия = бесконечность
    (пологая), дуга = её радиус. Смотрим 1-2 крайних сегмента: если конец
    угла — дуга малого радиуса, значит кривизна крутая и лид сядет плохо.

    at_start=True — начало фрагмента (там будет заход/lead-in);
    at_start=False — конец (lead-out).
    """
    from ..geometry.primitives import Arc as _ArcC
    if not polypath or not polypath.segments:
        return float('inf')
    segs = polypath.segments
    # КРАЙНИЙ сегмент (там начинается лид). Скругление угла в середине
    # фрагмента не смотрим — важна кривизна именно на краю (зоне pad),
    # куда сядет заход/выход.
    edge = segs[0] if at_start else segs[-1]
    if isinstance(edge, _ArcC):
        return edge.radius
    return float('inf')  # край — линия, пологий


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
        # Базовый pad 3.0мм (было 1.5): лид начинается ДАЛЬШЕ от
        # скругления → переход угол→заход/выход более ПОЛОГИЙ, поворот
        # лида не жмётся к острию.
        _base_pad = 3.0
        polypath = extract_subpath_around_indices(
            geom.polypath, first_idx, last_idx, pad_mm=_base_pad
        )
        # ── АДАПТИВНОЕ УДЛИНЕНИЕ угла по кривизне концов ──
        # Если на конце фрагмента (где начнётся заход/выход) кривизна ещё
        # КРУТАЯ (радиус < порога), поворот лида создаёт тесную петлю и
        # подрежет лезвие. Продлеваем pad, пока оба конца не выйдут на
        # ПОЛОГИЙ участок (радиус >= порога) — тогда лид садится на мягкую
        # кривую. Элементы разные, поэтому удлинение подбирается под каждый.
        try:
            _R_MIN = 1.5  # порог радиуса кривизны на концах (мм)
            _PAD_MAX = 5.0  # предел, чтобы не залезть на соседний угол
            _pad = _base_pad
            while _pad < _PAD_MAX:
                _r_start = _end_curvature_radius(polypath, at_start=True)
                _r_end = _end_curvature_radius(polypath, at_start=False)
                if _r_start >= _R_MIN and _r_end >= _R_MIN:
                    break  # оба конца пологие — достаточно
                _pad = min(_pad + 1.0, _PAD_MAX)
                polypath = extract_subpath_around_indices(
                    geom.polypath, first_idx, last_idx, pad_mm=_pad)
        except Exception:
            pass
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
            src_key = 'lead_inside'
        elif tp.side == ContourSide.INSIDE:
            src = cutting_params.lead_outside   # внешний рез
            src_key = 'lead_outside'
        else:
            src = None
            src_key = None
        if src is not None:
            # Per-op override (режим «Выделенные»): если у op'а есть 
            # lead_override, берём параметры оттуда — это позволяет 
            # каждому ножу иметь СВОИ параметры lead'а, не привязанные к 
            # текущим глобальным полям.
            ov = op.attributes.get('lead_override', {}).get(src_key, {})
            user_offset = ov.get('offset', src.offset)
            lead_in_angle = ov.get('angle', src.angle)
            lead_in_length_mult = ov.get('length', src.length)
            lead_in_radius_mult = ov.get('length', src.length)
            lead_out_angle = ov.get('angle', src.angle)
            lead_out_length_mult = ov.get('length', src.length)
            lead_out_radius_mult = ov.get('length', src.length)
    elif is_3d_corner or is_2d_corner:
        # Для углов override применяется: entry (заход) → lead_inside,
        # exit (выход) → lead_outside. Разные значения, как для обычных 
        # ножей. Юзер редактирует поля «Внутренний» — влияют на заход, 
        # поля «Внешний» — на выход. offset берётся из lead_inside 
        # (единая точка старта).
        ov_in = op.attributes.get('lead_override', {}).get('lead_inside', {})
        ov_out = op.attributes.get('lead_override', {}).get('lead_outside', {})
        if ov_in:
            user_offset = ov_in.get('offset', user_offset)
            lead_in_angle = ov_in.get('angle', lead_in_angle)
            lead_in_length_mult = ov_in.get('length', lead_in_length_mult)
            lead_in_radius_mult = ov_in.get('length', lead_in_radius_mult)
        if ov_out:
            lead_out_angle = ov_out.get('angle', lead_out_angle)
            lead_out_length_mult = ov_out.get('length', lead_out_length_mult)
            lead_out_radius_mult = ov_out.get('length', lead_out_radius_mult)
    
    # ── Нормализация направления и точка старта ──
    is_corner = is_3d_corner or is_2d_corner
    if is_corner:
        # Развернуть фрагмент под CW (центр справа).
        # CORNER_REWORK ноги имеют side=OUTSIDE (внутренний рез = CW). 
        # G-code emitter выдаст G42 (комп. вправо). Внутренний рез blade 
        # тоже идёт CW, углы должны продолжать это направление — тогда 
        # фреза с G42 уходит в ту же сторону = ВНУТРЬ ножа = крючок в угол.
        bb = polypath_bbox(geom.polypath)
        cx = (bb[0] + bb[2]) / 2.0
        cy = (bb[1] + bb[3]) / 2.0
        sp = polypath.segments[0].a
        tan = polypath.segments[0].tangent_at_start()
        cross = tan[0]*(cy - sp[1]) - tan[1]*(cx - sp[0])
        if cross > 0:  # центр слева = CCW → разворачиваем под CW
            polypath = reverse_polypath(polypath)
    elif geom.is_closed and tp.side in (ContourSide.OUTSIDE, ContourSide.INSIDE):
        side_name = "OUTSIDE" if tp.side == ContourSide.OUTSIDE else "INSIDE"
        polypath = normalize_for_side(polypath, side_name)

        # ── ПОЗИЦИЯ СТАРТА: считаем ТЕМ ЖЕ, ЧЕМ ЭМИТТЕР ──
        # Раньше брали тангенс из .anc (compute_anc_tangents). Но на сшитой
        # раскладке его парсинг у угла давал OUTSIDE неверную точку (уезжал
        # на другую грань → внутр/внешн расходились). Теперь эмиттер и вьювер
        # считают старт ОДНОЙ функцией (shift_start_from_diagonal_zero), так
        # что anc-тангенс не нужен: собственный расчёт вьювера = .anc, но без
        # ошибок парсинга. Позиционирование делает diagonal-zero блок ниже.
        anc_tangent = None
        
        # Если использовали .anc-тангенс — полипас УЖЕ в правильной точке
        # (эмиттер уже применил все нужные сдвиги при генерации). Пропускаем
        # весь fallback chain (направление, CW-extra, инверсия offset,
        # user_offset) — иначе получим двойное применение.
        if anc_tangent is None:
            # Смещение вдоль ВЕРХНЕЙ грани от верхне-правого угла контура
            # (как в эмиттере): 0 = угол, <0 = влево по верху, >0 = зажим у
            # угла. Единая точка для обоих проходов, не заворачивает вниз по
            # стороне (как делал сдвиг по периметру → каша на прямоугольниках).
            from ..geometry.path_offset import shift_start_from_diagonal_zero as _ssdz
            polypath = _ssdz(polypath, user_offset)
        
        # ── ДИАГНОСТИКА СМЕЩЕНИЯ (правый клик «Диагностика смещения») ──
        # Пишем на op по стороне прохода: какой offset пришёл, взялась ли
        # точка из .anc (тангенс) и куда встал старт. Юзер сравнивает −5/0/5/2.
        try:
            _dg = op.attributes.setdefault('_diag_offset', {})
            _st = polypath.segments[0].a if polypath.segments else (0, 0)
            _dg[tp.side.name] = {
                'user_offset': round(user_offset, 3),
                'from_anc': anc_tangent is not None,
                'anc_tangent': ([round(anc_tangent[0], 2), round(anc_tangent[1], 2)]
                                if anc_tangent is not None else None),
                'start': [round(_st[0], 2), round(_st[1], 2)],
            }
            # + в лог-файл (~/camsys_offset_diag.log) — удобно собрать по
            # нескольким значениям offset без кликанья по диалогам.
            import os as _os, datetime as _dt
            _logp = _os.path.join(_os.path.expanduser("~"),
                                  "camsys_offset_diag.log")
            _field = ("Внутр" if tp.side.name in ("OUTSIDE", "RIGHT")
                      else "Внешн")
            _tanx = ([round(anc_tangent[0], 2), round(anc_tangent[1], 2)]
                     if anc_tangent is not None else None)
            with open(_logp, "a", encoding="utf-8") as _lf:
                _lf.write(
                    "%s | %-16s | %-7s(%s) | offset=%-6s | from_anc=%-5s "
                    "tangent=%-16s | start=%s\n" % (
                        _dt.datetime.now().strftime("%H:%M:%S"),
                        getattr(op, 'name', '?'), tp.side.name, _field,
                        round(user_offset, 3), anc_tangent is not None,
                        _tanx, [round(_st[0], 2), round(_st[1], 2)]))
        except Exception:
            pass
        
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
                                         offset_polypath_toward_body,
                                         offset_polypath_uniform,
                                         simplify_for_visualization,
                                         flatten_arcs_to_chords,
                                         join_polypath_corners,
                                         trim_self_intersections,
                                         despike_polypath)
    
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
            simplify_geometry_via_shapely, has_real_3d_corners,
            merge_segments_to_arcs)
        _side = "OUTSIDE" if tp.side == ContourSide.OUTSIDE else "INSIDE"
        min_tool_r = options_extras.get('min_tool_radius', tool_offset * 0.9)
        
        # Адаптивно: если есть настоящие 3D углы — НЕ сглаживаем
        # (любое сглаживание их уничтожит)
        if not has_real_3d_corners(polypath, min_tool_radius_mm=min_tool_r):
            polypath_for_vis = simplify_geometry_via_shapely(polypath, tol_mm=0.1)
            polypath_for_vis = smooth_for_offset(polypath_for_vis, tool_offset, _side)
            # Обратная сборка полилинии в дуги (чтобы viewer показывал 
            # чистые кривые как в .anc, а не тысячи мелких Line)
            polypath_for_vis = merge_segments_to_arcs(polypath_for_vis, tol=0.02)
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
    
    _corner_mid_ref = None
    if is_3d_corner or is_2d_corner:
        # CORNER: эквидистанта фрезы. Направление (внутрь/наружу тела)
        # выбирается по ВЫПУКЛОСТИ угла — АБСОЛЮТНЫМ геометрическим
        # критерием (центр дуги скругления относительно тела), НЕ через
        # namotku+side (что было корнем давней путаницы: namotka исходного
        # контура любая, а side — это знак offset, они независимы).

        # ── ВЫПУКЛОСТЬ угла — АБСОЛЮТНЫЙ геометрический критерий ──
        # Не зависит от namotki/side/arc.ccw (источник давней путаницы).
        # Признак: где лежит ЦЕНТР дуги скругления относительно тела.
        #   центр ВНУТРИ контура  → ВЫПУКЛЫЙ угол (скругление торчит наружу)
        #   центр СНАРУЖИ контура → ВОГНУТЫЙ угол (карман внутрь)
        # Для 3D (нет дуги) — по стыку линий через знак площади вершины.
        from ..geometry.primitives import Arc as _Arc
        from ..geometry.path_offset import _point_in_polypath as _pip_cvx
        _convex = True
        _rounding_arc = None
        for _s in polypath_for_vis.segments:
            if isinstance(_s, _Arc) and _s.radius < 2.0:
                # Берём САМУЮ ТЕСНУЮ дугу (мин. радиус) — это и есть
                # скругление угла. «Первая попавшаяся <2мм» подхватывала
                # крупную дугу фланга (R~1.5-1.9) у некоторых углов, её
                # центр падал внутрь тела → ложный convex=True → эквидистанта
                # уходила не в ту сторону (123308 geom1 c1/c3).
                if _rounding_arc is None or _s.radius < _rounding_arc.radius:
                    _rounding_arc = _s
        if _rounding_arc is not None:
            # 2D: центр дуги скругления внутри тела → выпуклый.
            try:
                _cx, _cy = _rounding_arc.center
                _convex = _pip_cvx((_cx, _cy), geom.polypath)
            except Exception:
                _convex = True
        else:
            # 3D: знак площади треугольника (до-вершина-после) —
            # абсолютный поворот в координатах, затем сверяем с телом.
            try:
                _mid = len(polypath_for_vis.segments) // 2
                _s_in = polypath_for_vis.segments[max(0, _mid - 1)]
                _s_out = polypath_for_vis.segments[min(
                    len(polypath_for_vis.segments) - 1, _mid)]
                _pv = _s_in.a       # точка до вершины
                _vx = _s_in.b       # вершина
                _pn = _s_out.b      # точка после
                # пробная точка чуть в сторону биссектрисы внутрь поворота
                _mx = (_pv[0] + _pn[0]) / 2.0
                _my = (_pv[1] + _pn[1]) / 2.0
                # середина хорды до/после; если она внутри тела — вершина
                # торчит наружу (выпукло), если снаружи — вогнуто.
                _convex = _pip_cvx((_mx, _my), geom.polypath)
            except Exception:
                _convex = True

        # ЗНАК эквидистанты угла: зависит от ВЫПУКЛОСТИ, не только tp.side.
        # Все corner-операции помечены tp.side=OUTSIDE, но физически:
        #   ВЫПУКЛЫЙ угол (горб) → рез ИЗНУТРИ → путь ВНУТРИ тела (OUTSIDE)
        #   ВОГНУТЫЙ угол (линия ушла ВНУТРЬ элемента, внешний контур) →
        #     это сторона INSIDE → путь и лиды ОТ элемента (СНАРУЖИ).
        # _convex определён выше абсолютным критерием (центр дуги в теле).
        #   выпуклый → угол ВНУТРИ тела (_main_side_inside=True)
        #   вогнутый → угол СНАРУЖИ (_main_side_inside=False)
        _main_side_inside = _convex
        try:
            from ..geometry.path_offset import (
                offset_polypath_uniform as _ofu2,
                _point_in_polypath as _pip3,
                trim_self_intersections as _tsi2,
                join_polypath_corners as _jpc2)
            _inward = _main_side_inside
            _eq_try = _ofu2(polypath_for_vis, corner_tool_offset,
                            inward=True)
            _eq_try = _tsi2(_eq_try)
            _eq_try = _jpc2(_eq_try, tol=0.01)
            if _eq_try.segments:
                _mp3 = _eq_try.segments[len(_eq_try.segments)//2].a
                _in_true = _pip3(_mp3, geom.polypath)
                _inward = True if (_in_true == _main_side_inside) else False
        except Exception:
            _inward = _main_side_inside

        polypath_offset = offset_polypath_uniform(
            polypath_for_vis, corner_tool_offset,
            inward=_inward
        )
        polypath_offset = trim_self_intersections(polypath_offset)
        # После оффсета каждого сегмента в углу остаётся разрыв.
        # Стыкуем линии через их пересечение — острый угол на
        # эквидистанте (фреза заходит и выходит под прямым углом).
        polypath_offset = join_polypath_corners(polypath_offset, tol=0.01)
        # Эталон стороны «тела» угла фиксируем ДО despike: despike меняет
        # число сегментов, поэтому segments[len//2] ПОСЛЕ него укажет на
        # другую точку и может развернуть сторону лида (регрессия). Старт и
        # касательную на конце despike сохраняет, а середину фиксируем тут.
        _corner_mid_ref = polypath_offset.segments[
            len(polypath_offset.segments) // 2].a
        # ── УБИРАЕМ ПЕТЛИ В ТОЧКАХ ИЗЛОМА ──
        # Пооссегментный оффсет сырого биарк-фрагмента даёт микро-петли на
        # стыках offset-дуг (сегмент k+1 стартует «позади» конца k).
        # trim_self_intersections их не ловит (считает по хордам дуг), а
        # join не перекрывает зазоры 0.02–0.04мм. despike плотно сэмплирует
        # путь и вырезает самопересечения, сохраняя концы (к ним крепятся
        # lead-in/out). Это ТОЛЬКО визуальная эквидистанта — в .anc угол
        # пишется сырым фрагментом + G42, петель там нет.
        polypath_offset = despike_polypath(polypath_offset)
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
        # СТОРОНА ЛИДА УГЛА: кончик лида должен быть с ТОЙ ЖЕ стороны от
        # контура, что и сама ЭКВИДИСТАНТА УГЛА (она уже построена
        # правильно с _inward). Определяем ВЕКТОРНО, без point_in_polypath
        # (который врёт на полосах, особенно после удлинения угла, когда
        # старт лида уходит на пологий участок).
        #
        # Берём среднюю точку эквидистанты угла (эталон стороны) и
        # стартовую точку; сравниваем на какой стороне от касательной
        # лежит эквидистанта. Лид строим в ТУ ЖЕ сторону.
        if polypath_offset and polypath_offset.segments:
            try:
                from ..geometry.lead_inout import build_lead_in as _bli3
                from ..geometry.path_offset import _point_in_polypath as _pip_lead
                _sp = polypath_offset.segments[0].a
                _tan = polypath_offset.segments[0].tangent_at_start()
                # ЗНАК ЛИДА = ЗНАК ЭКВИДИСТАНТЫ, устойчиво.
                # Эквидистанта смещена от программного контура в сторону
                # inward. Лид должен идти в ТУ ЖЕ сторону (дальше от детали).
                # Определяем сторону эквидистанты как «внутри/снаружи
                # замкнутого контура ножа» по её СЕРЕДИНЕ (хорошо разнесённая
                # точка — устойчиво, в отличие от вектора у самого старта,
                # который у пограничных углов почти нулевой и переворачивался).
                # Затем строим обе пробы лида и берём ту, чей кончик на той же
                # стороне контура (внутри/снаружи), что и эквидистанта.
                _mid_off = _corner_mid_ref if _corner_mid_ref is not None \
                    else polypath_offset.segments[
                        len(polypath_offset.segments)//2].a
                _eq_inside = _pip_lead(_mid_off, geom.polypath)
                _chosen = None
                _fallback = None
                for _side in ('left', 'right'):
                    _lg = _bli3(start_point=_sp, tangent=_tan, side=_side,
                                line_length=1.0, arc_radius=0.5,
                                approach_angle_deg=45, style='line_arc')
                    _tip = _lg.line.a if _lg.line else _sp
                    if _fallback is None:
                        _fallback = _side
                    if _pip_lead(_tip, geom.polypath) == _eq_inside:
                        _chosen = _side
                        break
                forced_lead_side = _chosen or _fallback or "right"
            except Exception:
                forced_lead_side = "right"
        else:
            forced_lead_side = "right"
        # ── ДИАГНОСТИКА (для правого клика «Диагностика угла») ──
        # Пишем вычисленные признаки прямо на op, чтобы юзер мог их
        # прочитать в приложении и прислать — так видно, где превью
        # расходится с ожиданием без гадания.
        try:
            _rr = _rounding_arc.radius if _rounding_arc is not None else None
            _rc = _rounding_arc.center if _rounding_arc is not None else None
            op.attributes['_diag'] = {
                'convex': _convex,
                'inward': _inward,
                'forced_lead_side': forced_lead_side,
                'rounding_R': (round(_rr, 3) if _rr is not None else None),
                'rounding_center': ([round(_rc[0], 2), round(_rc[1], 2)]
                                    if _rc is not None else None),
                'corner_radius': op.attributes.get('corner_radius'),
                'apex': [round(op.attributes.get('corner_apex', (0, 0))[0], 1),
                         round(op.attributes.get('corner_apex', (0, 0))[1], 1)],
            }
        except Exception:
            pass
    # ОСНОВНОЙ путь (не corner): forced_lead_side=None. Основные лиды
    # строит auto-подбор — их НЕ трогаем, проблема только в углах.
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
    
    # Режим применения lead'а: 0=Авто, 1=Все, 2=Выделенные.
    # Selected режим означает: юзерские поля применяются ТОЛЬКО к 
    # выделенному ножу, остальные строятся автоалгоритмом.
    lead_mode = int(options_extras.get('lead_mode', 0))
    selected_op_id = str(options_extras.get('selected_op_id', ''))
    
    # Для конкретного op определяем: использовать юзерские поля точно 
    # (auto_avoid=False) или автосдвиг (auto_avoid=True):
    #   mode=0 Auto     → все → auto_avoid=True
    #   mode=1 All      → все → auto_avoid=False (юзерские поля точно)
    #   mode=2 Selected → выделенный op → False (поля к нему), остальные → True
    if lead_mode == 2:
        this_op_auto_avoid = (op.id != selected_op_id)
    elif lead_mode == 1:
        this_op_auto_avoid = False
    else:
        this_op_auto_avoid = True
    # (Legacy: если auto_avoid_all задан из старого кода — respect его)
    if not auto_avoid_all and lead_mode == 0:
        this_op_auto_avoid = False
    # РУЧНАЯ НАСТРОЙКА ЭЛЕМЕНТА ПОБЕЖДАЕТ АВТО-ПОДБОР.
    # Если у операции есть lead_override (оператор задал заход вручную) —
    # авто-подбор не двигает лид, иначе ручные заходы «возвращались к
    # умолчанию» после пересчёта/загрузки сборки. В эмиттере такая защита
    # (_has_user_override) уже была, во вьювере — не было.
    try:
        if op.attributes.get('lead_override'):
            this_op_auto_avoid = False
    except Exception:
        pass
    
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
            auto_avoid=(this_op_auto_avoid and project is not None),
            safety_factor=1.2,
            exit_request=exit_req,
            overlap=pending_overlap)
        # ── СОХРАНЯЕМ ТОЧКУ ЗАХОДА ДЛЯ ЭМИТТЕРА ──
        # Эмиттер возьмёт её как старт контура (см. mtx_anderson), чтобы
        # .anc давал те же заходы, что видны в превью: логичнее и меньше
        # холостых перемещений станка. Эмиттер проверяет валидность re-root
        # и откатывается на свой авто-подбор, если контур поехал.
        try:
            if polypath_offset and polypath_offset.segments:
                _e = polypath_offset.segments[0].a
                op.attributes.setdefault('_lead_entry', {})[tp.side.name] = [
                    float(_e[0]), float(_e[1])]
        except Exception:
            pass
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
            # Сторона выхода угла = та же, что вход (единый знак лида угла,
            # в сторону смещения эквидистанты угла). forced_lead_side уже
            # вычислен выше векторно.
            forced_exit_side = forced_lead_side
        # ОСНОВНОЙ путь: forced_exit_side=None — auto-подбор, не трогаем.
        
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
    _rapid_pts = []  # [(вход, выход)] по порядку — для перебегов станка
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
        
        # Фильтр «Выбран заказ сшивки» — оп не из активного региона.
        # Для одиночных заказов attribute отсутствует → все показываются.
        if op.attributes.get('stitch_filtered_out', False):
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
                # Селект работает через контур ВНУТРЕННЕГО реза (внутренний 
                # рез = ContourSide.OUTSIDE в терминах кода: toolpath ИНСАЙД 
                # контура ножа). Углы (CORNER_REWORK) тоже селектимые.
                is_selectable = (
                    (op.kind == OperationKind.BLADE_FORMING
                        and tp.side == ContourSide.OUTSIDE)
                    or op.kind == OperationKind.CORNER_REWORK
                )
                item = ToolpathItem(geo['contour'], kind='CONTOUR', op_index=op_idx,
                                    op_id=op.id, selectable=is_selectable)
                # Углы поднимаем в z-порядке НАД контурами blade, чтобы 
                # itemAt()/items(pos) отдавали приоритет углу когда клик 
                # попадает в область где путь blade широкий 3мм захвата.
                # Без этого угол лежит под blade и селектится blade.
                if op.kind == OperationKind.CORNER_REWORK:
                    item.setZValue(8)
                scene.addItem(item)
                items.append(item)
            
            if geo['lead_in'] and geo['lead_in'].segments:
                item = ToolpathItem(geo['lead_in'], kind='LEAD_IN', op_index=op_idx,
                                    collision=geo.get('lead_in_collision', False),
                                    op_id=op.id)
                scene.addItem(item)
                items.append(item)
            
            if geo['lead_out'] and geo['lead_out'].segments:
                item = ToolpathItem(geo['lead_out'], kind='LEAD_OUT', op_index=op_idx,
                                    collision=geo.get('lead_out_collision', False),
                                    op_id=op.id)
                scene.addItem(item)
                items.append(item)

            # Точки входа/выхода прохода — для отрисовки перебегов станка
            # (холостых перемещений между проходами) в порядке обработки.
            try:
                _li = geo.get('lead_in')
                _lo = geo.get('lead_out')
                _c = geo.get('contour')
                _p_in = (_li.segments[0].a if (_li and _li.segments)
                         else (_c.segments[0].a if (_c and _c.segments)
                               else None))
                _p_out = (_lo.segments[-1].b if (_lo and _lo.segments)
                          else (_c.segments[-1].b if (_c and _c.segments)
                                else None))
                if _p_in is not None and _p_out is not None:
                    # запоминаем op_id — перебег привязан к своему ножу и
                    # прячется вместе с ним (иначе висел бы в воздухе)
                    _rapid_pts.append((_p_in, _p_out, op.id))
            except Exception:
                pass
        
        # Прогресс после обработки всех toolpath'ов операции
        processed += 1
        if progress_callback is not None:
            if progress_callback(processed, total_ops) is False:
                # Юзер нажал Cancel — прерываем
                break
    
    # После пересоздания toolpath-items сбрасываем выделение — юзер начинает
    # с чистого листа: подсветка нигде не горит, override у выделенного 
    # op'а уже применён и остался.
    if hasattr(scene, '_selected_op_id'):
        scene._selected_op_id = ""

    # ── ПЕРЕБЕГИ СТАНКА (холостые перемещения) ──
    # Линия от выхода предыдущего прохода ко входу следующего. Рисуются
    # только по галке (show_filter['rapids']), по умолчанию выключены.
    try:
        # Строим ВСЕГДА (чтобы галка работала как переключатель видимости,
        # без пересчёта путей), а показываем по флагу.
        _rap_visible = bool((show_filter or {}).get('rapids'))
        if len(_rapid_pts) > 1:
            from PySide6 import QtGui as _QG, QtCore as _QC
            import math as _m_rap
            _pen = _QG.QPen(_QG.QColor(200, 200, 60))  # жёлто-серый, видно
            _pen.setStyle(_QC.Qt.DashLine)
            _pen.setWidth(0)
            _pen.setCosmetic(True)
            for _i in range(len(_rapid_pts) - 1):
                _a = _rapid_pts[_i][1]      # выход текущего
                _b = _rapid_pts[_i + 1][0]  # вход следующего
                _op_a = _rapid_pts[_i][2]
                _op_b = _rapid_pts[_i + 1][2]
                _ln = scene.addLine(_a[0], _a[1], _b[0], _b[1], _pen)
                _ln.setZValue(12)  # ПОВЕРХ путей (у путей 5-8)
                _ln.setData(0, 'rapid')
                _ln.setData(1, _op_a)
                _ln.setData(2, _op_b)
                _ln.setVisible(_rap_visible)
                items.append(_ln)
                # Стрелка направления (откуда → куда) в середине перебега
                _dx, _dy = _b[0]-_a[0], _b[1]-_a[1]
                _L = _m_rap.hypot(_dx, _dy)
                if _L < 1e-6:
                    continue
                _ux, _uy = _dx/_L, _dy/_L
                _mx, _my = (_a[0]+_b[0])/2.0, (_a[1]+_b[1])/2.0
                _sz = min(1.0, max(0.3, _L*0.03))  # наконечник (в 2р меньше)
                for _sgn in (1, -1):
                    _px = _mx - _ux*_sz + (-_uy)*_sz*0.45*_sgn
                    _py = _my - _uy*_sz + (_ux)*_sz*0.45*_sgn
                    _al = scene.addLine(_px, _py, _mx, _my, _pen)
                    _al.setZValue(12)
                    _al.setData(0, 'rapid')
                    _al.setData(1, _op_a)
                    _al.setData(2, _op_b)
                    _al.setVisible(_rap_visible)
                    items.append(_al)
    except Exception:
        pass

    return items


# ─────────────────────────────────────────────────────────────────────
#  РЕНДЕР ИЗ ПАРСЕННОГО .anc — ПОЛНЫЙ G-CODE VIEWER (v1.5.24+)
# ─────────────────────────────────────────────────────────────────────

def _add_line_to_path(path: QtGui.QPainterPath, start, end):
    """Добавляет линию к QPainterPath — от start к end."""
    if not path.elementCount() or (path.currentPosition().x() != start[0]
                                    or path.currentPosition().y() != start[1]):
        path.moveTo(start[0], start[1])
    path.lineTo(end[0], end[1])


def _sample_body_offset(body_movements, distance: float,
                        samples_per_seg: int = 32):
    """Сэмплирует body-контур из BladeBlock, применяя офсет на `distance`
    ВПРАВО от направления движения (=G42 компенсация).

    Возвращает список (x, y) — точки offset-полилинии, готовые для
    QPainterPath. Для рендера эквидистанты: где реально пойдёт ЦЕНТР
    ФРЕЗЫ, а не raw-контур ножа.

    Логика офсета:
      - LINE: сдвиг перпендикулярно направлению на `distance` вправо
        (right perp of A→B = (dy, -dx)/|AB|).
      - ARC_CW: тот же центр, новый радиус R − distance (потому что при
        CW-движении правая сторона обращена к центру).
      - ARC_CCW: тот же центр, новый радиус R + distance (правая сторона
        обращена от центра).
      - Если новый радиус ≤ 0 — арка вырождена (инструмент больше
        отверстия), пропускаем.

    При `distance <= 0` возвращает raw-контур без офсета.
    """
    import math
    points = []
    if distance <= 1e-9:
        # Без офсета — просто сэмплируем контур как есть
        for m in body_movements:
            if m.kind == 'line':
                points.append(m.start)
                points.append(m.end)
            elif m.kind in ('arc_cw', 'arc_ccw'):
                # Просто линия между start и end для no-offset случая
                points.append(m.start)
                points.append(m.end)
        return points

    for m in body_movements:
        if m.kind == 'line':
            dx = m.end[0] - m.start[0]
            dy = m.end[1] - m.start[1]
            L = math.hypot(dx, dy)
            if L < 1e-9:
                continue
            # RIGHT perpendicular = поворот направления на -90° (CW)
            # (dx,dy)→(dy,-dx). Нормированный.
            nx = dy / L
            ny = -dx / L
            for k in range(samples_per_seg + 1):
                t = k / samples_per_seg
                px = m.start[0] + t * dx + distance * nx
                py = m.start[1] + t * dy + distance * ny
                points.append((px, py))
        elif m.kind in ('arc_cw', 'arc_ccw'):
            r = m.radius
            if r is None or r < 1e-6:
                continue
            ax, ay = m.start
            bx, by = m.end
            chord = math.hypot(bx - ax, by - ay)
            if chord < 1e-9 or r < chord / 2.0 - 1e-6:
                continue
            # Реконструируем центр (см. _add_arc_to_path)
            mx = (ax + bx) / 2.0
            my = (ay + by) / 2.0
            h = math.sqrt(max(0.0, r * r - (chord / 2.0) ** 2))
            perp_x = -(by - ay) / chord
            perp_y = (bx - ax) / chord
            sign = -1.0 if m.kind == 'arc_cw' else 1.0
            cx = mx + sign * h * perp_x
            cy = my + sign * h * perp_y
            # Углы
            a_start = math.atan2(ay - cy, ax - cx)
            a_end = math.atan2(by - cy, bx - cx)
            sweep = a_end - a_start
            if m.kind == 'arc_cw':
                if sweep > 0:
                    sweep -= 2 * math.pi
            else:
                if sweep < 0:
                    sweep += 2 * math.pi
            # Новый радиус после офсета
            if m.kind == 'arc_cw':
                new_r = r - distance  # CW — правая сторона к центру
            else:
                new_r = r + distance
            if new_r < 1e-6:
                continue
            for k in range(samples_per_seg + 1):
                t = k / samples_per_seg
                angle = a_start + sweep * t
                px = cx + new_r * math.cos(angle)
                py = cy + new_r * math.sin(angle)
                points.append((px, py))
    return points


def _draw_lead_arc(path: QtGui.QPainterPath, arc_start, arc_end,
                   tangent_point, tangent_direction, radius: float):
    """Рисует лид-arc с направлением обхода ВЫВЕДЕННЫМ из геометрии.

    G12/G13 в Anderson MTX — макросы, направление вращения (CW/CCW)
    определяется контекстом компенсации, не самим G-кодом. Хардкодить
    `arc_cw` для G12 и `arc_ccw` для G13 неправильно: приводит к
    «выгибанию не в ту сторону» при OUTSIDE-проходе (там body идёт
    в обратном направлении, tangent направлен в противоположную
    сторону, лид должен изогнуться в другую сторону).

    Args:
        arc_start, arc_end: точки начала и конца дуги (как в .anc).
        tangent_point: точка на контуре где дуга касательна (для лид-in
            это = arc_end, для лид-out это = arc_start).
        tangent_direction: (dx, dy) — направление касательной в
            tangent_point (нормированный вектор, из направления body).
        radius: радиус дуги (уже реконструированный).
    """
    import math
    # Другая точка (не касательная)
    if tangent_point == arc_end:
        other_point = arc_start
    else:
        other_point = arc_end
    tx, ty = tangent_direction
    tlen = math.hypot(tx, ty)
    if tlen < 1e-9:
        _add_line_to_path(path, arc_start, arc_end)
        return
    tx, ty = tx / tlen, ty / tlen
    # На какой стороне D-линии (касательная в tangent_point) находится
    # other_point. Left-перпендикуляр к D в Y-up = (-Dy, Dx). Знак
    # (other - tangent) · left_perp говорит куда смотрит other.
    ox = other_point[0] - tangent_point[0]
    oy = other_point[1] - tangent_point[1]
    side = ox * (-ty) + oy * tx  # positive = LEFT, negative = RIGHT
    if abs(side) < 1e-9:
        # Точки коллинеарны с касательной — дуги нет, рисуем линию
        _add_line_to_path(path, arc_start, arc_end)
        return
    # Центр дуги лежит на перпендикуляре к D в tangent_point, на
    # ТОЙ ЖЕ стороне что и other_point (иначе окружность бы не
    # проходила через other).
    if side > 0:
        # LEFT of D
        nx, ny = -ty, tx
    else:
        # RIGHT of D
        nx, ny = ty, -tx
    cx = tangent_point[0] + radius * nx
    cy = tangent_point[1] + radius * ny
    # Углы от центра до start и end дуги
    a_start = math.atan2(arc_start[1] - cy, arc_start[0] - cx)
    a_end = math.atan2(arc_end[1] - cy, arc_end[0] - cx)
    # Sweep нормализуем в (-π, π] — это КОРОТКАЯ дуга (лид всегда
    # короткая, обычно 30-60°).
    sweep = a_end - a_start
    while sweep > math.pi:
        sweep -= 2 * math.pi
    while sweep <= -math.pi:
        sweep += 2 * math.pi
    # Отрисовываем полилинией
    if not path.elementCount() or (path.currentPosition().x() != arc_start[0]
                                    or path.currentPosition().y() != arc_start[1]):
        path.moveTo(arc_start[0], arc_start[1])
    N = 32
    for i in range(1, N + 1):
        t = i / float(N)
        angle = a_start + sweep * t
        if i == N:
            path.lineTo(arc_end[0], arc_end[1])
        else:
            path.lineTo(cx + radius * math.cos(angle),
                        cy + radius * math.sin(angle))


def _add_arc_to_path(path: QtGui.QPainterPath, start, end, radius: float,
                     cw: bool):
    """Добавляет дугу к QPainterPath через полилинейную аппроксимацию
    (32 сегмента). Гарантированно рисует КОРОТКУЮ дугу нужного
    направления вращения.

    Прошлая реализация через arcTo давала почти полные круги из-за
    неправильного выбора стороны центра — итерация `for sign in (+1,-1)`
    всегда брала первый вариант и возвращалась. Для CW-дуг это давало
    центр на противоположной стороне хорды → sweep уходил вдолгую
    (~360°) вместо коротких 44°, viewer рисовал полные окружности.

    Полилинейная аппроксимация избавляет от толкования Qt arcTo с
    учётом Y-flip сцены — точка на дуге считается через центр и
    угол, добавляется через lineTo. Проще и без багов.
    """
    import math
    ax, ay = start
    bx, by = end
    chord = math.hypot(bx - ax, by - ay)
    if chord < 1e-9:
        return
    if radius < chord / 2.0 - 1e-6:
        # Радиус слишком мал для этой хорды — падаем на линию
        _add_line_to_path(path, start, end)
        return
    # Центр дуги: на перпендикуляре к хорде через её середину, на
    # расстоянии h от неё.
    mx = (ax + bx) / 2.0
    my = (ay + by) / 2.0
    h = math.sqrt(max(0.0, radius * radius - (chord / 2.0) * (chord / 2.0)))
    # Единичная перпендикулярная к хорде (вращение хорды на +90° по
    # математическому направлению = ВЛЕВО от A→B):
    perp_x = -(by - ay) / chord
    perp_y = (bx - ax) / chord
    # Для CW-дуги (G2) центр СПРАВА от направления движения A→B,
    # т.е. −perp. Для CCW (G3) — слева, +perp.
    sign = -1.0 if cw else 1.0
    cx = mx + sign * h * perp_x
    cy = my + sign * h * perp_y
    # Углы от центра до концов
    a1 = math.atan2(ay - cy, ax - cx)
    a2 = math.atan2(by - cy, bx - cx)
    # Sweep нормализуем: для CW должен быть отрицательным, для CCW
    # положительным. Разница углов в диапазоне (-π, π]:
    sweep = a2 - a1
    while sweep > math.pi:
        sweep -= 2.0 * math.pi
    while sweep <= -math.pi:
        sweep += 2.0 * math.pi
    # Дополнительная страховка: если знак sweep'а не соответствует
    # направлению обхода — прибавляем/вычитаем 2π. На корректно
    # выбранном центре так не должно случаться, но защищаемся от
    # численных углов near-π.
    if cw and sweep > 0:
        sweep -= 2.0 * math.pi
    elif (not cw) and sweep < 0:
        sweep += 2.0 * math.pi
    # Рисуем полилинией — 32 сегмента, для мелких лид-дуг ~1мм это
    # ~0.5° на сегмент, визуально гладко.
    if not path.elementCount() or (path.currentPosition().x() != ax
                                    or path.currentPosition().y() != ay):
        path.moveTo(ax, ay)
    N = 32
    for i in range(1, N + 1):
        t = i / float(N)
        angle = a1 + sweep * t
        if i == N:
            # Последняя точка — точно на end (избегаем накопленной ошибки)
            path.lineTo(bx, by)
        else:
            px = cx + radius * math.cos(angle)
            py = cy + radius * math.sin(angle)
            path.lineTo(px, py)


def add_anc_blades_to_scene(scene: 'CamScene', blades, cutting_params=None,
                            extras: dict = None, show_filter: dict = None):
    """Рисует BladeBlock'и (из session.compute_anc_blades) прямо в сцене.

    Полностью заменяет `add_toolpaths_to_scene` — рендер ТОЛЬКО из
    распарсенных .anc-движений, никакой независимой геометрии в viewer'е.
    Гарантирует визуальное совпадение с реальной G-code программой.

    Body-контур рисуется через ЭКВИДИСТАНТУ (offset на tool_equidistant
    вправо от направления обхода), а не raw-контур из .anc. Это
    показывает где реально пойдёт ЦЕНТР ФРЕЗЫ после G41/G42 компенсации
    в станке.

    Цветовая схема:
      - body (эквидистанта): зелёный
      - lead_in: пурпурный
      - lead_out: розовый

    Args:
        scene: CamScene.
        blades: список BladeBlock из session.compute_anc_blades().
        cutting_params: legacy — не используется, оставлен для совместимости.
        extras: dict с 'tool_equidistant' — величина офсета для эквидистанты.

    Returns:
        Список QGraphicsPathItem — созданные items.
    """
    from ..geometry.anc_movement_parser import (
        reconstruct_lead_arc_radius, Movement)

    # Пены для каждой роли
    # (per-knife pen'ы создаются ниже в цикле — цвет из PALETTE по op_index)

    items = []
    # Радиус эквидистанты — из extras (передаётся main_window'ом,
    # уже включает tip + tan(angle) для реального центра фрезы).
    tool_radius = 0.0
    if extras is not None:
        v = extras.get('tool_equidistant', 0)
        if v and v > 0:
            tool_radius = v / 2.0

    # Палитра цветов для per-knife раскраски — совпадает с ToolpathItem'ной
    # палитрой из старого рендера, чтобы соседние ножи было легко различать.
    PALETTE = [
        '#ff6666', '#66ff66', '#6699ff', '#ffcc33', '#ff66cc', '#66ffff',
        '#cc99ff', '#ffff66', '#ff9966', '#99ff99', '#9999ff', '#ffaa00',
        '#ff3399', '#33ffcc', '#9966ff', '#ccff66',
    ]
    # Индексируем PROGRAM'ы (не op'ы) для назначения цветов.
    # Программа = группа блоков из одного файла: `_all_R.anc` → 'rough',
    # каждый `_N_M.anc` → уникальный ключ ('finish_1', 'finish_2', ...),
    # `_corner.anc` → 'corner_2d'.
    # Все блоки одной программы получают одинаковый цвет.
    # Стабильная сортировка ключей — цвета не «прыгают» между запусками.
    program_keys = sorted({getattr(b, 'program', getattr(b, 'kind', 'rough'))
                           for b in blades})
    program_color_index = {k: i for i, k in enumerate(program_keys)}

    # Фильтр по show_filter (по чекбоксам «Черновая»/«Чистовая»/
    # «2D углы»/«3D углы» из UI). Если None — показываем всё.
    if show_filter is not None:
        blades = [b for b in blades
                  if show_filter.get(getattr(b, 'kind', 'rough'), True)]

    def _body_length_and_arrows(body_mvs, fractions=(0.25, 0.5, 0.75)):
        """Возвращает список (point, tangent) для стрелок направления
        в fractions долях длины body-пути.

        body_mvs: список Movement с role='body'.
        fractions: доли длины пути где ставить стрелки (0..1).
        """
        import math as _mfn
        # Кэшируем длину каждого сегмента + суммарную длину
        seg_lens = []
        total = 0.0
        for m in body_mvs:
            if m.kind == 'line':
                L = _mfn.hypot(m.end[0] - m.start[0], m.end[1] - m.start[1])
            elif m.kind in ('arc_cw', 'arc_ccw'):
                r = m.radius or 0
                if r < 1e-6:
                    L = _mfn.hypot(m.end[0] - m.start[0],
                                   m.end[1] - m.start[1])
                else:
                    # Длина дуги = R * sweep angle
                    chord = _mfn.hypot(m.end[0] - m.start[0],
                                       m.end[1] - m.start[1])
                    if chord >= 2 * r:
                        sweep = _mfn.pi
                    else:
                        sweep = 2 * _mfn.asin(chord / (2 * r))
                    L = r * sweep
            else:
                L = 0.0
            seg_lens.append(L)
            total += L
        if total < 1e-6:
            return []
        # Для каждой доли найти точку и касательную
        result = []
        for f in fractions:
            target = total * f
            acc = 0.0
            for i, (m, L) in enumerate(zip(body_mvs, seg_lens)):
                if acc + L >= target - 1e-9:
                    local_t = ((target - acc) / L) if L > 1e-9 else 0.0
                    if m.kind == 'line':
                        px = m.start[0] + local_t * (m.end[0] - m.start[0])
                        py = m.start[1] + local_t * (m.end[1] - m.start[1])
                        dx = m.end[0] - m.start[0]
                        dy = m.end[1] - m.start[1]
                        tL = _mfn.hypot(dx, dy)
                        if tL > 1e-9:
                            result.append(((px, py), (dx / tL, dy / tL)))
                    elif m.kind in ('arc_cw', 'arc_ccw') and (m.radius or 0) > 1e-6:
                        # На дуге: точка в arc.start + local_t * sweep
                        r = m.radius
                        ax, ay = m.start
                        bx, by = m.end
                        chord = _mfn.hypot(bx - ax, by - ay)
                        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
                        h = _mfn.sqrt(max(0.0, r * r - (chord / 2.0) ** 2))
                        perp_x = -(by - ay) / chord if chord > 1e-9 else 0
                        perp_y = (bx - ax) / chord if chord > 1e-9 else 0
                        sign = -1.0 if m.kind == 'arc_cw' else 1.0
                        cx = mx + sign * h * perp_x
                        cy = my + sign * h * perp_y
                        a_start = _mfn.atan2(ay - cy, ax - cx)
                        a_end = _mfn.atan2(by - cy, bx - cx)
                        sweep = a_end - a_start
                        if m.kind == 'arc_cw' and sweep > 0:
                            sweep -= 2 * _mfn.pi
                        elif m.kind == 'arc_ccw' and sweep < 0:
                            sweep += 2 * _mfn.pi
                        angle = a_start + sweep * local_t
                        px = cx + r * _mfn.cos(angle)
                        py = cy + r * _mfn.sin(angle)
                        # Касательная: перпендикуляр к радиусу, в направлении
                        # движения по дуге. Для CW: rotate radius CW 90°.
                        # Для CCW: rotate CCW 90°.
                        rx = px - cx
                        ry = py - cy
                        if m.kind == 'arc_cw':
                            tx, ty = ry, -rx  # CW rotation
                        else:
                            tx, ty = -ry, rx  # CCW rotation
                        tL = _mfn.hypot(tx, ty)
                        if tL > 1e-9:
                            result.append(((px, py), (tx / tL, ty / tL)))
                    else:
                        # Дегенеративный случай — используем chord direction
                        dx = m.end[0] - m.start[0]
                        dy = m.end[1] - m.start[1]
                        tL = _mfn.hypot(dx, dy)
                        if tL > 1e-9:
                            px = m.start[0] + local_t * dx
                            py = m.start[1] + local_t * dy
                            result.append(((px, py), (dx / tL, dy / tL)))
                    break
                acc += L
        return result

    for b in blades:
        # Разделяем движения по ролям — по одному пути на роль
        paths_by_role = {
            'body': QtGui.QPainterPath(),
            'lead_in': QtGui.QPainterPath(),
            'lead_out': QtGui.QPainterPath(),
        }
        # Собираем body-движения в отдельный список — рисуем их через
        # ОФСЕТ (эквидистанту) вместо raw-контура. Это и есть Вариант B:
        # viewer показывает где реально пойдёт ЦЕНТР ФРЕЗЫ после
        # применения G41/G42 компенсации в станке.
        body_movements = [m for m in b.movements if m.role == 'body']
        # Первый и последний body — для реконструкции лид-дуг и bridge'ей
        body_first = body_movements[0] if body_movements else None
        body_last = body_movements[-1] if body_movements else None

        # Строим offset body path (эквидистанта)
        if body_movements:
            offset_pts = _sample_body_offset(body_movements, tool_radius)
            if offset_pts:
                body_path = paths_by_role['body']
                body_path.moveTo(offset_pts[0][0], offset_pts[0][1])
                for px, py in offset_pts[1:]:
                    body_path.lineTo(px, py)

        # ── ЛИД-IN: параллельный сдвиг всего лида на tool_radius ──
        # Проще некуда: все raw-точки лида (approach → ramp_end → arc_end)
        # сдвигаются на tool_radius * right_perp(body_direction). Линии
        # остаются параллельны raw, arc — того же радиуса.
        lead_in_arc = next((m for m in b.movements if m.role == 'lead_in'), None)
        approach_mv = next((m for m in b.movements if m.role == 'approach'), None)
        if (lead_in_arc is not None and body_first is not None
                and approach_mv is not None):
            # Ramp_end из raw последней line-сегмента перед arc'ом
            ramp_end = None
            for m in b.movements:
                if m is approach_mv or m.role == 'plunge':
                    continue
                if m is lead_in_arc:
                    break
                if m.role == 'other' and m.kind == 'line':
                    ramp_end = m.end
            if ramp_end is None:
                ramp_end = lead_in_arc.start
            # Body direction
            import math as _m_li
            body_dx = body_first.end[0] - body_first.start[0]
            body_dy = body_first.end[1] - body_first.start[1]
            bL = _m_li.hypot(body_dx, body_dy)
            body_dir = ((body_dx / bL, body_dy / bL) if bL > 1e-9 else (1.0, 0.0))
            # Shift vector = tool_radius * right_perp(body_dir) = t*(Dy, -Dx)
            shift_x = tool_radius * body_dir[1]
            shift_y = -tool_radius * body_dir[0]
            # Смещённые точки
            off_approach = (approach_mv.end[0] + shift_x,
                            approach_mv.end[1] + shift_y)
            off_ramp_end = (ramp_end[0] + shift_x, ramp_end[1] + shift_y)
            off_tangent = (lead_in_arc.end[0] + shift_x,
                           lead_in_arc.end[1] + shift_y)
            # Arc R из raw arc (или reconstruct)
            r = lead_in_arc.radius or reconstruct_lead_arc_radius(
                lead_in_arc, body_first)
            # Отрисовка: line + arc
            path = paths_by_role['lead_in']
            path.moveTo(off_approach[0], off_approach[1])
            path.lineTo(off_ramp_end[0], off_ramp_end[1])
            if r is not None and r >= 1e-6:
                _draw_lead_arc(path, off_ramp_end, off_tangent,
                               tangent_point=off_tangent,
                               tangent_direction=body_dir,
                               radius=r)
            else:
                path.lineTo(off_tangent[0], off_tangent[1])

        # ── ЛИД-OUT: параллельный сдвиг всего лида на tool_radius ──
        # Симметрично лид-in. Arc.start (первая точка lead_out) → offset,
        # затем линии lead_out тоже сдвигаются.
        lead_out_mvs = [m for m in b.movements if m.role == 'lead_out']
        if lead_out_mvs and body_last is not None:
            import math as _m_lo
            body_dx = body_last.end[0] - body_last.start[0]
            body_dy = body_last.end[1] - body_last.start[1]
            bL = _m_lo.hypot(body_dx, body_dy)
            body_dir = ((body_dx / bL, body_dy / bL) if bL > 1e-9 else (1.0, 0.0))
            shift_x = tool_radius * body_dir[1]
            shift_y = -tool_radius * body_dir[0]
            first_out = lead_out_mvs[0]
            off_tangent = (first_out.start[0] + shift_x,
                           first_out.start[1] + shift_y)
            path = paths_by_role['lead_out']
            path.moveTo(off_tangent[0], off_tangent[1])
            # Arc первый (если есть) — смещённый
            first_arc = None
            for m in lead_out_mvs:
                if m.kind in ('arc_cw', 'arc_ccw'):
                    first_arc = m
                    break
            if first_arc is not None:
                r = first_arc.radius
                if r is None:
                    fake_next = Movement(role='body', kind='line',
                                         start=body_last.end,
                                         end=body_last.start)
                    r = reconstruct_lead_arc_radius(first_arc, fake_next)
                if r is not None and r >= 1e-6:
                    off_arc_end = (first_arc.end[0] + shift_x,
                                   first_arc.end[1] + shift_y)
                    _draw_lead_arc(path, off_tangent, off_arc_end,
                                   tangent_point=off_tangent,
                                   tangent_direction=body_dir,
                                   radius=r)
                    prev_end = off_arc_end
                else:
                    prev_end = off_tangent
            else:
                prev_end = off_tangent
            # Оставшиеся lead_out сегменты (после первого arc'а) — линии
            saw_first_arc = False
            for m in lead_out_mvs:
                if m.kind in ('arc_cw', 'arc_ccw') and not saw_first_arc:
                    saw_first_arc = True
                    continue
                if m.kind == 'line':
                    off_end = (m.end[0] + shift_x, m.end[1] + shift_y)
                    path.lineTo(off_end[0], off_end[1])

        # Создаём items с per-PROGRAM цветом (не per-knife!).
        # Все блоки одной программы (rough/finish_N/corner_2d) → один цвет.
        prog_key = getattr(b, 'program', getattr(b, 'kind', 'rough'))
        knife_color_str = PALETTE[
            program_color_index.get(prog_key, 0) % len(PALETTE)]
        knife_color = QtGui.QColor(knife_color_str)
        pen_body = QtGui.QPen(knife_color, 0)
        pen_body.setCosmetic(True)
        pen_body.setWidthF(1.5)
        lead_color = QtGui.QColor(knife_color)
        lead_color.setAlphaF(0.7)
        pen_lead = QtGui.QPen(lead_color, 0)
        pen_lead.setCosmetic(True)
        pen_lead.setWidthF(1.2)

        for role, path in paths_by_role.items():
            if path.isEmpty():
                continue
            item = QtWidgets.QGraphicsPathItem(path)
            if role == 'body':
                item.setPen(pen_body)
            else:
                item.setPen(pen_lead)
            item.setZValue(5.0)
            if getattr(b, 'op_id', None):
                item.setData(0, b.op_id)
            scene.addItem(item)
            items.append(item)

        # ── СТРЕЛКИ НАПРАВЛЕНИЯ ХОДА ──
        # На body ножа 3 маленькие стрелки: 25%, 50%, 75% длины пути.
        # Показывают направление движения фрезы. Копия старого рендера.
        body_mvs = [m for m in b.movements if m.role == 'body']
        arrows = _body_length_and_arrows(body_mvs, fractions=(0.25, 0.5, 0.75))
        import math as _m_arr
        # Размер стрелки ~0.8мм в мировых координатах (визуально ~8px
        # при типовом зуме, косметически)
        arrow_size = 0.8
        for (pt, tan) in arrows:
            tx, ty = tan
            # Треугольник: tip впереди, крылья сзади
            tip = (pt[0] + tx * arrow_size * 0.5,
                   pt[1] + ty * arrow_size * 0.5)
            back_x = pt[0] - tx * arrow_size * 0.5
            back_y = pt[1] - ty * arrow_size * 0.5
            # Перпендикуляр для крыльев
            nx, ny = -ty, tx
            left = (back_x + nx * arrow_size * 0.35,
                    back_y + ny * arrow_size * 0.35)
            right = (back_x - nx * arrow_size * 0.35,
                     back_y - ny * arrow_size * 0.35)
            arrow_path = QtGui.QPainterPath()
            arrow_path.moveTo(tip[0], tip[1])
            arrow_path.lineTo(left[0], left[1])
            arrow_path.lineTo(right[0], right[1])
            arrow_path.closeSubpath()
            arrow_item = QtWidgets.QGraphicsPathItem(arrow_path)
            arrow_pen = QtGui.QPen(knife_color, 0)
            arrow_pen.setCosmetic(True)
            arrow_item.setPen(arrow_pen)
            arrow_item.setBrush(QtGui.QBrush(knife_color))
            arrow_item.setZValue(6.0)
            if getattr(b, 'op_id', None):
                arrow_item.setData(0, b.op_id)
            scene.addItem(arrow_item)
            items.append(arrow_item)
    return items
