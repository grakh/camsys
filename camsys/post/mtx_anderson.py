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
       - дуга захода G2/G3 если EntryExitConfig.enabled
       - G1/G2/G3 по сегментам
       - дуга выхода G2/G3
       - Подъём Z
    7. Опционально: сверление меток реперов
    8. Концовка с COR()/ATR()/TRS() и M30
"""

from __future__ import annotations
from io import StringIO
from datetime import datetime
from typing import List

from .base import PostProcessor, PostMetadata, PostOptions, PostRegistry


def _mach_log(options: PostOptions, geom, tp, message: str) -> None:
    """Запись в журнал проходимости фрезы.

    Копится в options.extras['machinability_report'] — список строк. UI
    показывает его после экспорта, чтобы оператор видел ИМЕННО какой нож
    и какой проход не удалось сделать проходимым.
    """
    try:
        side = getattr(getattr(tp, 'side', None), 'name', '?')
        gid = getattr(geom, 'id', '?')
        name = getattr(geom, 'name', None) or f"нож {gid}"
        options.extras.setdefault('machinability_report', []).append(
            f"[{name} / {side}] {message}")
    except Exception:
        pass


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
        # Eckenspitzenfunktion: вершину угла формирует стойка. То же
        # число MAN используется геометрией как предел продления опор в
        # эквидистанте, поэтому берётся из одного места (v1.7.54).
        from ..geometry.path_offset import CORNER_TIP_ANGLE_DEG as _MAN
        w(f"N45 COR(CAN[SD.WZRec.UD.Ed[1].Geo.Ang],MAN{_MAN:g},FF0.1)")
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

        # ── ТИК ПРОГРЕССА (v1.7.48) ──
        # Один проход — один тик. GUI на нём двигает полоску и прокачивает
        # очередь событий: без этого поток занят счётом, Qt не
        # перерисовывает окно, и «градусник» выглядит зависшим. Общее
        # число проходов известно заранее — считаем до генерации, чтобы
        # полоска была ЧЕСТНОЙ, а не бегущей по кругу.
        _on_part = options.extras.get('on_part')
        _parts_total = 0
        if _on_part is not None:
            for _sn, _ops in shape_groups:
                for _o in _ops:
                    if not _o.enabled or _o.kind == OperationKind.FIDUCIAL_DRILL:
                        continue
                    _parts_total += sum(1 for _t in _o.toolpaths if _t.visible)
        _parts_done = [0]

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
                # Дедупликация: per-fiducial ops хранят по 1 точке, а
                # _generate_rough_all дополнительно append'ит агрегированную
                # операцию со всеми точками сразу — тогда каждая точка
                # оказывается в combined_points дважды. Мы не хотим
                # сверлить один и тот же репер дважды.
                seen = set()
                deduped = []
                for pt in combined_points:
                    key = (round(pt[0], 4), round(pt[1], 4))
                    if key not in seen:
                        seen.add(key)
                        deduped.append(pt)
                combined_points = deduped
                if len(combined_points) >= 1:
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
                    if _on_part is not None:
                        try:
                            _on_part(_parts_done[0], _parts_total,
                                     getattr(op, 'name', ''))
                        except Exception:
                            pass
                        _parts_done[0] += 1
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
    
    def _project_contours_cache(self, project):
        """Кэш контуров ножей проекта — на ОДИН проект, с проверкой его же.

        Было: `self._contours_cache[id(project)]`. `self` — это синглтон
        из реестра постов, он живёт всю сессию, а `_generate_rough_all`
        каждый раз работает с `copy.deepcopy(self.project)` — новый
        объект, который после генерации освобождается. Python
        переиспользует адреса, поэтому очередной deepcopy попадал на
        `id` уже умершего проекта и получал ЧУЖОЙ кэш контуров: лид
        считался на геометрии другой программы, и точка старта прохода
        уезжала (v1.7.48, тот же класс ошибки, что в `_cached_points`
        в v1.7.46).

        Теперь кэш на один проект и хранит ССЫЛКУ на него: пока запись
        жива, адрес занят и переиспользован быть не может, а смена
        проекта сравнением `is` ловится сразу.
        """
        from ..geometry.lead_collision import build_contours_cache
        from ..core.project import OperationKind
        entry = getattr(self, '_contours_cache_entry', None)
        if entry is not None and entry[0] is project:
            return entry[1]
        # Контуры берутся из СЛОЯ ножей, а не из операций (v1.7.61).
        # Программу доработки пост собирает на проекте, из которого
        # операции основного прохода выброшены, — по операциям список
        # выходил ПУСТОЙ, и лид угла подбирался без оглядки на ножи.
        # Металл на листе от состава операций не зависит.
        all_geoms = []
        _klayer = project.get_layer_by_name("Knife")
        for g_other in (getattr(_klayer, 'geometries', ())
                        if _klayer is not None else ()):
            if g_other is not None and g_other.is_closed:
                all_geoms.append(g_other)
        data = build_contours_cache(all_geoms)
        self._contours_cache_entry = (project, data)
        return data

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
        
        Включает Lead-In и Lead-Out дугами G2/G3, если они включены 
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
        # ── УЧАСТОК ДОРАБОТКИ — ОБЩЕЙ ФУНКЦИЕЙ С ВЬЮВЕРОМ (v1.7.58) ──
        # Карман, 2D- или 3D-угол: какой именно, решают атрибуты
        # операции. Раньше это место было написано здесь и во вьювере по
        # отдельности, и припуск 2D-угла разошёлся (1.5 против 3.0+) —
        # картинка угла показывала не ту кривую, к которой в программе
        # крепится лид.
        is_corner_rework = False
        if op.kind == OperationKind.CORNER_REWORK:
            from ..geometry.path_offset import rework_fragment
            polypath = rework_fragment(
                geom, op, tp.side.name,
                options.extras.get('tool_equidistant', 1.3) / 2.0,
                log=lambda msg: _mach_log(options, geom, tp, msg))
            if polypath is None or not polypath.segments:
                return line_no
            is_corner_rework = True
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
            from ..geometry.path_offset import shift_start_to_top_x
        
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
        
        if (not is_corner_rework and geom.is_closed and tp.entry.enabled):
            # СМЕЩЕНИЕ ВДОЛЬ ВЕРХНЕЙ ГРАНИ от верхне-правого угла контура:
            # 0 = угол, <0 = влево по верху, >0 = зажим у угла. Единая точка
            # для обоих проходов → внутр/внешн выровнены. Не заворачивает за
            # угол вниз по стороне (как делал сдвиг по периметру → каша).
            from ..geometry.path_offset import shift_start_from_diagonal_zero
            polypath = shift_start_from_diagonal_zero(polypath, _override_offset)
        
        # ── OVERLAP откладывается до ПОСЛЕ автоподбора ──
        # Если применить overlap здесь, polypath перестанет быть замкнутым 
        # (открытый контур с продлением), и shift_start_along_contour в 
        # автоподборе сместит overlap-кусок вместе со стартом. 
        # Сохраним значение, применим в конце (после best_polypath).
        pending_overlap = 0.0
        if (not is_corner_rework and geom.is_closed 
                and tp.exit.enabled and _override_overlap > 1e-9):
            pending_overlap = _override_overlap
        
        # ── ОБРЕЗКА КАРМАНОВ (v1.7.33, за галкой) ──
        # Карман вырезается из осевой, через устье идёт дуга радиуса
        # `пятка/2 + ABS` направлением G2. Сверено с эталоном альфы на
        # Knife_3 файла 124173_test: концы моста совпадают до 11 мкм,
        # выброшенный кусок 12.13 мм — один в один.
        if (not is_corner_rework and geom.is_closed
                and polypath and polypath.closed):
            try:
                from ..geometry.path_offset import bridge_narrow_pits
                _eq_t = options.extras.get('tool_equidistant')
                _tip = options.extras.get('tool_radius', 0.4) * 2.0
                _abs_d = options.extras.get('z_depth', None)
                if _abs_d is None:
                    _abs_d = getattr(options, 'z_depth', 0.19)
                # Радиус моста ОБЯЗАН быть больше эквидистанты, иначе его
                # компенсированный радиус равен нулю, дуга вырождается и
                # путь перехлёстывается на встречную грань (v1.7.34).
                # Формула альфы `пятка/2 + ABS` даёт запас ABS·(1−tan(угол/2)):
                # при угле 80 это 40 мкм, при 90 — РОВНО НОЛЬ, при большем
                # угле уходит в минус. Оба эталонных файла альфы были с
                # углом 80, поэтому на 90 формула и подвела.
                # Ширина устья и радиус дуги — РАЗНЫЕ вещи, считает их
                # одна функция, общая с вьювером (v1.7.35).
                from ..geometry.path_offset import bridge_radii_for_tool
                _rm, _rb = bridge_radii_for_tool(
                    _tip, _abs_d, _eq_t,
                    margin=float(options.extras.get(
                        'machinable_margin', 0.02)))
                _pp2, _brs = bridge_narrow_pits(polypath, _rb, r_mouth=_rm)
                if _brs:
                    polypath = _pp2
                    # Обрезка пересобирает контур, начиная его с устья
                    # кармана, — стартовая точка уезжает с места, куда её
                    # поставил shift_start_from_diagonal_zero выше. Лид
                    # при этом ведёт в прежнюю точку, и путь прыгает через
                    # устье, пересекая контур (v1.7.34).
                    try:
                        from ..geometry.path_offset import (
                            shift_start_from_diagonal_zero as _shift2)
                        polypath = _shift2(polypath, _override_offset)
                    except Exception:
                        pass
                    for _b in _brs:
                        _mach_log(options, geom, tp,
                                  f"ОБРЕЗАН КАРМАН: выброшено "
                                  f"{_b['skip']:.2f} мм, устье "
                                  f"{_b.get('mouth_chord', 0.0):.4f} мм "
                                  f"(ушло внутрь "
                                  f"{_b.get('mouth_shrink', 0.0) * 1000:.0f} "
                                  f"мкм), мост R{_rb:.4f} "
                                  f"G{'3' if _b['ccw'] else '2'} "
                                  f"({_b['A'][0]:.2f} {_b['A'][1]:.2f}) -> "
                                  f"({_b['B'][0]:.2f} {_b['B'][1]:.2f})")
            except Exception:
                pass

        # ── ГАРАНТИЯ ПРОХОДИМОСТИ ФРЕЗЫ (v1.7) ──
        # В .anc уходит ОСЕВАЯ + G41/G42, эквидистанту считает станок.
        # Если на осевой есть дуга, которую компенсация сжимает (G42+G2 или
        # G41+G3) и её радиус меньше эквидистанты, то R_факт ≤ 0 — дуга
        # выворачивается, и контроллер NUM либо ругается, либо крутит петлю
        # на месте («мелкий барашек»). Раньше это никто не проверял.
        #
        # Проверка и правка идут ПОСЛЕ сглаживания и ДО merge_collinear_lines,
        # т.е. ровно на той геометрии, которая уйдёт в программу. Правится
        # только центр дуги — концы остаются на месте, поэтому стыки с
        # соседями точны и каскада искажений не возникает.
        _mach_side = tp.side
        _gcomp_early = {
            ContourSide.OUTSIDE: "G42",
            ContourSide.RIGHT:   "G42",
            ContourSide.INSIDE:  "G42",
            ContourSide.LEFT:    "G41",
        }.get(_mach_side, "G40")
        # CORNER_REWORK не трогаем: это НАМЕРЕННО тугой фрагмент, который
        # дорабатывается отдельной тонкой фрезой T3, а в extras поста лежит
        # эквидистанта ОСНОВНОЙ фрезы — проверять углы по ней некорректно
        # (даёт ложные «непроходимо 43% контура»). Сглаживание углы тоже
        # пропускает, поведение согласовано.
        if (_gcomp_early in ("G41", "G42") and not is_corner_rework
                and geom.is_closed and polypath and polypath.segments):
            from ..geometry.path_offset import ensure_machinable_arcs
            _eq = options.extras.get('tool_equidistant', None)
            if _eq is None:
                _eq = options.extras.get('tool_radius', 0.6) * 2.0
            _t_off = _eq / 2.0
            # Запас сверх эквидистанты: фрезы точатся, станок меряет их щупом
            # Renishaw и считает эквидистанту сам. Фрезеровщик старается не
            # брать фрезу больше заложенной, так что плановая эквидистанта —
            # верхняя граница; запас страхует обратный случай.
            _marg = float(options.extras.get('machinable_margin', 0.02))
            polypath, _fixed, _unfix = ensure_machinable_arcs(
                polypath, _t_off, _gcomp_early, margin=_marg,
                max_deviation_mm=float(options.extras.get(
                    'machinable_max_deviation', 0.05)))
            # ── УЗКИЕ МЕСТА (v1.7.22, пока только журнал) ──
            # Две части контура ближе ширины реза: канавки сливаются, и
            # фреза срезает лезвие противоположной стенки. Пути НЕ
            # меняем — сначала смотрим, сходится ли список с тем, что
            # видно глазами.
            if geom.is_closed and geom.polypath:
                try:
                    # Критерий — зазор фрезы между вершинами лезвия
                    # разных участков контура (v1.7.32). Свойство ножа, а
                    # не прохода, поэтому пишется один раз — на проход
                    # INSIDE, чтобы не дублировать.
                    if tp.side.name == 'INSIDE':
                        from ..geometry.path_offset import (
                            find_tool_clearance_issues)
                        _mc = float(options.extras.get(
                            'min_tool_clearance_mm', 0.030))
                        for _ci in find_tool_clearance_issues(
                                geom.polypath, _t_off * 2.0,
                                min_clearance_mm=_mc):
                            _cl = _ci['clearance']
                            _what = ('фреза срежет вершину соседнего лезвия'
                                     if _cl < 0 else 'фреза проходит впритык')
                            _mach_log(options, geom, tp,
                                      f"УЗКОЕ МЕСТО: зазор фрезы "
                                      f"{_cl * 1000:+.0f} мкм при допуске "
                                      f"{_mc * 1000:.0f} "
                                      f"(X{_ci['point'][0]:.2f} "
                                      f"Y{_ci['point'][1]:.2f}); {_what}, "
                                      f"нужна T3")
                except Exception:
                    pass

            if _fixed:
                _mach_log(options, geom, tp,
                          f"исправлено непроходимых дуг: {len(_fixed)} "
                          f"(мин. R {min(r['radius'] for r in _fixed):.4f} → "
                          f"{_t_off + _marg:.4f} мм)")
            for _r in _unfix:
                _mach_log(options, geom, tp,
                          f"НЕ ИСПРАВЛЕНО: дуга R={_r['radius']:.4f} мм "
                          f"нужно ≥{_r['needed']:.4f} — "
                          f"{_r.get('reason', 'причина не указана')}")
        
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

        # ── ПЛАНИРОВАНИЕ ЛИДОВ — ОБЩИМ КОДОМ С ВЬЮВЕРОМ (v1.7.58) ──
        # Раньше этот блок (≈600 строк) существовал здесь и во вьювере
        # по отдельности и расходился молча: v1.7.11, v1.7.29, v1.7.39,
        # v1.7.43, v1.7.45, v1.7.55, v1.7.56 — всё это правки одного и
        # того же, «в программе одно, на картинке другое». Теперь обе
        # стороны зовут plan_pass_leads и получают лид НА ОСЕВОЙ (именно
        # она уходит в .anc; путь фрезы из неё делает стойка по G42).
        from ..geometry.lead_collision import (
            PassLeadParams, plan_pass_leads)

        # Масштаб множителей «x_tool_rad» — эквидистанта фрезы ЭТОГО
        # прохода: у доработки режет ТОНКАЯ T3, и лид обязан быть по её
        # эквидистанте (v1.7.29).
        lead_scale = tool_eq
        if is_corner_rework:
            _c_eq = options.extras.get('corner_tool_equidistant')
            if _c_eq and float(_c_eq) > 0:
                lead_scale = float(_c_eq)

        def _x(key, dflt):
            try:
                return float(options.extras.get(key, dflt) or dflt)
            except Exception:
                return dflt

        # ── Параметры лида: ручная правка отдельного ножа побеждает ──
        # Юзер мог задать свои значения для конкретного ножа в режиме
        # «Выделенные» — они лежат в op.attributes['lead_override'].
        # Маппинг столбцов диалога:
        #   доработка — заход из «Внутренний», выход из «Внешний»;
        #   OUTSIDE (внутренний рез) → «Внутренний» на оба;
        #   INSIDE  (внешний рез)    → «Внешний»   на оба.
        _lead_ov = op.attributes.get('lead_override', {})
        if _lead_ov and is_corner_rework:
            _ov_in = _lead_ov.get('lead_inside', {})
            _ov_out = _lead_ov.get('lead_outside', {})
        elif _lead_ov and tp.side == ContourSide.OUTSIDE:
            _ov_in = _ov_out = _lead_ov.get('lead_inside', {})
        elif _lead_ov and tp.side == ContourSide.INSIDE:
            _ov_in = _ov_out = _lead_ov.get('lead_outside', {})
        else:
            _ov_in = _ov_out = {}

        # Автоподбор отключаем, если позицию задал юзер (ручная правка
        # или режим «Все элементы»): его точка — закон, автосдвиг её
        # «исправлял» и уводил заход не туда (41000/121555). Коллизия при
        # этом всё равно детектится и подсветится в превью красным.
        _proj_lead_mode = 0
        try:
            _proj_lead_mode = int(getattr(project, 'attributes', {})
                                  .get('lead_mode', 0))
        except Exception:
            _proj_lead_mode = 0

        _lead_params = PassLeadParams(
            pass_side=tp.side.name,
            is_corner_rework=bool(is_corner_rework),
            tool_offset=tool_offset,
            lead_scale=lead_scale,
            # Своих множителей у доработки больше нет (v1.7.61): угол
            # и карман — тот же проход, что основной, только фрезой с
            # меньшей эквидистантой. Масштаб лида задаёт lead_scale.
            length_mult=1.0,
            radius_mult=1.0,
            extra_mm=0.0,
            entry_enabled=(tp.entry.enabled and tp.entry.style in (
                LeadStyle.LINE_ARC_TANGENTIAL, LeadStyle.LINE)),
            entry_style=('line' if tp.entry.style == LeadStyle.LINE
                         else 'line_arc'),
            entry_angle=_ov_in.get('angle', tp.entry.approach_angle),
            entry_length=_ov_in.get('length',
                                    tp.entry.line_length_x_tool_rad),
            entry_radius=_ov_in.get('length',
                                    tp.entry.arc_radius_x_tool_rad),
            exit_enabled=(tp.exit.enabled and tp.exit.style in (
                LeadStyle.LINE_ARC_TANGENTIAL, LeadStyle.LINE)),
            exit_style=('line' if tp.exit.style == LeadStyle.LINE
                        else 'line_arc'),
            exit_angle=_ov_out.get('angle', tp.exit.approach_angle),
            exit_length=_ov_out.get('length',
                                    tp.exit.line_length_x_tool_rad),
            exit_radius=_ov_out.get('length',
                                    tp.exit.arc_radius_x_tool_rad),
            overlap=pending_overlap,
            no_avoid=bool(_lead_ov) or (_proj_lead_mode == 1),
            # Точка захода из превью. Пост ставит старт контура туда,
            # но подбор не выключает: он начинает с этой позиции, и
            # безопасная точка остаётся на месте, а небезопасная
            # исправляется (v1.7.57).
            entry_hint=(op.attributes.get('_lead_entry', {})
                        .get(tp.side.name)),
        )

        # Соседние ножи нужны аварийному подбору лида доработки.
        _nb = []
        if is_corner_rework:
            try:
                _klay = project.get_layer_by_name("Knife")
                for _go in (getattr(_klay, 'geometries', ()) if _klay
                            else ()):
                    if (_go is not None and _go.is_closed
                            and _go.polypath is not None and _go is not geom):
                        _nb.append(_go.polypath)
            except Exception:
                _nb = []

        _plan = plan_pass_leads(
            polypath, geom, _lead_params,
            *self._project_contours_cache(project),
            neighbours=_nb,
            log=lambda msg: _mach_log(options, geom, tp, msg))

        lead_in_geom = _plan.lead_in_geom
        lead_out_geom = _plan.lead_out_geom
        # Осевую перечитываем ТОЛЬКО если планировался заход: подбор мог
        # сдвинуть старт, а перебег — разомкнуть контур. Без захода
        # остаётся срез по start_t/end_t, сделанный выше.
        if _lead_params.entry_enabled:
            polypath = _plan.axis
            segments = polypath.segments
            if not segments:
                return line_no
            contour_start_point = segments[0].a
            contour_start_tangent = segments[0].tangent_at_start()
            contour_end_point = segments[-1].b
            contour_end_tangent = segments[-1].tangent_at_end()

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
        w(f"N{line_no} G1 Z0 F{op.settings.feed_plunge} ;40,10")
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
                # Дуга захода — ОБЫЧНАЯ G2/G3 с радиусом (v1.7.50).
                #
                # Раньше здесь был канонический цикл G12: станок сам
                # достраивал дугу подхода. Он её обрабатывает не всегда
                # корректно — редко, но выскакивает, поэтому Anderson
                # переделал свой пост (AEC_GVM_MTX_3D_V2_17.amp,
                # правка V2_14 от 2026-10-06), и теперь слот `;50,10`
                # пишется шаблоном
                #     N[N] G2 X[AX] Y[AY] R[R] F[FC] ;50,10 G12
                # то есть настоящей дугой, а `G12` осталось в конце
                # строки комментарием-маркером.
                #
                # Направление задаёт сам блок поста: `$50` — это G2,
                # `$60` — G3, поэтому у дуги против часовой и тег
                # меняется на `;60,10`. Намотку берём из своей же
                # геометрии лида.
                #
                # `SCLN(2)` со строки дуги ушёл: он стоит строкой выше,
                # на включении компенсации (G41/G42).
                # Подача на строке дуги НЕ печатается: она модальная и
                # уже стоит строкой выше, на включении компенсации.
                # Маркер `G12` в конце строки сохраняется — по нему
                # оператор видит, что это заход (эталон 124173_90_all_R
                # нового поста: `G2 X.. Y.. R1.3 ;50,10 G12`).
                g_arc = "G3" if arc.ccw else "G2"
                tag_in = ";60,10 G12" if arc.ccw else ";50,10 G12"
                w(f"N{line_no} {g_arc} X{self.format_coord(arc.b[0])} "
                  f"Y{self.format_coord(arc.b[1])} "
                  f"R{self.format_coord(arc.radius)} {tag_in}")
                line_no += 1
            # LINE (Альфакам-стиль): дуги нет, прямая уже доходит до 
            # contour_start_point — больше ничего не нужно.
        else:
            w(f"N{line_no} {gcomp} X{self.format_coord(contour_start_point[0])} "
              f"Y{self.format_coord(contour_start_point[1])} "
              f"F{op.settings.feed_cut} SCLN(2)")
            line_no += 1
        
        # ── ДВИЖЕНИЯ ПО КОНТУРУ (modal G-code как в AlphaCAM) ──
        # G-код указывается ТОЛЬКО при смене (G1 → G2, G2 → G3, G3 → G1). 
        # Последующие строки без явного G наследуют режим. Это уменьшает 
        # размер .anc в ~2× (у AlphaCAM 232 строки на нож, без modal у нас
        # было 106; с modal ~110 строк на нож).
        # После lead-in модальный режим станка — G12 (активация
        # SCLN-компенсации), а НЕ G1. Поэтому ПЕРВЫЙ сегмент тела обязан
        # нести ЯВНЫЙ G-код (G1 для линии, G2/G3 для дуги) — иначе станок
        # получает голые координаты после G12 и не может применить
        # компенсацию к прямому входу:
        #   «Internal error: no correction method for actual block».
        # Ставим last_g в None-подобный маркер, чтобы первый сегмент
        # ГАРАНТИРОВАННО напечатал свой G-код (даже G1 для линии).
        # РАНЬШЕ здесь было last_g="G1" — и когда первый сегмент контура
        # линия, g_prefix пустел (last_g уже G1), строка шла без G1 →
        # компенсация падала на кругах, начинающихся с прямого сегмента.
        last_g = None  # первый сегмент ВСЕГДА печатает явный G-код
        # ── Комментарии-метки как у эталонного AlphaCAM-постпроцессора ──
        # По стороннему конвейеру (внешние симуляторы/QA-инструменты для
        # Anderson GVM) метки в конце строки идентифицируют роль движения:
        #   ;40,20 — линейный проход по контуру (G1 body)
        #   ;50,21 — круговое движение G2 по контуру
        #   ;60,21 — круговое движение G3 по контуру
        # Номера взяты из шаблона AEC GVM MTX V2_13.amp (общие случаи $40,20 /
        # $50,21 / $60,21). Не влияют на исполнение, но нужны другим прогам.
        # ── ДРОБЛЕНИЕ ДУГ >90° (перед самой эмиссией) ──
        # R-дуга (G2/G3 X Y R) однозначна только при развороте <180°: станок
        # по знаку R берёт МЕНЬШУЮ дугу. Дуга >180° (круг из 2 полудуг,
        # Knife_56: 214°) выводилась как её малое дополнение → полукруг/эллипс,
        # брак. Дробим на квадранты ≤90° — R всегда однозначен. ВАЖНО: здесь,
        # у самого цикла, т.к. выше segments перезаписывается из polypath после
        # автоподбора лида (иначе split терялся).
        import math as _msplit
        _split_segs = []
        for _sg in segments:
            if isinstance(_sg, Arc):
                _cx, _cy = _sg.center
                _a0 = _msplit.atan2(_sg.a[1]-_cy, _sg.a[0]-_cx)
                _a1 = _msplit.atan2(_sg.b[1]-_cy, _sg.b[0]-_cx)
                if _sg.ccw:
                    _sw = _a1 - _a0
                    while _sw <= 1e-9:
                        _sw += 2*_msplit.pi
                else:
                    _sw = _a0 - _a1
                    while _sw <= 1e-9:
                        _sw += 2*_msplit.pi
                if _sw > _msplit.pi/2 + 1e-3:  # >90°
                    # ЗАЩИТА: у вырожденной дуги (начало≈конец после offset/
                    # despike) sweep вычисляется как 360°, и дробление
                    # «раздувает» её в ПОЛНЫЙ КРУГ из 4-5 дуг с одинаковым R
                    # (паразитная окружность в .anc). Такие не дробим.
                    if _msplit.hypot(_sg.b[0]-_sg.a[0],
                                     _sg.b[1]-_sg.a[1]) < 1e-4:
                        _split_segs.append(_sg)
                        continue
                    _n = int(_msplit.ceil(_sw / (_msplit.pi/2)))
                    _step = _sw / _n * (1.0 if _sg.ccw else -1.0)
                    _prev = _sg.a
                    for _k in range(1, _n):
                        _ang = _a0 + _step*_k
                        _pt = (_cx + _sg.radius*_msplit.cos(_ang),
                               _cy + _sg.radius*_msplit.sin(_ang))
                        _split_segs.append(Arc(a=_prev, b=_pt,
                                              center=_sg.center, ccw=_sg.ccw))
                        _prev = _pt
                    _split_segs.append(Arc(a=_prev, b=_sg.b,
                                          center=_sg.center, ccw=_sg.ccw))
                    continue
            _split_segs.append(_sg)
        segments = _split_segs

        # ── ФИЛЬТР ВЫРОЖДЕННЫХ СЕГМЕНТОВ (v1.7.10) ──
        # Дуга нулевой длины с заданным R для NUM НЕ ОПРЕДЕЛЕНА: начало
        # совпадает с концом, и контроллер обычно трактует её как полную
        # окружность — станок крутит петлю на месте. Такая дуга получалась
        # при замыкании контура: последний сегмент приходил ровно в точку
        # старта, а следом шёл его нулевой остаток от сдвига старта.
        #
        # Нулевые ПРЯМЫЕ безобидны (G1 в ту же точку — просто холостая
        # строка), но выкидываем и их: лишние строки в программе не нужны.
        # Сравниваем ОТФОРМАТИРОВАННЫЕ координаты, а не сырые: сегмент
        # может быть короче разрешения формата (5 знаков), тогда в
        # программе он печатается как движение в ту же точку, хотя
        # геометрически ненулевой. Порог по длине такие не ловит.
        _clean = []
        for _sg in segments:
            if (self.format_coord(_sg.a[0]) == self.format_coord(_sg.b[0])
                    and self.format_coord(_sg.a[1])
                    == self.format_coord(_sg.b[1])):
                continue
            _clean.append(_sg)
        if _clean:
            segments = _clean

        for seg in segments:
            if isinstance(seg, Line):
                end = seg.b
                # G1 в modal вставляем только если предыдущий был G2/G3
                g_prefix = "G1 " if last_g != "G1" else ""
                w(f"N{line_no} {g_prefix}X{self.format_coord(end[0])} "
                  f"Y{self.format_coord(end[1])} ;40,20")
                last_g = "G1"
            elif isinstance(seg, Arc):
                end = seg.b
                g_arc = "G3" if seg.ccw else "G2"
                # G2/G3 вставляем только при смене направления
                g_prefix = f"{g_arc} " if last_g != g_arc else ""
                tag = ";60,21" if seg.ccw else ";50,21"
                w(f"N{line_no} {g_prefix}X{self.format_coord(end[0])} "
                  f"Y{self.format_coord(end[1])} R{self.format_coord(seg.radius)} {tag}")
                last_g = g_arc
            line_no += 1
        
        # ── LEAD-OUT: дуга G12/G13 + прямая с G40 ──
        # Аналогично lead_in: G13 для дуги выхода (если style=line_arc),
        # или сразу G40 на внешнюю точку (если style=line, без дуги).
        if lead_out_geom is not None:
            arc = lead_out_geom.arc
            if arc is not None:
                # Дуга выхода — обычная G2/G3 с радиусом (v1.7.50),
                # как и заход. Слот `;50,15` в новом посте:
                #     N[N] G2 X[AX] Y[AY] R[R] F[FC] ;50,15 G13
                # `SCLN(1)` ушёл на следующую строку (`;40,18`), где он
                # и так стоит.
                # G2/G3 здесь МОДАЛЬНЫЙ: если контур закончился дугой
                # того же направления, буква не печатается. В эталоне
                # нового поста есть оба варианта — `N503 G2 ... ;50,15
                # G13` и `N728 X.. R1.3 ;50,15 G13` без G2.
                g_arc = "G3" if arc.ccw else "G2"
                g_prefix = f"{g_arc} " if last_g != g_arc else ""
                tag_out = ";60,15 G13" if arc.ccw else ";50,15 G13"
                w(f"N{line_no} {g_prefix}X{self.format_coord(arc.b[0])} "
                  f"Y{self.format_coord(arc.b[1])} "
                  f"R{self.format_coord(arc.radius)} {tag_out}")
                last_g = g_arc
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
        if len(points) < 1:
            return line_no

        w(f"N{line_no} M3 S70000 ;20,1")
        line_no += 1

        # Первый репер: ;210,1 → ;211,4 (первый прокол).
        # Разметка совместима с AlphaCAM: $210 = "First Hole",
        # $211 = "Next holes" в AEC GVM MTX V2_13.amp.
        p0 = points[0]
        w(f"N{line_no} G0 X{self.format_coord(p0[0])} "
          f"Y{self.format_coord(p0[1])} ;210,1")
        line_no += 1
        w(f"N{line_no} G1 Z10 F5000 ;210,2")
        line_no += 1
        w(f"N{line_no} G0 Z3 ;210,3")
        line_no += 1
        w(f"N{line_no} G1 Z{self.format_coord(depth)} F1500 ;211,4")
        line_no += 1
        w(f"N{line_no} Z10 F5000 ;211,1")
        line_no += 1

        # Все последующие реперы: цикл (XY → Z<depth> → Z10).
        # Раньше эмиттер обрабатывал только points[1] и игнорировал
        # points[2:] — заказ с 3+ реперами (121561: FID5/FID6/FID7)
        # получал пропуски. Теперь сверлятся все активные точки.
        for pn in points[1:]:
            w(f"N{line_no} G0 X{self.format_coord(pn[0])} "
              f"Y{self.format_coord(pn[1])} ;211,2")
            line_no += 1
            w(f"N{line_no} G1 Z{self.format_coord(depth)} F1500 ;211,3")
            line_no += 1
            w(f"N{line_no} Z10 F5000 ;211,1")
            line_no += 1

        # Закрывающий подъём (;200,1 = отмена цикла в исходном .amp)
        # Уже эмитировали последний Z10 в цикле — заменяем его тег.
        # Проще: убираем последний ;211,1 и добавляем ;200,1
        # (в текстовом эмиттере переписать сложно, оставляем последний
        # ;211,1 и добавляем явный ;200,1 в отдельной строке).
        w(f"N{line_no} ;")
        line_no += 1
        return line_no


# ─────────────────────────────────────────────────────────────────────────
#  АВТО-РЕГИСТРАЦИЯ
# ─────────────────────────────────────────────────────────────────────────

PostRegistry.register(MtxAndersonGVM())
