"""
ui/main_window.py — главное окно camsys.

Использует CamSession как единственный источник состояния. Никакой 
бизнес-логики в виджетах — только отрисовка и проброс действий в сессию.

Структура:
    QMainWindow
    └── QSplitter (горизонтальный)
        ├── Левая колонка (QSplitter вертикальный)
        │   ├── LayerTree         (дерево слоёв с галками)
        │   └── OperationsPanel   (список операций)
        ├── Viewer2D              (2D-вьюер геометрии в центре)
        └── ParamsPanel           (параметры Cutting справа)
    
    Menu: File (Open, Export, Quit) | View (Fit) | Help (About)
    StatusBar: координаты курсора, имя файла
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from camsys.core.session import CamSession
from camsys.ui.viewer_2d import CamScene, CamView


# ─────────────────────────────────────────────────────────────────────────
#  ДЕРЕВО СЛОЁВ
# ─────────────────────────────────────────────────────────────────────────

class LayerTree(QtWidgets.QTreeWidget):
    """Дерево слоёв проекта. Галки управляют видимостью."""
    
    layerVisibilityChanged = QtCore.Signal(str, bool)  # layer_name, visible
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Слой", "Контуров"])
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.itemChanged.connect(self._on_item_changed)
        self._block_signals = False
    
    def load_from_session(self, sess: CamSession):
        """Перезаполнение из состояния сессии."""
        self._block_signals = True
        self.clear()
        
        if not sess.has_project():
            self._block_signals = False
            return
        
        state = sess.get_state()
        for layer in state['project']['layers']:
            item = QtWidgets.QTreeWidgetItem()
            item.setText(0, layer['name'])
            item.setText(1, f"{layer['closed_count']}з/{layer['open_count']}о")
            item.setData(0, QtCore.Qt.UserRole, layer['name'])
            
            # Цвет в виде иконки
            pix = QtGui.QPixmap(14, 14)
            pix.fill(QtGui.QColor(layer['color'] or '#00ff00'))
            item.setIcon(0, QtGui.QIcon(pix))
            
            # Галка видимости
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.Checked if layer['visible'] 
                               else QtCore.Qt.Unchecked)
            
            self.addTopLevelItem(item)
        
        self.resizeColumnToContents(0)
        self.resizeColumnToContents(1)
        self._block_signals = False
    
    def _on_item_changed(self, item: QtWidgets.QTreeWidgetItem, col: int):
        if self._block_signals or col != 0:
            return
        layer_name = item.data(0, QtCore.Qt.UserRole)
        visible = item.checkState(0) == QtCore.Qt.Checked
        self.layerVisibilityChanged.emit(layer_name, visible)


# ─────────────────────────────────────────────────────────────────────────
#  ПАНЕЛЬ ОПЕРАЦИЙ
# ─────────────────────────────────────────────────────────────────────────

class OperationsPanel(QtWidgets.QWidget):
    """Панель списка операций (внизу под слоями).
    Аналог Operations panel из Альфакама."""
    
    operationSelected = QtCore.Signal(str)  # op_id
    operationToggled = QtCore.Signal()  # любая галочка ✓ изменена
    
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        
        # Заголовок с кнопками
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("<b>Operations</b>")
        header.addWidget(title)
        header.addStretch()
        
        self.btn_create = QtWidgets.QPushButton("Создать")
        self.btn_create.setToolTip("Создать blade-операции для всех "
                                    "замкнутых контуров слоя Knife")
        header.addWidget(self.btn_create)
        
        self.btn_sort = QtWidgets.QPushButton("Сортировать")
        self.btn_sort.setToolTip("Отсортировать операции слева-направо")
        header.addWidget(self.btn_sort)
        
        self.btn_clear = QtWidgets.QPushButton("Очистить")
        header.addWidget(self.btn_clear)
        
        layout.addLayout(header)
        
        # Таблица операций
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(5)
        # Колонка 0 — заголовок-чекбокс (☑/☐): клик переключает все галки разом
        self.table.setHorizontalHeaderLabels(["☑", "#", "Имя", "Инструмент", "Путей"])
        self.table.horizontalHeaderItem(0).setToolTip(
            "Вкл/выкл все ножи разом")
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self.table.itemSelectionChanged.connect(self._on_selection)
        # Сигнал изменения чекбокса
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)
    
    def _on_header_clicked(self, section: int):
        """Клик по заголовку колонки 0 (☑/☐) — переключает галки всех ножей."""
        if section != 0:
            return
        # Если хоть одна снята → ставим все; иначе снимаем все
        any_unchecked = False
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it is not None and it.checkState() != QtCore.Qt.Checked:
                any_unchecked = True
                break
        self._set_all_checked(any_unchecked)
    
    def _update_header_checkbox(self):
        """Обновляет глиф ☑/☐ в заголовке по текущему состоянию галок."""
        n = self.table.rowCount()
        checked = sum(1 for r in range(n)
                      if (it := self.table.item(r, 0)) is not None
                      and it.checkState() == QtCore.Qt.Checked)
        hdr = self.table.horizontalHeaderItem(0)
        if hdr is not None:
            hdr.setText("☑" if checked == n and n > 0 else
                        ("☐" if checked == 0 else "◪"))
    
    def load_from_session(self, sess: CamSession):
        self.table.blockSignals(True)  # чтобы itemChanged не сработал при загрузке
        self.table.setRowCount(0)
        if not sess.has_project():
            self.table.blockSignals(False)
            return
        self._session = sess
        state = sess.get_state()
        ops = state['project']['operations']
        # Фильтр «Выбран заказ сшивки» — отсеиваем ops не из активного 
        # региона. Для одиночных заказов attribute отсутствует → все 
        # проходят.
        ops = [op for op in ops 
               if not op.get('attributes', {}).get('stitch_filtered_out', False)]
        self.table.setRowCount(len(ops))
        for row, op in enumerate(ops):
            # Колонка 0: чекбокс «включена ли операция в экспорт»
            check_item = QtWidgets.QTableWidgetItem()
            check_item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            # Читаем текущее состояние из attributes (по умолчанию включено)
            excluded = op.get('attributes', {}).get('excluded', False)
            check_item.setCheckState(
                QtCore.Qt.Unchecked if excluded else QtCore.Qt.Checked)
            check_item.setData(QtCore.Qt.UserRole, op['id'])
            self.table.setItem(row, 0, check_item)
            
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(
                str(op['sequence_number'] or row+1)))
            name_item = QtWidgets.QTableWidgetItem(op['name'])
            name_item.setData(QtCore.Qt.UserRole, op['id'])
            self.table.setItem(row, 2, name_item)
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(
                f"T{op['tool_number']}"))
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(
                str(op['toolpaths_count'])))
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)
        self._update_header_checkbox()
    
    def _set_all_checked(self, checked: bool):
        """Ставит/снимает галки на ВСЕХ ножах разом.
        
        Снятая галка → op.attributes['excluded']=True → нож исключается из
        экспорта. Сигналы таблицы блокируются, чтобы не дёргать перерисовку
        на каждой строке — превью обновляется один раз в конце.
        """
        state = QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked
        self.table.blockSignals(True)
        # Собираем id операций из строк таблицы и выставляем галку + модель
        row_op_ids = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(state)
                oid = item.data(QtCore.Qt.UserRole)
                if oid:
                    row_op_ids.append(oid)
        self.table.blockSignals(False)
        # Обновляем модель сессии ровно по строкам таблицы (раз сигналы заглушены)
        if hasattr(self, '_session') and self._session is not None and row_op_ids:
            id_set = set(row_op_ids)
            for op in self._session.project.operations:
                if op.id in id_set:
                    op.attributes['excluded'] = (not checked)
        self._update_header_checkbox()
        self.operationToggled.emit()
    
    def _on_item_changed(self, item):
        """Обработчик изменения чекбокса в колонке 0."""
        if item.column() != 0:
            return
        if not hasattr(self, '_session') or self._session is None:
            return
        op_id = item.data(QtCore.Qt.UserRole)
        if not op_id:
            return
        # Находим операцию в сессии и обновляем attribute
        excluded = (item.checkState() == QtCore.Qt.Unchecked)
        for op in self._session.project.operations:
            if op.id == op_id:
                op.attributes['excluded'] = excluded
                break
        self._update_header_checkbox()
        # Уведомляем MainWindow для перерисовки превью
        self.operationToggled.emit()
    
    def _on_selection(self):
        items = self.table.selectedItems()
        if not items:
            return
        # Берём ItemData из колонки "Имя" (теперь 2 после добавления чекбокса)
        row = items[0].row()
        name_item = self.table.item(row, 2)
        if name_item is not None:
            op_id = name_item.data(QtCore.Qt.UserRole)
            if op_id:
                self.operationSelected.emit(op_id)


# ─────────────────────────────────────────────────────────────────────────
#  ПАРАМЕТРЫ CUTTING — повторяет диалог Cutting v-5.1 из Альфакама
# ─────────────────────────────────────────────────────────────────────────

class _AutoAvoidCompat:
    """Совместимость со старым интерфейсом `auto_avoid_all` (QCheckBox).
    
    Раньше был чекбокс с методами `isChecked()` и сигналом `toggled`. 
    Теперь на его месте 3 радио-кнопки (rb_lead_auto/all/selected). 
    Этот shim транслирует старые вызовы: `isChecked()` возвращает True 
    когда выбран режим «Авто-подбор», `toggled` эмитируется когда радио 
    переключается между «Авто» и другими режимами.
    """
    
    def __init__(self, panel):
        self._panel = panel
        # Прокидываем через buttonGroup.idToggled
        self._panel._lead_mode_bg.idToggled.connect(self._on_id_toggled)
        # ВАЖНО: сохраняем _SignalHost как атрибут — иначе Python GC 
        # уничтожит объект и Qt-сигнал упадёт с "Signal source has been 
        # deleted" при попытке эмитить.
        self._signal_host = _SignalHost()
        self.toggled = self._signal_host.sig
    
    def _on_id_toggled(self, mode_id: int, checked: bool):
        # Эмитим когда режим-Авто получает изменение (или теряет)
        if mode_id == 0 and checked:
            self.toggled.emit(True)
        elif mode_id == 0 and not checked:
            self.toggled.emit(False)
    
    def isChecked(self) -> bool:
        return self._panel.rb_lead_auto.isChecked()
    
    def setChecked(self, value: bool):
        # Ставим Авто при True, иначе оставляем текущий не-Авто режим 
        # (по умолчанию «Все элементы» если нужно снять с Авто).
        if value:
            self._panel.rb_lead_auto.setChecked(True)
        elif self._panel.rb_lead_auto.isChecked():
            self._panel.rb_lead_all.setChecked(True)
    
    def blockSignals(self, block: bool) -> bool:
        """Проксирование blockSignals — блокирует все радио-кнопки группы.
        
        Используется в set_defaults() чтобы не эмитить paramsChanged пока 
        сбрасываются значения.
        """
        prev = False
        for rb in (self._panel.rb_lead_auto, 
                   self._panel.rb_lead_all,
                   self._panel.rb_lead_selected):
            prev = rb.blockSignals(block) or prev
        return prev


class _SignalHost(QtCore.QObject):
    """Пустой QObject-контейнер только для эмиссии сигнала."""
    sig = QtCore.Signal(bool)



class CuttingParamsPanel(QtWidgets.QWidget):
    """Параметры макроса Cutting (правая панель).
    
    Соответствует диалогу Cutting v-5.1 со скриншота пользователя:
        Параметры ножа: Угол / Пятка / Верх / Низ
        Направление: → / ↑↑, реверс
        Точка входа/выхода: Угол, Длина, Смещение по
        Опции пакета: что генерировать
    """
    
    paramsChanged = QtCore.Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        
        # ── СШИВКА (только для многозаказных макетов) ──
        # Скрыто по умолчанию, показывается только если файл — сшивка.
        # Позволяет переключаться между «Вся сшивка» (default) и 
        # конкретными заказами. При переключении:
        #   - сцена фильтруется по региону заказа  
        #   - подгружается specification_<номер>.xml
        #   - экспорт идёт по этому заказу
        self.stitch_group = QtWidgets.QGroupBox("Заказ")
        stitch_layout = QtWidgets.QFormLayout(self.stitch_group)
        self.stitch_combo = QtWidgets.QComboBox()
        self.stitch_combo.setEnabled(False)  # disabled пока файл не загружен
        stitch_layout.addRow("№:", self.stitch_combo)
        # Всегда видима — юзер видит место где будет заказ
        layout.addWidget(self.stitch_group)
        
        # ── ПАРАМЕТРЫ НОЖА ──
        knife_group = QtWidgets.QGroupBox("Параметры ножа")
        knife_form = QtWidgets.QFormLayout(knife_group)
        
        self.angle = QtWidgets.QComboBox()
        self.angle.addItems(["60", "70", "80", "90"])
        self.angle.setCurrentText("70")
        knife_form.addRow("Угол:", self.angle)
        
        self.tip = QtWidgets.QComboBox()
        self.tip.addItems(["0_6", "0_8", "1_0", "1_2", "1_5"])
        self.tip.setCurrentText("0_8")
        knife_form.addRow("Пятка:", self.tip)
        
        self.top = QtWidgets.QDoubleSpinBox()
        self.top.setRange(0.0, 5.0); self.top.setDecimals(3)
        self.top.setSingleStep(0.001); self.top.setValue(0.440)
        self.top.setToolTip("Высота ножа над поверхностью материала")
        knife_form.addRow("Высота:", self.top)
        
        self.bottom = QtWidgets.QDoubleSpinBox()
        self.bottom.setRange(0.0, 5.0); self.bottom.setDecimals(3)
        self.bottom.setSingleStep(0.01); self.bottom.setValue(0.250)
        self.bottom.setToolTip("Глубина реза, абсолютная (ABS — absolute)")
        knife_form.addRow("ABS:", self.bottom)
        
        layout.addWidget(knife_group)
        
        # ── НАПРАВЛЕНИЕ ──
        dir_group = QtWidgets.QGroupBox("Направление")
        dir_layout = QtWidgets.QVBoxLayout(dir_group)
        
        self.dir_horiz = QtWidgets.QRadioButton("→ Горизонтально")
        self.dir_vert = QtWidgets.QRadioButton("↑↑ Вертикально")
        self.dir_horiz.setChecked(True)
        dir_layout.addWidget(self.dir_horiz)
        dir_layout.addWidget(self.dir_vert)
        
        self.use_reverse = QtWidgets.QCheckBox("Включить реверс")
        self.use_reverse.setChecked(True)
        dir_layout.addWidget(self.use_reverse)
        
        layout.addWidget(dir_group)
        
        # ── ГРУППИРОВКА В ПРОГРАММЫ ──
        group_group = QtWidgets.QGroupBox("Группировка чистовых программ")
        group_form = QtWidgets.QFormLayout(group_group)
        
        # Лимит длины геометрии (мм). Внутри ×2 = длина путей.
        # Группировка всегда по длине: длинная строка/столбец делится,
        # короткие объединяются. Направление задаётся выше (гориз./верт.).
        self.max_geom_len = QtWidgets.QDoubleSpinBox()
        self.max_geom_len.setRange(100, 100000)
        self.max_geom_len.setDecimals(0)
        self.max_geom_len.setSingleStep(500)
        self.max_geom_len.setValue(3000)
        self.max_geom_len.setSuffix(" мм геом.")
        self.max_geom_len.setToolTip(
            "Лимит длины по геометрии на одну программу.\n"
            "По путям удваивается (3000 геом. = 6000 путей,\n"
            "т.к. два прохода: внутренний + внешний).\n"
            "Длинная строка делится, короткие объединяются.")
        group_form.addRow("Лимит длины:", self.max_geom_len)
        
        # 2-я реперная точка X (PT_PT_DIS из .amp поста)
        # Пишется в .anc как N29 SSDE[SD.USR.Allign.DistC2C = ...]
        self.fiducial_x = QtWidgets.QDoubleSpinBox()
        self.fiducial_x.setRange(0, 5000)
        self.fiducial_x.setDecimals(1)
        self.fiducial_x.setSingleStep(10)
        self.fiducial_x.setValue(700.0)
        self.fiducial_x.setSuffix(" мм")
        self.fiducial_x.setToolTip(
            "X-координата второй реперной точки (мм).\n"
            "Расстояние между двумя репер-метками для сведения координат.\n"
            "В .anc записывается как SD.USR.Allign.DistC2C."
        )
        group_form.addRow("2-я реперная X:", self.fiducial_x)
        
        layout.addWidget(group_group)
        
        # ── ВХОД/ВЫХОД ──
        # Сохраняем в атрибуте — main_window будет менять enabled состояние 
        # в зависимости от режима (Авто → disabled, Все/Выделенные → enabled).
        self._lead_group = QtWidgets.QGroupBox("Точка входа/выхода")
        lead_group = self._lead_group
        lead_grid = QtWidgets.QGridLayout(lead_group)
        
        # Колонки заходов привязаны к ФИЗИЧЕСКОМУ резу (см. routing в
        # package_export/viewer): «Внутренний» управляет внутренним резом
        # (его даёт проход OUTSIDE после фикса компенсации), «Внешний» —
        # внешним (проход INSIDE). Надписи стоят в естественном порядке.
        lead_grid.addWidget(QtWidgets.QLabel("<b>Внутренний</b>"), 0, 0, 1, 2,
                            alignment=QtCore.Qt.AlignCenter)
        lead_grid.addWidget(QtWidgets.QLabel("<b>Внешний</b>"), 0, 2, 1, 2,
                            alignment=QtCore.Qt.AlignCenter)
        
        # Угол захода/выхода (автоподбирается, но юзер может перебить вручную)
        lead_grid.addWidget(QtWidgets.QLabel("Угол:"), 1, 0)
        self.lead_in_angle = QtWidgets.QDoubleSpinBox()
        self.lead_in_angle.setRange(0, 90); self.lead_in_angle.setValue(45)
        self.lead_in_angle.setToolTip(
            "Подбирается автоматически по размеру самого маленького ножа. "
            "Если изменить вручную — автоподбор больше не будет затирать."
        )
        # Флаг: True если юзер вручную изменил угол. setValue ниже идёт ДО
        # connect, поэтому начальное значение 45 не взводит флаг.
        self._lead_in_angle_user_set = False
        self.lead_in_angle.valueChanged.connect(
            lambda v: setattr(self, '_lead_in_angle_user_set', True))
        lead_grid.addWidget(self.lead_in_angle, 1, 1)
        
        lead_grid.addWidget(QtWidgets.QLabel("Угол:"), 1, 2)
        self.lead_out_angle = QtWidgets.QDoubleSpinBox()
        self.lead_out_angle.setRange(0, 90); self.lead_out_angle.setValue(45)
        lead_grid.addWidget(self.lead_out_angle, 1, 3)
        
        # Длина (автоподбирается, ручную правку юзера сохраняем)
        lead_grid.addWidget(QtWidgets.QLabel("Длина:"), 2, 0)
        self.lead_in_length = QtWidgets.QDoubleSpinBox()
        self.lead_in_length.setRange(0, 20); self.lead_in_length.setDecimals(2)
        self.lead_in_length.setValue(1.0); self.lead_in_length.setSingleStep(0.1)
        self.lead_in_length.setToolTip(
            "Подбирается автоматически (макс. 1.0×tool_radius). "
            "Если изменить вручную — автоподбор больше не будет затирать."
        )
        self._lead_in_length_user_set = False
        self.lead_in_length.valueChanged.connect(
            lambda v: setattr(self, '_lead_in_length_user_set', True))
        lead_grid.addWidget(self.lead_in_length, 2, 1)
        
        lead_grid.addWidget(QtWidgets.QLabel("Длина:"), 2, 2)
        self.lead_out_length = QtWidgets.QDoubleSpinBox()
        self.lead_out_length.setRange(0, 20); self.lead_out_length.setDecimals(2)
        self.lead_out_length.setValue(1.0); self.lead_out_length.setSingleStep(0.1)
        lead_grid.addWidget(self.lead_out_length, 2, 3)
        
        # Смещение
        lead_grid.addWidget(QtWidgets.QLabel("Смещение:"), 3, 0)
        self.lead_in_offset = QtWidgets.QDoubleSpinBox()
        self.lead_in_offset.setRange(-100, 100); self.lead_in_offset.setDecimals(2)
        self.lead_in_offset.setValue(-5); self.lead_in_offset.setSingleStep(0.5)
        # Флаг «юзер вручную менял offset». Стартовое значение -5 ставим
        # программно (setValue), поэтому _lead_in_user_set остаётся False
        # до фактического действия юзера.
        self._lead_in_user_set = False
        self.lead_in_offset.valueChanged.connect(
            lambda v: setattr(self, '_lead_in_user_set', True))
        self.lead_in_offset.setToolTip(
            "Смещение точки старта (мм) от RT-угла ножа по контуру. "
            "Отрицательное = влево по верхней стороне."
        )
        lead_grid.addWidget(self.lead_in_offset, 3, 1)
        
        lead_grid.addWidget(QtWidgets.QLabel("Смещение:"), 3, 2)
        self.lead_out_offset = QtWidgets.QDoubleSpinBox()
        self.lead_out_offset.setRange(-100, 100); self.lead_out_offset.setDecimals(2)
        self.lead_out_offset.setValue(-5); self.lead_out_offset.setSingleStep(0.5)
        self._lead_out_user_set = False
        self.lead_out_offset.valueChanged.connect(
            lambda v: setattr(self, '_lead_out_user_set', True))
        self.lead_out_offset.setToolTip(
            "Смещение точки старта (мм) от RT-угла ножа по контуру. "
            "Отрицательное = влево по верхней стороне."
        )
        lead_grid.addWidget(self.lead_out_offset, 3, 3)
        
        # Строка 4: Перекрытие (overlap) — продление контура после смыкания.
        # Только положительные значения; 0 = без перекрытия.
        lead_grid.addWidget(QtWidgets.QLabel("Перекрытие:"), 4, 0)
        self.lead_in_overlap = QtWidgets.QDoubleSpinBox()
        self.lead_in_overlap.setRange(0, 100)
        self.lead_in_overlap.setDecimals(2)
        self.lead_in_overlap.setValue(0); self.lead_in_overlap.setSingleStep(0.1)
        self.lead_in_overlap.setToolTip(
            "Перекрытие (мм) — насколько фреза продолжает движение по контуру "
            "после смыкания, прежде чем уйти в выход. Используется для "
            "разведения входа и выхода в разные точки контура (V-образно)."
        )
        lead_grid.addWidget(self.lead_in_overlap, 4, 1)
        
        lead_grid.addWidget(QtWidgets.QLabel("Перекрытие:"), 4, 2)
        self.lead_out_overlap = QtWidgets.QDoubleSpinBox()
        self.lead_out_overlap.setRange(0, 100)
        self.lead_out_overlap.setDecimals(2)
        self.lead_out_overlap.setValue(0); self.lead_out_overlap.setSingleStep(0.1)
        self.lead_out_overlap.setToolTip(
            "Перекрытие (мм) — насколько фреза продолжает движение по контуру "
            "после смыкания, прежде чем уйти в выход."
        )
        lead_grid.addWidget(self.lead_out_overlap, 4, 3)
        
        # ── Режим применения параметров lead-in/out ──
        # 3 режима:
        #   1) Авто-подбор: алгоритм автосдвигает позицию/угол/длину если 
        #      обнаружена коллизия с соседями. Юзерский offset — стартовый.
        #   2) Все элементы: НЕ сдвигать, применить точно юзерские значения 
        #      ко ВСЕМ ножам. Если возникает коллизия — показать RED.
        #   3) Выделенные: юзер кликом выделяет нож, меняет поля → 
        #      применяются только к нему. Остальные ножи — как раньше.
        lead_mode_group = QtWidgets.QGroupBox("Режим применения")
        lead_mode_layout = QtWidgets.QHBoxLayout(lead_mode_group)
        lead_mode_layout.setContentsMargins(6, 4, 6, 4)
        lead_mode_layout.setSpacing(8)
        
        self.rb_lead_auto = QtWidgets.QRadioButton("Авто-подбор")
        self.rb_lead_auto.setToolTip(
            "Алгоритм автоматически подбирает позицию/угол/длину чтобы "
            "избежать коллизий. Юзерские значения — стартовые.")
        self.rb_lead_all = QtWidgets.QRadioButton("Все элементы")
        self.rb_lead_all.setToolTip(
            "Применить юзерские значения (angle/length/offset/overlap) "
            "ТОЧНО ко всем ножам без автосдвига. При коллизии — RED.")
        self.rb_lead_selected = QtWidgets.QRadioButton("Выделенные")
        self.rb_lead_selected.setToolTip(
            "Кликом выделите нож на канвасе — изменения полей применятся "
            "только к нему. Остальные ножи не трогаются.")
        # По умолчанию — авто-подбор (как сейчас)
        self.rb_lead_auto.setChecked(True)
        
        # Группируем чтобы работали как радио
        self._lead_mode_bg = QtWidgets.QButtonGroup(self)
        self._lead_mode_bg.addButton(self.rb_lead_auto, 0)
        self._lead_mode_bg.addButton(self.rb_lead_all, 1)
        self._lead_mode_bg.addButton(self.rb_lead_selected, 2)
        
        lead_mode_layout.addWidget(self.rb_lead_auto)
        lead_mode_layout.addWidget(self.rb_lead_all)
        lead_mode_layout.addWidget(self.rb_lead_selected)
        
        # СОВМЕСТИМОСТЬ: старый атрибут auto_avoid_all оставляем как 
        # свойство — возвращает True когда выбран режим «Авто-подбор». 
        # Много кода уже смотрит на него через get_params_dict/etc.
        self.auto_avoid_all = _AutoAvoidCompat(self)
        
        # Подсветка изменённых полей автоподбором — снимается при экспорте.
        # Кнопка автоподбора удалена: вызывается автоматически при показе путей.
        
        # Добавляем группы В ОТДЕЛЬНОСТИ (не вкладываем режим в lead_group), 
        # чтобы можно было `lead_group.setEnabled(False)` в Auto-режиме — 
        # блокировать все поля разом, не трогая радио.
        layout.addWidget(lead_group)
        layout.addWidget(lead_mode_group)
        
        # ── ЧТО ГЕНЕРИРОВАТЬ ──
        gen_group = QtWidgets.QGroupBox("Генерировать файлы")
        gen_layout = QtWidgets.QVBoxLayout(gen_group)
        
        self.gen_rough = QtWidgets.QCheckBox("Черновая всех (_all_R.anc)")
        self.gen_rough.setChecked(True)
        gen_layout.addWidget(self.gen_rough)
        
        self.gen_reverse = QtWidgets.QCheckBox("Реверс черновая (_revers_R.anc)")
        self.gen_reverse.setChecked(True)
        gen_layout.addWidget(self.gen_reverse)
        
        self.gen_finish = QtWidgets.QCheckBox("Чистовые по операциям (_N_M.anc)")
        self.gen_finish.setChecked(True)
        gen_layout.addWidget(self.gen_finish)
        
        self.gen_sv = QtWidgets.QCheckBox("4 угловых (_SV.anc)")
        self.gen_sv.setChecked(True)
        gen_layout.addWidget(self.gen_sv)
        
        self.gen_corner = QtWidgets.QCheckBox("Острые углы 2D (_corner.anc)")
        # По умолчанию ВЫКЛЮЧЕНО: если геометрия без острых углов, юзеру 
        # не нужен пустой _corner.anc в выводе. Включает вручную при 
        # необходимости для конкретного заказа.
        self.gen_corner.setChecked(False)
        gen_layout.addWidget(self.gen_corner)
        
        self.gen_corner_3d = QtWidgets.QCheckBox("Острые углы 3D (_corner3D.anc)")
        gen_layout.addWidget(self.gen_corner_3d)
        
        self.gen_smooth = QtWidgets.QCheckBox("Сглаживание под фрезу (без самопересечений)")
        self.gen_smooth.setToolTip(
            "Скругляет тугие места до проходимого радиуса фрезы и убирает "
            "биарк-веера, чтобы эквидистанта (offset) не самопересекалась.\n"
            "Применяется и в превью, и в .anc. Требует shapely.")
        gen_layout.addWidget(self.gen_smooth)
        
        layout.addWidget(gen_group)
        
        # Две главные кнопки в один ряд: Пересчитать пути + Экспорт
        buttons_row = QtWidgets.QHBoxLayout()
        
        self.btn_show_paths = QtWidgets.QPushButton("Пересчитать пути")
        self.btn_show_paths.setToolTip(
            "Пересчитать траектории фрезы для выбранного заказа. "
            "Клик = построить заново с текущими параметрами. "
            "Пути кэшируются per-order — при переключении заказов пути "
            "уже построенных отображаются автоматически."
        )
        self.btn_show_paths.setMinimumHeight(40)
        buttons_row.addWidget(self.btn_show_paths, stretch=1)
        
        self.btn_export = QtWidgets.QPushButton("Экспорт")
        big_font = self.btn_export.font(); big_font.setBold(True)
        self.btn_export.setFont(big_font)
        self.btn_export.setMinimumHeight(40)
        buttons_row.addWidget(self.btn_export, stretch=1)
        
        layout.addLayout(buttons_row)
        
        layout.addStretch()
        
        # Сигналы изменений
        for w in [self.angle, self.tip]:
            w.currentTextChanged.connect(self._on_changed)
        for w in [self.top, self.bottom, 
                  self.lead_in_angle, self.lead_in_length, self.lead_in_offset,
                  self.lead_out_angle, self.lead_out_length, self.lead_out_offset,
                  self.max_geom_len]:
            w.valueChanged.connect(self._on_changed)
        for w in [self.dir_horiz, self.dir_vert, self.use_reverse,
                  self.gen_rough, self.gen_reverse, self.gen_finish,
                  self.gen_sv, self.gen_corner, self.gen_corner_3d]:
            w.toggled.connect(self._on_changed)
    
    def _on_changed(self):
        self.paramsChanged.emit()
    
    def set_defaults(self):
        """Сбрасывает все параметры панели к умолчаниям.
        
        Вызывается при загрузке нового .ai файла, чтобы каждый файл начинался
        с одних и тех же стартовых значений (не наследуется состояние от 
        предыдущего файла).
        
        Сигналы блокируются на время сброса — paramsChanged будет послан 
        ОДИН раз в конце через явный emit, чтобы не множить перерисовки.
        """
        # Список всех виджетов которые получают сигналы
        widgets_to_block = [
            self.angle, self.tip, self.top, self.bottom,
            self.dir_horiz, self.dir_vert, self.use_reverse,
            self.max_geom_len, self.fiducial_x,
            self.lead_in_angle, self.lead_in_length, 
            self.lead_in_offset, self.lead_in_overlap,
            self.lead_out_angle, self.lead_out_length,
            self.lead_out_offset, self.lead_out_overlap,
            self.gen_rough, self.gen_reverse, self.gen_finish,
            self.gen_sv, self.gen_corner, self.gen_corner_3d,
            self.gen_smooth, self.auto_avoid_all,
        ]
        for w in widgets_to_block:
            w.blockSignals(True)
        try:
            # ── Параметры ножа ──
            self.angle.setCurrentText("70")
            self.tip.setCurrentText("0_8")
            self.top.setValue(0.440)
            self.bottom.setValue(0.250)
            
            # ── Направление ──
            self.dir_horiz.setChecked(True)
            self.dir_vert.setChecked(False)
            self.use_reverse.setChecked(True)
            
            # ── Группировка ──
            self.max_geom_len.setValue(3000)
            # fiducial_x — НЕ сбрасываем, его выставит _auto_set_fiducial_x
            
            # ── Точка входа/выхода ──
            self.lead_in_angle.setValue(45)
            self.lead_in_length.setValue(1.0)
            self.lead_in_offset.setValue(-5)
            self.lead_in_overlap.setValue(0)
            self.lead_out_angle.setValue(45)
            self.lead_out_length.setValue(1.0)
            self.lead_out_offset.setValue(-5)
            self.lead_out_overlap.setValue(0)
            
            # Флаги «пользователь менял» — сбрасываются, чтобы автоподбор
            # снова мог переназначать значения если нужно
            self._lead_in_angle_user_set = False
            self._lead_in_length_user_set = False
            self._lead_in_user_set = False
            self._lead_out_user_set = False
            
            # ── Что генерировать ──
            self.gen_rough.setChecked(True)
            self.gen_reverse.setChecked(True)
            self.gen_finish.setChecked(True)
            self.gen_sv.setChecked(True)
            self.gen_corner.setChecked(False)
            self.gen_corner_3d.setChecked(False)
            self.gen_smooth.setChecked(False)
            self.auto_avoid_all.setChecked(True)
        finally:
            for w in widgets_to_block:
                w.blockSignals(False)
        # Один общий сигнал об изменении
        self.paramsChanged.emit()
    
    def get_params_dict(self) -> dict:
        """Возвращает словарь параметров для CamSession."""
        # Пятка из текста '1_2' → 1.2
        tip_str = self.tip.currentText().replace('_', '.')
        tip_value = float(tip_str)
        
        return {
            'knife_angle': float(self.angle.currentText()),
            'tip_diameter': tip_value,
            'top': self.top.value(),
            'bottom': self.bottom.value(),
            'direction': 'horizontal' if self.dir_horiz.isChecked() else 'vertical',
            'enable_reverse': self.use_reverse.isChecked(),
            'max_geom_len': self.max_geom_len.value(),
            'fiducial_distance': self.fiducial_x.value(),
            'lead_inside': {
                'angle': self.lead_in_angle.value(),
                'length': self.lead_in_length.value(),
                'offset': self.lead_in_offset.value(),
                'sign_offset': '+' if self.lead_in_offset.value() >= 0 else '-',
                'overlap': self.lead_in_overlap.value(),
                'user_set_offset': True,
            },
            'lead_outside': {
                'angle': self.lead_out_angle.value(),
                'length': self.lead_out_length.value(),
                'offset': self.lead_out_offset.value(),
                'sign_offset': '+' if self.lead_out_offset.value() >= 0 else '-',
                'overlap': self.lead_out_overlap.value(),
                'user_set_offset': True,
            },
            'generate_rough_all': self.gen_rough.isChecked(),
            'generate_reverse': self.gen_reverse.isChecked(),
            'generate_finish_per_op': self.gen_finish.isChecked(),
            'generate_sv': self.gen_sv.isChecked(),
            'generate_corner': self.gen_corner.isChecked(),
            'generate_corner_3d': self.gen_corner_3d.isChecked(),
            'smooth_offset_for_tool': self.gen_smooth.isChecked(),
            'auto_avoid_all': self.auto_avoid_all.isChecked(),
        }
    
    def _on_auto_lead_clicked(self):
        """Автоподбор угла и длины ВНУТРЕННЕГО лида (lead_in_*).
        
        Эмпирическая формула, калиброванная под пользовательский эталон
        (диам=2мм пятка=0.8 → L=0.7). Угол: <5R→90°, <10R→60°, иначе 45°.
        
        Применяет ТОЛЬКО к Внутреннему; внешний не трогает.
        Подсветка изменённых полей остаётся ДО экспорта или ручной правки.
        """
        main = self.window()
        sess = getattr(main, 'session', None)
        if sess is None or not sess.project.operations:
            return
        
        from camsys.geometry.path_offset import polypath_bbox
        from camsys.core.project import OperationKind
        import math
        
        min_diag = float('inf')
        for op in sess.project.operations:
            if op.kind != OperationKind.BLADE_FORMING: continue
            if not op.geometry_ids: continue
            g = sess.project.get_geometry(op.geometry_ids[0])
            if not g or not g.polypath: continue
            x0, y0, x1, y1 = polypath_bbox(g.polypath)
            diag = math.hypot(x1 - x0, y1 - y0)
            if diag < min_diag:
                min_diag = diag
        if min_diag == float('inf'):
            return
        
        try:
            tip_str = self.tip.currentText().replace('_', '.')
            pyatka = float(tip_str)
        except Exception:
            pyatka = 0.8
        tool_radius = pyatka / 2.0
        
        diam = min_diag / math.sqrt(2)
        if diam < 2 * pyatka:
            L_mm = 0.1
        elif diam < 4 * pyatka:
            L_mm = (diam - pyatka) / 4.0
        elif diam < 8 * pyatka:
            L_mm = tool_radius
        else:
            L_mm = 2 * tool_radius
        best_length = L_mm / tool_radius if tool_radius > 1e-9 else 1.0
        best_length = max(0.1, min(1.0, round(best_length * 10) / 10))
        
        char_width = diam / 2.0
        if char_width < tool_radius * 5:
            angle = 90.0
        elif char_width < tool_radius * 10:
            angle = 60.0
        else:
            angle = 45.0
        
        old_angle = self.lead_in_angle.value()
        old_length = self.lead_in_length.value()
        # Ручная правка юзера превыше автоподбора: если он трогал поле — 
        # оставляем как есть и не подсвечиваем.
        change_angle = (abs(old_angle - angle) > 1e-3 
                        and not getattr(self, '_lead_in_angle_user_set', False))
        change_length = (abs(old_length - best_length) > 1e-3 
                         and not getattr(self, '_lead_in_length_user_set', False))
        if not change_angle and not change_length:
            return
        # Подменяем значения с блокировкой сигналов, чтобы не взвести флаги
        if change_angle:
            self.lead_in_angle.blockSignals(True)
            self.lead_in_angle.setValue(angle)
            self.lead_in_angle.blockSignals(False)
        if change_length:
            self.lead_in_length.blockSignals(True)
            self.lead_in_length.setValue(best_length)
            self.lead_in_length.blockSignals(False)
        hi = "background-color: #fff3a0;"
        # Подсветка ОСТАЁТСЯ — снимется при экспорте или ручной правке поля.
        if change_angle:
            self.lead_in_angle.setStyleSheet(hi)
            try:
                self.lead_in_angle.valueChanged.disconnect(self._clear_lead_in_angle_hi)
            except Exception: pass
            self.lead_in_angle.valueChanged.connect(self._clear_lead_in_angle_hi)
        if change_length:
            self.lead_in_length.setStyleSheet(hi)
            try:
                self.lead_in_length.valueChanged.disconnect(self._clear_lead_in_length_hi)
            except Exception: pass
            self.lead_in_length.valueChanged.connect(self._clear_lead_in_length_hi)
    
    def _clear_lead_in_angle_hi(self, *_):
        self.lead_in_angle.setStyleSheet("")
    
    def _clear_lead_in_length_hi(self, *_):
        self.lead_in_length.setStyleSheet("")
    
    def clear_auto_lead_highlight(self):
        """Снимает подсветку автоподбора с полей. Вызывается из 
        главного окна при нажатии Экспорт."""
        self.lead_in_angle.setStyleSheet("")
        self.lead_in_length.setStyleSheet("")


# ─────────────────────────────────────────────────────────────────────────
#  ГЛАВНОЕ ОКНО
# ─────────────────────────────────────────────────────────────────────────

class MainWindow(QtWidgets.QMainWindow):
    """Главное окно camsys."""
    
    def __init__(self):
        super().__init__()
        # Базовый заголовок без файла. Меняется при загрузке .ai.
        from camsys import __version_info__
        self._base_title = f"camsys {__version_info__} — CAM для флексографических ножей"
        self.setWindowTitle(self._base_title)
        self.resize(1600, 950)
        
        # Сессия — единый источник состояния
        self.session = CamSession()
        
        self._build_menus()
        self._build_central()
        self._build_statusbar()
        
        # Запоминаем последнюю папку
        self._last_dir = str(Path.home())
    
    # ── меню ──
    def _build_menus(self):
        mb = self.menuBar()
        
        m_file = mb.addMenu("&Файл")
        a_open = m_file.addAction("&Открыть .ai...")
        a_open.setShortcut("Ctrl+O")
        a_open.triggered.connect(self.action_open)
        
        a_export = m_file.addAction("&Экспорт пакета...")
        a_export.setShortcut("Ctrl+E")
        a_export.triggered.connect(self.action_export)
        
        m_file.addSeparator()
        
        a_save_state = m_file.addAction("Сохранить настройки...")
        a_save_state.triggered.connect(self.action_save_state)
        
        a_load_state = m_file.addAction("Загрузить настройки...")
        a_load_state.triggered.connect(self.action_load_state)
        
        m_file.addSeparator()
        a_quit = m_file.addAction("Выход")
        a_quit.setShortcut("Ctrl+Q")
        a_quit.triggered.connect(self.close)
        
        m_view = mb.addMenu("&Вид")
        a_fit = m_view.addAction("&Вписать всё")
        a_fit.setShortcut("F")
        a_fit.triggered.connect(lambda: self.viewer.fit_all())
        
        m_help = mb.addMenu("&Помощь")
        a_about = m_help.addAction("О программе")
        a_about.triggered.connect(self.action_about)
    
    # ── центральная область ──
    def _build_central(self):
        # Главный сплиттер: левая колонка | вьюер | параметры
        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        
        # Левая колонка: слои сверху, операции снизу
        left_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        
        self.layer_tree = LayerTree()
        self.layer_tree.layerVisibilityChanged.connect(self._on_layer_visibility)
        left_splitter.addWidget(self.layer_tree)
        
        self.ops_panel = OperationsPanel()
        self.ops_panel.btn_create.clicked.connect(self.action_create_operations)
        self.ops_panel.btn_sort.clicked.connect(self.action_sort_operations)
        self.ops_panel.btn_clear.clicked.connect(self.action_clear_operations)
        self.ops_panel.operationToggled.connect(self._on_operation_toggled)
        left_splitter.addWidget(self.ops_panel)
        left_splitter.setSizes([250, 350])
        
        main_splitter.addWidget(left_splitter)
        
        # Центр: 2D-вьюер
        self.scene = CamScene()
        self.viewer = CamView(self.scene)
        self.viewer._on_mouse_move = self._on_cursor_move
        main_splitter.addWidget(self.viewer)
        
        # Правая панель: параметры
        self.params_panel = CuttingParamsPanel()
        self.params_panel.btn_export.clicked.connect(self.action_export)
        self.params_panel.btn_show_paths.clicked.connect(self.action_toggle_paths)
        
        # Связка радио-режимов «Авто/Все/Выделенные» с возможностью 
        # выделения ножей на канвасе. Селект работает только в режиме 
        # «Выделенные».
        self.params_panel._lead_mode_bg.idToggled.connect(
            self._on_lead_mode_changed)
        # Начальное состояние — по текущему выбранному радио
        current_id = self.params_panel._lead_mode_bg.checkedId()
        self.scene.set_toolpath_selection_enabled(current_id == 2)
        # В режиме Авто (id=0) вся группа lead-in/out блокируется
        self.params_panel._lead_group.setEnabled(current_id != 0)
        
        # ── ВЫДЕЛЕНИЕ НОЖА (для режима «Выделенные») ──
        # Отслеживаем id выделенного ножа. Используется в extras для 
        # _build_toolpath_geometry чтобы применить юзерские поля ТОЛЬКО 
        # к этому ножу; остальные строятся автоалгоритмом.
        self._selected_op_id: str = ""
        self.scene.toolpath_clicked.connect(self._on_toolpath_clicked)
        # Правый клик на toolpath — переключить excluded (быстрая замена 
        # галочки в operations-таблице).
        self.scene.toolpath_right_clicked.connect(self._on_toolpath_right_clicked)
        # Прокручиваемая обёртка
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self.params_panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        main_splitter.addWidget(scroll)
        
        # Стартовые размеры: максимум места центру (превью), панели компактные
        main_splitter.setSizes([230, 1100, 370])
        # На ресайз окна тянется только центр; панели остаются фиксированными
        main_splitter.setStretchFactor(0, 0)   # левая колонка
        main_splitter.setStretchFactor(1, 1)   # вьюер — забирает всё лишнее
        main_splitter.setStretchFactor(2, 0)   # правая панель
        # Боковые панели можно полностью свернуть мышью для макс. превью
        main_splitter.setCollapsible(0, True)
        main_splitter.setCollapsible(1, False)
        main_splitter.setCollapsible(2, True)
        # Ограничиваем разрастание боковых панелей
        left_splitter.setMaximumWidth(420)
        scroll.setMaximumWidth(440)
        self.setCentralWidget(main_splitter)
    
    # ── статус-бар ──
    def _build_statusbar(self):
        sb = self.statusBar()
        self.label_file = QtWidgets.QLabel("Проект не загружен")
        sb.addWidget(self.label_file)
        
        sb.addPermanentWidget(QtWidgets.QLabel(" | "))
        self.label_cursor = QtWidgets.QLabel("X: -.-  Y: -.-")
        sb.addPermanentWidget(self.label_cursor)
    
    def _on_cursor_move(self, x: float, y: float):
        self.label_cursor.setText(f"X: {x:.3f}  Y: {y:.3f}")
    
    # ─────────────────────────────────────────────────────────────────
    #  ДЕЙСТВИЯ
    # ─────────────────────────────────────────────────────────────────
    
    def action_open(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Открыть .ai файл", self._last_dir,
            "Adobe Illustrator (*.ai);;Все файлы (*.*)"
        )
        if not path:
            return
        self._last_dir = str(Path(path).parent)
        
        try:
            self.statusBar().showMessage(f"Загружаю {Path(path).name}...")
            QtWidgets.QApplication.processEvents()
            
            # ── СБРОС СОСТОЯНИЯ ПЕРЕД ЗАГРУЗКОЙ НОВОГО .ai ──
            # Старые toolpath items привязаны к старому проекту и должны 
            # быть удалены. Кнопка «Показать пути фрезы» возвращается в 
            # выключенное состояние.
            for item in getattr(self, '_toolpath_items', []):
                try:
                    self.scene.removeItem(item)
                except Exception:
                    pass
            self._toolpath_items = []
            # Сбрасываем кэш путей per-order при открытии нового файла —
            # старые пути не имеют отношения к новому проекту
            self._order_toolpath_items = {}
            self.params_panel.btn_show_paths.setText("Пересчитать пути")
            
            # Сбрасываем все параметры панели к умолчаниям — каждый новый файл
            # начинается со стандартных значений, не наследует предыдущие
            self.params_panel.set_defaults()
            
            self.session.load_ai(path)
            self.session._last_ai_path = path  # для поиска XML вокруг файла
            self._auto_detect_corner_programs()
            self._auto_set_fiducial_x()
            
            # Автоматически создаём операции сразу после загрузки — чтобы 
            # юзер видел все ножи+реперы в таблице без необходимости жать 
            # «Создать». Раньше «Создать» было ручной кнопкой, но по факту 
            # его надо жать ВСЕГДА перед любой работой — автоматизируем.
            try:
                self.session.create_blade_operations()
                self.session.sort_by_grid()
            except Exception:
                pass  # если что-то не так с .ai — юзер увидит через btn_create
            
            self._refresh_all()
            self.label_file.setText(f"Проект: {self.session.project.name}")
            # Имя файла в заголовке окна — видно во вкладках таскбара/Alt+Tab.
            self.setWindowTitle(f"{Path(path).name} — {self._base_title}")
            
            # ── Анализ сшивки ──
            # Если файл — сшивка (`<стичка>_<заказ1>_<заказ2>_..._.ai`),
            # автоматически определяем регионы + распределяем ножи по 
            # заказам. Результат сохраняем в session.stitch_info.
            self._analyze_and_setup_stitch(path)
            
            # ── Чтение specification_*.xml из папки заказа ──
            # Ищет ../XML/specification_*.xml, парсит УголЗаточкиКромки и 
            # ВысотаНожа. Найденные значения подставляются в поля панели и 
            # подсвечиваются розовым — юзер видит откуда взято.
            self._apply_spec_xml_values(path)
            
            self.statusBar().showMessage("Загружено", 3000)
            self.viewer.fit_all()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка импорта", str(e))
            self.statusBar().showMessage("Ошибка", 3000)
    
    def _analyze_and_setup_stitch(self, ai_path):
        """Анализирует .ai на предмет сшивки и настраивает dropdown.
        
        Логика:
        - Для сшивки: dropdown содержит N заказов (без «Всей сшивки»).
          Первый заказ автоматически выбран, к нему применяется фильтр.
        - Для одиночного заказа: dropdown содержит один элемент (номер 
          заказа из имени файла). Фильтра нет (все ножи и так одного 
          заказа).
        
        При выборе заказа автоматически:
        1. Фильтр сцены (только его ножи+реперы)
        2. output_prefix = номер заказа
        3. Автозагрузка specification_<номер>.xml
        """
        from pathlib import Path
        from ..io_.stitch import analyze_stitch
        
        self.params_panel.stitch_combo.blockSignals(True)
        self.params_panel.stitch_combo.clear()
        
        try:
            stitch_info = analyze_stitch(Path(ai_path), self.session.project)
        except Exception:
            stitch_info = None
        
        self.session._stitch_info = stitch_info
        
        if stitch_info is None or len(stitch_info.regions) < 2:
            # Одиночный заказ — 1 элемент в dropdown, без фильтра
            single_order = Path(ai_path).stem.rstrip('_')
            parts = single_order.split('_')
            numeric = [p for p in parts if p.isdigit()]
            if numeric:
                single_order = numeric[0]
            self.params_panel.stitch_combo.addItem(
                f"{single_order}", single_order)
            self.session.cutting_params.output_prefix = single_order
            self.params_panel.stitch_combo.setEnabled(True)
            self.params_panel.stitch_combo.blockSignals(False)
            return
        
        # Сшивка — заполняем комбо ТОЛЬКО заказами (без «Вся сшивка»)
        for r in stitch_info.regions:
            label = r.order_number or f"регион {stitch_info.regions.index(r) + 1}"
            n_knives = len(r.knife_ids)
            display = f"{label} ({n_knives} ножей)"
            self.params_panel.stitch_combo.addItem(display, label)
        
        self.params_panel.stitch_combo.setEnabled(True)
        self.params_panel.stitch_combo.blockSignals(False)
        
        if not hasattr(self, '_stitch_signal_connected'):
            self.params_panel.stitch_combo.currentIndexChanged.connect(
                self._on_stitch_order_changed)
            self._stitch_signal_connected = True
        
        # АВТОВЫБОР первого заказа — применяет фильтр
        self.params_panel.stitch_combo.setCurrentIndex(0)
        # Вызываем handler явно (setCurrentIndex(0) при уже 0 не эмитит сигнал)
        self._current_stitch_order = None  # сброс чтобы точно применить
        self._on_stitch_order_changed(0)
        
        self.statusBar().showMessage(
            f"Сшивка {stitch_info.stitch_number}: "
            f"{len(stitch_info.regions)} заказов", 5000)
    
    def _on_stitch_order_changed(self, idx):
        """Юзер выбрал заказ в dropdown.
        
        Всегда работаем в режиме одного заказа:
        1. Сохранить настройки предыдущего заказа
        2. Загрузить настройки нового (или из XML если первый раз)  
        3. Отфильтровать сцену только на его ножи
        """
        order = self.params_panel.stitch_combo.itemData(idx)
        if not order:
            return
        
        # 1. Сохранить настройки предыдущего заказа
        prev_order = getattr(self, '_current_stitch_order', None)
        if prev_order and prev_order != order:
            self._save_order_settings(prev_order)
        
        self._current_stitch_order = order
        
        # 2. Загрузить настройки (если сохранены)
        loaded = self._load_order_settings(order)
        
        # 3. Применить фильтр по региону (если сшивка)
        stitch = getattr(self.session, '_stitch_info', None)
        if stitch:
            region = stitch.get_region_by_order(order)
            if region:
                self._apply_stitch_filter(region)
        # Для одиночного заказа фильтр не нужен — все ножи и так его
        
        # 4. Prefix для экспорта = номер заказа (не имя сшивки!)
        self.session.cutting_params.output_prefix = order
        
        # 5. Если не было сохранённых настроек — грузим из XML
        if not loaded:
            self._apply_spec_xml_for_order(order)
        
        # Статус
        if stitch:
            region = stitch.get_region_by_order(order)
            if region:
                self.statusBar().showMessage(
                    f"Заказ {order}: {len(region.knife_ids)} ножей, "
                    f"{len(region.fiducial_ids)} реперов", 5000)
        else:
            self.statusBar().showMessage(f"Заказ {order}", 3000)
    
    def _save_order_settings(self, order):
        """Снимает текущие значения UI + attributes ops → в session dict."""
        if not hasattr(self.session, '_order_settings'):
            self.session._order_settings = {}
        p = self.params_panel
        # UI values
        ui = {
            'angle': p.angle.currentText(),
            'tip': p.tip.currentText(),
            'top': p.top.value(),
            'bottom': p.bottom.value(),
            'direction_horiz': p.dir_horiz.isChecked(),
            'use_reverse': p.use_reverse.isChecked(),
            'max_geom_len': p.max_geom_len.value(),
            'fiducial_x': p.fiducial_x.value(),
        }
        # Attributes всех операций этого заказа (excluded + lead_override)
        from ..core.project import OperationKind
        stitch = getattr(self.session, '_stitch_info', None)
        region = stitch.get_region_by_order(order) if stitch else None
        op_attrs = {}
        if region:
            allowed_knives = set(region.knife_ids)
            allowed_fids = set(region.fiducial_ids)
            for op in self.session.project.operations:
                # Определяем принадлежит ли op этому заказу
                belongs = False
                if op.kind == OperationKind.BLADE_FORMING:
                    belongs = any(g in allowed_knives for g in op.geometry_ids)
                elif op.kind == OperationKind.CORNER_REWORK:
                    belongs = op.attributes.get('parent_geom_id') in allowed_knives
                elif op.kind == OperationKind.FIDUCIAL_DRILL:
                    belongs = op.attributes.get('fiducial_id') in allowed_fids
                if belongs:
                    op_attrs[op.id] = {
                        'excluded': op.attributes.get('excluded', False),
                        'lead_override': dict(op.attributes.get('lead_override', {})),
                    }
        self.session._order_settings[order] = {
            'ui': ui,
            'op_attrs': op_attrs,
        }
    
    def _load_order_settings(self, order):
        """Загружает сохранённые настройки заказа в UI. Возвращает True 
        если данные были найдены (значит XML грузить не нужно).
        """
        settings = getattr(self.session, '_order_settings', {}).get(order)
        if not settings:
            return False
        p = self.params_panel
        # Заблокировать сигналы во время массовой установки
        widgets = [p.angle, p.tip, p.top, p.bottom, p.dir_horiz, p.dir_vert,
                   p.use_reverse, p.max_geom_len, p.fiducial_x]
        for w in widgets:
            w.blockSignals(True)
        try:
            ui = settings.get('ui', {})
            if 'angle' in ui: p.angle.setCurrentText(ui['angle'])
            if 'tip' in ui: p.tip.setCurrentText(ui['tip'])
            if 'top' in ui: p.top.setValue(ui['top'])
            if 'bottom' in ui: p.bottom.setValue(ui['bottom'])
            if 'direction_horiz' in ui:
                p.dir_horiz.setChecked(ui['direction_horiz'])
                p.dir_vert.setChecked(not ui['direction_horiz'])
            if 'use_reverse' in ui: p.use_reverse.setChecked(ui['use_reverse'])
            if 'max_geom_len' in ui: p.max_geom_len.setValue(ui['max_geom_len'])
            if 'fiducial_x' in ui: p.fiducial_x.setValue(ui['fiducial_x'])
        finally:
            for w in widgets:
                w.blockSignals(False)
        # Восстановить op.attributes для этого заказа
        op_attrs = settings.get('op_attrs', {})
        for op in self.session.project.operations:
            if op.id in op_attrs:
                stored = op_attrs[op.id]
                op.attributes['excluded'] = stored.get('excluded', False)
                lo = stored.get('lead_override')
                if lo:
                    op.attributes['lead_override'] = dict(lo)
                else:
                    op.attributes.pop('lead_override', None)
        return True
    
    def _apply_stitch_filter(self, region):
        """Фильтрует сцену: только ножи и реперы указанного региона.
        None → показать всё. Использует attribute 'stitch_filtered_out'."""
        from ..core.project import OperationKind
        
        if region is None:
            for op in self.session.project.operations:
                op.attributes.pop('stitch_filtered_out', None)
        else:
            allowed_knife_geoms = set(region.knife_ids)
            allowed_fid_ids = set(region.fiducial_ids)
            for op in self.session.project.operations:
                if op.kind == OperationKind.BLADE_FORMING:
                    keep = any(g in allowed_knife_geoms for g in op.geometry_ids)
                    op.attributes['stitch_filtered_out'] = not keep
                elif op.kind == OperationKind.FIDUCIAL_DRILL:
                    fid_id = op.attributes.get('fiducial_id', '')
                    op.attributes['stitch_filtered_out'] = fid_id not in allowed_fid_ids
                elif op.kind == OperationKind.CORNER_REWORK:
                    parent = op.attributes.get('parent_geom_id', '')
                    op.attributes['stitch_filtered_out'] = parent not in allowed_knife_geoms
        
        self._refresh_operations()
        # При смене заказа:
        # 1. СКРЫВАЕМ пути ВСЕХ прочих заказов
        # 2. ПОКАЗЫВАЕМ пути ТЕКУЩЕГО заказа (из кэша)
        # 3. Кнопка меняется:
        #    - Если у текущего заказа есть пути → «Обновить (N)»
        #    - Если нет → «Построение путей»
        if hasattr(self, '_order_toolpath_items'):
            current = getattr(self, '_current_stitch_order', None) or "_default"
            for order_name, items in self._order_toolpath_items.items():
                visible = (order_name == current)
                for item in items:
                    item.setVisible(visible)
            current_items = self._order_toolpath_items.get(current, [])
            if current_items:
                self.params_panel.btn_show_paths.setText(
                    f"Пересчитать пути ({len(current_items)})")
            else:
                self.params_panel.btn_show_paths.setText("Пересчитать пути")
    
    def _apply_spec_xml_for_order(self, order_number):
        """Ищет specification_<order>.xml в разных местах.
        
        Пробуем несколько вариантов расположения:
        1. <stitch_folder>/XML/specification_<order>.xml (стандарт для сшивок)
        2. <stitch_folder>/../<order>/XML/specification_<order>.xml (соседняя папка заказа)
        3. <stitch_folder>/../../<order>/XML/specification_<order>.xml (на 2 уровня выше)
        4. <ai_path>/../XML/ (для одиночного заказа с обычной структурой)
        
        Если XML не найден — оставляем поля розовыми (юзер введёт вручную).
        """
        # Определяем откуда искать — от stitch (если есть) или от project
        ai_path = None
        stitch = getattr(self.session, '_stitch_info', None)
        if stitch and getattr(stitch, 'ai_path', None):
            ai_path = Path(stitch.ai_path)
        elif getattr(self.session, '_last_ai_path', None):
            ai_path = Path(self.session._last_ai_path)
        if ai_path is None or not ai_path.exists():
            return
        
        # Список candidate-папок для поиска XML
        maket_dir = ai_path.parent
        stitch_root = maket_dir.parent
        candidates_dirs = [
            stitch_root / "XML",                     # <stitch>/XML/
            stitch_root.parent / order_number / "XML",  # sibling folder ordered by number
            stitch_root.parent.parent / order_number / "XML",  # на уровень выше
        ]
        
        for xml_dir in candidates_dirs:
            if not xml_dir.is_dir():
                continue
            # Ищем specification_<order>.xml
            found = list(xml_dir.glob(f"specification_{order_number}*.xml"))
            if not found:
                found = list(xml_dir.glob(f"*{order_number}*.xml"))
            if found:
                self._apply_spec_xml_values(found[0])
                return
        
        # XML не нашли — очистим поля от подсветки предыдущего заказа
        # и выставим розовые (юзер введёт вручную)
        self._apply_spec_xml_values("")  # пустой путь → всё розовое
    
    def _apply_spec_xml_values(self, ai_path: str):
        """Читает specification_*.xml (если есть) и заполняет поля.
        
        Логика подсветки:
        - Значения из XML успешно применены → поля БЕЗ подсветки (юзер знает 
          что они пришли из спецификации, доверять можно).
        - XML не найден или не распарсен → соответствующие поля 
          подсвечиваются РОЗОВЫМ, юзер должен проверить и ввести вручную.
        """
        p = self.params_panel
        pink_style = "background-color: #ffd6ec;"
        target_widgets = {'angle': p.angle, 'top': p.top}
        # Ключи из XML для каждого поля
        xml_key_of = {'angle': 'knife_angle', 'top': 'knife_height'}
        
        # Пытаемся читать XML
        spec = {}
        try:
            from ..io_.spec_xml import read_spec_for_ai
            spec = read_spec_for_ai(ai_path)
        except Exception:
            pass  # молча — XML необязателен
        
        applied = []
        
        for widget_key, widget in target_widgets.items():
            xml_key = xml_key_of[widget_key]
            if xml_key in spec:
                # Значение есть — применяем, снимаем подсветку
                value = spec[xml_key]
                widget.blockSignals(True)
                if isinstance(widget, QtWidgets.QComboBox):
                    # Проверяем что такой пункт есть
                    items = [widget.itemText(i) for i in range(widget.count())]
                    if str(value) in items:
                        widget.setCurrentText(str(value))
                    else:
                        # Не смогли применить — подсвечиваем
                        widget.setStyleSheet(pink_style)
                        widget.blockSignals(False)
                        continue
                else:
                    widget.setValue(value)
                widget.blockSignals(False)
                widget.setStyleSheet("")  # снимаем розовое, значение из XML
                applied.append(f"{widget_key}={value}")
            else:
                # Значения из XML нет — подсвечиваем, чтобы юзер обратил внимание
                widget.setStyleSheet(pink_style)
        
        # Сообщение в статус-бар
        if spec.get('order_number') and applied:
            order = spec['order_number']
            self.statusBar().showMessage(
                f"Из specification_{order}.xml: {', '.join(applied)}", 5000)
        elif not spec:
            self.statusBar().showMessage(
                "XML спецификации не найден — проверьте розовые поля вручную", 
                5000)
    
    def _auto_detect_corner_programs(self):
        """Анализирует загруженный проект и АВТОМАТИЧЕСКИ ставит чекбоксы
        генерации _corner.anc и _corner3D.anc в зависимости от того, есть
        ли в контурах соответствующие углы.
        
        Логика:
            - Есть скругления радиусом МЕНЬШЕ рабочего радиуса фрезы
              → _corner.anc ВКЛЮЧАЕТСЯ (фреза не проходит, нужен T3)
            - Нет таких скруглений             → _corner.anc ВЫКЛЮЧАЕТСЯ
            - Есть полностью острые углы       → _corner3D.anc ВКЛЮЧАЕТСЯ
            - Нет таких углов                  → _corner3D.anc ВЫКЛЮЧАЕТСЯ
        
        Порог для 2D — ДИНАМИЧЕСКИЙ, равен реальному радиусу фрезы 
        (tip/2 + ABS·tan(angle/2)), чтобы согласоваться с алгоритмом 
        _build_corner_operations в постпроцессоре. Если фреза 0.8 с 
        углом 70 и ABS 0.25 → порог 0.575мм. Скругления R>=0.575 фреза 
        проходит, corner_rework не нужен.
        """
        from ..geometry.corner_detect import (
            has_small_radius_corners, has_pointed_corners
        )
        import math
        
        prj = self.session.project
        if prj is None:
            return
        
        # Динамический порог = реальный радиус фрезы
        cp = self.session.cutting_params
        half_angle_rad = math.radians(cp.knife_angle / 2.0)
        dynamic_threshold = (cp.tip_diameter / 2.0 
                             + cp.bottom * math.tan(half_angle_rad))
        
        # Перебираем все геометрии на слое Knife
        knife = prj.get_layer_by_name("Knife")
        if knife is None:
            return
        
        any_small = False
        any_pointed = False
        for g in knife.geometries:
            if not g.polypath:
                continue
            if not any_small and has_small_radius_corners(g.polypath, dynamic_threshold):
                any_small = True
            if not any_pointed and has_pointed_corners(g.polypath, 30.0):
                any_pointed = True
            if any_small and any_pointed:
                break
        
        # Ставим чекбоксы (без триггера изменений если уже стоят)
        if hasattr(self, 'params_panel'):
            self.params_panel.gen_corner.setChecked(any_small)
            self.params_panel.gen_corner_3d.setChecked(any_pointed)
        
        msg_parts = []
        if any_small:
            msg_parts.append("углы 2D")
        if any_pointed:
            msg_parts.append("углы 3D")
        if msg_parts:
            self.statusBar().showMessage(
                f"Авто-детект: будут сгенерированы {', '.join(msg_parts)}", 5000)
    
    def _auto_set_fiducial_x(self):
        """После загрузки .ai вычисляет X-координату 2-й реперной точки 
        из импортированных fiducials и подставляет в поле UI (если оно 
        ещё имеет дефолтное значение 700.0)."""
        if not self.session.has_project():
            return
        fids = list(self.session.project.fiducials)
        if len(fids) < 2:
            return
        # Сортируем по X, берём правый репер
        fids.sort(key=lambda f: f.x)
        right_x = fids[-1].x
        if right_x <= 0:
            return
        # Поле UI: подставляем с подсветкой
        from PySide6 import QtCore
        self.params_panel.fiducial_x.blockSignals(True)
        self.params_panel.fiducial_x.setValue(round(right_x, 1))
        self.params_panel.fiducial_x.blockSignals(False)
        self.params_panel.fiducial_x.setStyleSheet("background-color: #fff3a0;")
        QtCore.QTimer.singleShot(
            3000, lambda: self.params_panel.fiducial_x.setStyleSheet(""))
        self.statusBar().showMessage(
            f"2-я реперная точка X = {right_x:.1f}мм (из макета)", 5000)
    
    def action_create_operations(self):
        if not self.session.has_project():
            QtWidgets.QMessageBox.warning(self, "Нет проекта", 
                                          "Сначала откройте .ai файл")
            return
        try:
            created = self.session.create_blade_operations()
            self.session.sort_by_grid()
            QtWidgets.QMessageBox.information(
                self, "Готово", 
                f"Создано {len(created)} операций. "
                f"Отсортированы слева-направо."
            )
            self._refresh_operations()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка", str(e))
    
    def action_sort_operations(self):
        if not self.session.has_project():
            return
        self.session.sort_by_grid()
        self._refresh_operations()
    
    def action_clear_operations(self):
        if not self.session.has_project():
            return
        ret = QtWidgets.QMessageBox.question(
            self, "Удалить операции?",
            "Удалить все операции из проекта?")
        if ret != QtWidgets.QMessageBox.Yes:
            return
        self.session.project.operations.clear()
        self._refresh_operations()
    
    def action_preview_files(self):
        if not self.session.has_project():
            QtWidgets.QMessageBox.warning(self, "Нет проекта", 
                                          "Сначала откройте .ai файл")
            return
        # Применить параметры из UI
        try:
            params = self.params_panel.get_params_dict()
            self.session.set_cutting_params_from_dict(params)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка параметров", str(e))
            return
        
        # Если ещё нет операций — создаём
        if not self.session.project.operations:
            self.session.create_blade_operations()
            self.session.sort_by_grid()
            self._refresh_operations()
        
        names = self.session.preview_package_filenames()
        QtWidgets.QMessageBox.information(
            self, "Будут созданы файлы",
            "\n".join(f"  {n}" for n in names))
    
    def _on_toolpath_clicked(self, op_id: str):
        """Юзер кликнул на toolpath на канвасе. Сохраняем id для 
        использования в extras (только в режиме «Выделенные»).
        
        Пустой op_id (клик мимо, Escape) → выделение снято.
        
        Автообновление НЕ вызываем — юзер сам жмёт «Обновить» когда 
        хочет применить текущие значения полей. Иначе неожиданно 
        сдвигается позиция ножа при простом клике-выделении.
        """
        self._selected_op_id = op_id
    
    def _on_toolpath_right_clicked(self, op_id: str):
        """Правый клик на toolpath — показать контекстное меню.
        
        Меню содержит:
        - Отключить/Включить нож (тоггл excluded)
        - Сбросить переопределение (убрать lead_override если есть)
        
        Меню предотвращает случайные срабатывания при промахах ПКМ.
        """
        if not op_id or self.session.project is None:
            return
        # Находим op
        op = next((o for o in self.session.project.operations if o.id == op_id), None)
        if op is None:
            return
        
        # Строим меню
        menu = QtWidgets.QMenu(self)
        is_excluded = op.attributes.get('excluded', False)
        has_override = 'lead_override' in op.attributes
        
        # Название ножа для заголовка
        op_name = f"{op.kind.value} {op.id[:6]}"
        header = menu.addAction(op_name)
        header.setEnabled(False)  # только заголовок
        menu.addSeparator()
        
        act_toggle = menu.addAction(
            "Включить нож" if is_excluded else "Отключить нож")
        act_reset = menu.addAction("Сбросить переопределение")
        act_reset.setEnabled(has_override)
        
        # Показываем меню у курсора
        chosen = menu.exec(QtGui.QCursor.pos())
        
        if chosen is act_toggle:
            new_excluded = not is_excluded
            op.attributes['excluded'] = new_excluded
            new_state = "исключён" if new_excluded else "активен"
            self.statusBar().showMessage(f"{op_name} → {new_state}", 3000)
            
            # Если отключаем/включаем BLADE — синхронизируем его CORNER'ы. 
            # Иначе галка у угла остаётся стоять, а визуально он всё равно 
            # скрыт (add_toolpaths_to_scene скрывает corner'ы для excluded 
            # blade через excluded_geom_ids). Синхронизация делает состояние 
            # понятным юзеру.
            from ..core.project import OperationKind
            if op.kind == OperationKind.BLADE_FORMING:
                blade_geom_ids = set(op.geometry_ids)
                for other in self.session.project.operations:
                    if (other.kind == OperationKind.CORNER_REWORK
                            and other.attributes.get('parent_geom_id') in blade_geom_ids):
                        other.attributes['excluded'] = new_excluded
            
            self._refresh_operations()
            # Синхронизируем визуал реперов (серый если отключены)
            self.scene.refresh_fiducial_state(self.session.project)
            if self.params_panel.btn_show_paths.isChecked():
                self.action_toggle_paths(True)
        elif chosen is act_reset:
            op.attributes.pop('lead_override', None)
            self.statusBar().showMessage(
                f"{op_name} → override сброшен", 3000)
            if self.params_panel.btn_show_paths.isChecked():
                self.action_toggle_paths(True)
    
    def _on_lead_mode_changed(self, mode_id: int, checked: bool):
        """Обработчик переключения радио-режима lead'а.
        
        mode_id: 0=Авто, 1=Все элементы, 2=Выделенные.
        - Селект на канвасе работает ТОЛЬКО в режиме «Выделенные».
        - В режиме «Авто» вся группа «Точка входа/выхода» блокируется 
          (значения ставит алгоритм).
        - В режимах «Все/Выделенные» группа разрешена.
        - При смене режима автоматически перестраиваются пути (если показаны).
        """
        if not checked:
            return  # игнорируем «toggle off» события (парный к «toggle on»)
        
        # Селект по клику включён только в режиме «Выделенные»
        self.scene.set_toolpath_selection_enabled(mode_id == 2)
        
        # Вся группа «Точка входа/выхода» блокируется в Авто
        self.params_panel._lead_group.setEnabled(mode_id != 0)
        
        # Управление per-op override'ами при смене режима:
        # - При входе в «Выделенные»: снапшотим текущие ГЛОБАЛЬНЫЕ поля в 
        #   op.attributes['lead_override'] для всех ножей (если ещё нет). 
        #   Тогда изменения полей будут «прилипать» только к выделенному 
        #   элементу, остальные останутся с этим снапшотом.
        # - При выходе (Авто/Все): удаляем все override'ы — глобальные поля 
        #   применяются ко всем.
        if self.session.project is not None:
            if mode_id == 2:  # Вход в «Выделенные»
                self._snapshot_lead_params_to_ops()
            else:  # Выход
                self._clear_lead_overrides()
        
        # Автообновление если пути уже показаны — юзер сразу видит эффект
        if self.params_panel.btn_show_paths.isChecked():
            self.action_toggle_paths(True)
    
    def _snapshot_lead_params_to_ops(self):
        """Сохраняет текущие глобальные lead-параметры в атрибуты каждой 
        операции ножа. Вызывается при переходе в режим «Выделенные» — 
        тогда изменения полей будут применяться ТОЛЬКО к выделенному op'у,
        остальные будут использовать сохранённые снапшоты.
        """
        p = self.params_panel
        snapshot = {
            'lead_inside': {
                'angle': p.lead_in_angle.value(),
                'length': p.lead_in_length.value(),
                'offset': p.lead_in_offset.value(),
                'overlap': p.lead_in_overlap.value(),
            },
            'lead_outside': {
                'angle': p.lead_out_angle.value(),
                'length': p.lead_out_length.value(),
                'offset': p.lead_out_offset.value(),
                'overlap': p.lead_out_overlap.value(),
            },
        }
        for op in self.session.project.operations:
            if 'lead_override' not in op.attributes:
                op.attributes['lead_override'] = {
                    'lead_inside': dict(snapshot['lead_inside']),
                    'lead_outside': dict(snapshot['lead_outside']),
                }
    
    def _clear_lead_overrides(self):
        """Удаляет per-op lead-override'ы у всех операций."""
        if self.session.project is None:
            return
        for op in self.session.project.operations:
            op.attributes.pop('lead_override', None)
    
    def action_refresh_paths(self):
        """Принудительная перерисовка путей. Если они скрыты — включает.
        Если уже показаны — снимает старые и рисует новые с актуальными 
        параметрами (после изменений в полях GUI)."""
        if not self.params_panel.btn_show_paths.isChecked():
            # Не отображались — просто включаем (toggled сигнал вызовет toggle_paths)
            self.params_panel.btn_show_paths.setChecked(True)
            return
        # Уже показаны — просто перевызываем action_toggle_paths(True), 
        # он сам снимет старые элементы и нарисует новые.
        # Кнопка остаётся checked, сигнал не дёргаем.
        self.action_toggle_paths(True)
    
    def action_toggle_paths(self, checked: bool = True):
        """Кнопка «Построение путей» — строит/обновляет пути для 
        АКТИВНОГО заказа.
        
        Кнопка всегда работает как REBUILD: клик = построить заново с 
        текущими параметрами. Текст меняется в зависимости от состояния:
        - Ничего не построено: «Построение путей»
        - Пути этого заказа уже есть: «Обновить (N)» — клик пересборет
        
        Скрыть пути можно только переключением на другой заказ (пути 
        текущего заказа автоматически скрываются, показываются кэш 
        нового).
        """
        # Кэш путей per-order
        if not hasattr(self, '_order_toolpath_items'):
            self._order_toolpath_items = {}
        
        current_order = getattr(self, '_current_stitch_order', None) or "_default"
        
        # Удаляем старые пути ЭТОГО заказа перед перестройкой (rebuild)
        old_items = self._order_toolpath_items.pop(current_order, [])
        for item in old_items:
            self.scene.removeItem(item)
        
        if self.session.project is None or not self.session.project.operations:
            QtWidgets.QMessageBox.information(
                self, "Пути не доступны",
                "Сначала загрузите .ai файл и создайте операции."
            )
            return
        
        # ── Автоподбор лидов перед отрисовкой ──
        # Анализируем макет и подставляем оптимальные angle/length внутреннего
        # лида (lead_in_*). Поле подсвечивается жёлтым на 2 сек если изменилось.
        # Юзер видит что было предложено и может поправить, нажать «Обновить».
        try:
            self.params_panel._on_auto_lead_clicked()
        except Exception:
            pass
        
        # В режиме «Выделенные» — обновляем override у выделенного op'а 
        # ТЕКУЩИМИ значениями полей. Тогда только он получит изменения.
        # Остальные ноги остаются со своим снапшот-override'ом.
        lead_mode_id = self.params_panel._lead_mode_bg.checkedId() \
            if hasattr(self.params_panel, '_lead_mode_bg') else 0
        if lead_mode_id == 2 and self._selected_op_id:
            sel_op = next((o for o in self.session.project.operations
                            if o.id == self._selected_op_id), None)
            if sel_op is not None:
                p = self.params_panel
                sel_op.attributes['lead_override'] = {
                    'lead_inside': {
                        'angle': p.lead_in_angle.value(),
                        'length': p.lead_in_length.value(),
                        'offset': p.lead_in_offset.value(),
                        'overlap': p.lead_in_overlap.value(),
                    },
                    'lead_outside': {
                        'angle': p.lead_out_angle.value(),
                        'length': p.lead_out_length.value(),
                        'offset': p.lead_out_offset.value(),
                        'overlap': p.lead_out_overlap.value(),
                    },
                }
        
        try:
            # Применим текущие параметры к session
            params = self.params_panel.get_params_dict()
            self.session.set_cutting_params_from_dict(params)
            
            # Подготовим extras для построения путей (как при экспорте)
            import math
            tip = params.get('tip_diameter', 0.8)
            bottom = params.get('bottom', 0.2)
            top = params.get('top', 0.5)
            angle = params.get('knife_angle', 80)
            tool_radius = tip / 2.0
            # Эквидистанта = tip + 2 * bottom * tan(угол/2)
            # где bottom = ABS = глубина реза от вершины ножа.
            # При уменьшении ABS радиус уменьшается — путь приближается 
            # к контуру.
            tool_eq = tip + 2 * bottom * math.tan(math.radians(angle/2))
            
            # Для CORNER операций используется тонкая фреза T3 (пятка 0.6мм)
            # с пропорционально меньшей эквидистантой.
            corner_tip = 0.6  # фреза T3 для углов 2D
            corner_tool_radius = corner_tip / 2.0
            corner_tool_eq = corner_tip + 2 * bottom * math.tan(math.radians(angle/2))
            
            # Режим применения lead'а: 0=Авто, 1=Все, 2=Выделенные.
            # В viewer передаём вместе с id выделенного ножа (для режима 2).
            lead_mode_id = self.params_panel._lead_mode_bg.checkedId() \
                if hasattr(self.params_panel, '_lead_mode_bg') else 0
            
            extras = {
                'tool_radius': tool_radius,
                'tool_equidistant': tool_eq,
                'corner_tool_radius': corner_tool_radius,
                'corner_tool_equidistant': corner_tool_eq,
                'smooth_offset_for_tool': self.gen_smooth.isChecked()
                    if hasattr(self, 'gen_smooth') else False,
                'auto_avoid_all': self.params_panel.auto_avoid_all.isChecked()
                    if hasattr(self.params_panel, 'auto_avoid_all') else True,
                'lead_mode': lead_mode_id,  # 0=Авто, 1=Все, 2=Выделенные
                'selected_op_id': self._selected_op_id,
            }
            
            # Вызываем визуализатор
            from .viewer_2d import add_toolpaths_to_scene
            
            # Также прогоняем _analyze_layout_lead_side чтобы preferred_lead_side был
            # проставлен (как при экспорте) — иначе визуализация по середине не сработает
            from ..post.package_export import PackageExporter
            from ..core.cutting_macro import CuttingMacroParams
            macro = CuttingMacroParams(
                knife_angle=params.get('knife_angle', 80),
                tip_diameter=params.get('tip_diameter', 0.8),
                top=params.get('top', 0.45),
                bottom=params.get('bottom', 0.2),
                generate_corner=params.get('generate_corner', True),
                generate_corner_3d=params.get('generate_corner_3d', False),
            )
            exp = PackageExporter(self.session.project, macro)
            preferred = exp._analyze_layout_lead_side()
            from ..core.project import OperationKind
            for op in self.session.project.operations:
                if op.kind == OperationKind.BLADE_FORMING:
                    op.attributes['preferred_lead_side'] = preferred
            
            # Получаем дополнительно corner-операции (они создаются в Package­Exporter
            # при экспорте, тут их явно генерируем для визуализации).
            # ВАЖНО: добавляем ДО assign_program_numbers, иначе на первом 
            # вызове (когда corner'ов ещё нет) программы распределяются 
            # только по blade'ам, а на втором (после добавления corner'ов) — 
            # по всем ops. Результат разный между кликами «Обновить».
            corner_ops_2d, corner_ops_3d = [], []
            try:
                if macro.generate_corner or macro.generate_corner_3d:
                    corner_ops_2d, corner_ops_3d = exp._build_corner_operations()
            except Exception:
                pass
            
            # Добавляем corner-операции в проект ОДИН РАЗ (постоянно).
            # При повторном вызове action_toggle_paths не дублируем.
            
            # СТРАЖА: сначала уберём все дубликаты corner-операций если они 
            # каким-то образом закрались (что бывает при переключении настроек
            # generate_corner/generate_corner_3d между вызовами, или при 
            # изменении параметров ножа влияющих на распознавание углов).
            # Ключ дубликата: (parent_geom_id, corner_index, corner_is_3d).
            seen_corner_keys = set()
            deduped_ops = []
            dropped = 0
            for op in self.session.project.operations:
                if op.kind == OperationKind.CORNER_REWORK:
                    key = (op.attributes.get('parent_geom_id'),
                           op.attributes.get('corner_index'),
                           op.attributes.get('corner_is_3d', False))
                    if key in seen_corner_keys:
                        dropped += 1
                        continue
                    seen_corner_keys.add(key)
                deduped_ops.append(op)
            if dropped > 0:
                self.session.project.operations[:] = deduped_ops
            
            existing_corner_ids = seen_corner_keys
            
            extra_ops = []
            if macro.generate_corner:
                extra_ops.extend(corner_ops_2d)
            if macro.generate_corner_3d:
                extra_ops.extend(corner_ops_3d)
            
            new_corners_added = 0
            for op in extra_ops:
                key = (op.attributes.get('parent_geom_id'),
                       op.attributes.get('corner_index'),
                       op.attributes.get('corner_is_3d', False))
                if key not in existing_corner_ids:
                    self.session.project.operations.append(op)
                    existing_corner_ids.add(key)
                    new_corners_added += 1
            
            # Назначаем program_number ПОСЛЕ добавления corner'ов — чтобы 
            # цвета визуализации совпадали с реальной разбивкой на чистовые 
            # программы (1_M, 2_M, ...) с учётом всех операций.
            #
            # ВАЖНО для сшивок: временно ВЫДЕЛЯЕМ только ноги активного 
            # заказа (не отфильтрованные). Иначе алгоритм видит все ноги 
            # сшивки, длины суммируются кросс-между заказами, и в 121561 
            # первый нож попадает в другую программу чем следующие.
            from ..core.macros import assign_program_numbers
            _saved_ops = list(self.session.project.operations)
            active_ops = [op for op in _saved_ops 
                          if not op.attributes.get('stitch_filtered_out', False)]
            self.session.project.operations = active_ops
            try:
                assign_program_numbers(
                    self.session.project,
                    max_geom_len=self.session.cutting_params.max_geom_len,
                    direction=self.session.cutting_params.direction.value,
                    corridor_tolerance=self.session.cutting_params.corridor_tolerance,
                    passes_per_part=2,
                )
            finally:
                self.session.project.operations = _saved_ops
            
            if new_corners_added > 0:
                # Обновим таблицу чтобы CORNER появились
                self._refresh_operations()
            
            # Фильтр визуализации по чекбоксам программ
            show_filter = {
                'blade': self.params_panel.gen_rough.isChecked() or 
                         self.params_panel.gen_finish.isChecked(),
                'corner_2d': self.params_panel.gen_corner.isChecked(),
                'corner_3d': self.params_panel.gen_corner_3d.isChecked(),
            }
            
            # Прогресс-диалог. Показывает %+статус во время построения путей.
            # Пути на плотном макете могут строиться 2-10 секунд из-за 
            # автоподбора lead-in/out — юзеру полезно видеть что процесс идёт.
            progress = QtWidgets.QProgressDialog(
                "Построение путей фрезы...", "Отмена", 0, 100, self)
            progress.setWindowTitle("Пожалуйста подождите")
            progress.setWindowModality(QtCore.Qt.WindowModal)
            progress.setMinimumDuration(500)  # показывать только если > 500мс
            progress.setValue(0)
            
            def _on_progress(current, total):
                # Возврат False = юзер нажал Cancel → прерываем
                if progress.wasCanceled():
                    return False
                pct = int(current * 100 / max(1, total))
                progress.setValue(pct)
                progress.setLabelText(
                    f"Построение путей фрезы...  {current} / {total}")
                QtWidgets.QApplication.processEvents()
                return True
            
            self._toolpath_items = add_toolpaths_to_scene(
                self.scene, self.session.project, extras,
                cutting_params=self.session.cutting_params,
                show_filter=show_filter,
                progress_callback=_on_progress,
            )
            # Кэшируем построенные items под ключом текущего заказа
            self._order_toolpath_items[current_order] = list(self._toolpath_items)
            progress.setValue(100)
            progress.close()
            
            # Сбрасываем выделение — юзер видит: изменения применились, 
            # подсветка нигде не горит. Готов кликать следующий нож.
            self._selected_op_id = ""
            
            n = len(self._toolpath_items)
            self.params_panel.btn_show_paths.setText(f"Пересчитать пути ({n})")
            self.statusBar().showMessage(f"Показано путей: {n}", 5000)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Ошибка визуализации", str(e)
            )
            self.params_panel.btn_show_paths.setChecked(False)
    
    def action_export(self):
        # Снимаем подсветку автоподбора с полей лидов (юзер начал экспорт)
        try:
            self.params_panel.clear_auto_lead_highlight()
        except Exception:
            pass
        # Весь экспорт обёрнут в защиту: под .pyw (без консоли) любое
        # необработанное исключение иначе уронило бы операцию молча
        # (предупреждение появилось, а запись — нет, папка пустая).
        try:
            self._do_export()
        except Exception as e:
            import traceback
            from pathlib import Path as _P
            tb = traceback.format_exc()
            try:
                log = _P.home() / "camsys_crash.log"
                prev = log.read_text(encoding='utf-8') if log.exists() else ""
                log.write_text(prev + "\n[action_export]\n" + tb + "\n",
                               encoding='utf-8')
            except Exception:
                pass
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Critical)
            box.setWindowTitle("Ошибка экспорта")
            box.setText(f"{e}")
            box.setDetailedText(tb)
            box.exec()

    def _do_export(self):
        if not self.session.has_project():
            QtWidgets.QMessageBox.warning(self, "Нет проекта", 
                                          "Сначала откройте .ai файл")
            return
        
        # Применить параметры из UI до проверки
        params = self.params_panel.get_params_dict()
        self.session.set_cutting_params_from_dict(params)
        
        # Если включено сглаживание под фрезу, но shapely нет — предупредим
        if getattr(self.session.cutting_params, 'smooth_offset_for_tool', False):
            try:
                import shapely  # noqa
            except Exception:
                QtWidgets.QMessageBox.warning(
                    self, "Нужен модуль shapely",
                    "Включено «Сглаживание под фрезу», но модуль shapely не "
                    "установлен — сглаживание НЕ будет применено.\n\n"
                    "Установите его командой:\n    pip install shapely")
        
        # Если ещё нет операций — создаём
        if not self.session.project.operations:
            self.session.create_blade_operations()
            self.session.sort_by_grid()
            self._refresh_operations()
        
        # ── ПРОВЕРКА СОВМЕСТИМОСТИ ФРЕЗЫ С МАКЕТОМ ──
        # Если эквидистанты соседей пересекаются — фреза не подходит, 
        # выводим предупреждение. Юзер сам решает: переключиться на 
        # меньшую фрезу или экспортировать как есть.
        import math
        from ..post.package_export import PackageExporter
        from ..core.cutting_macro import CuttingMacroParams
        
        cp = self.session.cutting_params
        tool_eq = cp.tip_diameter + 2.0 * cp.bottom * math.tan(
            math.radians(cp.knife_angle / 2.0))
        tool_offset = tool_eq / 2.0
        
        macro = CuttingMacroParams(
            knife_angle=cp.knife_angle, tip_diameter=cp.tip_diameter,
            top=cp.top, bottom=cp.bottom)
        check_exp = PackageExporter(self.session.project, macro)
        problem = check_exp.check_tool_fits_layout(tool_offset)
        
        if problem:
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setWindowTitle("Фреза не подходит для этого макета")
            box.setText(
                f"{problem}\n\n"
                "Это предупреждение, а НЕ ошибка. Можно экспортировать как есть "
                "(оператор проверит) или отменить и поставить меньшую пятку фрезы.")
            btn_go = box.addButton("Экспортировать всё равно",
                                   QtWidgets.QMessageBox.AcceptRole)
            box.addButton("Отмена", QtWidgets.QMessageBox.RejectRole)
            box.setDefaultButton(btn_go)
            box.exec()
            if box.clickedButton() is not btn_go:
                self.statusBar().showMessage("Экспорт отменён", 3000)
                return
        
        # ── ОПРЕДЕЛЕНИЕ ПАПКИ NC (авто, по пути .ai) ──
        # Папка <номер>-NC рядом с .ai. Старые .anc архивируются в oldN.
        # Если путь .ai неизвестен (проект из сохранённого состояния) —
        # просим выбрать папку вручную.
        from pathlib import Path
        nc_dir = None
        try:
            nc_dir = self.session.resolve_nc_dir()
        except Exception as e:
            QtWidgets.QMessageBox.information(
                self, "Папка NC не определена",
                f"{e}\nВыберите папку вручную.")
        
        # ── ПОДМЕНА ПАПКИ ДЛЯ СШИВОК ──
        # Для сшивки resolve_nc_dir возвращает папку с именем стички 
        # (напр. '41000_121554_121561_121555_-NC'), т.к. знает только имя 
        # .ai. Но у сшивки ЭКСПОРТИРУЕМ ОДИН ЗАКАЗ — папка должна называться 
        # по номеру заказа (напр. '121554-NC'). Пробуем найти уже существующую 
        # папку заказа на 1-2 уровня выше .ai, иначе создаём рядом.
        current_order = getattr(self, '_current_stitch_order', None)
        stitch_info = getattr(self.session, '_stitch_info', None)
        if current_order and stitch_info:
            ai_path = Path(self.session.project.source_ai_path or "")
            if ai_path.exists():
                stitch_root = ai_path.parent.parent  # <stitch>/maket/ai → <stitch>
                candidates = [
                    stitch_root.parent / f"{current_order}-NC",   # sibling
                    stitch_root.parent / f"{current_order}_NC",
                    stitch_root.parent / current_order / "NC",
                    stitch_root.parent / current_order / f"{current_order}-NC",
                    stitch_root / f"{current_order}-NC",           # внутри стички
                    stitch_root / f"{current_order}_NC",
                ]
                # Ищем существующую (приоритет)
                found = None
                for c in candidates:
                    if c.is_dir():
                        found = c
                        break
                if found:
                    nc_dir = found
                else:
                    # Не нашли — создаём рядом со сшивочной папкой
                    nc_dir = stitch_root.parent / f"{current_order}-NC"
        
        if nc_dir is not None:
            # Покажем куда будем писать и предупредим про архивацию
            existing = sorted(Path(nc_dir).glob("*.anc")) if Path(nc_dir).is_dir() else []
            arch_note = (f"\n\nВ папке уже есть {len(existing)} .anc — они будут "
                         f"перенесены в подпапку old.") if existing else ""
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Question)
            box.setWindowTitle("Экспорт пакета")
            box.setText(f"Писать .anc в папку:\n{nc_dir}{arch_note}")
            b_ok = box.addButton("Писать сюда", QtWidgets.QMessageBox.AcceptRole)
            b_other = box.addButton("Другая папка…", QtWidgets.QMessageBox.ActionRole)
            box.addButton("Отмена", QtWidgets.QMessageBox.RejectRole)
            box.setDefaultButton(b_ok)
            box.exec()
            clicked = box.clickedButton()
            if clicked is b_other:
                nc_dir = None  # уйдём в ручной выбор ниже
            elif clicked is not b_ok:
                self.statusBar().showMessage("Экспорт отменён", 3000)
                return
        
        # ── Лог экспорта: пишется РЯДОМ С .ai (где юзер и смотрит) и в дом.
        # папку, плюс показывается прямо в окне результата (кнопка «Подробнее»).
        from pathlib import Path as _P
        import datetime, os as _os
        ai_src = self.session.project.source_ai_path
        log_targets = []
        if ai_src:
            log_targets.append(_P(ai_src).parent / "camsys_export.log")
        log_targets.append(_P.home() / "camsys_export.log")

        def _write_log(text):
            for lp in log_targets:
                try:
                    prev = lp.read_text(encoding='utf-8') if lp.exists() else ""
                    lp.write_text(prev + text + "\n", encoding='utf-8')
                except Exception:
                    pass

        cp2 = self.session.cutting_params
        from ..core.project import OperationKind as _OK
        n_blade = sum(1 for o in self.session.project.operations
                      if o.kind == _OK.BLADE_FORMING)
        n_active = sum(1 for o in self.session.project.operations
                       if o.kind == _OK.BLADE_FORMING
                       and not o.attributes.get('excluded', False))
        loglines = [
            f"\n===== ЭКСПОРТ {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====",
            f".ai: {ai_src}",
            f"целевая папка: {nc_dir}",
            f"ножей всего={n_blade} активно(галка)={n_active}",
            f"типы файлов: rough={cp2.generate_rough_all} reverse={cp2.generate_reverse} "
            f"finish={cp2.generate_finish_per_op} sv={cp2.generate_sv} "
            f"corner={cp2.generate_corner} corner3d={cp2.generate_corner_3d}",
            f"лог пишется в: {', '.join(str(t) for t in log_targets)}",
        ]

        def _show(icon, title, text, details):
            box = QtWidgets.QMessageBox(self)
            box.setIcon(icon)
            box.setWindowTitle(title)
            box.setText(text)
            box.setDetailedText(details)
            box.exec()

        try:
            self.statusBar().showMessage("Экспорт...")
            QtWidgets.QApplication.processEvents()
            
            # ── Прогресс-диалог показывается ТОЛЬКО когда началась 
            # реальная генерация (после выбора папки). Иначе он 
            # выскакивал поверх диалога выбора папки и мешал юзеру.
            def _make_progress():
                d = QtWidgets.QProgressDialog(
                    "Генерация файлов...", "Отмена", 0, 100, self)
                d.setWindowTitle("Экспорт")
                d.setWindowModality(QtCore.Qt.WindowModal)
                d.setMinimumDuration(500)
                d.setValue(0)
                return d
            
            def _make_callback(dlg):
                def _cb(current, total, stage_name):
                    if dlg.wasCanceled():
                        return False
                    pct = int(current * 100 / max(1, total))
                    dlg.setValue(pct)
                    dlg.setLabelText(
                        f"Генерация: {stage_name}...  ({current+1}/{total})")
                    QtWidgets.QApplication.processEvents()
                    return True
                return _cb
            
            progress = None
            try:
                if nc_dir is not None:
                    # Папка известна — прогресс сразу, передаём override 
                    # чтобы session использовал именно эту папку (не 
                    # resolve_nc_dir которая для сшивки даст стичка_имя-NC)
                    progress = _make_progress()
                    self.session._progress_callback = _make_callback(progress)
                    result = self.session.export_package_auto(
                        nc_dir_override=str(nc_dir))
                    written = result['written']
                    out_dir = result['dir']
                    archived = result['archived']
                else:
                    # Ждём выбора папки
                    out_dir = QtWidgets.QFileDialog.getExistingDirectory(
                        self, "Папка для пакета (.anc)", self._last_dir)
                    if not out_dir:
                        self.statusBar().showMessage(
                            "Экспорт отменён — папка не выбрана", 4000)
                        return
                    self._last_dir = out_dir
                    # Папку выбрали — теперь показываем прогресс
                    progress = _make_progress()
                    self.session._progress_callback = _make_callback(progress)
                    written = self.session.export_package(out_dir)
                    archived = None
            finally:
                self.session._progress_callback = None
                if progress is not None:
                    progress.setValue(100)
                    progress.close()

            # ── POSITION-вариант: пишется автоматически рядом, в подпапку
            #    POSITION/. Заказ вырезается со стички и кладётся на станок
            #    отдельно — у него локальный (0,0) = LB-репер. Ошибка
            #    POSITION не должна ломать сообщение о главном экспорте,
            #    он уже прошёл. Warnings собираем и покажем оператору.
            pos_result = None
            pos_error = None
            if written:  # только если основной экспорт что-то дал
                try:
                    pos_order = (current_order
                                 if current_order and current_order != "_default"
                                 else None)
                    pos_nc_override = (
                        str(nc_dir) if nc_dir is not None else out_dir)
                    pos_result = self.session.export_package_position(
                        order_number=pos_order,
                        nc_dir_override=pos_nc_override)
                except Exception as e:
                    import traceback as _tb
                    pos_error = f"{e}\n{_tb.format_exc()}"

            loglines.append(f"записано файлов: {len(written)}")
            for f in written:
                loglines.append(f"   {f['path']}  ({f['size']} б)")
            if archived:
                loglines.append(f"старые -> {archived}")

            # ── POSITION в лог ──
            if pos_error:
                loglines.append("")
                loglines.append("POSITION: ошибка")
                loglines.append(pos_error)
            elif pos_result is not None:
                loglines.append("")
                if pos_result.get('skipped'):
                    loglines.append("POSITION: пропущен")
                else:
                    pw = pos_result.get('written', [])
                    loglines.append(f"POSITION: записано {len(pw)} файлов")
                    for f in pw:
                        loglines.append(f"   {f['path']}  ({f['size']} б)")
                    t = pos_result.get('transform') or {}
                    loglines.append(
                        f"   трансформ: pair={t.get('pair')} "
                        f"rotate_cw90={t.get('rotate_cw90')} "
                        f"dist={t.get('dist')}")
                for w in pos_result.get('warnings', []):
                    loglines.append(f"   ! {w}")

            log_text = "\n".join(loglines)
            _write_log(log_text)
            
            if not written:
                _show(QtWidgets.QMessageBox.Warning, "Ничего не записано",
                      "Экспорт завершился, но не создано ни одного файла.\n"
                      "Проверьте, включены ли типы программ в панели справа.",
                      log_text)
                self.statusBar().showMessage("Экспорт: 0 файлов", 4000)
                return
            
            real_dir = _os.path.dirname(_os.path.abspath(written[0]['path']))
            msg = (f"Записано {len(written)} файлов.\n\nПАПКА:\n{real_dir}\n\n")
            if archived:
                msg += f"Старые файлы перенесены в:\n{archived}\n\n"
            for f in written:
                msg += f"  {f['name']:<40s} {f['size']:>8} байт\n"

            # ── POSITION в сообщение оператору ──
            icon = QtWidgets.QMessageBox.Information
            title = "Экспорт завершён"
            if pos_error:
                msg += f"\n[POSITION] Ошибка: {pos_error.splitlines()[0]}\n"
                icon = QtWidgets.QMessageBox.Warning
                title = "Экспорт завершён, POSITION с ошибкой"
            elif pos_result is not None:
                if pos_result.get('skipped'):
                    msg += "\n[POSITION] Пропущен:\n"
                    for w in pos_result.get('warnings', []):
                        msg += f"  {w}\n"
                    icon = QtWidgets.QMessageBox.Warning
                else:
                    pw = pos_result.get('written', [])
                    msg += (f"\n[POSITION] Записано {len(pw)} файлов "
                            f"в подпапку POSITION/:\n")
                    for f in pw:
                        msg += f"  {f['name']:<40s} {f['size']:>8} байт\n"
                    warns = pos_result.get('warnings', [])
                    if warns:
                        msg += "\n[POSITION] ПРЕДУПРЕЖДЕНИЯ:\n"
                        for w in warns:
                            msg += f"  ! {w}\n"
                        icon = QtWidgets.QMessageBox.Warning
                        title = ("Экспорт завершён, "
                                 "POSITION с предупреждениями")

            _show(icon, title, msg, log_text)
            self.statusBar().showMessage(f"Экспортировано: {len(written)} файлов", 5000)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            loglines.append("ОШИБКА:")
            loglines.append(tb)
            log_text = "\n".join(loglines)
            _write_log(log_text)
            _show(QtWidgets.QMessageBox.Critical, "Ошибка экспорта",
                  f"{e}", log_text)
            self.statusBar().showMessage("Ошибка экспорта", 3000)
    
    def action_save_state(self):
        if not self.session.has_project():
            QtWidgets.QMessageBox.warning(self, "Нет проекта", 
                                          "Нечего сохранять")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Сохранить настройки", self._last_dir,
            "JSON (*.json)")
        if not path:
            return
        # Применить текущие параметры
        params = self.params_panel.get_params_dict()
        self.session.set_cutting_params_from_dict(params)
        self.session.save_state_to_json(path)
        self.statusBar().showMessage(f"Сохранено: {path}", 3000)
    
    def action_load_state(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Загрузить настройки", self._last_dir,
            "JSON (*.json)")
        if not path:
            return
        try:
            self.session.load_state_from_json(path)
            self._refresh_all()
            self.statusBar().showMessage(f"Загружено: {path}", 3000)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка", str(e))
    
    def action_about(self):
        from camsys import __version__, __version_date__
        QtWidgets.QMessageBox.about(
            self, "О camsys",
            f"<h3>camsys v{__version__}</h3>"
            f"<p><small>Сборка: {__version_date__}</small></p>"
            "<p>CAM-система для прецизионной обточки "
            "флексографических ножей</p>"
            "<p>Поддержка: Anderson Europe GVM (MTX V2.13)</p>"
        )
    
    # ─────────────────────────────────────────────────────────────────
    #  ОБНОВЛЕНИЕ ВИДЖЕТОВ ПОСЛЕ ИЗМЕНЕНИЯ СЕССИИ
    # ─────────────────────────────────────────────────────────────────
    
    def _refresh_all(self):
        self.layer_tree.load_from_session(self.session)
        self.ops_panel.load_from_session(self.session)
        self.scene.load_project(self.session.project)
    
    def _refresh_operations(self):
        self.ops_panel.load_from_session(self.session)
    
    def _on_operation_toggled(self):
        """Обработчик изменения галочки в таблице операций.
        Перерисовываем визуализацию путей если они уже построены.
        Также синхронизируем визуал реперов (серый если отключены).
        """
        # Синхронизируем FiducialItem с новым excluded flag
        self.scene.refresh_fiducial_state(self.session.project)
        # Пересобираем пути ТЕКУЩЕГО заказа если они уже были построены —
        # иначе снятие галки не влияет на визуал (пути кэшированы и не 
        # знают об изменении excluded)
        current_order = getattr(self, '_current_stitch_order', None) or "_default"
        cached = getattr(self, '_order_toolpath_items', {})
        if current_order in cached and cached[current_order]:
            # Пересчитаем — action_toggle_paths удалит старые и построит новые
            self.action_toggle_paths()
    
    def _on_layer_visibility(self, layer_name: str, visible: bool):
        self.session.set_layer_visibility(layer_name, visible)
        self.scene.set_layer_visible(layer_name, visible)


# ─────────────────────────────────────────────────────────────────────────
#  ТОЧКА ВХОДА
# ─────────────────────────────────────────────────────────────────────────

def run_app():
    """Запуск UI как самостоятельного приложения."""
    import sys, traceback, datetime, io
    from pathlib import Path
    # ── ВАЖНО для .pyw (pythonw): там sys.stdout/stderr == None, и любой
    # вызов sys.stderr.write(...) падает с AttributeError. Подменяем на
    # безопасный приёмник (лог-файл в домашней папке), чтобы весь код,
    # пишущий в stderr/stdout, работал без падений.
    if sys.stderr is None or sys.stdout is None:
        try:
            _sink = open(Path.home() / "camsys_stdio.log", "a",
                         encoding="utf-8", buffering=1)
        except Exception:
            _sink = io.StringIO()
        if sys.stderr is None:
            sys.stderr = _sink
        if sys.stdout is None:
            sys.stdout = _sink

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("camsys")

    # ── Глобальный перехватчик исключений ──
    # Под .pyw (pythonw) НЕТ консоли, поэтому необработанные исключения иначе
    # роняли бы приложение молча. Показываем окно и пишем лог в домашнюю папку.
    def _excepthook(exc_type, exc_value, exc_tb):
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            log = Path.home() / "camsys_crash.log"
            prev = log.read_text(encoding='utf-8') if log.exists() else ""
            log.write_text(
                prev + f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n"
                + tb + "\n", encoding='utf-8')
        except Exception:
            pass
        try:
            box = QtWidgets.QMessageBox()
            box.setIcon(QtWidgets.QMessageBox.Critical)
            box.setWindowTitle("Внутренняя ошибка")
            box.setText("Произошла необработанная ошибка. Приложение продолжит "
                        "работу, но операция не завершилась.\n\n"
                        "Лог: ~/camsys_crash.log")
            box.setDetailedText(tb)
            box.exec()
        except Exception:
            pass
    sys.excepthook = _excepthook

    win = MainWindow()
    win.show()
    
    return app.exec()
