"""
geometry/corner_detect.py — детектор острых углов на контуре.

Острый угол = точка стыка двух сегментов, где касательные расходятся 
на угол меньше заданного порога. Используется для:
    - выделения участков, недоступных основной фрезе (узкие места)
    - разделения файлов: основная программа vs corner / corner3D

Алгоритм:
    1. Идём по сегментам Polypath попарно (segments[i], segments[i+1])
    2. Касательная в конце segments[i] и в начале segments[i+1]
    3. Угол между ними. Если < threshold → острый угол
    4. Возвращаем список (sharp_angle, point, index_segment)

Для замкнутого контура также проверяется стык последнего и первого
сегментов.
"""

from __future__ import annotations
from typing import List, Tuple
import math

from .primitives import Line, Arc, Polypath, Segment, Point


def _segment_tangent_at_start(seg: Segment) -> Tuple[float, float]:
    """Единичная касательная в начале сегмента."""
    if isinstance(seg, Line):
        return seg.tangent_at_start()
    return seg.tangent_at_start()


def _segment_tangent_at_end(seg: Segment) -> Tuple[float, float]:
    """Единичная касательная в конце сегмента."""
    if isinstance(seg, Line):
        return seg.tangent_at_end()
    return seg.tangent_at_end()


def angle_between(t1: Tuple[float, float], t2: Tuple[float, float]) -> float:
    """Угол между двумя единичными касательными, в градусах [0..180].
    
    180° = касательные сонаправлены (нет излома)
    0° = касательные направлены навстречу (полный разворот)
    
    Угол поворота контура в точке = 180 - angle_between.
    """
    # Скалярное произведение, ограничиваем [-1, 1] от ошибок округления
    dot = t1[0]*t2[0] + t1[1]*t2[1]
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))


from dataclasses import dataclass


@dataclass
class SharpCorner:
    """Найденный острый угол на контуре."""
    point: Point             # координаты вершины
    segment_index: int       # индекс сегмента, ПОСЛЕ которого изгиб
                             # (т.е. угол между segments[i].end и segments[i+1].start)
    interior_angle: float    # внутренний угол излома, градусы
                             # (180° = плавный, 0° = полный разворот)
    turn_sign: int           # +1 = поворот влево (CCW), -1 = вправо (CW)


def detect_sharp_corners(polypath: Polypath,
                         threshold_deg: float = 90.0
                         ) -> List[SharpCorner]:
    """Находит острые углы в контуре.
    
    Args:
        polypath: путь для анализа
        threshold_deg: внутренний угол меньше этого считается острым
                       (90° = прямой угол, 60° = более острый чем прямой)
    
    Returns:
        Список SharpCorner с координатами и параметрами каждого острого угла.
    """
    if not polypath or len(polypath.segments) < 2:
        return []
    
    corners: List[SharpCorner] = []
    segments = polypath.segments
    n = len(segments)
    
    # Пары соседних сегментов
    pairs = [(i, i+1) for i in range(n - 1)]
    # Для замкнутого контура — последний → первый
    if polypath.closed:
        pairs.append((n - 1, 0))
    
    for i, j in pairs:
        s1 = segments[i]
        s2 = segments[j]
        
        t1 = _segment_tangent_at_end(s1)
        t2 = _segment_tangent_at_start(s2)
        
        # Угол между касательными
        a = angle_between(t1, t2)
        
        # Внутренний угол излома (плавно = 180°, острый = меньше)
        # Если касательные сонаправлены (a=0) → нет излома (180°)
        # Если разворот (a=180) → 0°
        interior = 180.0 - a
        
        if interior < threshold_deg:
            # Знак поворота: через 2D кросс касательных
            # Положительный = CCW (влево), отрицательный = CW (вправо)
            cross = t1[0]*t2[1] - t1[1]*t2[0]
            turn_sign = 1 if cross > 0 else -1
            
            corners.append(SharpCorner(
                point=s1.b,                # конец первого сегмента = угол
                segment_index=i,
                interior_angle=interior,
                turn_sign=turn_sign,
            ))
    
    return corners


def classify_corners_for_tooling(corners: List[SharpCorner],
                                 thin_threshold: float = 60.0,
                                 ) -> Tuple[List[SharpCorner], List[SharpCorner]]:
    """Делит острые углы на две группы:
        - умеренно острые (> thin_threshold) → corner (тонкая фреза)
        - очень острые (≤ thin_threshold)    → corner3D (3D-фреза)
    
    Logic: чем острее угол, тем тоньше должна быть фреза, чтобы добраться
    до самой вершины. Если угол меньше определённого порога, основная 
    тонкая 2D-фреза не подойдёт — нужна 3D-фреза с малым tip_diameter.
    
    Returns:
        (corner_list, corner3d_list)
    """
    corner_2d = []
    corner_3d = []
    for c in corners:
        if c.interior_angle <= thin_threshold:
            corner_3d.append(c)
        else:
            corner_2d.append(c)
    return corner_2d, corner_3d


# ─────────────────────────────────────────────────────────────────────────
#  ПОИСК УГЛОВ ПО РАДИУСУ СКРУГЛЕНИЯ
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class GeometricCorner:
    """Угол найденный по геометрии — дуга с малым радиусом скругления.
    
    Это не «излом касательных», а реальная дуга в контуре, которую основная
    фреза не может выточить (её пятка больше радиуса). Тонкая фреза 0.6мм
    проходит по этой дуге как по обычному пути с компенсацией.
    """
    arc_index: int           # индекс дуги в polypath.segments
    arc_center: Point        # центр дуги (= центр скругления)
    arc_radius: float        # радиус скругления (мм)
    arc_start: Point         # начало дуги (стык с предыдущим сегментом)
    arc_end: Point           # конец дуги (стык со следующим сегментом)
    ccw: bool                # направление дуги (True = против часовой)


def _point_at_arclen(polypath: Polypath, target: float) -> Point:
    """Точка на контуре на расстоянии target мм вдоль пути (с обёрткой по
    замкнутому контуру). Линейная интерполяция по хорде сегмента — достаточно
    для оценки кривизны."""
    segs = polypath.segments
    total = sum(s.length() for s in segs)
    if total <= 0:
        return segs[0].a
    target %= total
    acc = 0.0
    for s in segs:
        sl = s.length()
        if acc + sl >= target:
            t = (target - acc) / sl if sl > 0 else 0.0
            return (s.a[0] + (s.b[0] - s.a[0]) * t,
                    s.a[1] + (s.b[1] - s.a[1]) * t)
        acc += sl
    return segs[-1].b


def _true_curvature_radius(polypath: Polypath, arc_index: int,
                           window_mm: float = 1.5) -> float:
    """Оценивает ИСТИННЫЙ радиус кривизны контура в районе дуги arc_index,
    вписывая окружность по 3 точкам на расстоянии ±window_mm вдоль пути.

    Зачем: биарк-аппроксимация гладких кривых из .ai оставляет множество
    крошечных дуг (R может быть < 0.7мм), которые НЕ являются реальными
    острыми углами — это шум аппроксимации. Их собственный радиус мал, но
    истинная кривизна контура в этом месте большая (R велик). Окружность по
    точкам на физическом окне усредняет биарк-шум и даёт реальный радиус.

    Returns:
        Радиус вписанной окружности (мм). Большое значение = пологий участок.
    """
    segs = polypath.segments
    # позиция середины дуги вдоль пути
    acc = 0.0
    mid = 0.0
    for i, s in enumerate(segs):
        if i == arc_index:
            mid = acc + s.length() / 2.0
            break
        acc += s.length()
    A = _point_at_arclen(polypath, mid - window_mm)
    B = _point_at_arclen(polypath, mid)
    C = _point_at_arclen(polypath, mid + window_mm)
    a = math.dist(B, C)
    b = math.dist(A, C)
    c = math.dist(A, B)
    area = abs((B[0] - A[0]) * (C[1] - A[1]) -
               (C[0] - A[0]) * (B[1] - A[1])) / 2.0
    if area < 1e-9:
        return 1e9  # три точки почти на прямой → бесконечный радиус
    return a * b * c / (4.0 * area)


def _arc_swept_deg(arc: Arc) -> float:
    """Угол разворота дуги (на сколько поворачивает касательная), в градусах.

    Ключевой признак РЕАЛЬНОГО угла: малый радиус + БОЛЬШОЙ разворот.
    Биарк-аппроксимация гладких кривых оставляет крошечные дуги (R<0.7) с
    МАЛЫМ разворотом (единицы градусов) — это шум, не углы. Настоящее тугое
    скругление разворачивает касательную на десятки градусов (угол ~90° даёт
    дугу ~90°).
    """
    a0 = math.atan2(arc.a[1] - arc.center[1], arc.a[0] - arc.center[0])
    a1 = math.atan2(arc.b[1] - arc.center[1], arc.b[0] - arc.center[0])
    d = a1 - a0
    if arc.ccw and d < 0:
        d += 2 * math.pi
    if (not arc.ccw) and d > 0:
        d -= 2 * math.pi
    return abs(math.degrees(d))


def detect_geometric_corners(polypath: Polypath,
                             radius_threshold_mm: float = 0.7,
                             min_swept_deg: float = 60.0,
                             max_swept_deg: float = 180.0,
                             ) -> List[GeometricCorner]:
    """Находит РЕАЛЬНЫЕ острые углы (тугие скругления), отсеивая биарк-шум.

    Каждый такой угол = «скругление слишком мало для основной фрезы» =
    кандидат на обработку тонкой фрезой 0.6 в программе _corner.anc.

    Дуга считается углом, только если выполнены ВСЕ условия:
      1. собственный радиус дуги < radius_threshold_mm;
      2. дуга разворачивает касательную на >= min_swept_deg градусов;
      3. дуга разворачивает касательную на <= max_swept_deg градусов.
    
    Второе условие (>= 60°) отсеивает:
      - биарк-шум (единицы градусов),
      - тангенциальные соединения на амёбах/почках (30° разворот),
      - плавные соединения гнутых сторон прямоугольников (~30-45°).
    Настоящий острый угол ≥ 60° (например, 90° угла прямоугольника с 
    маленьким скруглением даёт дугу на 90°).
    
    Третье условие (<= 180°) отсеивает «фальшивые» углы, где дуга 
    случайно оказалась < порога, а разворачивает 200-350° (обход 
    вокруг выпуклости или петля).

    Args:
        polypath: контур ножа
        radius_threshold_mm: порог радиуса скругления (мм). По умолчанию 0.7.
        min_swept_deg: минимальный угол разворота дуги (град). По умолчанию 60.
        max_swept_deg: максимальный угол разворота (град). По умолчанию 180.

    Returns:
        Список GeometricCorner. Пусто если нет острых углов.
    """
    if not polypath or not polypath.segments:
        return []
    
    corners: List[GeometricCorner] = []
    for i, seg in enumerate(polypath.segments):
        if not isinstance(seg, Arc):
            continue
        if seg.radius >= radius_threshold_mm:
            continue
        # Отсев биарк-шума: настоящий угол разворачивается значительно
        swept = _arc_swept_deg(seg)
        if swept < min_swept_deg:
            continue
        # Отсев ложных углов: реальный тугой угол не может развернуть 
        # > 180° (это будет обход вокруг выпуклости, а не угол)
        if swept > max_swept_deg:
            continue
        corners.append(GeometricCorner(
            arc_index=i,
            arc_center=seg.center,
            arc_radius=seg.radius,
            arc_start=seg.a,
            arc_end=seg.b,
            ccw=seg.ccw,
        ))
    return corners




@dataclass
class CornerGroup:
    """Логический угол ножа — группа близких дуг малого радиуса.
    
    На скруглении угла биарк-фит может создать несколько мелких дуг.
    Эта группа объединяет их и даёт цельную картину: где угол, в каком 
    направлении идёт обход, какие сегменты охватывает.
    """
    center: Point            # средняя точка центров всех дуг группы
    radius: float            # средний радиус (мм)
    first_idx: int           # индекс первой дуги в polypath
    last_idx: int            # индекс последней дуги
    start_point: Point       # начало первой дуги в группе (вход в угол)
    end_point: Point         # конец последней дуги в группе (выход из угла)
    apex: Point              # «вершина угла» — самая дальняя от центра bbox 
                             # точка дуг группы (физический пик угла)
    ccw: bool                # направление обхода (от первой дуги группы)


def group_corner_arcs(corners: List[GeometricCorner],
                      proximity_mm: float = 2.0
                      ) -> List[CornerGroup]:
    """Группирует близкие дуги в логические углы.
    
    Дуги, центры которых находятся в пределах proximity_mm друг от друга, 
    объединяются в одну группу — это всё один физический угол ножа 
    (биарк-фит разбил скругление на несколько мелких дуг).
    
    Args:
        corners: список найденных дуг (от detect_geometric_corners)
        proximity_mm: радиус слияния (мм). По умолчанию 2мм — обычные 
            углы скругления.
    
    Returns:
        Список CornerGroup — по одному на каждый физический угол.
    """
    if not corners:
        return []
    
    # Сортируем по индексу — соседние дуги обычно идут подряд в контуре
    sorted_corners = sorted(corners, key=lambda c: c.arc_index)
    
    groups: List[List[GeometricCorner]] = []
    current_group: List[GeometricCorner] = [sorted_corners[0]]
    
    for c in sorted_corners[1:]:
        # Сравниваем центр с центром последней дуги в текущей группе
        last_c = current_group[-1]
        dx = c.arc_center[0] - last_c.arc_center[0]
        dy = c.arc_center[1] - last_c.arc_center[1]
        d = math.sqrt(dx*dx + dy*dy)
        if d <= proximity_mm:
            current_group.append(c)
        else:
            groups.append(current_group)
            current_group = [c]
    if current_group:
        groups.append(current_group)
    
    # Преобразуем в CornerGroup, отбрасывая ложные группы
    # (которые не дают реального изменения направления — это просто 
    # плавная кривая аппроксимированная биарком).
    result: List[CornerGroup] = []
    for grp in groups:
        # Средний центр и радиус
        cx = sum(c.arc_center[0] for c in grp) / len(grp)
        cy = sum(c.arc_center[1] for c in grp) / len(grp)
        avg_r = sum(c.arc_radius for c in grp) / len(grp)
        
        first = grp[0]
        last = grp[-1]
        
        # Суммарный угол поворота на группе. Для дуги это |arc.length / R|
        # в радианах. На реальном угле ~90° = π/2. На плавной кривой 
        # с биарк-аппроксимацией суммарный поворот может быть очень мал.
        total_turn = 0.0
        for c in grp:
            # Дугу аппроксимируем |start - end| / R, без учёта направления.
            # Лучше: угол между радиус-векторами от центра до start и end.
            sx = c.arc_start[0] - c.arc_center[0]
            sy = c.arc_start[1] - c.arc_center[1]
            ex = c.arc_end[0] - c.arc_center[0]
            ey = c.arc_end[1] - c.arc_center[1]
            # Угол между векторами
            dot = sx*ex + sy*ey
            mag = math.sqrt(sx*sx+sy*sy) * math.sqrt(ex*ex+ey*ey)
            if mag < 1e-9:
                continue
            cos_a = max(-1.0, min(1.0, dot / mag))
            total_turn += math.acos(cos_a)
        
        # Минимум 20° (π/9) — реальный угол ножа делает резкий поворот.
        # На плавной кривой биарк даёт малые повороты для каждого 
        # биарк-сегмента и суммарно меньше 20° за группу.
        if total_turn < math.radians(20):
            continue
        
        # Apex угла — самая дальняя от среднего центра точка дуг
        all_points: List[Point] = []
        for c in grp:
            all_points.append(c.arc_start)
            all_points.append(c.arc_end)
        apex = max(all_points, key=lambda p: (p[0]-cx)**2 + (p[1]-cy)**2)
        
        result.append(CornerGroup(
            center=(cx, cy),
            radius=avg_r,
            first_idx=first.arc_index,
            last_idx=last.arc_index,
            start_point=first.arc_start,
            end_point=last.arc_end,
            apex=apex,
            ccw=first.ccw,
        ))
    return result


# ─────────────────────────────────────────────────────────────────────────
#  ПОИСК ПОЛНОСТЬЮ ОСТРЫХ УГЛОВ (БЕЗ СКРУГЛЕНИЯ) — ДЛЯ 3D
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class SharpAngularCorner:
    """Полностью острый угол: стык двух сегментов БЕЗ дуги между ними.
    
    Это узкое место, недоступное обычной (даже тонкой) 2D-фрезе, потому
    что у фрезы есть конечный радиус пятки. Нужна 3D-фреза, которая
    может опуститься в самую точку угла на нужную глубину.
    
    Считаем «полностью острым» углом стык где:
        - касательные на стыке расходятся больше чем sharp_threshold_deg
          (то есть внутренний угол меньше 180 − sharp_threshold)
        - и НЕТ дуги малого радиуса между сегментами
    """
    point: Point             # вершина угла (стык двух сегментов)
    segment_index: int       # индекс сегмента, после которого излом
    interior_angle: float    # внутренний угол излома (градусы)
    turn_sign: int           # +1 = CCW, -1 = CW


def detect_pointed_corners(polypath: Polypath,
                           sharp_threshold_deg: float = 30.0
                           ) -> List[SharpAngularCorner]:
    """Находит полностью острые углы — Line→Line стыки с резким изломом.
    
    «Острый угол без скругления» = стык двух СЕГМЕНТОВ (любых, чаще Line→Line,
    но возможно и Arc→Line/Line→Arc для больших радиусов), где касательные
    расходятся больше чем sharp_threshold_deg. И НЕ соседствует с дугой
    малого радиуса (иначе это уже скруглённый угол → задача для 2D _corner).
    
    Args:
        polypath: контур для анализа
        sharp_threshold_deg: минимальное расхождение касательных, чтобы 
            считать стык острым углом (по умолчанию 30°). Меньше — мягкий
            переход, не угол.
    
    Returns:
        Список SharpAngularCorner — точечные острые углы для 3D-обработки.
    """
    if not polypath or len(polypath.segments) < 2:
        return []
    
    segments = polypath.segments
    n = len(segments)
    
    # Радиус считаем «маленьким» если меньше 1мм — такие дуги это уже 
    # скруглённые углы (обрабатываются в 2D _corner). Для 3D нужны точные 
    # стыки без видимого скругления.
    SMALL_ARC_R = 1.0
    
    # Стык: сегменты i и i+1 (для замкнутого — последний и первый)
    pairs = [(i, i+1) for i in range(n - 1)]
    if polypath.closed:
        pairs.append((n - 1, 0))
    
    result: List[SharpAngularCorner] = []
    for i, j in pairs:
        s1 = segments[i]
        s2 = segments[j]
        
        # Если ХОТЯ БЫ ОДИН из соседних сегментов — короткая дуга малого
        # радиуса, то это скруглённый угол (2D задача), пропускаем.
        if isinstance(s1, Arc) and s1.radius < SMALL_ARC_R:
            continue
        if isinstance(s2, Arc) and s2.radius < SMALL_ARC_R:
            continue
        
        t1 = s1.tangent_at_end()
        t2 = s2.tangent_at_start()
        
        # Угол между касательными в градусах
        a = angle_between(t1, t2)
        # Внутренний угол излома (180° = плавно, 0° = разворот)
        interior = 180.0 - a
        
        if a >= sharp_threshold_deg:  # касательные расходятся достаточно
            cross = t1[0]*t2[1] - t1[1]*t2[0]
            turn_sign = 1 if cross > 0 else -1
            result.append(SharpAngularCorner(
                point=s1.b,
                segment_index=i,
                interior_angle=interior,
                turn_sign=turn_sign,
            ))
    
    return result


def has_pointed_corners(polypath: Polypath,
                        sharp_threshold_deg: float = 30.0) -> bool:
    """Быстрая проверка: есть ли в контуре хотя бы один полностью острый
    угол (для 3D-обработки). Используется в UI для авто-определения 
    нужности _corner3D.anc программы.
    """
    return len(detect_pointed_corners(polypath, sharp_threshold_deg)) > 0


def has_small_radius_corners(polypath: Polypath,
                             radius_threshold_mm: float = 0.7) -> bool:
    """Быстрая проверка: есть ли в контуре скруглённые углы с радиусом 
    меньше порога (нужны для 2D _corner.anc программы). Используется в UI.
    """
    return len(detect_geometric_corners(polypath, radius_threshold_mm)) > 0
