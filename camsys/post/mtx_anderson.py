"""
post/mtx_anderson.py — постпроцессор для Anderson Europe GVM (MTX V2.13).

Воспроизводит формат файлов .anc, который генерирует Альфакам с постом
AEC_GVM_MTX_3D_V2_13.amp. Базируется на эталоне 118917_60_all_R.anc:

    1. Шапка с фирменным комментарием
    2. PRESETTINGS с системными переменными SSDE[SD.USR.*]
    3. ALIGN / TOOLADJUST / TOOLDATA (стандартные вызовы)
    4. G8 G17 G90 G47 с параметрами движения
    5. Видеорежим для CCD-привязки оператором
    6. SHAPE с PART для каждого ToolPath:
       - PREPART
       - G0 в точку входа
       - G1 Z=0 F=plunge (опускание на подложку)
       - G42/G41 + начало контура
       - G12 (плавный вход) если EntryExitConfig.enabled
       - G1/G2/G3 по сегментам
       - G13 (плавный выход)
       - Подъём Z
    7. Опционально: сверление меток реперов
    8. Концовка с COR()/ATR()/TRS() и M30
"""

from __future__ import annotations
from io import StringIO
from datetime import datetime
from typing import List

from .base import PostProcessor, PostMetadata, PostOptions, PostRegistry


def _seg_intersect(p1, p2, p3, p4) -> bool:
    """True если отрезки (p1,p2) и (p3,p4) СТРОГО пересекаются (не касание
    в конце/начале — используется для детекции реального пересечения lead
    с контуром)."""
    def ccw(A, B, C):
        return (C[1]-A[1])*(B[0]-A[0]) - (B[1]-A[1])*(C[0]-A[0])
    d1 = ccw(p3, p4, p1)
    d2 = ccw(p3, p4, p2)
    d3 = ccw(p1, p2, p3)
    d4 = ccw(p1, p2, p4)
    return (d1*d2 < 0) and (d3*d4 < 0)


def _lead_pts_for_check(lead, side_type: str, n_line=6, n_arc=9):
    """Точки lead (line+arc) в порядке от внешней точки к точке стыковки
    (side_type='in') или от стыковки к внешней (side_type='out').
    Возвращает (pts, join_index) где join_index — индекс точки стыковки
    с собственным контуром в pts."""
    line_pts = []
    arc_pts = []
    if hasattr(lead, 'line') and lead.line:
        for i in range(n_line):
            line_pts.append(lead.line.point_at(i / (n_line - 1)))
    if hasattr(lead, 'arc') and lead.arc:
        for i in range(n_arc):
            arc_pts.append(lead.arc.point_at(i / (n_arc - 1)))
    if side_type == 'in':
        pts = line_pts + arc_pts
        join_index = len(pts) - 1
    else:
        pts = arc_pts + line_pts
        join_index = 0
    return pts, join_index


def _lead_crosses_contour(lead, side_type: str, contour_pts,
                          exclude_join: bool) -> bool:
    """True если полилиния lead (line+arc) пересекает замкнутый контур
    contour_pts (список точек по периметру).

    exclude_join=True: сегмент(ы) lead, примыкающий к точке стыковки с
    СОБСТВЕННЫМ контуром, исключается из проверки (он естественно касается
    контура в этой точке — это не пересечение). Используется для own-contour.
    Для sibling-контура (другого INSIDE/OUTSIDE прохода) exclude_join=False —
    там join-точка собственного контура НЕ лежит на sibling, и пересечение
    там — реальная проблема (см. скрин: лиды двух проходов пересекают друг
    друга у тугого угла).
    """
    if not contour_pts or len(contour_pts) < 2:
        return False
    pts, join_index = _lead_pts_for_check(lead, side_type)
    if len(pts) < 2:
        return False
    m = len(contour_pts)
    for i in range(len(pts) - 1):
        if exclude_join:
            # Сегмент, один из концов которого — точка стыковки, пропускаем
            if i == join_index or i + 1 == join_index:
                continue
        a, b = pts[i], pts[i + 1]
        for j in range(m):
            c, d = contour_pts[j], contour_pts[(j + 1) % m]
            if _seg_intersect(a, b, c, d):
                return True
    return False


class MtxAndersonGVM(PostProcessor):
    """Постпроцессор Anderson Europe GVM с контроллером NUM Power MTX V2.13.
    
    Эта реализация максимально близко повторяет эталонный вывод
    AEC_GVM_MTX_3D_V2_13.amp — основано на анализе 118917_60_all_R.anc.
    """
    
    @property
    def metadata(self) -> PostMetadata:
        return PostMetadata(
            name="MTX Anderson GVM V2.13",
            machine="Anderson Europe GVM",
            controller="NUM Power MTX",
            file_extension=".anc",
            version="1.0",
            description="Anderson Europe GVM High Speed Engraving Machine, "
                        "MTX V2.13 controller, .anc output for V-bit blade forming.",
        )
    
    def generate(self, project, options: PostOptions) -> str:
        # Импортируем модели только в нужный момент, чтобы избежать 
        # циклических импортов
        from ..core.project import (Operation, ToolPath, ContourSide,
                                     PassType, OperationKind)
        from ..geometry.primitives import Line, Arc
        
        out = StringIO()
        w = lambda s='': out.write(s + '\n')
        
        # ── ПОДГОТОВКА ПАРАМЕТРОВ ──
        prg_name_upper = options.program_name.upper()
        now = datetime.now().strftime("%d %b %y - %H:%M").upper()
        # Радиус и эквидистанта инструмента из options
        tool_radius = options.extras.get('tool_radius', 0.6)
        tool_diameter = tool_radius * 2.0
        # Эквидистанта = радиус_кончика + h * tan(угол/2)
        # При угле 80°, h=0.25: tool_radius=0.4, equidistant = 0.4 + 0.25*tan(40°) = 0.4 + 0.21 = 0.61
        # Реальное значение в эталоне 1.11886 (для V80, d=0.8, h=0.438)
        # Возьмём из options.extras если задано, иначе вычислим
        tool_eq = options.extras.get('tool_equidistant',
                                     tool_diameter * 2.0)  # упрощённо
        tool_angle = int(options.extras.get('tool_angle', 80))
        
        # Определяем — это 3D программа?
        # 3D программа = ВСЕ операции CORNER_REWORK с corner_is_3d=True.
        # Тогда в станок передаётся ToolType=1 (3D режим — подъём фрезы).
        is_3d_program = False
        if project.operations:
            corner_3d_ops = [
                op for op in project.operations
                if op.kind == OperationKind.CORNER_REWORK
                and op.attributes.get('corner_is_3d')
            ]
            if len(corner_3d_ops) == len(project.operations):
                is_3d_program = True
        
        # ── 1. Шапка-комментарий (1:1 с эталоном) ──
        w("1 #DRYRUN%=0")
        w("2 SRLOCK%=SD.SysChSRun.LockTargBlock : "
          "SD.SysChSRun.LockTargBlock=14 ")
        w("N3 ;****************************************************")
        w("N4 ;** AEC ANDERSON EUROPE D-32758 DETMOLD            **")
        w("N5 ;** GVM High Speed Engraving Machine               **")
        w("N6 ;** AlphaCAM POSTPROZESSOR A.KOENEMANN             **")
        w("N7 ;** MTX ACT.DATE: 2020-01-29                       **")
        w("N8 ;** Copyright(c) Anderson Europe GmbH              **")
        w("N9 ;** AEC GVM    MTX V2_13                           **")
        w("N10 ;** Advanced 3D Milling and Engraving              **")
        w(f"N11 ;** NC-Program created on: {now:<23}**")
        w("N12 ;****************************************************")
        
        # ── 2. PRESETTINGS ──
        w("N13 ;***** Presettings *********************************")
        w("N14 PRESETTINGS")
        w(f'N15 SSDE[SD.USR.PrgName = "{prg_name_upper}.ANC"]')
        w(f"N16 (MSG, {prg_name_upper} START) ")
        w("17 IF SD.Operator.ToolData.ProgToolNumber < 1 THEN GOTO .ERR_1 ENDIF")
        w("18 IF SD.AEC.TC.Type = 2 THEN")
        w("19 IF SD.Operator.ToolData.ProgToolNumber > 120 THEN GOTO .ERR_1 ENDIF")
        w("20 ELSE")
        w("21 IF SD.Operator.ToolData.ProgToolNumber > 24 THEN GOTO .ERR_1 ENDIF")
        w("22 ENDIF")
        w("N23 SSDE[SD.USR.ToolAdjust.ToolCompValue = 0]")
        w("N24 SSDE[SD.USR.ToolAdjust.ToolCompMode = 0]")
        w("N25 SSDE[SD.Operator.ProcessData.Precision = 3]")
        w(f"N26 SSDE[SD.USR.ProcessData.ProgZDepth = ABS({options.z_depth})]")
        w(f"N27 SSDE[SD.AEC.TM.ZPosDiaMeas = ABS({options.z_depth})]")
        w(f"N28 SSDE[SD.USR.ProcessData.ProgDieHeight = {options.sheet_thickness}]")
        w(f"N29 SSDE[SD.USR.Allign.DistC2C = {options.fiducial_distance:g}]")
        w("30 GOTO .TOOLDATA")
        w("31 .TD_OK")
        w("32 WAIT")
        w("N33 ; Offset Spindel zu CCD")
        w("34 XOFFCCD! = SD.AEC.Offset.XOffSpCCD")
        w("35 YOFFCCD! = SD.AEC.Offset.YOffSpCCD")
        w("N36 ;")
        w("N37 ALIGN")
        w("N38 TOOLADJUST")
        w("N39 TOOLDATA")
        w("N40 ;")
        w("N41 G8 G17 G90 G47 JKC(1) FFW(1) PMS(1) ED1 F30000")
        w("N42 CLN(1)")
        w("N43 CLN(CollErr0)")
        w("N44 CLN(DLA80)")
        w("N45 COR(CAN[SD.WZRec.UD.Ed[1].Geo.Ang],MAN30,FF0.1)")
        w("N46 ;")
        w("N47 G53 G153 G0 Z50")
        w("N48 ATR()")
        w("N49 G154.1 G154.2")
        
        # ── 3. VIDEO MODE block (1:1 с эталоном) ──
        # Это НЕ безусловный переход в систему координат камеры. Это IF,
        # который проверяет включён ли VideoMode оператором на станке:
        # - Если ДА → G52 переключает в систему CCD-камеры (для проверки 
        #   реперов оператором), пропуская включение оборудования
        # - Если НЕТ → станок работает в обычном режиме: включается чип-сосание,
        #   шпиндель, вакуум, опускается кожух
        w("N50 ;****** VIDEO MODE ***************************************")
        w("51 IF SD.Operator.ToolAdjust.VideoMode = TRUE THEN")
        w(" 52 #VIDEO?=TRUE")
        w(" N53 G52 (X[XOFFCCD!], Y[YOFFCCD!], Z20)")
        w(" 54 GOTO .NEXT01")
        w(" 55 ELSE #VIDEO?=FALSE")
        w("56 ENDIF ")
        w("N57 ;*********************************************************")
        w("N58 M988 ; Ext. Chip Suction Unit ON")
        w("N59 M991 ; Shutter Milling Spindle OPEN")
        w("N60 M979 ; Control Vacuum ON")
        w("N61 M996 ; Tool Cover DOWN")
        w("N62 ;***************************************************")
        
        # ── 4. TOOL DATAS — параметры инструмента для контроллера ──
        # Это GOTO .NEXT01 пропускает блок при первом проходе — он 
        # выполняется когда был сделан переход .TOOLDATA из строки 30.
        w("N63 ;***** TOOL DATAS *********************************")
        w("64 GOTO .NEXT01")
        w("65 .TOOLDATA")
        w("N66 SSDE[SD.USR.BDE.MilledNCPathSubtotal = 0]")
        w("N67 SSDE[SD.USR.BDE.ProgNCPathShape = 0]")
        w("N68 ; Werkzeugtyp (0=Std., 1=3D, 99=MicroPerf)")
        tool_type = 1 if is_3d_program else 0
        w(f"N69 SSDE[SD.USR.ToolAdjust.ToolType = {tool_type}] ")
        w("N70 ; Programmierte Aequidistante")
        w(f"N71 SSDE[SD.USR.ToolData.ProgToolEquidistant = {tool_eq:g}/2]")
        w("N72 ; Werkzeug Spitzenradius")
        w(f"N73 SSDE[SD.WZRec.UD.Ed[1].Geo.Rad = {tool_diameter:g}/2] ")
        w("N74 ;Werkzeug Oeffnungswinkel der Schneide")
        w(f"N75 SSDE[SD.WZRec.UD.Ed[1].Geo.Ang = {tool_angle}] ")
        w("76 WAIT")
        w("77 GOTO .TD_OK")
        w("78 .NEXT01")
        w("N79 ;***************************************************")
        
        # ── 5. Безопасность: проверка OperationMode ──
        w("N80 ; Select Operation Mode ")
        w("81 IF SD.Operator.OperationMode = 0 THEN ")
        w(' 82 PRN#(0,"OperationMode is 0: Program will be completed / Programm wird beendet")')
        w(" 83 WAIT (,2000)")
        w(" 84 GOTO .PRGEND ")
        w(" 85 ENDIF")
        w("N86 ;")
        
        # ── 6. Включение шпинделя ──
        w("N87 ;***** TOOL / SPINDLE DATA *************************")
        w("N88 WAIT")
        w(";N89 T")
        w("N90 ;")
        w("N91 M3 S70000 ;20,1")
        w("N92 G0 Z3 ;25,2")
        
        # ── 7. Основная программа ──
        w("N93 ;40,1")
        w("N94 ;***** Begin of Loop (.SHAPE1) *****")
        w("95 #IHOCNS?=TRUE")
        
        # Группируем операции по sequence_number в SHAPE'ы
        shape_groups = self._group_by_shape(project)
        
        line_no = 96  # счётчик строк N (следующая после #IHOCNS?=TRUE)
        for shape_idx, (shape_num, ops) in enumerate(shape_groups, start=1):
            w(f"{line_no} .SHAPE{shape_num}")
            line_no += 1
            
            # Для каждой операции в shape
            part_num = 0
            # DRILL реперов: собираем drill_points со ВСЕХ активных 
            # (не-excluded) FIDUCIAL_DRILL операций. Каждая per-fiducial 
            # op хранит 1 точку в drill_points — объединяем в единый 
            # список чтобы эмитить как в эталоне (2 репера в одном блоке).
            fid_ops = [o for o in ops 
                       if o.enabled and o.kind == OperationKind.FIDUCIAL_DRILL
                       and not o.attributes.get('excluded', False)]
            if fid_ops:
                combined_points = []
                combined_depth = 0.1
                for fop in fid_ops:
                    combined_points.extend(fop.attributes.get('drill_points', []))
                    combined_depth = fop.attributes.get('drill_depth', combined_depth)
                if len(combined_points) >= 2:
                    # Создаём временную "виртуальную" op с накопленными точками
                    # (не меняя оригиналы). У _emit_fiducial_drill сигнатура 
                    # принимает op, читает attributes — прокинем через 
                    # временный SimpleNamespace.
                    from types import SimpleNamespace
                    virt = SimpleNamespace()
                    virt.attributes = {
                        'drill_points': combined_points,
                        'drill_depth': combined_depth,
                    }
                    line_no = self._emit_fiducial_drill(
                        out, virt, line_no, options)
            
            for op in ops:
                if not op.enabled:
                    continue
                # Отдельные FIDUCIAL_DRILL операции уже обработаны выше 
                # общим блоком — пропускаем в основном цикле.
                if op.kind == OperationKind.FIDUCIAL_DRILL:
                    continue
                for tp in op.toolpaths:
                    if not tp.visible:
                        continue
                    part_num += 1
                    line_no = self._emit_toolpath(
                        out, project, op, tp,
                        shape_num=shape_num, part_num=part_num,
                        line_no=line_no, options=options,
                    )
        
        # ── 6. Концовка ──
        w(f"N{line_no} ;")
        w(f"N{line_no+1} ;***** Postsettings ********************************")
        w(f"N{line_no+2} ;")
        w("; Programmende")
        w(f"{line_no+3} .PRGEND")
        w(f"N{line_no+4} COR() ATR() TRS()")
        w(f"N{line_no+5} G153 G53 G0 Z50")
        w(f"N{line_no+6} X785 Y600")
        w(f"N{line_no+7} M987  ; Ext. chip suction unit OFF")
        w(f"N{line_no+8} M992  ; Shutter milling spindle CLOSE")
        w(f"N{line_no+9} M978  ; Control Vacuum OFF")
        w(f"N{line_no+10} ;")
        w(f"N{line_no+11} ;***** END OF PROGRAM ******************************")
        w(f"N{line_no+12} (MSG, {prg_name_upper} END)")
        w(f"N{line_no+13} M30")
        w(f"N{line_no+14} ;")
        w(f"N{line_no+15} ;***** Error Sequences ****************************")
        w(f"{line_no+16} .ERR_1")
        w(f"N{line_no+17} ;Error: Programmed tool number is incorrect")
        w(f"{line_no+18} PLC(3,,4006,2)=1")
        w(f"{line_no+19} GOTO .PRGEND")
        w(f"N{line_no+20} ;")
        w(f"N{line_no+21} ;*********************************************************")
        
        return out.getvalue()
    
    # ─────────────────────────────────────────────────────────────────────
    #  ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ─────────────────────────────────────────────────────────────────────
    
    def _group_by_shape(self, project):
        """Группирует операции по их sequence_number → SHAPE.
        
        Несколько операций с одинаковым sequence_number попадают в один SHAPE
        (с PART1, PART2, ... в порядке списка). Это поведение типового
        эталона: 5 ножей × 2 прохода = 1 SHAPE с 10 PART.
        """
        shape_map = {}
        for op in project.operations:
            num = op.sequence_number if op.sequence_number > 0 else 1
            shape_map.setdefault(num, []).append(op)
        return sorted(shape_map.items(), key=lambda x: x[0])
    
    def _emit_toolpath(self, out, project, op, tp,
                       shape_num: int, part_num: int,
                       line_no: int, options: PostOptions) -> int:
        """Выводит один ToolPath как PART в текущий SHAPE.
        
        Включает Lead-In (G12) и Lead-Out (G13), если они включены 
        в tp.entry / tp.exit (LINE_ARC_TANGENTIAL).
        
        Returns:
            Следующий свободный line_no.
        """
        from ..core.project import ContourSide, LeadStyle, OperationKind
        from ..geometry.primitives import Line, Arc
        from ..geometry.direction import normalize_for_side
        from ..geometry.lead_inout import build_lead_in, build_lead_out
        from ..geometry.path_offset import shift_start_along_contour
        
        w = lambda s: out.write(s + '\n')
        prg = options.program_name.upper()
        
        geom = project.get_geometry(tp.geometry_id)
        if geom is None or geom.polypath is None or not geom.polypath.segments:
            return line_no
        
        # ── ОБРАБОТКА УГЛА (CORNER_REWORK) ──
        # И 2D и 3D углы обрабатываются ОДИНАКОВО — как фрагмент контура
        # «крючок» (заход + проход вокруг острия + выход).
        # Различие в:
        #   - инструменте (T3 для 2D, T4 для 3D)
        #   - способе извлечения фрагмента:
        #     * 2D: corner_first_idx..corner_last_idx (диапазон дуг скругления)
        #     * 3D: corner3d_point + segment_index (точка острого излома)
        #   - ToolType в шапке программы (0 для 2D, 1 для 3D)
        is_corner_rework = False
        if (op.kind == OperationKind.CORNER_REWORK
            and op.attributes.get('corner_is_3d')
            and 'corner3d_point' in op.attributes):
            # 3D угол — фрагмент вокруг точки острого излома
            from ..geometry.path_offset import extract_subpath_around_point
            pt = op.attributes['corner3d_point']
            seg_hint = op.attributes['corner3d_segment_index']
            polypath = extract_subpath_around_point(
                geom.polypath, pt, seg_hint, pad_mm=1.5
            )
            is_corner_rework = True
            if not polypath.segments:
                return line_no
            
            # ── НАПРАВЛЕНИЕ ФРАГМЕНТА (3D corner) ──
            # CORNER_REWORK 3D идут с side=OUTSIDE → G42 (комп. вправо), 
            # т.к. corner rework — часть внутреннего реза blade'а. По 
            # конвенции (normalize_for_side): OUTSIDE = внутренний рез = CW.
            # Разворот под CW: центр должен быть СПРАВА от касательной. 
            # Тогда G42 (комп. вправо) даёт фрезу с той же стороны = со 
            # стороны центра ножа = ВНУТРЬ контура (крючок).
            from ..geometry.path_offset import polypath_bbox
            full_bb = polypath_bbox(geom.polypath)
            knife_cx = (full_bb[0] + full_bb[2]) / 2.0
            knife_cy = (full_bb[1] + full_bb[3]) / 2.0
            sp = polypath.segments[0].a
            tan = polypath.segments[0].tangent_at_start()
            cross = tan[0]*(knife_cy - sp[1]) - tan[1]*(knife_cx - sp[0])
            if cross > 0:  # центр слева = CCW → разворачиваем под CW
                from ..geometry.direction import reverse_polypath
                polypath = reverse_polypath(polypath)
        
        elif (op.kind == OperationKind.CORNER_REWORK
            and 'corner_first_idx' in op.attributes
            and 'corner_last_idx' in op.attributes):
            from ..geometry.path_offset import extract_subpath_around_indices
            first_idx = op.attributes['corner_first_idx']
            last_idx = op.attributes['corner_last_idx']
            polypath = extract_subpath_around_indices(
                geom.polypath, first_idx, last_idx, pad_mm=1.5
            )
            is_corner_rework = True
            if not polypath.segments:
                return line_no
            
            # ── НАПРАВЛЕНИЕ ФРАГМЕНТА ──
            # CORNER операции идут с side=OUTSIDE → G42 (комп. вправо), 
            # т.к. corner rework — часть внутреннего реза blade'а. По 
            # конвенции (normalize_for_side): OUTSIDE = внутренний рез = CW.
            # Внутренний рез blade'ов идёт CW, corners должны продолжать 
            # это направление: центр ножа СПРАВА от касательной, фреза с 
            # G42 идёт в ту же сторону = ВНУТРЬ ножа = крючок в угол.
            from ..geometry.path_offset import polypath_bbox
            full_bb = polypath_bbox(geom.polypath)
            knife_cx = (full_bb[0] + full_bb[2]) / 2.0
            knife_cy = (full_bb[1] + full_bb[3]) / 2.0
            
            sp = polypath.segments[0].a
            tan = polypath.segments[0].tangent_at_start()
            cross = tan[0]*(knife_cy - sp[1]) - tan[1]*(knife_cx - sp[0])
            center_is_right = cross < 0
            
            # Нужно CW (центр справа). Если центр слева (CCW) — развернуть.
            if not center_is_right:
                from ..geometry.direction import reverse_polypath
                polypath = reverse_polypath(polypath)
        else:
            polypath = geom.polypath
        
        # ── НОРМАЛИЗАЦИЯ НАПРАВЛЕНИЯ ──
        # OUTSIDE → CCW, INSIDE → CW (производственный стандарт)
        # ВАЖНО: для CORNER_REWORK фрагмента это пропускаем — фрагмент уже
        # имеет правильное направление обхода контура.
        if (not is_corner_rework and geom.is_closed 
                and tp.side in (ContourSide.OUTSIDE, ContourSide.INSIDE)):
            side_name = "OUTSIDE" if tp.side == ContourSide.OUTSIDE else "INSIDE"
            polypath = normalize_for_side(polypath, side_name)
        
        # ── СМЕЩЕНИЕ ТОЧКИ СТАРТА В ПРАВЫЙ ВЕРХНИЙ УГОЛ ──
        # Как в Альфакамовском макросе Cutting: точка старта обхода 
        # выбирается в правом верхнем углу bbox ножа. Дальше при необходимости
        # применяется пользовательский start_offset (сдвиг от угла).
        # ВАЖНО: для CORNER_REWORK фрагмента это пропускаем — фрагмент уже
        # начинается с нужной точки.
        if not is_corner_rework and geom.is_closed:
            from ..geometry.path_offset import (
                shift_start_to_corner, shift_start_to_top_line,
                shift_start_along_contour)
            polypath = shift_start_to_corner(polypath, "RT")
            # Если RT-угол попал на дугу (овал со скруглениями = ширина/высоте),
            # сдвинуть start к началу ближайшей прямой стороны по обходу,
            # чтобы offset работал по прямой, а не по скруглению.
            polypath = shift_start_to_top_line(polypath)
            
            # ── СИММЕТРИЯ INSIDE/OUTSIDE: оба прохода на RT конец top line ──
            # После shift_start_to_top_line старт оказывается на НАЧАЛЕ top line.
            # Для CCW (INSIDE) это RT угол ✓ (top line идёт RIGHT→LEFT).
            # Для CW (OUTSIDE) это LT угол ✗ (top line идёт LEFT→RIGHT, начало = TL).
            # Чтобы оба прохода стартовали с RT (как в Alpha), для CW дополнительно
            # сдвигаем на длину top line — попадаем на её правый конец = TR угол.
            seg0 = polypath.segments[0]
            from ..geometry.primitives import Line
            if isinstance(seg0, Line) and seg0.b[0] > seg0.a[0]:
                # Top line идёт слева направо = CW → сдвиг на длину к RT
                import math as _m_shift
                top_len = _m_shift.hypot(seg0.b[0] - seg0.a[0], 
                                          seg0.b[1] - seg0.a[1])
                polypath = shift_start_along_contour(polypath, top_len)
        
        # ── СМЕЩЕНИЕ ТОЧКИ СТАРТА ВДОЛЬ КОНТУРА ──
        # Поле «Смещение по» из диалога Cutting / start_offset в EntryExitConfig.
        # ЕДИНАЯ СИСТЕМА: и для INSIDE и для OUTSIDE точка 0 = RT угол, 
        # offset -5 = 5мм влево по верхней стороне ножа.
        # 
        # Намотки (после фикса normalize_for_side):
        #   OUTSIDE — CW (внутр.рез): сдвиг -5 = влево по верху ✓ (без инверсии)
        #   INSIDE  — CCW (внешн.рез): сдвиг -5 = вниз по правой ✗ (нужна инверсия)
        # Чтобы юзер видел ОДНО число и оба прохода шли от RT в одну сторону 
        # (влево по верху), для INSIDE инвертируем знак.
        # 
        # Для CORNER_REWORK не применяем — там точка старта = начало фрагмента.
        # ── Per-op lead_override: offset и overlap ──
        # Юзер мог отредактировать отдельный нож в режиме «Выделенные». 
        # Читаем override и подменяем tp.entry.start_offset / tp.exit.overlap.
        # (angle/length подменяются позже при построении req_in/req_out.)
        _lead_ov_early = op.attributes.get('lead_override', {})
        _override_offset = tp.entry.start_offset
        _override_overlap = tp.exit.overlap
        if _lead_ov_early:
            if tp.side == ContourSide.OUTSIDE:
                _ov = _lead_ov_early.get('lead_inside', {})
            elif tp.side == ContourSide.INSIDE:
                _ov = _lead_ov_early.get('lead_outside', {})
            else:
                _ov = _lead_ov_early.get('lead_inside', {})
            _override_offset = _ov.get('offset', _override_offset)
            _override_overlap = _ov.get('overlap', _override_overlap)
        
        if (not is_corner_rework and geom.is_closed and tp.entry.enabled 
                and abs(_override_offset) > 1e-9):
            effective_offset = _override_offset
            if tp.side == ContourSide.INSIDE:
                effective_offset = -effective_offset
            polypath = shift_start_along_contour(polypath, effective_offset)
        
        # ── OVERLAP откладывается до ПОСЛЕ автоподбора ──
        # Если применить overlap здесь, polypath перестанет быть замкнутым 
        # (открытый контур с продлением), и shift_start_along_contour в 
        # автоподборе сместит overlap-кусок вместе со стартом. 
        # Сохраним значение, применим в конце (после best_polypath).
        pending_overlap = 0.0
        if (not is_corner_rework and geom.is_closed 
                and tp.exit.enabled and _override_overlap > 1e-9):
            pending_overlap = _override_overlap
        
        # ── Сглаживание под фрезу (по флагу) ──
        # АДАПТИВНЫЙ режим:
        # - Если в ноже есть настоящие 3D углы (fillet ~R_tool, swept >= 20°,
        #   L 0.15-1.5мм) — НЕ трогаем геометрию (любое сглаживание их 
        #   уничтожит). Полагаемся на контроллер NUM/MTX: при R_fillet >= 
        #   R_tool destruction не будет.
        # - Если 3D углов нет (плавный нож с биарк-шумом) — применяем 
        #   полный pipeline (simplify + smooth) для устранения зигзагов.
        smooth_on = bool(options.extras.get('smooth_offset_for_tool', False))
        if (smooth_on and not is_corner_rework and geom.is_closed
                and tp.side in (ContourSide.OUTSIDE, ContourSide.INSIDE)):
            from ..geometry.path_offset import (smooth_for_offset, 
                simplify_geometry_via_shapely, has_real_3d_corners,
                merge_segments_to_arcs)
            _tool_eq = options.extras.get('tool_equidistant', None)
            if _tool_eq is None:
                _tool_eq = options.extras.get('tool_radius', 0.6) * 2
            tool_r = _tool_eq / 2.0
            min_tool_r = options.extras.get('min_tool_radius', tool_r * 0.9)
            _side = "OUTSIDE" if tp.side == ContourSide.OUTSIDE else "INSIDE"
            
            if not has_real_3d_corners(polypath, min_tool_radius_mm=min_tool_r):
                # Безопасно сглаживать
                polypath = simplify_geometry_via_shapely(polypath, tol_mm=0.1)
                polypath = smooth_for_offset(polypath, tool_r, _side)
                # После сглаживания получаем полилинию из мелких Line — 
                # собираем обратно в дуги где возможно, чтобы NC-файл был 
                # компактным (одна G3/G2 команда на дугу вместо десятков G1).
                polypath = merge_segments_to_arcs(polypath, tol=0.02)
            # else: пропускаем сглаживание — сохраняем 3D углы как есть
        
        # ── ВНУТРЕННЕЕ СГЛАЖИВАНИЕ для NC-эмиссии ──
        # Объединяем последовательные коллинеарные G1-линии (угол перегиба 
        # < 1°) в одну. Без этого NUM-контроллер видит «море углов» на 
        # каждом стыке мелких отрезков и замедляется/делает рывки на ровном 
        # контуре. Безопасная операция — не меняет геометрию арок.
        # 
        # Tangent-repair арок не делаем здесь: попытка модифицировать центр 
        # одной арки нарушает её стыки со следующими арками (точка перехода 
        # больше не лежит на окружности следующей арки) → каскад искажений.
        # Для NC-controller smoothing арок нужен другой подход — например 
        # увеличение ED-параметра в NC-заголовке или использование HSC.
        from ..geometry.path_offset import merge_collinear_lines
        polypath = merge_collinear_lines(polypath, angle_tol_deg=1.0)
        
        segments = polypath.segments
        # Применяем срез по start_t / end_t (для CORNER_REWORK)
        if tp.start_t > 0.0 or tp.end_t < 1.0:
            n = len(segments)
            i_start = int(tp.start_t * n)
            i_end = int(tp.end_t * n)
            segments = segments[i_start:i_end]
        if not segments:
            return line_no
        
        first = segments[0]
        last = segments[-1]
        contour_start_point = first.a
        contour_start_tangent = first.tangent_at_start()
        contour_end_point = last.b
        contour_end_tangent = last.tangent_at_end()
        
        # ── ПОДГОТОВКА LEAD-IN/OUT ──
        # Tool radius для масштабирования.
        # Берётся из options.extras['tool_radius'] если задан, иначе 0.6 мм.
        tool_radius_estimate = options.extras.get('tool_radius', 0.6)
        
        # tool_offset = на сколько станок смещает фактическую траекторию 
        # фрезы от программной при G41/G42. По эталону = tool_equidistant/2.
        # Это ФАКТИЧЕСКИЙ боковой зазор от оси контура до края фрезы.
        # ИСПОЛЬЗУЕТСЯ для масштабирования lead-in/out (как в Alpha CAM).
        tool_eq = options.extras.get('tool_equidistant', tool_radius_estimate * 2)
        tool_offset = tool_eq / 2.0
        
        # Формула lead-in (как в Alpha):
        #   lateral_clearance = user_factor × tool_offset
        #   line_length = lateral_clearance / sin(approach_angle)
        # При user=1, angle=45°, tool_offset=0.575: line=0.575/0.707=0.813
        # При user=1, angle=90°: line=0.575 (минимум).
        import math as _m
        def _line_len_alpha(user_factor: float, angle_deg: float) -> float:
            ang_rad = _m.radians(max(5.0, min(175.0, angle_deg)))
            sin_a = _m.sin(ang_rad)
            if sin_a < 0.05: sin_a = 0.05  # защита от близких к 0/180
            return user_factor * tool_offset / sin_a
        
        # ── ВЫБОР СТОРОНЫ ЗАХОДА/ВЫХОДА ──
        # Заходы ДВУХ проходов должны расходиться В РАЗНЫЕ СТОРОНЫ (не
        # пересекать друг друга и канал между резами):
        #   INSIDE (CW, режет внешний контур) → заход НАРУЖУ
        #   OUTSIDE (CCW, режет внутренний)   → заход ВНУТРЬ
        # Внутри pick_lead_side_for_pass — динамический выбор left/right через
        # point-in-polygon дальней точки (фиксированная сторона не годится:
        # «наружу» зависит от локальной касательной).
        from ..geometry.lead_inout import pick_lead_side_for_pass
        _ll = _line_len_alpha(tp.entry.line_length_x_tool_rad, tp.entry.approach_angle)
        _ar = tp.entry.arc_radius_x_tool_rad * tool_offset
        _ang = tp.entry.approach_angle
        if tp.side in (ContourSide.INSIDE, ContourSide.OUTSIDE):
            lead_side = pick_lead_side_for_pass(
                contour_start_point, contour_start_tangent, polypath,
                tp.side.name, _ll, _ar, _ang, is_exit=False)
        elif tp.side == ContourSide.LEFT:
            lead_side = "left"
        elif tp.side == ContourSide.RIGHT:
            lead_side = "right"
        else:
            lead_side = "right"
        # Сторона выхода считается отдельно по КОНЦЕВОЙ касательной.
        if tp.side in (ContourSide.INSIDE, ContourSide.OUTSIDE):
            lead_out_side = pick_lead_side_for_pass(
                contour_end_point, contour_end_tangent, polypath, tp.side.name,
                _line_len_alpha(tp.exit.line_length_x_tool_rad, tp.exit.approach_angle),
                tp.exit.arc_radius_x_tool_rad * tool_offset,
                tp.exit.approach_angle, is_exit=True)
        else:
            lead_out_side = lead_side
        
        # Геометрия захода/отхода
        lead_in_geom = None
        lead_out_geom = None
        
        # Минимальный зазор (мм) от точек захода:
        #   - INSIDE: внутри своего bbox с учётом ПУТИ фрезы 
        #     (path внутри bbox смещён на tool_offset)
        #   - OUTSIDE: снаружи своего bbox с учётом пути + ≥1мм от 
        #     соседних bbox С УЧЁТОМ их INSIDE-пути (тоже смещён внутрь
        #     на tool_offset, но это значит реальный край соседа = его bbox)
        SAFE_CLEARANCE = 1.0
        
        # Bbox обрабатываемого контура (свой нож)
        from ..geometry.path_offset import (polypath_bbox, 
                                             shift_start_along_contour)
        bb_x0, bb_y0, bb_x1, bb_y1 = polypath_bbox(polypath)
        
        # Bbox'ы СОСЕДНИХ ножей на листе (для проверки OUTSIDE заходов)
        # Кэшируется на уровне проекта чтобы не пересчитывать для каждого PART
        if not hasattr(self, '_neighbor_bboxes_cache'):
            self._neighbor_bboxes_cache = {}
        cache_key = id(project)
        if cache_key not in self._neighbor_bboxes_cache:
            neighbors = []
            for other_op in project.operations:
                if other_op is op or other_op.kind != OperationKind.BLADE_FORMING:
                    continue
                for gid in other_op.geometry_ids:
                    g_other = project.get_geometry(gid)
                    if g_other and g_other.polypath:
                        neighbors.append(polypath_bbox(g_other.polypath))
            self._neighbor_bboxes_cache[cache_key] = neighbors
        else:
            # Пересчитаем без текущей операции
            neighbors = []
            for other_op in project.operations:
                if other_op is op or other_op.kind != OperationKind.BLADE_FORMING:
                    continue
                for gid in other_op.geometry_ids:
                    g_other = project.get_geometry(gid)
                    if g_other and g_other.polypath:
                        neighbors.append(polypath_bbox(g_other.polypath))
        
        def _point_safely_inside(p):
            """True если точка валидна для INSIDE захода.
            
            INSIDE-путь фрезы при G41 идёт ВНУТРИ контура на tool_offset.
            Фактический край пути = bbox − tool_offset со всех сторон.
            Заход должен быть ещё на SAFE_CLEARANCE глубже = 
              bbox − tool_offset − 1мм
            """
            inset = tool_offset + SAFE_CLEARANCE
            return (bb_x0 + inset <= p[0] <= bb_x1 - inset
                    and bb_y0 + inset <= p[1] <= bb_y1 - inset)
        
        def _point_outside_own_bbox(p):
            """True если точка снаружи своего пути фрезы.
            
            OUTSIDE-путь фрезы при G42 идёт СНАРУЖИ контура на tool_offset.
            Фактический внешний край пути = bbox + tool_offset.
            Главное условие: точка НЕ ВНУТРИ этого пути.
            """
            ex_x0 = bb_x0 - tool_offset
            ex_y0 = bb_y0 - tool_offset
            ex_x1 = bb_x1 + tool_offset
            ex_y1 = bb_y1 + tool_offset
            dx = max(ex_x0 - p[0], 0, p[0] - ex_x1)
            dy = max(ex_y0 - p[1], 0, p[1] - ex_y1)
            # СНАРУЖИ расширенного bbox: хоть с одной стороны за границей
            return (dx + dy) > 0
        
        def _point_only_neighbors_clear(p):
            """True если точка не залезает в путь фрезы соседнего ножа.
            
            Главное условие: точка снаружи (соседний_bbox + tool_offset).
            Соседний путь фрезы (OUTSIDE) уходит наружу его bbox на 
            tool_offset — точка не должна туда попадать.
            """
            for (nx0, ny0, nx1, ny1) in neighbors:
                ex_x0 = nx0 - tool_offset
                ex_y0 = ny0 - tool_offset
                ex_x1 = nx1 + tool_offset
                ex_y1 = ny1 + tool_offset
                dx = max(ex_x0 - p[0], 0, p[0] - ex_x1)
                dy = max(ex_y0 - p[1], 0, p[1] - ex_y1)
                # ВНУТРИ расширенного соседнего bbox = пересекает путь соседа
                if dx == 0 and dy == 0:
                    return False
            return True
        
        def _point_safe_outside(p):
            """True если точка валидна для OUTSIDE захода:
              1. Снаружи своего пути фрезы (bbox + tool_offset)
              2. Не залезает в путь фрезы соседнего ножа
            Без избыточных +1мм отступов — главное чтобы не пересекать
            пути фрезы.
            """
            return _point_outside_own_bbox(p) and _point_only_neighbors_clear(p)
        
        def _check_lead(lead, side_type):
            """Проверка валидности захода/выхода.
            side_type: 'in' (заход) или 'out' (выход).
            
            Точка СТЫКОВКИ (lead.arc.b для in / lead.arc.a для out) лежит
            физически на контуре = на границе своего bbox. Поэтому она
            проверяется ТОЛЬКО на соседей, а не на «снаружи своего».
            Остальные точки траектории (в воздухе) — на полную проверку.
            """
            # Соберём точки выборки на прямой и дуге.
            # Точку стыковки выделим отдельно — она крайняя на дуге.
            line_points = []
            arc_points = []
            if hasattr(lead, 'line') and lead.line:
                for i in range(6):
                    line_points.append(lead.line.point_at(i / 5))
            if hasattr(lead, 'arc') and lead.arc:
                for i in range(9):
                    arc_points.append(lead.arc.point_at(i / 8))
            
            # Определим точку стыковки и остальные «воздушные» точки
            if side_type == 'in':
                # стыковка = конец дуги, остальное = всё кроме последней
                join_pt = arc_points[-1] if arc_points else None
                air_points = line_points + arc_points[:-1]
            else:
                # стыковка = начало дуги
                join_pt = arc_points[0] if arc_points else None
                air_points = arc_points[1:] + line_points
            
            if tp.side == ContourSide.INSIDE:
                # INSIDE: все точки в воздухе должны быть внутри своего 
                # bbox с зазором. Стыковка не проверяется (она на контуре).
                return all(_point_safely_inside(p) for p in air_points)
            else:
                # OUTSIDE: главное чтобы lead НЕ ПЕРЕСЕКАЛ путь соседних 
                # ножей (bbox соседа + tool_offset).
                # Проверка "снаружи своего bbox+tool_offset" не нужна — это 
                # наш собственный путь, lead просто стыкуется к нему.
                if join_pt is not None and not _point_only_neighbors_clear(join_pt):
                    return False
                return all(_point_only_neighbors_clear(p) for p in air_points)
        
        def _lead_violation(lead, side_type):
            """Метрика плохости захода — максимальная глубина вторжения
            какой-либо точки в запретный bbox (свой или соседний с tool_offset).
            
            0.0 = заход полностью валиден.
            >0  = есть точки внутри запретной зоны, число — глубина вторжения.
            
            Используется для выбора «лучшего плохого» варианта когда
            идеального не найти.
            """
            # Точки выборки
            points = []
            for i in range(6):
                points.append(lead.line.point_at(i / 5))
            for i in range(9):
                points.append(lead.arc.point_at(i / 8))
            
            if side_type == 'in':
                join_pt = points[-1]
                air_points = points[:-1]
            else:
                join_pt = points[6]  # первая точка дуги
                air_points = points[:6] + points[7:]
            
            max_violation = 0.0
            
            if tp.side == ContourSide.INSIDE:
                # Все воздушные точки должны быть внутри bbox - tool_offset - 1
                inset = tool_offset + SAFE_CLEARANCE
                ix0 = bb_x0 + inset
                iy0 = bb_y0 + inset
                ix1 = bb_x1 - inset
                iy1 = bb_y1 - inset
                for p in air_points:
                    dx = max(ix0 - p[0], 0, p[0] - ix1)
                    dy = max(iy0 - p[1], 0, p[1] - iy1)
                    viol = (dx*dx + dy*dy) ** 0.5  # «насколько вне допустимой зоны»
                    if viol > max_violation:
                        max_violation = viol
            else:
                # OUTSIDE: проверяем ТОЛЬКО пересечение с соседями.
                # «Внутри своего bbox+tool_offset» — это наш собственный путь,
                # lead естественно проходит через эту зону и это НЕ нарушение.
                # Точку стыковки также не включаем (она на bbox).
                for p_check in air_points:
                    # внутри соседнего bbox+tool_offset?
                    for (nx0, ny0, nx1, ny1) in neighbors:
                        if (nx0 - tool_offset <= p_check[0] <= nx1 + tool_offset
                            and ny0 - tool_offset <= p_check[1] <= ny1 + tool_offset):
                            d_to_edge = min(
                                p_check[0] - (nx0 - tool_offset),
                                (nx1 + tool_offset) - p_check[0],
                                p_check[1] - (ny0 - tool_offset),
                                (ny1 + tool_offset) - p_check[1],
                            )
                            if d_to_edge > max_violation:
                                max_violation = d_to_edge
            
            return max_violation
        
        # ── ПЛАНИРОВАНИЕ LEAD-IN ЕДИНОЙ ФУНКЦИЕЙ (общая с viewer'ом) ──
        # plan_lead_in делает всё:
        #   1) построение line+arc на старте polypath'а
        #   2) проверка коллизий через line-only апроксимацию контуров +
        #      bbox-prefilter (быстро) + distance-check от соседей с safety×1.2
        #   3) автоподбор позиции (сдвиги ±1..±8мм) если коллизия
        #   4) если не помогло — варианты угла + укорачивание
        # Тот же код использует viewer_2d.py → одинаковые результаты в превью и .anc.
        if tp.entry.enabled and tp.entry.style in (LeadStyle.LINE_ARC_TANGENTIAL, LeadStyle.LINE):
            from ..geometry.lead_collision import (
                LeadGeometryRequest, plan_lead_in, build_contours_cache)
            
            # Кеш контуров для коллизий — все замкнутые ножи проекта. 
            # Кешируется на уровне self для переиспользования между toolpath'ами.
            if not hasattr(self, '_contours_cache') or id(project) not in self._contours_cache:
                if not hasattr(self, '_contours_cache'):
                    self._contours_cache = {}
                all_geoms = []
                for other_op in project.operations:
                    if other_op.kind == OperationKind.BLADE_FORMING:
                        for gid in other_op.geometry_ids:
                            g_other = project.get_geometry(gid)
                            if g_other is not None and g_other.is_closed:
                                all_geoms.append(g_other)
                self._contours_cache[id(project)] = build_contours_cache(all_geoms)
            contours_lines_cache, contours_bboxes_cache = self._contours_cache[id(project)]
            
            # Сторона захода:
            # - INSIDE/OUTSIDE: НЕ задаём forced_side, чтобы plan_lead_in 
            #   пересчитывал сторону на КАЖДОЙ кандидатной позиции через 
            #   pick_lead_side_for_pass (после автоподбора стартовая позиция 
            #   могла сдвинуться и сторона измениться).
            # - LEFT/RIGHT/CORNER: жёстко lead_side (определён выше).
            from ..core.project import ContourSide
            forced_side = None if tp.side in (ContourSide.INSIDE, ContourSide.OUTSIDE) else lead_side
            
            # ── Применение per-op lead_override (режим «Выделенные» из viewer'а) ──
            # Юзер мог отредактировать параметры отдельных ножей — сохранены 
            # в op.attributes['lead_override']. Читаем их и подменяем 
            # соответствующие поля tp.entry/tp.exit до построения request'ов.
            # 
            # Маппинг:
            #   CORNER — entry из lead_inside, exit из lead_outside (разные)
            #   OUTSIDE (внутренний рез) → lead_inside (внутренний столбец)
            #   INSIDE (внешний рез) → lead_outside (внешний столбец)
            _lead_ov = op.attributes.get('lead_override', {})
            if _lead_ov:
                # Проверяем is_corner_rework СНАЧАЛА — corner ops теперь тоже 
                # имеют side=OUTSIDE (внутренний рез), но у них entry/exit из 
                # РАЗНЫХ столбцов полей (Внутренний/Внешний), не из одного.
                if is_corner_rework:
                    _ov_in = _lead_ov.get('lead_inside', {})
                    _ov_out = _lead_ov.get('lead_outside', {})
                elif tp.side == ContourSide.OUTSIDE:
                    _ov_in = _lead_ov.get('lead_inside', {})
                    _ov_out = _lead_ov.get('lead_inside', {})
                elif tp.side == ContourSide.INSIDE:
                    _ov_in = _lead_ov.get('lead_outside', {})
                    _ov_out = _lead_ov.get('lead_outside', {})
                else:
                    _ov_in = _ov_out = {}
            else:
                _ov_in = _ov_out = {}
            
            _entry_angle = _ov_in.get('angle', tp.entry.approach_angle)
            _entry_length = _ov_in.get('length', tp.entry.line_length_x_tool_rad)
            _entry_radius = _ov_in.get('length', tp.entry.arc_radius_x_tool_rad)
            _exit_angle = _ov_out.get('angle', tp.exit.approach_angle)
            _exit_length = _ov_out.get('length', tp.exit.line_length_x_tool_rad)
            _exit_radius = _ov_out.get('length', tp.exit.arc_radius_x_tool_rad)
            
            req_in = LeadGeometryRequest(
                is_entry=True,
                pass_side=tp.side.name,
                angle_deg=_entry_angle,
                line_length=_line_len_alpha(_entry_length, _entry_angle),
                arc_radius=_entry_radius * tool_offset,
                style=('line' if tp.entry.style == LeadStyle.LINE else 'line_arc'),
                forced_side=forced_side,
            )
            
            # exit_request — чтобы plan_lead_in учитывал коллизию lead-out'а
            # при поиске сдвига. Иначе может выйти: lead-in OK, lead-out COL.
            exit_req = None
            if tp.exit.enabled and tp.exit.style in (LeadStyle.LINE_ARC_TANGENTIAL, LeadStyle.LINE):
                forced_out_side = None if tp.side in (ContourSide.INSIDE, ContourSide.OUTSIDE) else lead_out_side
                exit_req = LeadGeometryRequest(
                    is_entry=False,
                    pass_side=tp.side.name,
                    angle_deg=_exit_angle,
                    line_length=_line_len_alpha(_exit_length, _exit_angle),
                    arc_radius=_exit_radius * tool_offset,
                    style=('line' if tp.exit.style == LeadStyle.LINE else 'line_arc'),
                    forced_side=forced_out_side,
                )
            
            polypath, _lead_in_poly, _coll, lead_in_geom = plan_lead_in(
                polypath, req_in,
                contours_lines_cache, contours_bboxes_cache,
                geom.id, tool_offset,
                auto_avoid=True, safety_factor=1.2,
                exit_request=exit_req,
                overlap=pending_overlap)
            
            # Применяем overlap ПОСЛЕ автоподбора (см. pending_overlap выше)
            if pending_overlap > 1e-9 and polypath.closed:
                from ..geometry.path_offset import apply_overlap
                polypath = apply_overlap(polypath, pending_overlap)
            
            # Обновляем точки контура для последующего кода
            segments = polypath.segments
            contour_start_point = segments[0].a
            contour_start_tangent = segments[0].tangent_at_start()
            contour_end_point = segments[-1].b
            contour_end_tangent = segments[-1].tangent_at_end()
        
        # ── ПЛАНИРОВАНИЕ LEAD-OUT ЕДИНОЙ ФУНКЦИЕЙ (как и lead-in) ──
        # Lead-out строится из ФИНАЛЬНОГО конца контура (после автоподбора 
        # и overlap'а). Без авто-сдвига позиции — она жёстко определена 
        # концом polypath'а. Сторона пересчитывается на финальном конце 
        # внутри plan_lead_out → build_lead_for_polypath (если 
        # forced_side=None для INSIDE/OUTSIDE).
        if tp.exit.enabled and tp.exit.style in (LeadStyle.LINE_ARC_TANGENTIAL, LeadStyle.LINE):
            from ..geometry.lead_collision import plan_lead_out
            from ..core.project import ContourSide
            
            # Для INSIDE/OUTSIDE: forced_side=None → внутри пересчитают
            # Для LEFT/RIGHT/CORNER: используем lead_out_side (определён выше)
            forced_out = None if tp.side in (ContourSide.INSIDE, ContourSide.OUTSIDE) else lead_out_side
            
            req_out = LeadGeometryRequest(
                is_entry=False,
                pass_side=tp.side.name,
                angle_deg=_exit_angle,
                line_length=_line_len_alpha(_exit_length, _exit_angle),
                arc_radius=_exit_radius * tool_offset,
                style=('line' if tp.exit.style == LeadStyle.LINE else 'line_arc'),
                forced_side=forced_out,
            )
            
            # Тот же кеш контуров — может быть уже построен в lead-in блоке
            if not hasattr(self, '_contours_cache') or id(project) not in self._contours_cache:
                from ..geometry.lead_collision import build_contours_cache
                if not hasattr(self, '_contours_cache'):
                    self._contours_cache = {}
                all_geoms = []
                for other_op in project.operations:
                    if other_op.kind == OperationKind.BLADE_FORMING:
                        for gid in other_op.geometry_ids:
                            g_other = project.get_geometry(gid)
                            if g_other is not None and g_other.is_closed:
                                all_geoms.append(g_other)
                self._contours_cache[id(project)] = build_contours_cache(all_geoms)
            contours_lines_cache, contours_bboxes_cache = self._contours_cache[id(project)]
            
            _lead_out_poly, _coll_out, lead_out_geom = plan_lead_out(
                polypath, req_out,
                contours_lines_cache, contours_bboxes_cache,
                geom.id, tool_offset, safety_factor=1.2)
        
        # ── Команда компенсации в зависимости от стороны ──
        # Стандарт ISO: G41 = слева по ходу, G42 = справа.
        # Для флексо-резки с лезвием СЛЕВА по ходу фреза смещается СПРАВА от
        # программной линии → G42 для обоих проходов.
        # Намотки: INSIDE (CCW, внешний рез), OUTSIDE (CW, внутренний рез).
        #   INSIDE (CCW)  + G42 (справа) → НАРУЖУ от центра = «+» ✓
        #   OUTSIDE (CW)  + G42 (справа) → ВНУТРЬ к центру  = «−» ✓
        # Внешний рез расходится наружу, внутренний сходится внутрь, между
        # ними образуется V-канавка — это и есть правильное поведение.
        gcomp = {
            ContourSide.OUTSIDE: "G42",   # внутренний рез
            ContourSide.RIGHT:   "G42",
            ContourSide.INSIDE:  "G42",   # внешний рез
            ContourSide.LEFT:    "G41",
        }.get(tp.side, "G40")
        
        # ── Подсчёт длины пути контура (для BDE-статистики) ──
        # Длина в метрах = сумма длин сегментов / 1000.
        path_length_mm = sum(s.length() for s in segments)
        # Учтём также заходы/отходы
        if lead_in_geom is not None:
            if lead_in_geom.line:  path_length_mm += lead_in_geom.line.length()
            if lead_in_geom.arc:   path_length_mm += lead_in_geom.arc.length()
        if lead_out_geom is not None:
            if lead_out_geom.arc:  path_length_mm += lead_out_geom.arc.length()
            if lead_out_geom.line: path_length_mm += lead_out_geom.line.length()
        path_length_m = path_length_mm / 1000.0
        
        # ── DLA (Dynamic Look Ahead) ──
        # Формула из .amp: DLA = clamp(число_сегментов - 2, 1, 80).
        # Число сегментов = движения по контуру (SCLN-счётчик).
        scln_count = len(segments)
        if scln_count > 81:
            dla = 80
        elif scln_count <= 3:
            dla = 1
        else:
            dla = scln_count - 2
        
        # ── ШАПКА PART ──
        w(f"N{line_no} ; DLA Subject ; 40,2")
        line_no += 1
        w(f"N{line_no} (MSG, {prg}, SHAPE{shape_num}, PART{part_num}) ")
        line_no += 1
        w(f"N{line_no} SSDE[SD.USR.ToolAdjust.ToolCompMode = 0]")
        line_no += 1
        w(f"{line_no} GOTO .PART{part_num}")
        line_no += 1
        w(f"{line_no} .COL{part_num}")
        line_no += 1
        w(f"N{line_no} TOOLDATA")
        line_no += 1
        w(f"N{line_no} PREPART ")
        line_no += 1
        
        # ── ПОДХОД ──
        if lead_in_geom is not None and lead_in_geom.line is not None:
            approach_point = lead_in_geom.line.a
        else:
            approach_point = contour_start_point
        
        w(f"N{line_no} G0 X{self.format_coord(approach_point[0])} "
          f"Y{self.format_coord(approach_point[1])} ;40,9")
        line_no += 1
        w(f"N{line_no} G1 Z0 F{op.settings.feed_plunge} ")
        line_no += 1
        
        # ── LEAD-IN: прямая + G12/G13 (дуга захода) для line_arc стиля,
        # или одна прямая G1 для line стиля (Альфакам).
        if lead_in_geom is not None:
            line_end = lead_in_geom.line.b
            w(f"N{line_no} {gcomp} X{self.format_coord(line_end[0])} "
              f"Y{self.format_coord(line_end[1])} "
              f"F{op.settings.feed_cut} SCLN(2)")
            line_no += 1
            arc = lead_in_geom.arc
            if arc is not None:
                # LINE_ARC_TANGENTIAL: дуга G12 (активация компенсации)
                # См. эталон .amp $50/$60: блоки с "+ IN" эмиттят G12.
                # Комментарий ;50,10 — маркер из эталонного POST AlphaCAM
                # (номер строки шаблона Lead-in arc), полезен оператору при
                # трассировке. Парный ;50,15 идёт на G13 (lead-out arc).
                g_arc = "G12"
                w(f"N{line_no} {g_arc} X{self.format_coord(arc.b[0])} "
                  f"Y{self.format_coord(arc.b[1])} SCLN(2) ;50,10")
                line_no += 1
            # LINE (Альфакам-стиль): дуги нет, прямая уже доходит до 
            # contour_start_point — больше ничего не нужно.
        else:
            w(f"N{line_no} {gcomp} X{self.format_coord(contour_start_point[0])} "
              f"Y{self.format_coord(contour_start_point[1])} "
              f"F{op.settings.feed_cut} SCLN(2)")
            line_no += 1
        
        # ── ДВИЖЕНИЯ ПО КОНТУРУ ──
        for seg in segments:
            if isinstance(seg, Line):
                end = seg.b
                w(f"N{line_no} G1 X{self.format_coord(end[0])} "
                  f"Y{self.format_coord(end[1])}")
            elif isinstance(seg, Arc):
                end = seg.b
                g_arc = "G3" if seg.ccw else "G2"
                w(f"N{line_no} {g_arc} X{self.format_coord(end[0])} "
                  f"Y{self.format_coord(end[1])} R{self.format_coord(seg.radius)}")
            line_no += 1
        
        # ── LEAD-OUT: дуга G12/G13 + прямая с G40 ──
        # Аналогично lead_in: G13 для дуги выхода (если style=line_arc),
        # или сразу G40 на внешнюю точку (если style=line, без дуги).
        if lead_out_geom is not None:
            arc = lead_out_geom.arc
            if arc is not None:
                # LINE_ARC_TANGENTIAL: дуга G13 (деактивация компенсации)
                g_arc = "G13"
                w(f"N{line_no} {g_arc} X{self.format_coord(arc.b[0])} "
                  f"Y{self.format_coord(arc.b[1])} SCLN(1) ;50,15")
                line_no += 1
            # Прямой отъезд с отменой компенсации G40
            line_end = lead_out_geom.line.b
            final_x = self.format_coord(line_end[0])
            final_y = self.format_coord(line_end[1])
            w(f"N{line_no} G1 G40 X{final_x} Y{final_y} SCLN(1) ;40,18 ")
            line_no += 1
        else:
            final_x = self.format_coord(contour_end_point[0])
            final_y = self.format_coord(contour_end_point[1])
            w(f"N{line_no} G1 G40 X{final_x} Y{final_y} SCLN(1) ;40,18 ")
            line_no += 1
        
        # ── DLA-БЛОК (переход вокруг него) ──
        # По эталону: после контура идёт GOTO .STEPn, потом метка .PARTn
        # с CLN(DLA..), WAIT, GOTO .COLn, и метка .STEPn куда был прыжок.
        w(f"{line_no} GOTO .STEP{part_num} ")
        line_no += 1
        w(f"{line_no} .PART{part_num}")
        line_no += 1
        w(f"N{line_no} CLN(DLA{dla}) ;25.1")
        line_no += 1
        w(f"N{line_no} WAIT")
        line_no += 1
        w(f"{line_no} GOTO .COL{part_num}")
        line_no += 1
        w(f"{line_no} .STEP{part_num}")
        line_no += 1
        
        # ── ПОДЪЁМ С ОТМЕНОЙ КОРРЕКЦИИ ──
        # Фреза уже находится в финальной точке после lead-out (G1 G40 
        # в строке выше). Здесь только подъём Z + отмена коррекции 
        # (если она ещё не была сделана). Координаты XY не повторяем —
        # это вызывало визуальный артефакт в Альфакам preview (длинная 
        # жёлтая линия от прошлой точки в эту).
        w(f"N{line_no} G0 Z10 ;25,2")
        line_no += 1
        w(f"N{line_no} WAIT ;25,6")
        line_no += 1
        
        # ── BDE-СТАТИСТИКА (длина пути) ──
        w(f"N{line_no} ; Programmed NC-path (actual shape [m])")
        line_no += 1
        w(f"N{line_no} SSDE[SD.USR.BDE.ProgNCPathShape = "
          f"{path_length_m:.5f}]")
        line_no += 1
        w(f"N{line_no} ; Milled NC-Path Counter (sub length [m]) + "
          f"Programmed NC-Path length (actual shape [m])")
        line_no += 1
        w(f"N{line_no} SSDE[SD.USR.BDE.MilledNCPathSubtotal = "
          f"SD.USR.BDE.MilledNCPathSubtotal + SD.USR.BDE.ProgNCPathShape]")
        line_no += 1
        w(f"N{line_no} ; Milled NC-Path Counter (total length [m]) + "
          f"Programmed NC-Path length (actual shape [m])")
        line_no += 1
        w(f"N{line_no} SSDE[SD.USR.BDE.MilledNCPathTotal = "
          f"SD.USR.BDE.MilledNCPathTotal + SD.USR.BDE.ProgNCPathShape]")
        line_no += 1
        w(f"N{line_no} WAIT")
        line_no += 1
        w(f"N{line_no} ;")
        line_no += 1
        
        # ── ЗАКРЫТИЕ ЧАСТИ ──
        w(f"N{line_no} POSTPART ;25,7")
        line_no += 1
        w(f"N{line_no} ;")
        line_no += 1
        
        # ── ПОДГОТОВКА К СЛЕДУЮЩЕЙ ЧАСТИ ──
        # Шпиндель и Z-позиция (как в эталоне между PART)
        w(f"N{line_no} M3 S70000 ;20,1")
        line_no += 1
        w(f"N{line_no} G0 Z3 ;25,2")
        line_no += 1
        
        return line_no
    
    def _emit_fiducial_drill(self, out, op, line_no: int,
                             options: PostOptions) -> int:
        """Выводит DRILL-операцию реперов (kind=FIDUCIAL_DRILL).
        
        Точки берутся из op.attributes['drill_points'] — их туда положила
        стратегия (Project.make_fiducial_drill_operation) на основе реперов
        из .ai макета. Постпроцессор НЕ генерирует точки сам — только выводит
        то что дала стратегия.
        
        Структура по эталону Альфакама (N611-619):
            M3 S70000
            G0 X.. Y.. ;210,1      ; первый репер
            G1 Z10 F5000 ;210,2
            G0 Z3 ;210,3
            G1 Z<depth> F1500 ;211,4
            Z10 F5000 ;211,1
            G0 X.. Y.. ;211,2      ; второй репер
            G1 Z<depth> F1500 ;211,3
            Z10 F5000 ;200,1
        """
        w = lambda s: out.write(s + '\n')
        points = op.attributes.get('drill_points', [])
        depth = op.attributes.get('drill_depth', 0.1)
        if len(points) < 2:
            return line_no
        
        p1, p2 = points[0], points[1]
        
        w(f"N{line_no} M3 S70000 ;20,1")
        line_no += 1
        # Первый репер
        w(f"N{line_no} G0 X{self.format_coord(p1[0])} "
          f"Y{self.format_coord(p1[1])} ;210,1")
        line_no += 1
        w(f"N{line_no} G1 Z10 F5000 ;210,2")
        line_no += 1
        w(f"N{line_no} G0 Z3 ;210,3")
        line_no += 1
        w(f"N{line_no} G1 Z{self.format_coord(depth)} F1500 ;211,4")
        line_no += 1
        w(f"N{line_no} Z10 F5000 ;211,1")
        line_no += 1
        # Второй репер
        w(f"N{line_no} G0 X{self.format_coord(p2[0])} "
          f"Y{self.format_coord(p2[1])} ;211,2")
        line_no += 1
        w(f"N{line_no} G1 Z{self.format_coord(depth)} F1500 ;211,3")
        line_no += 1
        w(f"N{line_no} Z10 F5000 ;200,1")
        line_no += 1
        w(f"N{line_no} ;")
        line_no += 1
        return line_no


# ─────────────────────────────────────────────────────────────────────────
#  АВТО-РЕГИСТРАЦИЯ
# ─────────────────────────────────────────────────────────────────────────

PostRegistry.register(MtxAndersonGVM())
