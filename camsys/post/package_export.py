"""
post/package_export.py — генератор пакета .anc файлов для одной детали.

По образцу выхода макроса Cutting v-5.1 (см. скриншот пользователя):

    <prefix>_<angle>_all_R.anc       — черновая всех элементов
    <prefix>_<angle>_revers_R.anc    — черновая в обратном порядке
    <prefix>_<angle>_1_M.anc         — чистовая по операции 1
    <prefix>_<angle>_2_M.anc         — чистовая по операции 2
    ...
    <prefix>_<angle>_N_M.anc         — чистовая по операции N
    <prefix>_<angle>_SV.anc          — 4 угловых элемента (контроль сведения)
    <prefix>_<angle>_corner.anc      — острые углы (тонкая 2D-фреза)
    <prefix>_<angle>_corner3D.anc    — самые острые углы (3D-фреза)

Использование:
    package = PackageExporter(project, params)
    package.write_all('./output/')
    
    # или поштучно:
    files = package.generate()  # → dict {filename: content}
"""

from __future__ import annotations
from typing import Dict, List, Tuple, Optional
from pathlib import Path
import copy

from ..core.project import Project, Operation, OperationKind
from ..core.cutting_macro import CuttingMacroParams
from ..geometry.corner_detect import (
    detect_sharp_corners, classify_corners_for_tooling
)
from .base import PostProcessor, PostOptions, PostRegistry


class PackageExporter:
    """Экспортирует Project в пакет .anc файлов по схеме Cutting-макроса."""
    
    def __init__(self, project: Project, params: CuttingMacroParams,
                 post_name: str = "MTX Anderson GVM V2.13"):
        self.project = project
        self.params = params
        self.post: PostProcessor = PostRegistry.get(post_name)
        if self.post is None:
            raise ValueError(f"Постпроцессор '{post_name}' не зарегистрирован. "
                             f"Доступны: {PostRegistry.names()}")
    
    # ─────────────────────────────────────────────────────────────────────
    #  ОСНОВНОЙ МЕТОД
    # ─────────────────────────────────────────────────────────────────────
    
    def generate(self, progress_callback=None) -> Dict[str, str]:
        """Возвращает словарь {имя_файла: содержимое}.
        
        Args:
            progress_callback: опциональный callable(current, total, stage_name) -> bool.
                Вызывается перед началом каждого этапа генерации. current=N-1, 
                total=число включённых этапов. Возвращает False → отмена.
        """
        files: Dict[str, str] = {}
        
        prefix = self.params.output_prefix or self.project.name or "OUTPUT"
        angle = int(self.params.knife_angle)
        
        # ── ФИЛЬТР EXCLUDED ──
        # Операции с attribute 'excluded' = True исключаются из экспорта.
        # Временно убираем их из project.operations, восстановим в конце.
        from ..core.project import OperationKind
        _all_ops = list(self.project.operations)
        
        # СОХРАНЯЕМ юзерские lead_override с corner-ops в проекте ДО их 
        # удаления. _build_corner_operations() построит фрагменты заново, 
        # но по (parent_geom_id, corner_index) достанем сохранённый override
        # и перенесём на новые.
        self._corner_overrides = {
            (op.attributes.get('parent_geom_id'), 
             op.attributes.get('corner_index'),
             op.attributes.get('corner_is_3d', False)): 
             dict(op.attributes['lead_override'])
            for op in _all_ops
            if op.kind == OperationKind.CORNER_REWORK
            and 'lead_override' in op.attributes
        }
        
        # Также сохраняем KEY отключенных corner'ов — при пересборке 
        # фильтруем их из ANC. Иначе corner возвращается т.к. экспортер 
        # строит фрагменты заново из активных blade'ов, не зная какие 
        # corner'ы юзер снял в UI.
        self._corner_excluded = {
            (op.attributes.get('parent_geom_id'), 
             op.attributes.get('corner_index'),
             op.attributes.get('corner_is_3d', False))
            for op in _all_ops
            if op.kind == OperationKind.CORNER_REWORK
            and op.attributes.get('excluded', False)
        }
        
        # Аналогично для FIDUCIAL — сохраняем set fiducial_id снятых юзером
        # реперов И реперов ДРУГИХ заказов сшивки. Иначе _generate_rough_all
        # пересобирает drill_op из ПОЛНОГО списка prj.fiducials (без учёта
        # stitch_filtered_out) и сверлит все реперы стички, а не только
        # текущего заказа.
        self._fiducial_excluded_ids = {
            op.attributes.get('fiducial_id')
            for op in _all_ops
            if op.kind == OperationKind.FIDUCIAL_DRILL
            and (op.attributes.get('excluded', False)
                 or op.attributes.get('stitch_filtered_out', False))
            and op.attributes.get('fiducial_id')
        }
        
        # Полное число ножей ДО фильтра excluded, но В ПРЕДЕЛАХ scope
        # текущего экспорта: stitch_filtered_out исключён из счёта. Иначе
        # маленький заказ на большой сшивке (например 6/25) ошибочно
        # триггерит режим доработки _dop.anc, потому что active_n=6 <
        # 25*0.5. Смысл dop-порога — «оператор снял большинство галок
        # для доработки», а не «заказ занимает малую долю сшивки».
        self._total_blade_count = sum(
            1 for op in _all_ops
            if op.kind == OperationKind.BLADE_FORMING
            and not op.attributes.get('stitch_filtered_out', False))
        # Отбрасываем:
        #  - операции с галкой excluded (сняты в UI);
        #  - НАКОПЛЕННЫЕ CORNER_REWORK: они могли быть добавлены в проект при
        #    предпросмотре путей. На экспорте угловые программы (_corner/_corner3D)
        #    генерируются ЗАНОВО из BLADE-операций, поэтому стрэй-углы нужно
        #    убрать, иначе они протекут в черновую/чистовые как полные контуры.
        self.project.operations = [
            op for op in _all_ops 
            if not op.attributes.get('excluded', False)
            and not op.attributes.get('stitch_filtered_out', False)
            and op.kind != OperationKind.CORNER_REWORK
        ]
        excluded_count = len(_all_ops) - len(self.project.operations)
        if excluded_count:
            import sys as _sys
            if _sys.stderr is not None:
                try:
                    _sys.stderr.write(
                        f"Исключено из экспорта (галочки сняты): {excluded_count} операций\n")
                except Exception:
                    pass
        
        try:
            return self._generate_impl(files, prefix, angle, progress_callback)
        finally:
            # Восстанавливаем исходный список операций
            self.project.operations = _all_ops
    
    def _generate_impl(self, files: dict, prefix: str, angle: int,
                       progress_callback=None) -> Dict[str, str]:
        from ..core.project import OperationKind
        # ── ПРОВЕРКА СОВМЕСТИМОСТИ ФРЕЗЫ С МАКЕТОМ ──
        # Формула эквидистанты: tip + 2 * bottom * tan(angle/2)
        # где bottom = ABS = глубина реза от вершины ножа.
        # При уменьшении ABS путь приближается к контуру (радиус 
        # уменьшается) — это ожидаемое поведение оператора.
        import math
        half_angle_rad = math.radians(self.params.knife_angle / 2.0)
        tool_eq = self.params.tip_diameter + 2.0 * self.params.bottom * math.tan(half_angle_rad)
        tool_offset = tool_eq / 2.0
        
        problem = self.check_tool_fits_layout(tool_offset)
        if problem:
            import sys
            # ВАЖНО: под .pyw (pythonw) sys.stderr == None — пишем только если есть
            if sys.stderr is not None:
                try:
                    sys.stderr.write(f"ВНИМАНИЕ: {problem}\n")
                except Exception:
                    pass
            # Сохраняем для GUI
            self._fit_warning = problem
        else:
            self._fit_warning = None
        
        # ── АНАЛИЗ МАКЕТА: общая сторона для OUTSIDE заходов ──
        # Все ножи будут иметь заходы на ОДНОЙ стороне (предсказуемость 
        # для оператора). Сторона выбирается по максимальному суммарному
        # зазору к соседям по этой стороне.
        preferred_side = self._analyze_layout_lead_side()
        from ..core.project import OperationKind
        for op in self.project.operations:
            if op.kind == OperationKind.BLADE_FORMING:
                op.attributes['preferred_lead_side'] = preferred_side
        
        # ── Прогресс: считаем этапы ──
        p = self.params
        stages = []
        if p.generate_rough_all:  stages.append('rough_all')
        if p.generate_reverse:    stages.append('rough_revers')
        if p.generate_finish_per_op: stages.append('finish_group')
        if p.generate_sv:         stages.append('sv')
        if p.generate_corner:     stages.append('corner')
        if p.generate_corner_3d:  stages.append('corner3d')
        total_stages = len(stages)
        current_stage = [0]  # mutable для closure
        
        def _report(name):
            if progress_callback is None: return True
            result = progress_callback(current_stage[0], total_stages, name)
            current_stage[0] += 1
            return result if result is not None else True
        
        # ── 1. Черновая всех элементов (_all_R.anc) ──
        if self.params.generate_rough_all:
            if not _report("Черновая всех"): return files
            name = f"{prefix}_{angle}_all_R.anc"
            content = self._generate_rough_all()
            files[name] = content
        
        # ── 2. Черновая в реверсе (_revers_R.anc) ──
        if self.params.generate_reverse:
            if not _report("Реверс черновая"): return files
            name = f"{prefix}_{angle}_revers_R.anc"
            content = self._generate_rough_all(reverse=True)
            files[name] = content
        
        # ── РЕШЕНИЕ: обычные чистовые _N_M или режим доработки _dop ──
        # «Активные» ножи = те, что прошли фильтр excluded (галка стоит) и
        # enabled. «Всего» = полное число ножей ДО фильтра (self._total_blade_count,
        # запоминается в generate()). Если активна меньшая доля, чем
        # dop_threshold_ratio — собираем один _dop.anc только по активным
        # ножам, а пофайловые чистовые _N_M пропускаем.
        active_blades = [o for o in self.project.operations
                         if o.kind == OperationKind.BLADE_FORMING and o.enabled]
        total_n = getattr(self, '_total_blade_count', len(active_blades))
        active_n = len(active_blades)
        use_dop = (
            self.params.generate_finish_per_op
            and total_n > 0
            and 0 < active_n < total_n * self.params.dop_threshold_ratio
        )

        # ── 3. Чистовые программы (_N_M.anc), группировка по длине ──
        # Детали группируются в программы по накопленной длине путей.
        # Порядок обхода задаётся направлением (горизонталь=строки,
        # вертикаль=столбцы). Длинная строка делится, короткие объединяются.
        if self.params.generate_finish_per_op and not use_dop:
            if not _report("Чистовые"): return files
            from ..core.macros import assign_program_numbers
            
            dir_str = self.params.direction.value
            assign_program_numbers(
                self.project,
                max_geom_len=self.params.max_geom_len,
                direction=dir_str,
                corridor_tolerance=self.params.corridor_tolerance,
                passes_per_part=2,  # внутр. + внешн.
            )
            
            # Группируем операции по program_number
            # ВАЖНО: FIDUCIAL_DRILL сюда НЕ включаем — drill эмитится
            # только в _all_R (прямой черновой). Иначе первый _N_M-файл
            # (тот, что содержит операции с program_number=1) получит
            # прошивку реперов вдобавок к чистовому проходу.
            from collections import OrderedDict
            prog_groups: "OrderedDict[int, list]" = OrderedDict()
            for op in self.project.operations:
                if op.kind == OperationKind.FIDUCIAL_DRILL:
                    continue
                pn = op.attributes.get('program_number', 1)
                prog_groups.setdefault(pn, []).append(op)
            
            # Один файл на программу
            for i, (pn, ops_in_prog) in enumerate(prog_groups.items(), start=1):
                name = f"{prefix}_{angle}_{i}_M.anc"
                content = self._generate_with_operations(
                    ops_in_prog, program_name=f"{prefix}_{angle}_{i}_M")
                files[name] = content
        
        # ── 4. 4 угловых элемента для контроля сведения (_SV.anc) ──
        if self.params.generate_sv:
            if not _report("4 угловых"): return files
            name = f"{prefix}_{angle}_SV.anc"
            content = self._generate_sv()
            files[name] = content
        
        # ── 5. Острые углы тонкой 2D-фрезой (_corner.anc) ──
        # ── 6. Самые острые углы 3D-фрезой (_corner3D.anc) ──
        if self.params.generate_corner or self.params.generate_corner_3d:
            corner_ops_2d, corner_ops_3d = self._build_corner_operations()
            
            # Перенос lead_override с уже добавленных corner-ops в проекте 
            # (там юзер редактирует их через режим «Выделенные») на свежие 
            # ops. Переносим по (parent_geom_id, corner_index) из 
            # self._corner_overrides — снапшот сделан до strip'а corner-ops.
            for new_op in corner_ops_2d + corner_ops_3d:
                key = (new_op.attributes.get('parent_geom_id'),
                       new_op.attributes.get('corner_index'),
                       new_op.attributes.get('corner_is_3d', False))
                ov = self._corner_overrides.get(key)
                if ov is not None:
                    new_op.attributes['lead_override'] = dict(ov)
            
            # Фильтруем отключенные corner'ы — юзер снял с них галки в 
            # operations-таблице или через ПКМ на канвасе.
            def _corner_not_excluded(new_op):
                key = (new_op.attributes.get('parent_geom_id'),
                       new_op.attributes.get('corner_index'),
                       new_op.attributes.get('corner_is_3d', False))
                return key not in self._corner_excluded
            
            corner_ops_2d = [op for op in corner_ops_2d if _corner_not_excluded(op)]
            corner_ops_3d = [op for op in corner_ops_3d if _corner_not_excluded(op)]
            
            if self.params.generate_corner:
                if not _report("Углы 2D"): return files
                name = f"{prefix}_{angle}_corner.anc"
                if corner_ops_2d:
                    content = self._generate_with_operations(corner_ops_2d)
                else:
                    content = self._generate_empty_program(
                        f"{prefix}_{angle}_corner",
                        comment="No sharp corners detected for 2D thin tool"
                    )
                files[name] = content
            
            if self.params.generate_corner_3d:
                if not _report("Углы 3D"): return files
                name = f"{prefix}_{angle}_corner3D.anc"
                if corner_ops_3d:
                    content = self._generate_with_operations(corner_ops_3d)
                else:
                    content = self._generate_empty_program(
                        f"{prefix}_{angle}_corner3D",
                        comment="No very sharp corners detected for 3D tool"
                    )
                files[name] = content
        
        # ── 7. Режим доработки: _dop.anc (только активные ножи, чистовой) ──
        # Пишется ПОСЛЕДНИМ и ТОЛЬКО когда активна меньшая доля ножей, чем
        # порог (dop_threshold_ratio). Заменяет собой пофайловые чистовые _N_M.
        if use_dop:
            name = f"{prefix}_{angle}_dop.anc"
            content = self._generate_with_operations(
                active_blades, program_name=f"{prefix}_{angle}_dop")
            files[name] = content
        
        return files
    
    def write_files(self, output_dir: str,
                    files: Dict[str, str]) -> List[Path]:
        """Записывает УЖЕ сгенерированный набор файлов в папку, с проверкой
        факта записи. Отделено от generate(), чтобы сначала убедиться, что
        генерация удалась (и только потом трогать диск/архивировать)."""
        out = Path(output_dir)
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"Не удалось создать/открыть папку:\n{out}\n\n{e}\n\n"
                f"Проверьте права и доступность сетевого диска.")
        if not files:
            raise RuntimeError(
                "Нет файлов для записи. Проверьте, что есть активные ножи "
                "(галки) и включены типы программ.")
        written = []
        for filename, content in files.items():
            path = out / filename
            try:
                path.write_text(content, encoding='utf-8')
            except OSError as e:
                raise RuntimeError(
                    f"Не удалось записать файл:\n{path}\n\n{e}\n\n"
                    f"Похоже на проблему с сетевым диском или правами доступа.")
            if not path.exists() or path.stat().st_size == 0:
                raise RuntimeError(
                    f"Файл не материализовался после записи:\n{path}\n\n"
                    f"Вероятно, сетевой диск {out.anchor} недоступен для записи.")
            written.append(path)
        return written

    def write_all(self, output_dir: str) -> List[Path]:
        """Генерирует и записывает все файлы пакета в указанную директорию.
        Пишет РОВНО в output_dir (никаких жёстких путей и архивов)."""
        files = self.generate()
        if not files:
            raise RuntimeError(
                "Генератор не вернул ни одного файла. Проверьте, что есть "
                "активные ножи (галки) и включены типы программ.")
        return self.write_files(output_dir, files)
    
    # ─────────────────────────────────────────────────────────────────────
    #  ГЕНЕРАЦИЯ КОНКРЕТНЫХ ФАЙЛОВ
    # ─────────────────────────────────────────────────────────────────────
    
    def _build_post_options(self, program_name: str) -> PostOptions:
        """Базовые опции поста на основе параметров макроса."""
        opts = PostOptions(
            program_name=program_name,
            sheet_thickness=self.params.top,
            z_depth=self.params.bottom,
            fiducial_distance=getattr(self.params, 'fiducial_distance', 700.0),
            include_fiducial_marks=False,  # drill теперь через операцию
            include_video_block=True,
        )
        # Радиус инструмента — для масштабирования Lead-In/Out
        opts.extras['tool_radius'] = self.params.tip_diameter / 2.0
        # Угол инструмента — для SD.WZRec.UD.Ed[1].Geo.Ang
        opts.extras['tool_angle'] = self.params.knife_angle
        # Эквидистанта = d_tip + 2·bottom·tan(угол/2)
        # где bottom = ABS = глубина реза от вершины ножа.
        # При уменьшении ABS путь приближается к контуру.
        import math
        half_angle_rad = math.radians(self.params.knife_angle / 2.0)
        tool_eq = self.params.tip_diameter + 2.0 * self.params.bottom * math.tan(half_angle_rad)
        opts.extras['tool_equidistant'] = tool_eq
        opts.extras['smooth_offset_for_tool'] = bool(
            getattr(self.params, 'smooth_offset_for_tool', False))
        return opts
    
    def _apply_lead_params(self, project: Project) -> None:
        """Применяет к ToolPath'ам операций параметры Lead-In/Out из 
        CuttingMacroParams (поля lead_inside / lead_outside).
        
        Это связывает диалог Cutting с фактической геометрией заходов:
        внутренний проход получает entry/exit из lead_inside,
        внешний — из lead_outside.
        """
        from ..core.project import (
            EntryExitConfig, ContourSide, LeadStyle,
        )
        
        for op in project.operations:
            for tp in op.toolpaths:
                # Привязка параметров к ФИЗИЧЕСКОМУ резу (не к имени прохода):
                # после фикса компенсации (оба G41) проход INSIDE (CW) режет
                # ВНЕШНИЙ контур, а OUTSIDE (CCW) — ВНУТРЕННИЙ. Поэтому
                # параметры «Внутренний» (lead_inside) идут на проход OUTSIDE,
                # а «Внешний» (lead_outside) — на INSIDE.
                if tp.side in (ContourSide.OUTSIDE, ContourSide.RIGHT):
                    src = self.params.lead_inside   # внутренний рез
                elif tp.side in (ContourSide.INSIDE, ContourSide.LEFT):
                    src = self.params.lead_outside  # внешний рез
                else:
                    continue
                
                # Создаём EntryExitConfig из LeadInOutParams
                # При экспорте знак offset уже отрицательный (как в диалоге)
                tp.entry = EntryExitConfig(
                    enabled=True,
                    style=LeadStyle.LINE_ARC_TANGENTIAL,
                    line_length_x_tool_rad=src.length,
                    arc_radius_x_tool_rad=src.length,  # часто совпадают
                    approach_angle=src.angle,
                    start_offset=src.offset,
                    user_set_offset=getattr(src, 'user_set_offset', False),
                )
                tp.exit = EntryExitConfig(
                    enabled=True,
                    style=LeadStyle.LINE_ARC_TANGENTIAL,
                    line_length_x_tool_rad=src.length,
                    arc_radius_x_tool_rad=src.length,
                    approach_angle=src.angle,
                    start_offset=src.offset,
                    user_set_offset=getattr(src, 'user_set_offset', False),
                    overlap=getattr(src, 'overlap', 0.0),
                )
    
    def _generate_rough_all(self, reverse: bool = False) -> str:
        """Черновая программа: все операции одна за другой.
        
        ПРЯМАЯ (_all_R):
            - Порядок обхода = от LB (левый нижний), направление как в params
            - horizontal: строки снизу вверх, внутри слева направо
            - vertical:   столбцы слева направо, внутри снизу вверх
            - Добавляется DRILL реперов в конец.
        
        РЕВЕРС (_revers_R):
            - Порядок обхода = от RT (правый верхний), направление 
              ПЕРПЕНДИКУЛЯРНО прямой
            - Если прямая horizontal → реверс vertical (столбцы справа налево,
              внутри сверху вниз)
            - Если прямая vertical → реверс horizontal (строки сверху вниз,
              внутри справа налево)
            - DRILL НЕ добавляется (одной программы достаточно).
        """
        prj = copy.deepcopy(self.project)
        self._apply_lead_params(prj)
        
        # ── Упорядочивание операций ──
        from ..core.macros import operation_center
        centers = {op.id: operation_center(op, prj) for op in prj.operations}
        # POSITION-хук: если задан self._center_transform, координаты центров
        # прогоняются через него ПЕРЕД сортировкой. Это позволяет получить
        # порядок обхода ножей в POSITION-виде (после -90° поворота столбцы
        # исходной сшивки становятся строками, направление user-setting'а
        # переносится корректно). Обычный экспорт не задаёт хук, сортировка
        # идёт по оригинальным центрам как раньше.
        _ct = getattr(self, '_center_transform', None)
        if _ct is not None:
            centers = {op_id: _ct(c) for op_id, c in centers.items()}
        direction = self.params.direction.value  # "horizontal" | "vertical"
        
        if not reverse:
            # ПРЯМАЯ: от LB, направление = direction
            if direction == "horizontal":
                # Строки снизу вверх, внутри слева направо
                corridor_key = lambda op: centers[op.id][1]  # Y
                within_key = lambda op: centers[op.id][0]    # X
                corridor_desc = False
                within_desc = False
            else:  # vertical
                # Столбцы слева направо, внутри снизу вверх
                corridor_key = lambda op: centers[op.id][0]  # X
                within_key = lambda op: centers[op.id][1]    # Y
                corridor_desc = False
                within_desc = False
        else:
            # РЕВЕРС: от RT, направление ПЕРПЕНДИКУЛЯРНО direction
            if direction == "horizontal":
                # Прямая = строки → реверс = столбцы, СПРАВА НАЛЕВО,
                # внутри столбца СВЕРХУ ВНИЗ
                corridor_key = lambda op: centers[op.id][0]  # X
                within_key = lambda op: centers[op.id][1]    # Y
                corridor_desc = True   # столбцы справа налево (X убыв.)
                within_desc = True     # сверху вниз (Y убыв.)
            else:  # vertical
                # Прямая = столбцы → реверс = строки, СВЕРХУ ВНИЗ,
                # внутри строки СПРАВА НАЛЕВО
                corridor_key = lambda op: centers[op.id][1]  # Y
                within_key = lambda op: centers[op.id][0]    # X
                corridor_desc = True   # строки сверху вниз
                within_desc = True     # справа налево
        
        # Группировка в коридоры по близости координаты
        sorted_by_corr = sorted(
            prj.operations, key=corridor_key, reverse=corridor_desc)
        corridors = []
        cur_group = []
        last_coord = None
        tol = self.params.corridor_tolerance
        for op in sorted_by_corr:
            c = corridor_key(op)
            if last_coord is None or abs(c - last_coord) <= tol:
                cur_group.append(op)
            else:
                corridors.append(cur_group)
                cur_group = [op]
            last_coord = c
        if cur_group:
            corridors.append(cur_group)
        
        # Внутри каждого коридора сортируем по within_key
        ordered = []
        for group in corridors:
            group.sort(key=within_key, reverse=within_desc)
            ordered.extend(group)
        
        prj.operations = ordered
        
        # Все операции в один SHAPE
        for op in prj.operations:
            op.sequence_number = 1
        
        # Стратегия: DRILL реперов добавляется ТОЛЬКО в прямую черновую
        # (_all_R). В reverse-программе (_revers_R) реперы уже насверлены
        # прямой, поэтому здесь их не эмитим — иначе засверлимся дважды.
        # Убираем существующие FIDUCIAL_DRILL из prj.operations на этом
        # проходе (в prima ниже они пересобираются заново).
        if reverse:
            prj.operations = [
                op for op in prj.operations
                if op.kind != OperationKind.FIDUCIAL_DRILL
            ]
        
        # Стратегия: DRILL реперов добавляется ТОЛЬКО в прямую черновую.
        # Учитываем что юзер мог отключить конкретные реперы в UI 
        # (per-fiducial FIDUCIAL_DRILL операции сняты галкой). Их 
        # fiducial_id сохранены в self._fiducial_excluded_ids до strip'а.
        if not reverse:
            # Временно фильтруем project.fiducials по excluded flag
            excluded = getattr(self, '_fiducial_excluded_ids', set())
            all_fids = prj.fiducials
            prj.fiducials = [f for f in all_fids if f.id not in excluded]
            
            drill_op = prj.make_fiducial_drill_operation(
                tool_number=1, drill_depth=0.1)
            if drill_op is not None:
                drill_op.sequence_number = 1
                prj.operations.append(drill_op)
            
            # Восстанавливаем исходный список (deepcopy изолирует, но 
            # для чистоты — вернём)
            prj.fiducials = all_fids
        
        prefix = self.params.output_prefix or prj.name or "OUTPUT"
        angle = int(self.params.knife_angle)
        suffix = "revers_R" if reverse else "all_R"
        opts = self._build_post_options(f"{prefix}_{angle}_{suffix}")
        
        return self.post.generate(prj, opts)
    
    def _generate_single_operation(self, op: Operation, op_number: int) -> str:
        """Чистовая программа для одной операции (один M-файл)."""
        prj = copy.deepcopy(self.project)
        self._apply_lead_params(prj)
        # Только эта одна операция (по индексу в исходном проекте)
        # Найдём её в копии по тому же id
        single_op = next((o for o in prj.operations if o.id == op.id), None)
        if single_op is None:
            return self._generate_empty_program(
                f"op{op_number}", comment="operation not found"
            )
        
        single_op.sequence_number = 1
        prj.operations = [single_op]
        
        prefix = self.params.output_prefix or prj.name or "OUTPUT"
        angle = int(self.params.knife_angle)
        opts = self._build_post_options(f"{prefix}_{angle}_{op_number}_M")
        
        return self.post.generate(prj, opts)
    
    def _generate_sv(self) -> str:
        """4 угловых элемента — для контроля сведения координат на станке.
        
        Берём 4 операции из углов раскладки (left-top, right-top, 
        left-bottom, right-bottom) по центру bbox каждой операции.
        """
        if not self.project.operations:
            return self._generate_empty_program(
                "SV", comment="No operations for SV check")
        
        # Считаем центры всех операций
        # ВАЖНО: FIDUCIAL_DRILL пропускаем — SV показывает 4 крайних НОЖА
        # для контроля сведения, реперы туда не входят.
        from ..core.macros import operation_center
        op_centers = [(op, operation_center(op, self.project))
                      for op in self.project.operations
                      if op.kind != OperationKind.FIDUCIAL_DRILL]
        # POSITION-хук: см. пояснение в _generate_rough_all
        _ct = getattr(self, '_center_transform', None)
        if _ct is not None:
            op_centers = [(op, _ct(c)) for op, c in op_centers]
        
        if len(op_centers) <= 4:
            sv_ops = [op for op, _ in op_centers]
        else:
            # 4 крайних угла
            min_x = min(c[0] for _, c in op_centers)
            max_x = max(c[0] for _, c in op_centers)
            min_y = min(c[1] for _, c in op_centers)
            max_y = max(c[1] for _, c in op_centers)
            
            def closest_to(tx, ty):
                return min(op_centers, key=lambda oc: 
                           (oc[1][0] - tx)**2 + (oc[1][1] - ty)**2)
            
            lt = closest_to(min_x, max_y)
            rt = closest_to(max_x, max_y)
            lb = closest_to(min_x, min_y)
            rb = closest_to(max_x, min_y)
            
            # Уникальные (мог попасть один и тот же)
            seen = set()
            sv_ops = []
            for op, _ in (lt, rt, lb, rb):
                if op.id not in seen:
                    sv_ops.append(op)
                    seen.add(op.id)
        
        prj = copy.deepcopy(self.project)
        # Берём только выбранные SV операции
        sv_ids = {op.id for op in sv_ops}
        prj.operations = [op for op in prj.operations if op.id in sv_ids]
        for op in prj.operations:
            op.sequence_number = 1
        
        prefix = self.params.output_prefix or prj.name or "OUTPUT"
        angle = int(self.params.knife_angle)
        opts = self._build_post_options(f"{prefix}_{angle}_SV")
        
        return self.post.generate(prj, opts)
    
    def _build_corner_operations(self) -> Tuple[List[Operation], List[Operation]]:
        """Находит углы по геометрии (дуги с малым радиусом скругления) и
        создаёт операции CORNER_REWORK для каждого ножа.
        
        Правило (от пользователя): если радиус скругления угла МЕНЬШЕ
        0.7мм, то основная фреза (пятка 0.8/1.2мм) не может выточить такой
        угол — нужна тонкая фреза 0.6мм. Если угол ПОЛНОСТЬЮ острый
        (без скругления, выявляется отдельным правилом) — нужна 3D-фреза.
        
        Returns:
            (operations_for_corner_2d, operations_for_corner_3d)
        """
        from ..core.project import (
            Operation, OperationKind, CutSettings, ToolPath, ContourSide,
            LeadStyle, EntryExitConfig, PassType
        )
        from ..geometry.corner_detect import (detect_geometric_corners,
                                               group_corner_arcs)
        
        # Порог радиуса — динамический, зависит от реального радиуса фрезы.
        # Если у скругления R >= tool_offset, то основная фреза физически 
        # проходит внутри → corner_rework НЕ НУЖЕН.
        # Раньше был фикс 0.7мм → детектились «углы» даже там, где фреза 
        # 0.8 с угом 70° и ABS 0.25 (tool_offset=0.575) вполне проходит.
        # 
        # Формула tool_offset уже посчитана в _build_post_options:
        # tool_offset = tip/2 + ABS * tan(angle/2)
        import math
        half_angle_rad = math.radians(self.params.knife_angle / 2.0)
        tool_offset = (self.params.tip_diameter / 2.0 
                       + self.params.bottom * math.tan(half_angle_rad))
        # Порог = actual_tool_offset + небольшой запас на неточность биарк-
        # аппроксимации Illustrator'а. Юзер задаёт `corner_radius_threshold_mm`
        # как ФИКСИРОВАННЫЙ верхний предел — уважаем если он МЕНЬШЕ 
        # динамического (юзер хочет более строгий отбор).
        dynamic_threshold = tool_offset
        radius_threshold_2d = min(
            self.params.corner_radius_threshold_mm,
            dynamic_threshold
        )
        
        ops_2d: List[Operation] = []
        ops_3d: List[Operation] = []
        
        # Перебираем существующие BLADE_FORMING операции (в их текущем 
        # порядке — после sort_by_grid). Это даёт «по ножам» обход углов.
        # ВАЖНО для сшивок: пропускаем ноги отфильтрованных заказов —
        # углы для соседних заказов НЕ создаём вообще (не только не 
        # выводим). Иначе они могут случайно попасть в _corner.anc и 
        # запортить соседний заказ на плите.
        blade_ops = [op for op in self.project.operations 
                     if op.kind == OperationKind.BLADE_FORMING
                     and not op.attributes.get('stitch_filtered_out', False)]
        
        for blade_op in blade_ops:
            if not blade_op.geometry_ids:
                continue
            geom_id = blade_op.geometry_ids[0]
            geom = self.project.get_geometry(geom_id)
            if not geom or not geom.polypath:
                continue
            
            small_arcs = detect_geometric_corners(
                geom.polypath, 
                radius_threshold_mm=radius_threshold_2d
            )
            if not small_arcs:
                continue
            
            groups = group_corner_arcs(small_arcs, proximity_mm=2.0)
            
            for grp_idx, grp in enumerate(groups):
                # Параметры резания для тонкой фрезы
                settings = CutSettings(
                    tool_number=self.params.corner_tool_number,
                    pass_type=PassType.SINGLE,
                    feed_cut=1500,
                    feed_plunge=800,
                    prog_z_depth=0.3,
                )
                op = Operation(
                    name=f"{blade_op.name} corner #{grp_idx+1}",
                    kind=OperationKind.CORNER_REWORK,
                    geometry_ids=[geom_id],
                    settings=settings,
                    sequence_number=1,
                )
                # Метаданные о группе для постпроцессора и дедупликации UI
                op.attributes['parent_geom_id'] = geom_id
                op.attributes['corner_index'] = grp_idx
                op.attributes['corner_first_idx'] = grp.first_idx
                op.attributes['corner_last_idx'] = grp.last_idx
                op.attributes['corner_center'] = grp.center
                op.attributes['corner_radius'] = grp.radius
                op.attributes['corner_apex'] = grp.apex
                op.attributes['corner_ccw'] = grp.ccw
                
                tp = ToolPath(
                    geometry_id=geom_id,
                    side=ContourSide.OUTSIDE,  # внутренний рез (CW) — corner rework часть внутр. реза
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
                ops_2d.append(op)
        
        # 3D углы — те же ФРАГМЕНТЫ как 2D, ДРУГАЯ детекция и инструмент:
        # - 2D: малые дуги (R < 0.7мм)
        # - 3D: острые изломы Line→Line (расхождение > 30°)
        # - В обоих случаях: фрагмент контура + lead-in/out (как крючок)
        # - Отличие: для 3D в шапке ToolType=1 → станок делает погружение/
        #   подъём фрезы в каждом проходе (3D режим работы).
        from ..geometry.corner_detect import detect_pointed_corners
        
        for blade_op in blade_ops:
            if not blade_op.geometry_ids:
                continue
            geom_id = blade_op.geometry_ids[0]
            geom = self.project.get_geometry(geom_id)
            if not geom or not geom.polypath:
                continue
            
            pointed = detect_pointed_corners(
                geom.polypath, 
                sharp_threshold_deg=30.0
            )
            if not pointed:
                continue
            
            for p_idx, pc in enumerate(pointed):
                settings = CutSettings(
                    tool_number=self.params.corner_3d_tool_number,
                    pass_type=PassType.SINGLE,
                    feed_cut=1000,
                    feed_plunge=600,
                    prog_z_depth=0.45,
                )
                op = Operation(
                    name=f"{blade_op.name} corner3D #{p_idx+1}",
                    kind=OperationKind.CORNER_REWORK,
                    geometry_ids=[geom_id],
                    settings=settings,
                    sequence_number=1,
                )
                # Метаданные: точка вершины острого угла + индекс сегмента
                # ЗА которым она находится (для корректного поиска по контуру)
                op.attributes['parent_geom_id'] = geom_id
                op.attributes['corner_index'] = p_idx
                op.attributes['corner3d_point'] = pc.point
                op.attributes['corner3d_segment_index'] = pc.segment_index
                op.attributes['corner3d_interior_angle'] = pc.interior_angle
                op.attributes['corner_is_3d'] = True
                # НЕ ставим corner_first_idx/last_idx — для 3D углов
                # эмиттер использует extract_subpath_around_point.
                
                tp = ToolPath(
                    geometry_id=geom_id,
                    side=ContourSide.OUTSIDE,  # внутренний рез (CW) — corner rework часть внутр. реза
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
                ops_3d.append(op)
        
        return ops_2d, ops_3d
    
    def _make_corner_op(self, geometry_id: str, start_t: float, end_t: float,
                        tool_number: int, name_prefix: str = "Corner"
                        ) -> Operation:
        """Создаёт операцию доработки тонкой/3D-фрезой."""
        from ..core.project import (
            Operation, OperationKind, CutSettings, ToolPath, ContourSide
        )
        settings = CutSettings(tool_number=tool_number)
        op = Operation(
            name=f"{name_prefix} T{tool_number}",
            kind=OperationKind.CORNER_REWORK,
            geometry_ids=[geometry_id],
            settings=settings,
            sequence_number=1,
        )
        op.toolpaths = [
            ToolPath(
                geometry_id=geometry_id,
                side=ContourSide.OUTSIDE,
                start_t=start_t,
                end_t=end_t,
            ),
        ]
        return op
    
    def _analyze_layout_lead_side(self) -> str:
        """Анализирует макет и возвращает ОДНУ сторону для захода OUTSIDE
        у всех ножей. Эта сторона должна давать наибольший зазор в среднем
        по всем ножам (предсказуемость для оператора).
        
        Для каждой стороны (TOP/BOTTOM/RIGHT/LEFT) считаем СУММАРНЫЙ зазор
        по всем ножам, выбираем максимальную.
        """
        from ..core.project import OperationKind
        from ..geometry.path_offset import polypath_bbox
        
        blade_ops = [op for op in self.project.operations 
                     if op.kind == OperationKind.BLADE_FORMING]
        if not blade_ops:
            return 'TOP'  # дефолт
        
        # Собираем все bbox
        bboxes = []
        for op in blade_ops:
            if not op.geometry_ids: continue
            g = self.project.get_geometry(op.geometry_ids[0])
            if g and g.polypath:
                bboxes.append(polypath_bbox(g.polypath))
        if not bboxes:
            return 'TOP'
        
        # Суммируем зазоры по каждой стороне
        gaps = {'TOP': 0.0, 'BOTTOM': 0.0, 'RIGHT': 0.0, 'LEFT': 0.0}
        BIG = 1e6
        for own in bboxes:
            ox0, oy0, ox1, oy1 = own
            g_top = BIG
            g_bot = BIG
            g_right = BIG
            g_left = BIG
            for nb in bboxes:
                if nb is own: continue
                nx0, ny0, nx1, ny1 = nb
                # верх — сосед выше с пересечением X
                if ny0 >= oy1 and nx1 > ox0 and nx0 < ox1:
                    g_top = min(g_top, ny0 - oy1)
                if ny1 <= oy0 and nx1 > ox0 and nx0 < ox1:
                    g_bot = min(g_bot, oy0 - ny1)
                if nx0 >= ox1 and ny1 > oy0 and ny0 < oy1:
                    g_right = min(g_right, nx0 - ox1)
                if nx1 <= ox0 and ny1 > oy0 and ny0 < oy1:
                    g_left = min(g_left, ox0 - nx1)
            # Подменяем БЕСКОНЕЧНОСТЬ конкретным большим числом для усреднения
            # (если у ножа нет соседа сверху — это «свободно сверху»)
            # Ограничим 100мм чтобы один край-нож не доминировал
            gaps['TOP'] += min(g_top, 100.0)
            gaps['BOTTOM'] += min(g_bot, 100.0)
            gaps['RIGHT'] += min(g_right, 100.0)
            gaps['LEFT'] += min(g_left, 100.0)
        
        # Сторона с максимальной суммой зазоров
        # При равенстве предпочтение TOP > BOTTOM > RIGHT > LEFT
        best = max(['TOP', 'BOTTOM', 'RIGHT', 'LEFT'], key=lambda s: gaps[s])
        return best
    
    def check_tool_fits_layout(self, tool_offset: float) -> Optional[str]:
        """Проверяет физически ли влезает эквидистанта фрезы между ножами.
        
        OUTSIDE-эквидистанта каждого ножа расширяет его контур на tool_offset
        с каждой стороны. Если между двумя ножами зазор меньше 2*tool_offset,
        то их эквидистанты ПЕРЕСЕКАЮТСЯ — физически фреза не может пройти.
        
        Алгоритм двухступенчатый:
        1) BBox-фильтр (быстро, ~10мс на 76 ножей): находим подозрительные
           пары с bbox-зазором < required.
        2) Точная проверка ТОЛЬКО для них через shapely.distance (~3мс/пара,
           всего обычно 10-30 пар → < 100мс).
        
        BBox даёт много ложных тревог на фигуристых контурах (большие пустые
        углы внутри bbox). Точная проверка их отсеивает.
        
        Args:
            tool_offset: смещение пути фрезы от программной геометрии (мм)
        
        Returns:
            None если фреза подходит, иначе сообщение об ошибке (str)
        """
        from ..core.project import OperationKind
        from ..geometry.path_offset import polypath_bbox, _sample_polypath_points
        
        # Только ножи слоя Knife. Тестовые/калибровочные контуры (L-Test и пр.)
        # в проверку фрезы НЕ входят — они режутся иначе или не режутся вовсе.
        blade_ops = [op for op in self.project.operations 
                     if op.kind == OperationKind.BLADE_FORMING]
        data = []  # (name, bbox, polypath)
        for op in blade_ops:
            if not op.geometry_ids: continue
            g = self.project.get_geometry(op.geometry_ids[0])
            if not g or not g.polypath:
                continue
            src = (getattr(g, 'source_layer', '') or '').strip().lower()
            if src and src != 'knife':
                continue
            data.append((op.name or g.name or 'unnamed',
                         polypath_bbox(g.polypath), g.polypath))
        if len(data) < 2:
            return None
        
        required = 2 * tool_offset
        
        # ── Шаг 1: BBox-фильтр ──
        # Подозрительная пара: ХОТЯ БЫ ОДИН зазор < required И bbox-ы
        # потенциально близко (расстояние между bbox-углами < required).
        suspect_pairs = []
        for i in range(len(data)):
            ax0, ay0, ax1, ay1 = data[i][1]
            for j in range(i + 1, len(data)):
                bx0, by0, bx1, by1 = data[j][1]
                # реальное расстояние между bbox-ами (0 если пересекаются)
                dx = max(0.0, max(ax0 - bx1, bx0 - ax1))
                dy = max(0.0, max(ay0 - by1, by0 - ay1))
                # если bbox-ы дальше required по диагонали — точно ок
                if (dx * dx + dy * dy) ** 0.5 >= required:
                    continue
                suspect_pairs.append((i, j))
        
        if not suspect_pairs:
            return None
        
        # ── Шаг 2: точная проверка через shapely.distance ──
        # Расстояние между РЕАЛЬНЫМИ контурами (не bbox-ами).
        try:
            from shapely.geometry import Polygon
        except Exception:
            # shapely недоступен — возвращаем bbox-предупреждение
            # (грубое, может быть ложным, но лучше чем ничего)
            i, j = suspect_pairs[0]
            return (f"Возможно фреза слишком большая (bbox-проверка между "
                    f"«{data[i][0]}» и «{data[j][0]}»). Установите shapely "
                    f"для точной проверки.")
        
        polys_cache = {}
        def _poly(idx):
            if idx not in polys_cache:
                pts = _sample_polypath_points(data[idx][2], chord_err_mm=0.05)
                polys_cache[idx] = Polygon(pts).buffer(0)
            return polys_cache[idx]
        
        violations = []  # (name_i, name_j, distance)
        for i, j in suspect_pairs:
            try:
                dist = _poly(i).distance(_poly(j))
            except Exception:
                continue
            if dist < required:
                violations.append((data[i][0], data[j][0], dist))
        
        if not violations:
            return None
        
        # Сортируем по тяжести (минимальное расстояние первое)
        violations.sort(key=lambda v: v[2])
        # Берём топ-3 чтобы не перегружать сообщение
        head = violations[:3]
        more = len(violations) - len(head)
        
        lines = []
        for name_i, name_j, dist in head:
            lines.append(f"между «{name_i}» и «{name_j}» зазор {dist:.2f}мм "
                         f"(нужно ≥{required:.2f}мм, не хватает "
                         f"{required - dist:.2f}мм)")
        suffix = f" и ещё {more} пар(ы)" if more > 0 else ""
        return (f"Фреза слишком большая для этого макета: "
                + "; ".join(lines) + suffix
                + f". Эквидистанта OUTSIDE = ±{tool_offset:.3f}мм, "
                "пути фрезы этих ножей будут пересекаться. "
                "Используйте меньшую пятку фрезы.")
    
    def _generate_with_operations(self, operations: List[Operation],
                                  program_name: str = None) -> str:
        """Генерирует .anc программу с указанным набором операций
        (используется для чистовых _N_M, corner и corner3D)."""
        prj = copy.deepcopy(self.project)
        prj.operations = list(operations)
        self._apply_lead_params(prj)
        for op in prj.operations:
            op.sequence_number = 1
        
        prefix = self.params.output_prefix or prj.name or "OUTPUT"
        angle = int(self.params.knife_angle)
        if program_name is None:
            program_name = f"{prefix}_{angle}_corner"
        opts = self._build_post_options(program_name)
        
        return self.post.generate(prj, opts)
    
    def _generate_empty_program(self, name: str, comment: str = "") -> str:
        """Заглушка для пустого файла (когда углов нет)."""
        return (
            f"; {name}.anc\n"
            f"; {comment}\n"
            f"; (no toolpaths)\n"
            f"M30\n"
        )
