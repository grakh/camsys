"""
geometry/path_offset.py — операции над Polypath по параметру длины:
получение точки на расстоянии d от начала, вырезание участка контура.

Используется для:
    - Сдвига точки старта обхода вдоль контура (параметр start_offset
      из EntryExitConfig / поле «Смещение по» из диалога Cutting)
    - Получения участка контура [t_start, t_end] для CORNER_REWORK
    - Вырезания подучастка между двумя точками для anti-overlap
"""

from __future__ import annotations
from typing import List, Tuple, Optional
import math

from .primitives import Line, Arc, Polypath, Segment, Point, EPS


def segment_length(seg: Segment) -> float:
    """Длина одного сегмента."""
    return seg.length()


def polypath_total_length(polypath: Polypath) -> float:
    """Общая длина контура в мм."""
    return sum(segment_length(s) for s in polypath.segments)


def point_and_tangent_at_distance(polypath: Polypath, distance: float
                                  ) -> Optional[Tuple[Point, Point, int, float]]:
    """Находит точку и касательную на контуре на расстоянии `distance`
    от начала первого сегмента.
    
    Если distance отрицательный — отсчитывается с КОНЦА контура
    (для замкнутого — циклически назад по контуру).
    
    Args:
        polypath: контур
        distance: расстояние вдоль контура, мм
    
    Returns:
        (point, unit_tangent, segment_index, local_t) или None если контур пуст.
        segment_index — индекс сегмента, на котором найдена точка
        local_t — параметр [0..1] внутри сегмента
    """
    if not polypath or not polypath.segments:
        return None
    
    total = polypath_total_length(polypath)
    if total < EPS:
        return None
    
    # Нормализация для замкнутого контура: оборачиваем distance по модулю
    if polypath.closed:
        distance = distance % total
        # Python модуль для отрицательного даёт положительный остаток,
        # это нам и нужно (циклический сдвиг вперёд)
    else:
        # Для открытого контура зажимаем в [0, total]
        distance = max(0.0, min(total, distance))
    
    # Идём по сегментам, накапливая длину
    accumulated = 0.0
    for idx, seg in enumerate(polypath.segments):
        slen = segment_length(seg)
        if accumulated + slen >= distance - EPS:
            # Точка лежит на этом сегменте
            local_dist = distance - accumulated
            local_t = local_dist / slen if slen > EPS else 0.0
            local_t = max(0.0, min(1.0, local_t))
            
            point = seg.point_at(local_t)
            
            # Касательная: для Line — постоянная, для Arc — зависит от точки
            if isinstance(seg, Line):
                tangent = seg.tangent_at_start()
            elif isinstance(seg, Arc):
                # Касательная к дуге в точке: перпендикулярна радиусу,
                # знак зависит от ccw
                cx, cy = seg.center
                px, py = point
                rx, ry = (px - cx, py - cy)
                rlen = math.hypot(rx, ry)
                if rlen > EPS:
                    rx, ry = rx/rlen, ry/rlen
                    # Касательная = perp(r), знак по направлению
                    if seg.ccw:
                        tangent = (-ry, rx)
                    else:
                        tangent = (ry, -rx)
                else:
                    tangent = (1.0, 0.0)
            else:
                tangent = (1.0, 0.0)
            
            return (point, tangent, idx, local_t)
        accumulated += slen
    
    # Дошли до конца — возвращаем последнюю точку
    last = polypath.segments[-1]
    return (last.b, last.tangent_at_end(), len(polypath.segments) - 1, 1.0)


def apply_overlap(polypath: Polypath, overlap: float) -> Polypath:
    """Продляет замкнутый контур на `overlap` мм после точки смыкания.
    
    Назначение: чтобы выход (lead-out) не оказывался в той же точке, что
    вход (lead-in), и заходы/выходы не пересекали друг друга. Фреза прихо-
    дит к месту входа, продолжает движение ещё `overlap` мм (повторяя начало
    контура) и только потом отходит.
    
    Контур становится формально НЕ замкнутым (end != start), но геометрия
    самой петли не меняется — добавляется только небольшое перекрытие 
    в начале.
    
    Args:
        polypath: замкнутый контур после всех shift'ов (НЕ модифицируется)
        overlap: длина перекрытия в мм. >0 — продляет вперёд по обходу,
                 <=0 — без изменений (поддержка Support Tag, отрицательный
                 overlap нужен для других целей и здесь не используется).
    
    Returns:
        Новый Polypath с добавленными сегментами в конце.
    """
    if not polypath or not polypath.closed or not polypath.segments:
        return polypath
    if overlap <= EPS:
        return polypath
    
    # Берём первые overlap мм пути и копируем их в конец.
    # Используем point_and_tangent_at_distance для нахождения точки на 
    # расстоянии overlap от старта.
    end_result = point_and_tangent_at_distance(polypath, overlap)
    if end_result is None:
        return polypath
    end_point, _, end_seg_idx, end_local_t = end_result
    
    # Собираем продолжение: сегменты [0..end_seg_idx-1] полностью + кусок 
    # сегмента end_seg_idx до точки end_point.
    extra_segs: List[Segment] = []
    for k in range(end_seg_idx):
        extra_segs.append(polypath.segments[k])
    
    # Последний (частичный) сегмент
    last = polypath.segments[end_seg_idx]
    if isinstance(last, Line):
        extra_segs.append(Line(a=last.a, b=end_point))
    elif isinstance(last, Arc):
        # дуга от last.a до end_point вокруг того же центра
        extra_segs.append(Arc(
            a=last.a, b=end_point,
            center=last.center, ccw=last.ccw
        ))
    else:
        extra_segs.append(last)
    
    new_segs = list(polypath.segments) + extra_segs
    return Polypath(segments=new_segs, closed=False)


def shift_start_along_contour(polypath: Polypath, offset: float) -> Polypath:
    """Сдвигает точку старта замкнутого контура на `offset` мм вдоль обхода.
    
    Открытый контур не сдвигается (возвращается как есть), КРОМЕ случая когда 
    endpoints практически совпадают (< 0.05мм) — тогда работает как для 
    замкнутого. Это нужно потому что offset_polypath_uniform / 
    trim_self_intersections могут флажок closed=False оставить у контура 
    который геометрически замкнут → без этой поблажки автосдвиг lead-in'а 
    молча не работал бы для таких контуров.
    
    Положительное смещение = вперёд по направлению обхода.
    Отрицательное = назад.
    
    Args:
        polypath: исходный контур (НЕ модифицируется)
        offset: смещение в мм
    
    Returns:
        Новый Polypath с другой точкой старта (та же геометрия, но 
        переразложенная относительно новой стартовой точки).
    """
    if not polypath or not polypath.segments:
        return polypath
    if abs(offset) < EPS:
        return polypath
    
    # Проверка на «эффективно замкнутый» — endpoints близко
    is_effectively_closed = polypath.closed
    if not is_effectively_closed:
        first_pt = polypath.segments[0].a
        last_pt = polypath.segments[-1].b
        if math.hypot(first_pt[0]-last_pt[0], first_pt[1]-last_pt[1]) < 0.05:
            is_effectively_closed = True
    
    if not is_effectively_closed:
        return polypath
    
    result = point_and_tangent_at_distance(polypath, offset)
    if result is None:
        return polypath
    
    new_start_point, _, start_idx, local_t = result
    
    # Стратегия:
    #   1. Текущий сегмент start_idx разрезаем в точке local_t.
    #      Вторая половина становится первым сегментом нового контура.
    #   2. Сегменты [start_idx+1 ... end] идут далее как есть.
    #   3. Сегменты [0 ... start_idx-1] идут в самом конце.
    #   4. Первая половина текущего сегмента (до local_t) присоединяется 
    #      в конец, чтобы контур замкнулся обратно в new_start_point.
    
    segs = polypath.segments
    n = len(segs)
    cur = segs[start_idx]
    
    # Разделяем cur на две части в параметре local_t
    if local_t < EPS:
        # Точка точно на начале — просто переставляем циклически
        first_half = []
        second_half = [cur]
    elif local_t > 1.0 - EPS:
        # Точка точно в конце сегмента
        first_half = [cur]
        second_half = []
    else:
        first_half, second_half = _split_segment(cur, local_t)
    
    # Собираем новый контур
    new_segs = []
    # Вторая половина текущего сегмента (с new_start_point до cur.b)
    new_segs.extend(second_half)
    # Сегменты после текущего
    new_segs.extend(segs[start_idx + 1:])
    # Сегменты до текущего
    new_segs.extend(segs[:start_idx])
    # Первая половина текущего сегмента (от cur.a до new_start_point)
    new_segs.extend(first_half)
    
    return Polypath(segments=new_segs, closed=True)


def _split_segment(seg: Segment, t: float) -> Tuple[List[Segment], List[Segment]]:
    """Разделяет сегмент в параметре t на две части.
    Возвращает (first_half, second_half), каждая — список (может быть пустым)."""
    if isinstance(seg, Line):
        mid = seg.point_at(t)
        return ([Line(seg.a, mid)], [Line(mid, seg.b)])
    elif isinstance(seg, Arc):
        mid = seg.point_at(t)
        # Обе части имеют тот же центр и тот же ccw
        return (
            [Arc(seg.a, mid, seg.center, seg.ccw)],
            [Arc(mid, seg.b, seg.center, seg.ccw)],
        )
    raise TypeError(f"Unsupported segment: {type(seg).__name__}")


# ─────────────────────────────────────────────────────────────────────────
#  СТАРТОВАЯ ТОЧКА У УГЛА BBOX
# ─────────────────────────────────────────────────────────────────────────

def polypath_bbox(polypath: Polypath) -> Tuple[float, float, float, float]:
    """Bbox контура: (min_x, min_y, max_x, max_y).
    
    Для дуг учитываются экстремумы окружности (±r по X/Y от центра),
    но ТОЛЬКО если они лежат на самой дуге (в её угловом диапазоне).
    Это критично: на длинных квазипрямых биарковских дугах радиус может
    быть миллионы мм, и без проверки попадания bbox распухает на полпланеты.
    """
    xs, ys = [], []
    for s in polypath.segments:
        xs.extend([s.a[0], s.b[0]])
        ys.extend([s.a[1], s.b[1]])
        if isinstance(s, Arc):
            cx, cy = s.center
            r = s.radius
            # Углы начала и конца дуги
            sa = math.atan2(s.a[1] - cy, s.a[0] - cx)
            ea = math.atan2(s.b[1] - cy, s.b[0] - cx)
            sweep = ea - sa
            if s.ccw:
                while sweep < 0: sweep += 2*math.pi
            else:
                while sweep > 0: sweep -= 2*math.pi
            # 4 экстремума окружности: углы 0, π/2, π, 3π/2
            for ext_ang in (0.0, math.pi/2, math.pi, 3*math.pi/2):
                # Проверим попадает ли ext_ang в диапазон [sa, sa+sweep]
                delta = ext_ang - sa
                if s.ccw:
                    while delta < 0: delta += 2*math.pi
                    on_arc = 0 <= delta <= sweep
                else:
                    while delta > 0: delta -= 2*math.pi
                    on_arc = sweep <= delta <= 0
                if on_arc:
                    xs.append(cx + r * math.cos(ext_ang))
                    ys.append(cy + r * math.sin(ext_ang))
    return (min(xs), min(ys), max(xs), max(ys))


def point_to_segment_distance(point: Point, seg: Segment) -> Tuple[float, Point]:
    """Расстояние от точки до сегмента и ближайшая точка на сегменте.
    
    Returns:
        (distance, closest_point_on_segment)
    """
    px, py = point
    if isinstance(seg, Line):
        ax, ay = seg.a
        bx, by = seg.b
        dx, dy = bx - ax, by - ay
        L2 = dx*dx + dy*dy
        if L2 < EPS*EPS:
            return (math.hypot(px-ax, py-ay), seg.a)
        # Параметр t проекции на отрезок (0..1)
        t = ((px - ax) * dx + (py - ay) * dy) / L2
        t = max(0.0, min(1.0, t))
        cx = ax + t * dx
        cy = ay + t * dy
        return (math.hypot(px-cx, py-cy), (cx, cy))
    elif isinstance(seg, Arc):
        # Ближайшая точка дуги к point: проекция на окружность + проверка попадания
        # в угловой диапазон дуги. Упрощённо — переберём концы и проекцию на круг.
        cx, cy = seg.center
        r = seg.radius
        dx, dy = px - cx, py - cy
        d = math.hypot(dx, dy)
        if d < EPS:
            # точка совпадает с центром — равноудалена от всех точек дуги
            return (r, seg.a)
        # Проекция на окружность
        proj = (cx + r * dx / d, cy + r * dy / d)
        # Проверим попадание угла проекции в диапазон дуги
        sa = math.atan2(seg.a[1] - cy, seg.a[0] - cx)
        ea = math.atan2(seg.b[1] - cy, seg.b[0] - cx)
        pa = math.atan2(proj[1] - cy, proj[0] - cx)
        # Угол развёртки дуги
        sweep = ea - sa
        if seg.ccw:
            while sweep < 0: sweep += 2*math.pi
        else:
            while sweep > 0: sweep -= 2*math.pi
        # Проверим лежит ли pa в [sa, sa+sweep]
        delta = pa - sa
        if seg.ccw:
            while delta < 0: delta += 2*math.pi
            on_arc = 0 <= delta <= sweep
        else:
            while delta > 0: delta -= 2*math.pi
            on_arc = sweep <= delta <= 0
        if on_arc:
            return (abs(d - r), proj)
        # Иначе — ближайший из концов
        da = math.hypot(px - seg.a[0], py - seg.a[1])
        db = math.hypot(px - seg.b[0], py - seg.b[1])
        if da < db:
            return (da, seg.a)
        else:
            return (db, seg.b)
    return (float('inf'), seg.a)


def find_closest_point_on_polypath(polypath: Polypath, target: Point
                                    ) -> Tuple[Point, int, float, float]:
    """Находит ближайшую точку на контуре к заданной target.
    
    Returns:
        (closest_point, segment_index, local_t, distance)
    """
    best_dist = float('inf')
    best_point = polypath.segments[0].a if polypath.segments else target
    best_idx = 0
    best_t = 0.0
    
    for idx, seg in enumerate(polypath.segments):
        d, pt = point_to_segment_distance(target, seg)
        if d < best_dist:
            best_dist = d
            best_point = pt
            best_idx = idx
            # Локальный параметр на сегменте (для информации)
            slen = seg.length()
            if slen > EPS:
                # расстояние от seg.a до pt
                local_d = math.hypot(pt[0] - seg.a[0], pt[1] - seg.a[1])
                best_t = local_d / slen
            else:
                best_t = 0.0
    
    return (best_point, best_idx, best_t, best_dist)


def distance_along_polypath(polypath: Polypath, point: Point) -> float:
    """Расстояние от начала контура до заданной точки ВДОЛЬ контура.
    
    Точка должна лежать на контуре или рядом с ним. Возвращает 
    суммарную длину сегментов от начала до проекции точки.
    """
    accumulated = 0.0
    best_dist = float('inf')
    best_acc = 0.0
    for seg in polypath.segments:
        d, pt = point_to_segment_distance(point, seg)
        if d < best_dist:
            best_dist = d
            slen = seg.length()
            if slen > EPS:
                local_d = math.hypot(pt[0] - seg.a[0], pt[1] - seg.a[1])
                best_acc = accumulated + local_d
            else:
                best_acc = accumulated
        accumulated += seg.length()
    return best_acc


def shift_start_to_top_line(polypath: Polypath) -> Polypath:
    """Сдвигает старт к началу САМОЙ ВЕРХНЕЙ прямой стороны контура.
    
    Поиск делается по МАКСИМАЛЬНОЙ Y-координате начала Line-сегмента 
    (не «следующая Line после RT», как было раньше). Это нужно чтобы 
    обходы CW и CCW сходились к одной и той же точке старта:
    у CW shift_start_to_corner('RT') ставит на дугу→правую сторону,
    у CCW — сразу на верхнюю прямую. Раньше алгоритм брал «первую Line 
    после RT по ходу», и точки оказывались на разных сторонах ножа.
    Теперь оба обхода всегда стартуют на ВЕРХНЕЙ прямой.
    
    Если в контуре нет ни одной Line — возвращает как есть.
    """
    if not polypath or not polypath.closed or not polypath.segments:
        return polypath
    
    # Найти индекс Line-сегмента с максимальным Y начальной точки.
    best_idx = -1
    best_y = float('-inf')
    for i, seg in enumerate(polypath.segments):
        if not isinstance(seg, Line):
            continue
        # Берём средний Y отрезка — это устойчивее чем seg.a 
        # (например для наклонной верхней грани)
        mid_y = (seg.a[1] + seg.b[1]) / 2.0
        if mid_y > best_y:
            best_y = mid_y
            best_idx = i
    
    if best_idx < 0:
        return polypath  # ни одной Line — оставляем как есть
    
    if best_idx == 0:
        return polypath  # уже на верхней Line
    
    # Сдвигаем start вперёд по обходу на сумму длин сегментов [0..best_idx-1]
    total_skip = 0.0
    for i in range(best_idx):
        seg = polypath.segments[i]
        if isinstance(seg, Line):
            total_skip += math.hypot(seg.b[0]-seg.a[0], seg.b[1]-seg.a[1])
        elif isinstance(seg, Arc):
            a0 = math.atan2(seg.a[1]-seg.center[1], seg.a[0]-seg.center[0])
            a1 = math.atan2(seg.b[1]-seg.center[1], seg.b[0]-seg.center[0])
            if seg.ccw and a1 < a0: a1 += 2*math.pi
            if not seg.ccw and a1 > a0: a1 -= 2*math.pi
            total_skip += seg.radius * abs(a1 - a0)
    return shift_start_along_contour(polypath, total_skip)


def shift_start_to_corner(polypath: Polypath, corner: str = "RT") -> Polypath:
    """Сдвигает точку старта замкнутого контура в позицию,
    ближайшую к указанному углу bbox.
    
    Args:
        polypath: замкнутый контур
        corner: "LB" | "RB" | "RT" | "LT" — какой угол bbox
    
    Returns:
        Новый Polypath с переставленной точкой старта (геометрия та же).
    """
    if not polypath or not polypath.closed or not polypath.segments:
        return polypath
    
    min_x, min_y, max_x, max_y = polypath_bbox(polypath)
    targets = {
        "LB": (min_x, min_y),
        "RB": (max_x, min_y),
        "RT": (max_x, max_y),
        "LT": (min_x, max_y),
    }
    target = targets.get(corner, (max_x, max_y))
    
    # Расстояние вдоль контура от начала до ближайшей к углу точки
    dist = distance_along_polypath(polypath, target)
    
    return shift_start_along_contour(polypath, dist)


# ─────────────────────────────────────────────────────────────────────────
#  ПРОВЕРКА ПЕРЕСЕЧЕНИЯ ЗАХОДА С КОНТУРОМ
# ─────────────────────────────────────────────────────────────────────────

def lead_distance_to_contour(lead_segments: List[Segment],
                              contour: Polypath,
                              skip_first_n: int = 1,
                              skip_last_n: int = 1) -> float:
    """Минимальное расстояние между сегментами захода и контуром, 
    игнорируя точку стыковки.
    
    Заход состоит из прямой + дуги, и его последняя точка (конец дуги) 
    лежит ровно на контуре в точке стыковки. Это не пересечение, 
    а нормальное соединение — поэтому первые и последние сегменты контура 
    у точки стыковки можно пропустить.
    
    Args:
        lead_segments: список сегментов захода (обычно [Line, Arc] на входе
            или [Arc, Line] на выходе)
        contour: контур обработки
        skip_first_n: сколько начальных сегментов контура пропустить 
            (точка стыковки + соседний)
        skip_last_n: сколько конечных сегментов пропустить
    
    Returns:
        Минимальное расстояние от любого сегмента захода до любого 
        НЕ-пропущенного сегмента контура. Если 0 или близко — есть 
        пересечение/касание.
    """
    if not lead_segments or not contour.segments:
        return float('inf')
    
    contour_segs = contour.segments
    # Сегменты контура для проверки (без точки стыковки)
    n = len(contour_segs)
    start_i = skip_first_n
    end_i = n - skip_last_n
    if start_i >= end_i:
        return float('inf')  # слишком короткий контур, нечего проверять
    
    min_dist = float('inf')
    
    # Проверим расстояние между каждой парой (сегмент захода, сегмент контура)
    for lseg in lead_segments:
        # Много точек выборки на сегменте захода — нужно для надёжного
        # детектирования пересечений (если выборка редкая, точки могут 
        # «перепрыгнуть» через границу контура и пересечение не заметится).
        n_samples = 20
        for i in range(n_samples + 1):
            t = i / n_samples
            sample = lseg.point_at(t)
            # Минимальное расстояние от этой точки до контура
            for ci in range(start_i, end_i):
                cseg = contour_segs[ci]
                d, _ = point_to_segment_distance(sample, cseg)
                if d < min_dist:
                    min_dist = d
    
    return min_dist


# ─────────────────────────────────────────────────────────────────────────
#  ИЗВЛЕЧЕНИЕ ФРАГМЕНТА КОНТУРА (для CORNER_REWORK)
# ─────────────────────────────────────────────────────────────────────────

def _split_segment_at_param(seg: Segment, t: float) -> Tuple[Segment, Segment]:
    """Делит сегмент в точке параметра t∈(0,1). Возвращает (head, tail)."""
    if isinstance(seg, Line):
        mid = seg.point_at(t)
        return (Line(seg.a, mid), Line(mid, seg.b))
    elif isinstance(seg, Arc):
        mid = seg.point_at(t)
        # head: от a до mid с тем же ccw и тем же центром (radius — property)
        head = Arc(a=seg.a, b=mid, center=seg.center, ccw=seg.ccw)
        tail = Arc(a=mid, b=seg.b, center=seg.center, ccw=seg.ccw)
        return (head, tail)
    return (seg, seg)


def extract_subpath_around_indices(polypath: Polypath,
                                    first_idx: int,
                                    last_idx: int,
                                    pad_mm: float = 1.5) -> Polypath:
    """Извлекает фрагмент замкнутого контура от точки -pad мм до first_idx
    до точки +pad мм после last_idx. Используется для обработки 2D угла
    со скруглением (диапазон мелких дуг скругления + поля).
    
    Сегменты first_idx..last_idx идут ЦЕЛИКОМ; pad_mm добавляется ИЗ 
    соседних сегментов (предыдущего перед first_idx и следующего после 
    last_idx).
    
    Для угла-точки (3D без скругления) используй extract_subpath_around_point.
    
    Args:
        polypath: замкнутый контур
        first_idx: индекс первой дуги угла (включительно)
        last_idx: индекс последней дуги угла (включительно)
        pad_mm: добавочный участок контура до/после угла (мм)
    
    Returns:
        Открытый Polypath из сегментов между нужными точками
    """
    if not polypath or not polypath.segments:
        return Polypath(segments=[], closed=False)
    
    n = len(polypath.segments)
    if not (0 <= first_idx < n and 0 <= last_idx < n):
        return Polypath(segments=[], closed=False)
    
    # Длины сегментов и их кумулятивные смещения
    lens = [s.length() for s in polypath.segments]
    
    # Начальная точка угла = начало сегмента first_idx
    # Найдём точку в -pad мм по контуру назад от first_idx
    remaining = pad_mm
    start_idx = first_idx
    start_t = 0.0  # точка в начале сегмента start_idx
    
    while remaining > 1e-9:
        # Идём назад: предыдущий сегмент
        prev_idx = (start_idx - 1) % n
        prev_len = lens[prev_idx]
        if prev_len >= remaining:
            # Точка лежит ВНУТРИ prev_idx — на расстоянии prev_len-remaining от его начала
            start_idx = prev_idx
            start_t = (prev_len - remaining) / prev_len
            remaining = 0
        else:
            # Не хватает — переходим на ещё один сегмент назад
            remaining -= prev_len
            start_idx = prev_idx
            start_t = 0.0
            # Защита от бесконечного цикла
            if start_idx == first_idx:
                break
    
    # Конечная точка = конец сегмента last_idx + pad мм вперёд
    remaining = pad_mm
    end_idx = last_idx
    end_t = 1.0  # точка в конце сегмента end_idx
    
    while remaining > 1e-9:
        next_idx = (end_idx + 1) % n
        next_len = lens[next_idx]
        if next_len >= remaining:
            end_idx = next_idx
            end_t = remaining / next_len
            remaining = 0
        else:
            remaining -= next_len
            end_idx = next_idx
            end_t = 1.0
            if end_idx == last_idx:
                break
    
    # Собираем фрагмент. Идём от start_idx (с обрезкой по start_t) 
    # через все сегменты до end_idx (с обрезкой по end_t).
    result_segments: List[Segment] = []
    
    cur = start_idx
    safety = 0
    while True:
        safety += 1
        if safety > 2 * n:
            break  # защита
        
        seg = polypath.segments[cur]
        
        # Обрезка слева на старте
        if cur == start_idx and start_t > 1e-9:
            _, seg = _split_segment_at_param(seg, start_t)
        
        # Обрезка справа на конце
        if cur == end_idx and end_t < 1.0 - 1e-9:
            seg, _ = _split_segment_at_param(seg, end_t)
        
        result_segments.append(seg)
        
        if cur == end_idx:
            break
        cur = (cur + 1) % n
    
    return Polypath(segments=result_segments, closed=False)


def extract_subpath_around_point(polypath: Polypath,
                                  point_on_contour: Point,
                                  segment_hint: int,
                                  pad_mm: float = 1.5) -> Polypath:
    """Извлекает фрагмент замкнутого контура: pad_mm до и pad_mm после 
    указанной точки. Точка должна лежать на стыке двух сегментов (для 
    острых 3D углов) или в любой точке контура.
    
    Args:
        polypath: замкнутый контур
        point_on_contour: точка на контуре (вершина 3D угла)
        segment_hint: индекс сегмента ЗА которым находится точка 
            (т.е. point ≈ segments[segment_hint].b или start следующего)
        pad_mm: половина длины фрагмента (по pad_mm с каждой стороны)
    
    Returns:
        Открытый Polypath коротким фрагментом 2*pad_mm с центром в точке.
    """
    if not polypath or not polypath.segments:
        return Polypath(segments=[], closed=False)
    
    n = len(polypath.segments)
    lens = [s.length() for s in polypath.segments]
    
    # Идём НАЗАД на pad_mm от точки. Точка ≈ конец сегмента segment_hint.
    remaining = pad_mm
    start_idx = segment_hint
    start_t = 1.0  # начинаем в конце segment_hint (= точка)
    while remaining > 1e-9:
        seg_len = lens[start_idx]
        # Доступно назад в текущем сегменте: start_t * seg_len
        avail = start_t * seg_len
        if avail >= remaining:
            start_t -= remaining / seg_len
            remaining = 0
        else:
            remaining -= avail
            start_idx = (start_idx - 1) % n
            start_t = 1.0
            if start_idx == segment_hint:
                break
    
    # Идём ВПЕРЁД на pad_mm от точки. Точка ≈ начало сегмента segment_hint+1
    remaining = pad_mm
    end_idx = (segment_hint + 1) % n
    end_t = 0.0
    while remaining > 1e-9:
        seg_len = lens[end_idx]
        avail = (1.0 - end_t) * seg_len
        if avail >= remaining:
            end_t += remaining / seg_len
            remaining = 0
        else:
            remaining -= avail
            end_idx = (end_idx + 1) % n
            end_t = 0.0
            if end_idx == segment_hint:
                break
    
    # Собираем фрагмент от (start_idx, start_t) до (end_idx, end_t)
    result_segments: List[Segment] = []
    cur = start_idx
    safety = 0
    while True:
        safety += 1
        if safety > 2 * n:
            break
        seg = polypath.segments[cur]
        
        # Обрезаем слева/справа
        cur_start_t = start_t if cur == start_idx else 0.0
        cur_end_t = end_t if cur == end_idx else 1.0
        
        if cur_start_t > 1e-9:
            _, seg = _split_segment_at_param(seg, cur_start_t)
            # После split нужно скорректировать end_t (он был относительно
            # исходного сегмента, теперь относительно укороченного)
            if cur == end_idx:
                # был t=end_t из [start_t, 1.0] → стал (end_t-start_t)/(1-start_t)
                cur_end_t = (end_t - cur_start_t) / (1.0 - cur_start_t)
        
        if cur_end_t < 1.0 - 1e-9:
            seg, _ = _split_segment_at_param(seg, cur_end_t)
        
        if seg.length() > 1e-9:
            result_segments.append(seg)
        
        if cur == end_idx:
            break
        cur = (cur + 1) % n
    
    return Polypath(segments=result_segments, closed=False)


def offset_polypath_simple(polypath: Polypath, offset: float,
                            inside: bool = True) -> Polypath:
    """Простой оффсет каждого сегмента на `offset` мм перпендикулярно.
    
    Для Line: сдвиг параллельно (по нормали).
    Для Arc: изменение радиуса (внутрь = уменьшение, наружу = увеличение).
    
    Args:
        polypath: исходный контур
        offset: расстояние (мм). Если положительное и inside=True — внутрь
            контура (центр слева для CCW), если False — наружу.
        inside: True = эквидистанта внутри контура, False = снаружи.
    
    Returns:
        Новый Polypath. Сегменты могут разрывать стыки — это не точная
        офсетная кривая, но достаточно для визуализации фактических 
        путей фрезы.
    """
    if not polypath or not polypath.segments or offset < 1e-9:
        return polypath
    
    import math
    new_segments: List[Segment] = []
    # Знак: для CCW обхода и inside=True смещение влево (= внутрь)
    sign = -1.0 if inside else 1.0
    
    for seg in polypath.segments:
        if isinstance(seg, Line):
            dx = seg.b[0] - seg.a[0]
            dy = seg.b[1] - seg.a[1]
            d = math.sqrt(dx*dx + dy*dy)
            if d < 1e-9:
                new_segments.append(seg)
                continue
            # Нормаль = (-dy/d, dx/d) — влево от направления
            nx = -dy / d * sign * offset
            ny = dx / d * sign * offset
            new_segments.append(Line(
                a=(seg.a[0] + nx, seg.a[1] + ny),
                b=(seg.b[0] + nx, seg.b[1] + ny),
            ))
        elif isinstance(seg, Arc):
            # Для дуги меняем радиус через перерасчёт точек a/b на новой 
            # окружности с тем же центром
            r_delta = offset * sign * (-1 if seg.ccw else 1)
            new_r = max(0.001, seg.radius + r_delta)
            # Пересчитываем a/b: те же углы относительно центра, новый радиус
            ax = seg.center[0] + (seg.a[0] - seg.center[0]) * new_r / seg.radius
            ay = seg.center[1] + (seg.a[1] - seg.center[1]) * new_r / seg.radius
            bx = seg.center[0] + (seg.b[0] - seg.center[0]) * new_r / seg.radius
            by = seg.center[1] + (seg.b[1] - seg.center[1]) * new_r / seg.radius
            new_segments.append(Arc(
                a=(ax, ay), b=(bx, by),
                center=seg.center, ccw=seg.ccw
            ))
        else:
            new_segments.append(seg)
    
    return Polypath(segments=new_segments, closed=polypath.closed)


def offset_polypath_toward_center(polypath: Polypath, offset: float,
                                    center: Point) -> Polypath:
    """Оффсет каждого сегмента к центру (положительный offset) или от 
    центра (отрицательный).
    
    Для каждого сегмента выбирается нормаль, направленная к указанному
    центру bbox ножа. Это гарантирует что эквидистанта пройдёт ВНУТРИ
    контура при положительном offset (INSIDE/CORNER) или СНАРУЖИ при 
    отрицательном (OUTSIDE), независимо от направления обхода CCW/CW.
    
    Args:
        polypath: исходный контур
        offset: смещение в мм. Положительный = к центру (внутрь). 
            Отрицательный = от центра (наружу).
        center: точка центра ножа (X, Y)
    """
    if not polypath or not polypath.segments or abs(offset) < 1e-9:
        return polypath
    
    # Для отрицательного offset инвертируем выбор нормали (от центра)
    toward = offset > 0
    abs_offset = abs(offset)
    
    import math
    new_segments: List[Segment] = []
    cx, cy = center
    
    for seg in polypath.segments:
        if isinstance(seg, Line):
            dx = seg.b[0] - seg.a[0]
            dy = seg.b[1] - seg.a[1]
            d = math.sqrt(dx*dx + dy*dy)
            if d < 1e-9:
                new_segments.append(seg)
                continue
            mid = ((seg.a[0]+seg.b[0])/2, (seg.a[1]+seg.b[1])/2)
            n1 = (-dy/d, dx/d)
            # Точки по двум противоположным нормалям
            p1 = (mid[0] + n1[0]*abs_offset, mid[1] + n1[1]*abs_offset)
            p2 = (mid[0] - n1[0]*abs_offset, mid[1] - n1[1]*abs_offset)
            d1 = (p1[0]-cx)**2 + (p1[1]-cy)**2
            d2 = (p2[0]-cx)**2 + (p2[1]-cy)**2
            # toward=True → выбираем нормаль К центру (меньшее d)
            # toward=False → ОТ центра (большее d)
            if toward:
                sign = 1.0 if d1 < d2 else -1.0
            else:
                sign = 1.0 if d1 > d2 else -1.0
            nx, ny = n1[0]*abs_offset*sign, n1[1]*abs_offset*sign
            new_segments.append(Line(
                a=(seg.a[0]+nx, seg.a[1]+ny),
                b=(seg.b[0]+nx, seg.b[1]+ny),
            ))
        elif isinstance(seg, Arc):
            # Для дуг БОЛЬШОГО радиуса (R > 50мм, почти прямая) — работаем
            # как с прямой: оффсет по нормали в средней точке. Иначе центр
            # такой дуги очень далеко от ножа и сравнение "к центру vs от 
            # центра ножа" даёт ошибку.
            if seg.radius > 50.0:
                mid = seg.point_at(0.5)
                # Касательная в средней точке дуги = перпендикуляр к радиус-вектору
                rx = mid[0] - seg.center[0]
                ry = mid[1] - seg.center[1]
                r_len = (rx*rx + ry*ry) ** 0.5
                if r_len < 1e-9:
                    new_segments.append(seg)
                    continue
                # Касательная: повернуть радиус-вектор на 90° (с учётом ccw)
                if seg.ccw:
                    tx, ty = -ry/r_len, rx/r_len
                else:
                    tx, ty = ry/r_len, -rx/r_len
                # Нормаль = (-ty, tx)
                n_x, n_y = -ty, tx
                # Две точки по двум нормалям
                p1 = (mid[0] + n_x*abs_offset, mid[1] + n_y*abs_offset)
                p2 = (mid[0] - n_x*abs_offset, mid[1] - n_y*abs_offset)
                d1 = (p1[0]-cx)**2 + (p1[1]-cy)**2
                d2 = (p2[0]-cx)**2 + (p2[1]-cy)**2
                if toward:
                    sign = 1.0 if d1 < d2 else -1.0
                else:
                    sign = 1.0 if d1 > d2 else -1.0
                nx, ny = n_x*abs_offset*sign, n_y*abs_offset*sign
                # Сдвигаем дугу: меняем центр + a + b на (nx,ny)
                new_segments.append(Arc(
                    a=(seg.a[0]+nx, seg.a[1]+ny),
                    b=(seg.b[0]+nx, seg.b[1]+ny),
                    center=(seg.center[0]+nx, seg.center[1]+ny),
                    ccw=seg.ccw
                ))
                continue
            
            # Обычные дуги: меняем радиус, сравниваем расстояния от центра 
            # дуги и средней точки дуги до центра ножа:
            #   - центр дуги ближе → дуга смотрит "от ножа" → toward=True уменьшает R
            #   - центр дуги дальше → дуга смотрит "к ножу" → toward=True увеличивает R
            mid = seg.point_at(0.5)
            d_arc_center_sq = (seg.center[0]-cx)**2 + (seg.center[1]-cy)**2
            d_arc_mid_sq    = (mid[0]-cx)**2 + (mid[1]-cy)**2
            center_is_inner = d_arc_center_sq < d_arc_mid_sq
            if toward:
                r_delta = -abs_offset if center_is_inner else abs_offset
            else:
                r_delta = abs_offset if center_is_inner else -abs_offset
            new_r = max(0.001, seg.radius + r_delta)
            ax = seg.center[0] + (seg.a[0]-seg.center[0]) * new_r / seg.radius
            ay = seg.center[1] + (seg.a[1]-seg.center[1]) * new_r / seg.radius
            bx = seg.center[0] + (seg.b[0]-seg.center[0]) * new_r / seg.radius
            by = seg.center[1] + (seg.b[1]-seg.center[1]) * new_r / seg.radius
            new_segments.append(Arc(
                a=(ax, ay), b=(bx, by),
                center=seg.center, ccw=seg.ccw
            ))
        else:
            new_segments.append(seg)
    
    return Polypath(segments=new_segments, closed=polypath.closed)


def offset_polypath_uniform(polypath: Polypath, offset: float,
                             inward: bool) -> Polypath:
    """Равномерный оффсет полипаса по направлению обхода контура.
    
    Определяет CCW/CW обход замкнутого контура **один раз** и применяет 
    одинаковую нормаль ко всем сегментам. Это даёт согласованную эквидистанту
    без скачков в углах между мелкими сегментами.
    
    Args:
        polypath: ЗАМКНУТЫЙ контур
        offset: расстояние смещения в мм
        inward: True = внутрь контура, False = наружу
    
    Returns:
        Новый Polypath
    """
    from .direction import is_ccw
    
    if not polypath or not polypath.segments or abs(offset) < 1e-9:
        return polypath
    
    is_contour_ccw = is_ccw(polypath) if polypath.closed else True
    # Для CCW обхода: левая нормаль ведёт ВНУТРЬ контура (центр слева).
    # Для CW обхода: левая нормаль ведёт НАРУЖУ.
    # Сторона смещения:
    #   CCW + inward  → влево  (sign = -1 в системе (n=(-dy,dx)))
    #   CCW + outward → вправо (sign = +1)
    #   CW + inward   → вправо
    #   CW + outward  → влево
    if is_contour_ccw:
        # CCW: внутренность контура СЛЕВА от направления.
        # Левая нормаль = (-dy, dx) — положительный sign.
        sign = 1.0 if inward else -1.0
    else:
        # CW: внутренность СПРАВА → правая нормаль (отрицательный sign)
        sign = -1.0 if inward else 1.0
    
    abs_offset = abs(offset)
    
    import math
    new_segments: List[Segment] = []
    
    for seg in polypath.segments:
        if isinstance(seg, Line):
            dx = seg.b[0] - seg.a[0]
            dy = seg.b[1] - seg.a[1]
            d = math.sqrt(dx*dx + dy*dy)
            if d < 1e-9:
                new_segments.append(seg)
                continue
            # Нормаль = (-dy/d, dx/d) — это «левая» нормаль от направления
            nx = -dy / d * sign * abs_offset
            ny = dx / d * sign * abs_offset
            new_segments.append(Line(
                a=(seg.a[0] + nx, seg.a[1] + ny),
                b=(seg.b[0] + nx, seg.b[1] + ny),
            ))
        elif isinstance(seg, Arc):
            # БОЛЬШИЕ ДУГИ (R > 50мм) — обрабатываем как прямую, по нормали
            # в средней точке. Иначе при изменении радиуса дуги R=472мм
            # получаются артефакты (центр дуги очень далеко, сравнение даёт
            # ошибки).
            if seg.radius > 50.0:
                mid = seg.point_at(0.5)
                rx = mid[0] - seg.center[0]
                ry = mid[1] - seg.center[1]
                r_len = math.sqrt(rx*rx + ry*ry)
                if r_len < 1e-9:
                    new_segments.append(seg)
                    continue
                # Касательная в средней точке = перпендикуляр к радиус-вектору
                if seg.ccw:
                    tx, ty = -ry/r_len, rx/r_len
                else:
                    tx, ty = ry/r_len, -rx/r_len
                # Нормаль = (-ty, tx), та же логика что для Line
                nx = -ty * sign * abs_offset
                ny = tx * sign * abs_offset
                # Сдвигаем всю дугу как целое: a, b и центр
                new_segments.append(Arc(
                    a=(seg.a[0]+nx, seg.a[1]+ny),
                    b=(seg.b[0]+nx, seg.b[1]+ny),
                    center=(seg.center[0]+nx, seg.center[1]+ny),
                    ccw=seg.ccw
                ))
                continue
            
            # Обычные малые дуги: меняем радиус.
            # Критерий выпуклости: совпадает ли направление обхода ЭТОЙ дуги
            # (seg.ccw) с ОБЩИМ направлением обхода контура (is_contour_ccw)?
            #   - совпадает   → дуга ВЫПУКЛАЯ наружу (скругление угла):
            #     inward УМЕНЬШАЕТ радиус, outward УВЕЛИЧИВАЕТ
            #   - не совпадает → дуга ВОГНУТАЯ (выемка):
            #     inward УВЕЛИЧИВАЕТ радиус, outward УМЕНЬШАЕТ
            #
            # ИСПРАВЛЕНО: раньше использовался ГЛОБАЛЬНЫЙ point-in-polygon
            # тест центра дуги (_point_in_polypath). Для мелких биарк-дуг
            # центр лежит ВПЛОТНУЮ к границе контура (на расстоянии = радиус
            # дуги, часто доли мм), и ray-casting тест на таком расстоянии от
            # сложной ломаной из тысяч мелких сегментов классифицирует ~21%
            # дуг НЕПРАВИЛЬНО — центр «видится» снаружи/внутри в зависимости
            # от соседних участков контура, а не от локальной формы.
            # Результат — на 24 из 76 ножей offset получал r_delta с ПРОТИВО-
            # ПОЛОЖНЫМ знаком для отдельных дуг: OUTSIDE-эквидистанта в этом
            # месте УМЕНЬШАЛАСЬ вместо увеличения (смещение на 2×T от истинной
            # offset-кривой shapely.buffer — полный переброс знака).
            #
            # НОВЫЙ критерий — ЛОКАЛЬНЫЙ: направление поворота САМОЙ дуги
            # относительно общего обхода. Для CCW-контура (OUTSIDE) дуга,
            # поворачивающая CCW (как и весь обход) — это выпуклое скругление
            # угла (как у круга, обходимого CCW: каждая точка поворачивает
            # CCW). Дуга, поворачивающая CW (против общего обхода) — вогнутая
            # выемка. Не зависит от размера/позиции дуги, не требует
            # point-in-polygon. Проверено: на тех же 24 ножах устраняет
            # 2×T-сдвиг (худшее отклонение от shapely.buffer падает с 1150мкм
            # до базовых ~576мкм = 1×T, что является нормальной погрешностью
            # дискретизации биарков).
            arc_is_convex = (seg.ccw == is_contour_ccw)
            
            if arc_is_convex:
                # Выпуклая наружу (скругление угла)
                r_delta = -abs_offset if inward else abs_offset
            else:
                # Вогнутая внутрь
                r_delta = abs_offset if inward else -abs_offset
            new_r = max(0.001, seg.radius + r_delta)
            ax = seg.center[0] + (seg.a[0] - seg.center[0]) * new_r / seg.radius
            ay = seg.center[1] + (seg.a[1] - seg.center[1]) * new_r / seg.radius
            bx = seg.center[0] + (seg.b[0] - seg.center[0]) * new_r / seg.radius
            by = seg.center[1] + (seg.b[1] - seg.center[1]) * new_r / seg.radius
            new_segments.append(Arc(
                a=(ax, ay), b=(bx, by),
                center=seg.center, ccw=seg.ccw
            ))
        else:
            new_segments.append(seg)
    
    return Polypath(segments=new_segments, closed=polypath.closed)


def offset_polypath_shapely_clean(polypath: Polypath, offset: float,
                                   inward: bool) -> Polypath:
    """Чистый offset через shapely buffer — без самопересечений.
    
    Использует shapely.buffer() который автоматически обрабатывает 
    самопересечения на тесных вогнутых углах: заменяет пересечение
    на маленькую дугу (rounding).
    
    Работает так же как AlphaCAM визуализирует пути на канвасе —
    чистые сглаженные offset-полилинии без крестов на sharp corners.
    
    Args:
        polypath: замкнутый контур
        offset: расстояние смещения в мм (> 0)
        inward: True = внутрь контура (buffer -), False = наружу (buffer +)
    
    Returns:
        Новый Polypath (полилиния Line, дуги преобразованы в короткие
        линии из-за shapely). Возвращает original если shapely 
        недоступен или offset очень мал.
    """
    if not polypath or not polypath.segments or abs(offset) < 1e-9:
        return polypath
    
    try:
        from shapely.geometry import Polygon, LineString
        from shapely.ops import unary_union
    except ImportError:
        # Fallback на обычный offset если shapely не установлен
        return offset_polypath_uniform(polypath, offset, inward)
    
    # Сэмплим контур в точки с высокой точностью (маленький шаг). 
    # Это принципиально для качества offset — при chord_err=0.05 мелкие 
    # дуги превращались в 8-16 отрезков ломаной. При 0.005 — плотная 
    # сетка точек, offset получается близким к оригиналу арок.
    pts = _sample_polypath_points(polypath, chord_err_mm=0.005)
    if len(pts) < 3:
        return polypath
    
    try:
        poly = Polygon(pts)
        if not poly.is_valid:
            # Пытаемся починить самопересекающийся полигон
            poly = poly.buffer(0)
            if not poly.is_valid or poly.is_empty:
                return offset_polypath_uniform(polypath, offset, inward)
        
        # Знак offset: inward → сжатие (-), outward → расширение (+)
        signed_offset = -offset if inward else offset
        # join_style=2 (mitre) с большим mitre_limit — сохраняет ОСТРЫЕ 
        # углы на местах где фреза не пролезает (как AlphaCAM). Не 
        # добавляет полукруг-скругление. cap_style=1 (round) для 
        # открытых линий, но у нас замкнутый контур — не влияет.
        # mitre_limit=10 достаточно чтобы не резать «острия» на очень 
        # тесных углах.
        result = poly.buffer(signed_offset, join_style=2, mitre_limit=10.0, 
                             quad_segs=32)
        
        if result.is_empty:
            return polypath
        
        # Берём внешнюю границу (если MultiPolygon — самый большой)
        if result.geom_type == 'MultiPolygon':
            result = max(result.geoms, key=lambda p: p.area)
        
        if not hasattr(result, 'exterior'):
            return polypath
        
        # Конвертируем обратно в Polypath (полилиния из Line сегментов)
        coords = list(result.exterior.coords)
        if len(coords) < 3:
            return polypath
        
        # Удаляем дубликат последней точки если совпадает с первой
        if coords[0] == coords[-1]:
            coords = coords[:-1]
        
        segments = []
        for i in range(len(coords)):
            a = coords[i]
            b = coords[(i+1) % len(coords)]
            if abs(a[0]-b[0]) < 1e-9 and abs(a[1]-b[1]) < 1e-9:
                continue
            segments.append(Line(a=a, b=b))
        
        result_pp = Polypath(segments=segments, closed=True)
        # После shapely получаем много мелких прямых даже на скруглениях.
        # Собираем обратно в дуги где возможно. short_seg=20мм — сегменты 
        # 1-5мм аппроксимирующие арки объединяются в одну дугу вместо 
        # ломаной. tol=0.05мм — точность оффсета.
        result_pp = merge_segments_to_arcs(
            result_pp, tol=0.05, short_seg=20.0)
        return result_pp
    except Exception:
        # На любую ошибку — fallback
        return offset_polypath_uniform(polypath, offset, inward)


def _segment_chord_intersect(s1, s2, eps: float = 1e-6):
    """Пересечение хорд (a-b) двух сегментов.
    
    Возвращает (t1, t2, ix, iy) если хорды пересекаются СТРОГО внутри 
    (не на концах), иначе None. t1/t2 — параметры вдоль хорд [0,1].
    """
    x1, y1 = s1.a; x2, y2 = s1.b
    x3, y3 = s2.a; x4, y4 = s2.b
    d = (x2-x1)*(y4-y3) - (y2-y1)*(x4-x3)
    if abs(d) < 1e-12:
        return None
    t = ((x3-x1)*(y4-y3) - (y3-y1)*(x4-x3)) / d
    u = ((x3-x1)*(y2-y1) - (y3-y1)*(x2-x1)) / d
    if eps < t < 1-eps and eps < u < 1-eps:
        ix = x1 + t*(x2-x1)
        iy = y1 + t*(y2-y1)
        return (t, u, ix, iy)
    return None


def _truncate_segment_to_t(seg, t: float, keep_start: bool):
    """Обрезает сегмент по параметру t [0..1].
    
    keep_start=True: возвращает сегмент от a до точки t (укорачиваем конец).
    keep_start=False: возвращает сегмент от точки t до b (укорачиваем начало).
    
    Для Arc используем точку на хорде, спроецированную на окружность.
    """
    if isinstance(seg, Line):
        x = seg.a[0] + t*(seg.b[0]-seg.a[0])
        y = seg.a[1] + t*(seg.b[1]-seg.a[1])
        if keep_start:
            return Line(a=seg.a, b=(x, y))
        else:
            return Line(a=(x, y), b=seg.b)
    elif isinstance(seg, Arc):
        import math
        cx, cy = seg.center
        hx = seg.a[0] + t*(seg.b[0]-seg.a[0])
        hy = seg.a[1] + t*(seg.b[1]-seg.a[1])
        dx = hx - cx; dy = hy - cy
        L = math.hypot(dx, dy)
        if L < 1e-9:
            return seg
        px = cx + dx * seg.radius / L
        py = cy + dy * seg.radius / L
        if keep_start:
            return Arc(a=seg.a, b=(px, py), center=seg.center, ccw=seg.ccw)
        else:
            return Arc(a=(px, py), b=seg.b, center=seg.center, ccw=seg.ccw)
    return seg


def trim_self_intersections(polypath: Polypath, max_loop_segs: int = 6) -> Polypath:
    """Удаляет петли (self-intersections) на эквидистантном контуре.
    
    После offset_polypath_uniform могут возникать петли там где радиус 
    кривизны контура меньше расстояния офсета. Алгоритм находит пары 
    пересекающихся неcoседних сегментов, и заменяет последовательность 
    [s_i (укороченный), s_{i+1} ... s_{j-1}, s_j (укороченный)] на 
    [s_i (укороченный), s_j (укороченный)] — петля вырезается.
    
    Args:
        polypath: контур после offset
        max_loop_segs: макс. длина петли в сегментах (защита от удаления 
                       больших фрагментов; типичная петля = 1-3 сегмента)
    
    Returns:
        Полипас без петель (если их не было — оригинал)
    """
    if not polypath or len(polypath.segments) < 4:
        return polypath
    
    segs = list(polypath.segments)
    
    max_iterations = 20
    for _iter in range(max_iterations):
        n = len(segs)
        found = None
        for i in range(n):
            s1 = segs[i]
            for delta in range(2, min(max_loop_segs + 1, n - 1)):
                j = (i + delta) % n
                if j == i: continue
                if (j + 1) % n == i: continue  # соседние при замыкании
                s2 = segs[j]
                result = _segment_chord_intersect(s1, s2)
                if result is not None:
                    t1, t2, ix, iy = result
                    found = (i, j, t1, t2, ix, iy, delta)
                    break
            if found:
                break
        
        if not found:
            break  # больше петель нет
        
        i, j, t1, t2, ix, iy, delta = found
        s_i_trimmed = _truncate_segment_to_t(segs[i], t1, keep_start=True)
        s_j_trimmed = _truncate_segment_to_t(segs[j], t2, keep_start=False)
        
        # Точно состыкуем endpoints на точке пересечения
        if isinstance(s_i_trimmed, Line):
            s_i_trimmed = Line(a=s_i_trimmed.a, b=(ix, iy))
        elif isinstance(s_i_trimmed, Arc):
            s_i_trimmed = Arc(a=s_i_trimmed.a, b=(ix, iy),
                              center=s_i_trimmed.center, ccw=s_i_trimmed.ccw)
        if isinstance(s_j_trimmed, Line):
            s_j_trimmed = Line(a=(ix, iy), b=s_j_trimmed.b)
        elif isinstance(s_j_trimmed, Arc):
            s_j_trimmed = Arc(a=(ix, iy), b=s_j_trimmed.b,
                              center=s_j_trimmed.center, ccw=s_j_trimmed.ccw)
        
        new_segs = []
        if i < j:
            # Обычный случай: оставляем 0..i-1, обрезанные i и j, j+1..конец
            for k in range(i):
                new_segs.append(segs[k])
            new_segs.append(s_i_trimmed)
            new_segs.append(s_j_trimmed)
            for k in range(j+1, n):
                new_segs.append(segs[k])
        else:
            # Wrap-around: петля проходит через start контура.
            # Оставляем j_trimmed, [j+1..i-1], i_trimmed
            new_segs.append(s_j_trimmed)
            for k in range(j+1, i):
                new_segs.append(segs[k])
            new_segs.append(s_i_trimmed)
        
        segs = new_segs
    
    return Polypath(segments=segs, closed=polypath.closed)


def simplify_for_visualization(polypath: Polypath,
                                small_arc_threshold_mm: float = 1.0
                                ) -> Polypath:
    """Упрощает контур для визуализации: убирает biarc-аппроксимационные
    дуги, оставляя настоящие скругления.
    
    КРИТЕРИЙ:
    - Большие дуги (R > 50мм) — заменяются на прямые (это «почти прямые»
      участки из biarc-аппроксимации).
    - Очень мелкие или короткие дуги (R < threshold ИЛИ длина < threshold) — 
      заменяются на прямые (biarc-сегменты в скруглениях).
    - НАСТОЯЩИЕ скругления (например R=10мм с длиной дуги несколько мм)
      остаются как есть — они видимы и геометрически значимы.
    
    Это убирает биарк-артефакты при визуализации (множество мелких 
    разнонаправленных дуг → веер при оффсете), сохраняя крупные 
    скругления.
    
    Args:
        polypath: исходный контур (не изменяется)
        small_arc_threshold_mm: дуги с радиусом ИЛИ длиной меньше этого 
            порога заменяются на прямые. По умолчанию 1.0мм.
    
    Returns:
        Новый Polypath. Используется ТОЛЬКО для визуализации.
    """
    if not polypath or not polypath.segments:
        return polypath
    
    new_segments: List[Segment] = []
    for seg in polypath.segments:
        if isinstance(seg, Arc):
            # Очень большие дуги (R > 50мм) — это «почти прямые» из биарка
            if seg.radius > 50.0:
                new_segments.append(Line(a=seg.a, b=seg.b))
                continue
            # Мелкие И короткие дуги — биарк-мусор (если хотя бы один 
            # критерий "большой" — это настоящая дуга)
            if seg.radius < small_arc_threshold_mm and seg.length() < small_arc_threshold_mm:
                new_segments.append(Line(a=seg.a, b=seg.b))
                continue
            # Иначе — настоящее скругление, оставляем как Arc
        new_segments.append(seg)
    
    return Polypath(segments=new_segments, closed=polypath.closed)


def _sample_polypath_points(polypath: Polypath, chord_err_mm: float = 0.002
                            ) -> List[Point]:
    """Дискретизирует контур в точки с контролем хорды (по умолчанию ~2 мкм).
    Дуги разбиваются так, чтобы отклонение хорды не превышало chord_err_mm."""
    pts: List[Point] = []
    for s in polypath.segments:
        if isinstance(s, Arc):
            R = max(s.radius, 1e-9)
            # макс. угол шага по допуску хорды: cos(da/2) = 1 - err/R
            ratio = max(-1.0, min(1.0, 1.0 - chord_err_mm / R))
            max_step = 2.0 * math.acos(ratio) if R > chord_err_mm else math.pi
            sweep = s.length() / R
            n = max(2, int(math.ceil(sweep / max(max_step, 1e-6))))
            a0 = math.atan2(s.a[1] - s.center[1], s.a[0] - s.center[0])
            a1 = math.atan2(s.b[1] - s.center[1], s.b[0] - s.center[0])
            if s.ccw and a1 < a0:
                a1 += 2 * math.pi
            if (not s.ccw) and a1 > a0:
                a1 -= 2 * math.pi
            for k in range(n):
                t = a0 + (a1 - a0) * k / n
                pts.append((s.center[0] + R * math.cos(t),
                            s.center[1] + R * math.sin(t)))
        else:
            pts.append(s.a)
    return pts


def simplify_geometry_via_shapely(polypath: Polypath, 
                                   tol_mm: float = 0.1,
                                   chord_err_mm: float = 0.002) -> Polypath:
    """Сглаживает контур через Douglas-Peucker simplify (shapely).
    
    Это «лёгкое» сглаживание: убирает мелкие зигзаги (биарк-шум, 
    лишние вершины), сохраняя крупные дуги и углы. В отличие от 
    smooth_for_offset, НЕ применяет морфологический buffer — то есть 
    форма ножа меняется минимально (в пределах tol_mm).
    
    Полезно перед offset для устранения «вееров» из мелких смежных 
    дуг, которые после merge_segments_to_arcs всё ещё могут давать 
    мелкие тангенциальные изломы.
    
    Args:
        polypath: исходный контур
        tol_mm: допуск simplify (Douglas-Peucker), мм. Типично 0.05-0.15.
        chord_err_mm: точность дискретизации дуг
    
    Returns:
        Упрощённый контур (Arc + Line). Если shapely недоступен или 
        контур слишком короткий — возвращает оригинал.
    """
    if not polypath or len(polypath.segments) < 3 or tol_mm <= 0:
        return polypath
    try:
        from shapely.geometry import Polygon
    except Exception:
        return polypath
    
    pts = _sample_polypath_points(polypath, chord_err_mm)
    if len(pts) < 3:
        return polypath
    try:
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        simp = poly.simplify(tol_mm, preserve_topology=True)
        if simp.is_empty or simp.geom_type != 'Polygon':
            return polypath
        coords = list(simp.exterior.coords)
        if coords[0] == coords[-1]:
            coords = coords[:-1]
        if len(coords) < 4:
            return polypath
    except Exception:
        return polypath
    
    # Сохраняем намотку
    def signed_area(c):
        a = 0.0
        for i in range(len(c)):
            x1, y1 = c[i]
            x2, y2 = c[(i + 1) % len(c)]
            a += x1 * y2 - x2 * y1
        return a / 2.0
    orig_coords = [s.a for s in polypath.segments]
    if (signed_area(coords) > 0) != (signed_area(orig_coords) > 0):
        coords = list(reversed(coords))
    
    # Сдвигаем старт ближе к исходному (для захода)
    start = polypath.segments[0].a
    best_i = min(range(len(coords)),
                 key=lambda i: (coords[i][0] - start[0]) ** 2
                 + (coords[i][1] - start[1]) ** 2)
    coords = coords[best_i:] + coords[:best_i]
    
    # Строим Line-полипас
    line_segs: List[Segment] = [
        Line(a=coords[i], b=coords[(i + 1) % len(coords)])
        for i in range(len(coords))
    ]
    line_polypath = Polypath(segments=line_segs, closed=True)
    
    # Восстанавливаем дуги
    try:
        return merge_segments_to_arcs(
            line_polypath, tol=0.01, min_chain=4, tangent_tol_deg=3.0
        )
    except Exception:
        return line_polypath


def has_real_3d_corners(polypath: Polypath, 
                         min_tool_radius_mm: float = 0.5) -> bool:
    """Определяет есть ли в контуре настоящие 3D углы (физические скругления
    инструмента) в отличие от биарк-шума или плавных больших дуг.
    
    Настоящий 3D угол — это fillet вокруг минимально-возможного радиуса 
    инструмента:
    - R в диапазоне [min_tool_radius_mm * 0.9, min_tool_radius_mm * 1.5]
      (около физического минимума инструмента, не «биарк-шум» и не 
      «плавная большая дуга»)
    - swept_angle >= 20° (резкое изменение направления — fillet)
    - 0.15 <= length <= 1.5мм (типичная длина дуги fillet'а)
    
    Args:
        polypath: контур
        min_tool_radius_mm: минимальный радиус инструмента
    
    Returns:
        True если хотя бы один Arc — настоящий 3D угол fillet
    """
    if not polypath or not polypath.segments:
        return False
    try:
        from .corner_detect import _arc_swept_deg
    except Exception:
        return False
    
    r_min = min_tool_radius_mm * 0.9
    r_max = min_tool_radius_mm * 1.5
    
    for seg in polypath.segments:
        if not isinstance(seg, Arc):
            continue
        if not (r_min <= seg.radius <= r_max):
            continue
        L = seg.length()
        if not (0.15 <= L <= 1.5):
            continue
        if _arc_swept_deg(seg) < 20.0:
            continue
        return True
    return False


def smooth_for_offset(polypath: Polypath, tool_offset: float, side: str,
                      chord_err_mm: float = 0.002) -> Polypath:
    """Сглаживает осевую так, чтобы её эквидистанта (offset фрезы на
    tool_offset) НЕ самопересекалась.

    Тугие места, куда фреза радиуса tool_offset не входит, скругляются ровно
    до проходимого радиуса (морфологическое замыкание для OUTSIDE / размыкание
    для INSIDE). Прямые и пологие участки сохраняются (отклонение микроны).
    Также убирает «веера» компенсации на биарк-кластерах (осевая становится
    чистой ломаной без чередующихся мелких дуг).

    Требует shapely. Если его нет — возвращает исходный контур без изменений.

    Args:
        polypath: осевая (уже с нужной намоткой под сторону)
        tool_offset: эквидистанта фрезы (мм), радиус, на который offset
        side: 'OUTSIDE' (замыкание) или 'INSIDE' (размыкание)
        chord_err_mm: точность дискретизации дуг

    Returns:
        Сглаженный Polypath (ломаная из Line). Намотка и старт сохранены.
    """
    if not polypath or len(polypath.segments) < 3 or tool_offset <= 1e-6:
        return polypath
    try:
        from shapely.geometry import Polygon
    except Exception:
        return polypath  # shapely не установлен — без сглаживания

    pts = _sample_polypath_points(polypath, chord_err_mm)
    if len(pts) < 3:
        return polypath
    try:
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        T = tool_offset
        up = str(side).upper()
        # Морфология должна соответствовать НАПРАВЛЕНИЮ offset стороны:
        #   INSIDE  смещается НАРУЖУ (+) → замыкание (buffer +T,-T): скругляет
        #           вогнутые места уже радиуса фрезы.
        #   OUTSIDE смещается ВНУТРЬ (−) → размыкание (buffer -T,+T): скругляет
        #           выпуклые места уже радиуса фрезы.
        # (См. gcomp/viewer: оба прохода = G41, внутр.+ / внешн.−)
        if up == 'OUTSIDE':
            corrected = poly.buffer(-T, join_style=1, quad_segs=24).buffer(
                T, join_style=1, quad_segs=24)
        else:  # INSIDE / прочее
            corrected = poly.buffer(T, join_style=1, quad_segs=24).buffer(
                -T, join_style=1, quad_segs=24)
        if corrected.is_empty:
            return polypath
        if corrected.geom_type == 'MultiPolygon':
            corrected = max(corrected.geoms, key=lambda p: p.area)
        # Упрощаем результат: убираем избыточные вершины (прямые остаются
        # прямыми, кривые — в пределах допуска), чтобы .anc не распухал
        # И ЧТОБЫ offset не давал петли из мелких зигзагов. Допуск 0.05мм 
        # это компромисс между точностью реза и гладкостью эквидистанты.
        try:
            simp = corrected.simplify(0.05, preserve_topology=True)
            if not simp.is_empty and simp.geom_type == 'Polygon':
                corrected = simp
        except Exception:
            pass
        coords = list(corrected.exterior.coords)
    except Exception:
        return polypath
    if len(coords) < 4:
        return polypath
    if coords[0] == coords[-1]:
        coords = coords[:-1]

    # Сохраняем намотку исходного контура (shapely отдаёт CCW для exterior)
    def signed_area(c):
        a = 0.0
        for i in range(len(c)):
            x1, y1 = c[i]
            x2, y2 = c[(i + 1) % len(c)]
            a += x1 * y2 - x2 * y1
        return a / 2.0

    def orig_area():
        oc = [s.a for s in polypath.segments]
        return signed_area(oc)

    if (signed_area(coords) > 0) != (orig_area() > 0):
        coords = list(reversed(coords))

    # Сдвигаем старт к ближайшей точке к исходному старту (для захода)
    start = polypath.segments[0].a
    best_i = min(range(len(coords)),
                 key=lambda i: (coords[i][0] - start[0]) ** 2
                 + (coords[i][1] - start[1]) ** 2)
    coords = coords[best_i:] + coords[:best_i]

    # Строим polyline из Line
    line_segs: List[Segment] = [
        Line(a=coords[i], b=coords[(i + 1) % len(coords)])
        for i in range(len(coords))
    ]
    line_polypath = Polypath(segments=line_segs, closed=True)
    
    # ── Восстанавливаем дуги из ломаной ──
    # После shapely buffer мы получаем плотную полилинию (точки через ~0.25мм).
    # Если оставить её как Line — offset параллельных линий в местах кривизны 
    # будет давать «веер» (соседние нормали расходятся). Применяем 
    # merge_segments_to_arcs чтобы превратить цепочки коротких Line обратно 
    # в Arc там где они апроксимируют дугу. 
    # 
    # ВАЖНО: ограничиваем минимальный радиус дуг по tool_offset — иначе 
    # merge будет фитить мелкие дуги R<tool, которые создают петли при offset
    # (буферизация shapely как раз ДОЛЖНА была их устранить, но в углах 
    # buffer оставляет «загиб» с малым R).
    try:
        result_polypath = merge_segments_to_arcs(
            line_polypath, tol=chord_err_mm * 5,
            min_chain=4, tangent_tol_deg=3.0
        )
        # Пост-обработка: arc с R < tool_offset заменяем на хорду (Line a→b).
        # Это места которые буфер оставил с микро-кривизной — для G42 они 
        # будут петлями, лучше прямая.
        filtered_segs = []
        for s in result_polypath.segments:
            if isinstance(s, Arc) and s.radius < tool_offset * 0.95:
                filtered_segs.append(Line(a=s.a, b=s.b))
            else:
                filtered_segs.append(s)
        return Polypath(segments=filtered_segs, closed=True)
    except Exception:
        return line_polypath


def flatten_arcs_to_chords(polypath: Polypath) -> Polypath:
    """Заменяет ВСЕ дуги на прямые-хорды (a→b). ТОЛЬКО для визуализации.

    Биарк-аппроксимация кривых из .ai уже разбивает их на множество мелких
    дуг, поэтому ломаная из хорд практически повторяет форму контура. Но
    оффсет ЛОМАНОЙ (параллельный сдвиг прямых + стыковка углов) не порождает
    «вееров»/зигзагов, в отличие от оффсета цепочки мелких разнонаправленных
    дуг, у которых центры и направления чуть разные — их эквидистанты
    пересекаются.

    Превью получается чуть грубее (плавные дуги показаны как частые прямые),
    но без артефактов. В .anc программу по-прежнему идёт исходная геометрия
    (с фильтром biarc по simplify в эмиттере, если включён).
    """
    if not polypath or not polypath.segments:
        return polypath
    new_segments: List[Segment] = []
    for seg in polypath.segments:
        if isinstance(seg, Arc):
            new_segments.append(Line(a=seg.a, b=seg.b))
        else:
            new_segments.append(seg)
    return Polypath(segments=new_segments, closed=polypath.closed)


def join_polypath_corners(polypath: Polypath, tol: float = 0.01) -> Polypath:
    """Соединяет соседние Line-сегменты в их точке пересечения, если 
    между ними есть разрыв.
    
    Используется после оффсета фрагментов CORNER: каждый Line оффсетится 
    параллельно, и в углу между ними образуется разрыв. Эта функция 
    продлевает обе линии до пересечения, заменяя разрыв на острый угол.
    
    Применяется ТОЛЬКО к парам Line→Line. Дуги и Line→Arc оставляются 
    без изменений (для них разрывы решаются по-другому).
    
    Args:
        polypath: контур с возможными разрывами в стыках
        tol: если разрыв меньше — не трогаем (уже состыковано)
    
    Returns:
        Новый Polypath с состыкованными сегментами
    """
    import math
    if not polypath or len(polypath.segments) < 2:
        return polypath
    
    segs = list(polypath.segments)
    n = len(segs)
    
    # Для замкнутого контура также стыкуем последний сегмент с первым.
    # Перебираем все пары (i, i+1) с wrap-around если контур закрыт.
    max_i = n if polypath.closed else n - 1
    for i in range(max_i):
        j = (i + 1) % n
        s1 = segs[i]
        s2 = segs[j]
        if not (isinstance(s1, Line) and isinstance(s2, Line)):
            continue
        
        gap_sq = (s1.b[0]-s2.a[0])**2 + (s1.b[1]-s2.a[1])**2
        if gap_sq < tol*tol:
            continue
        
        dx1 = s1.b[0] - s1.a[0]
        dy1 = s1.b[1] - s1.a[1]
        dx2 = s2.b[0] - s2.a[0]
        dy2 = s2.b[1] - s2.a[1]
        
        det = dx1 * (-dy2) - dy1 * (-dx2)
        if abs(det) < 1e-12:
            continue
        
        rhs_x = s2.a[0] - s1.a[0]
        rhs_y = s2.a[1] - s1.a[1]
        t1 = (rhs_x * (-dy2) - rhs_y * (-dx2)) / det
        
        ix = s1.a[0] + t1 * dx1
        iy = s1.a[1] + t1 * dy1
        
        segs[i] = Line(a=s1.a, b=(ix, iy))
        segs[j] = Line(a=(ix, iy), b=s2.b)
    
    return Polypath(segments=segs, closed=polypath.closed)


def merge_short_segments(polypath: Polypath, 
                          min_segment_len_mm: float = 1.0) -> Polypath:
    """Объединяет цепочки коротких Line-сегментов в одну длинную линию.
    
    Когда biarc-аппроксимация заменена на Line (через simplify_for_visualization),
    остаётся ступенчатая ломаная из 16+ мелких отрезков по 0.06мм в каждом
    скруглении. При оффсете каждый кусочек сдвигается по своей нормали 
    → видны «усики».
    
    Эта функция склеивает соседние короткие Line в одну прямую от старта 
    первого до конца последнего. Получается резкий угол на месте скругления
    (приемлемо для визуализации, скругления R=0.15мм невидимы на экране).
    
    Args:
        polypath: контур с короткими сегментами
        min_segment_len_mm: сегменты короче этого — кандидаты на объединение
    
    Returns:
        Новый Polypath с сокращённым числом сегментов
    """
    if not polypath or len(polypath.segments) < 2:
        return polypath
    
    new_segments: List[Segment] = []
    i = 0
    segs = polypath.segments
    n = len(segs)
    
    while i < n:
        seg = segs[i]
        
        # Если это Line и она короткая — попробуем объединить с цепочкой
        if isinstance(seg, Line) and seg.length() < min_segment_len_mm:
            # Ищем где заканчивается цепочка коротких Line
            j = i
            chain_start = seg.a
            while (j < n 
                   and isinstance(segs[j], Line) 
                   and segs[j].length() < min_segment_len_mm):
                j += 1
            # Цепочка от индекса i до j-1, объединяем в одну Line
            chain_end = segs[j-1].b
            # Если общая длина цепочки тоже маленькая (вся группа < min) — 
            # ещё раз агрессивнее: одной прямой от start до end
            merged = Line(a=chain_start, b=chain_end)
            if merged.length() > 1e-9:
                new_segments.append(merged)
            i = j
        else:
            new_segments.append(seg)
            i += 1
    
    return Polypath(segments=new_segments, closed=polypath.closed)


def merge_arc_clusters_to_arcs(polypath: Polypath,
                                 cluster_tol_mm: float = 0.5,
                                 max_gap_segments: int = 3
                                 ) -> Polypath:
    """Группирует цепочки соседних мелких дуг с близкими центрами в ОДНУ 
    Arc. Используется для упрощения биарк-аппроксимации скруглений из 
    Illustrator: множество микро-дуг (даже разделённых короткими Line 
    в биарке) с центрами в ~0.2мм друг от друга заменяются одной дугой.
    
    Между дугами с близкими центрами могут быть промежуточные Line — 
    они «поглощаются» в группу, если их меньше max_gap_segments подряд.
    
    Args:
        polypath: контур с биарк-аппроксимацией
        cluster_tol_mm: если центры дуг в пределах этого расстояния — 
            они в одной группе
        max_gap_segments: максимум Line между двумя Arc одной группы
    
    Returns:
        Новый Polypath
    """
    if not polypath or len(polypath.segments) < 2:
        return polypath
    
    segs = polypath.segments
    n = len(segs)
    
    # Найдём кластеры дуг по индексам.
    # Кластер = список индексов Arc, центры которых в пределах cluster_tol,
    # и которые разделены не более max_gap_segments промежуточными Line.
    clusters = []  # каждый элемент: (start_idx, end_idx) включительно
    
    i = 0
    while i < n:
        if not isinstance(segs[i], Arc):
            i += 1
            continue
        # Начинаем новый кластер
        cluster_start = i
        cluster_end = i
        cluster_center = segs[i].center
        cluster_ccw = segs[i].ccw
        j = i + 1
        gap = 0  # счётчик подряд идущих не-Arc сегментов
        while j < n:
            if isinstance(segs[j], Arc):
                # Проверим близость центра
                cdx = segs[j].center[0] - cluster_center[0]
                cdy = segs[j].center[1] - cluster_center[1]
                cdist = (cdx*cdx + cdy*cdy) ** 0.5
                if cdist <= cluster_tol_mm and segs[j].ccw == cluster_ccw:
                    cluster_end = j
                    gap = 0
                    j += 1
                else:
                    break
            else:
                gap += 1
                if gap > max_gap_segments:
                    break
                j += 1
        clusters.append((cluster_start, cluster_end))
        i = cluster_end + 1
    
    # Теперь собираем новый список с заменой кластеров на одну Arc
    new_segments: List[Segment] = []
    cluster_map = {start: end for start, end in clusters if end > start}
    
    i = 0
    while i < n:
        if i in cluster_map:
            end = cluster_map[i]
            # Объединяем все дуги от i до end в одну
            # Используем первую и последнюю Arc для конечных точек
            arcs_in_cluster = [k for k in range(i, end+1) if isinstance(segs[k], Arc)]
            first_arc = segs[arcs_in_cluster[0]]
            last_arc = segs[arcs_in_cluster[-1]]
            cnt = len(arcs_in_cluster)
            avg_cx = sum(segs[k].center[0] for k in arcs_in_cluster) / cnt
            avg_cy = sum(segs[k].center[1] for k in arcs_in_cluster) / cnt
            new_segments.append(Arc(
                a=first_arc.a, b=last_arc.b,
                center=(avg_cx, avg_cy),
                ccw=first_arc.ccw
            ))
            i = end + 1
        else:
            new_segments.append(segs[i])
            i += 1
    
    return Polypath(segments=new_segments, closed=polypath.closed)


def _point_in_polypath(point: Point, polypath: Polypath) -> bool:
    """Простой ray-casting тест: лежит ли точка ВНУТРИ замкнутой полилинии.
    
    Для каждого сегмента проверяем пересечение с горизонтальным лучом
    направо от точки. Чётное число пересечений = снаружи, нечётное = внутри.
    
    Для Arc используется аппроксимация хордой (a→b) — достаточно для 
    мелких дуг скруглений.
    """
    if not polypath or not polypath.segments:
        return False
    
    x, y = point
    inside = False
    
    for seg in polypath.segments:
        # Используем точки a и b как хорду
        ax, ay = seg.a
        bx, by = seg.b
        # Стандартный ray casting: пересекает ли отрезок горизонтальный луч y=y вправо?
        if (ay > y) != (by > y):
            # Пересекает y-координату; найдём x пересечения
            t = (y - ay) / (by - ay)
            x_cross = ax + t * (bx - ax)
            if x_cross > x:
                inside = not inside
    
    return inside


def merge_segments_to_arcs(polypath: Polypath, tol: float = 0.02, 
                            min_chain: int = 3,
                            tangent_tol_deg: float = 3.0,
                            short_seg: float = 1.0) -> Polypath:
    """Объединяет цепочки коротких сегментов в одну дугу или линию.
    
    После biarc-разбиения кривых Безье из .ai контуры могут содержать 
    десятки мелких Line+Arc сегментов на одно скругление. Эта функция
    жадно объединяет их в минимально возможное число сегментов:
    
    - длинные одиночные сегменты (Line >= short_seg мм или Arc) остаются как есть;
    - подряд идущие короткие сегменты накапливаются в цепочку, для 
      которой подбирается ОДНА дуга через все точки (с допуском tol).
      Радиус берётся такой, который удовлетворяет ВСЕМ точкам цепочки.
    - если точки лежат на прямой — выводится Line.
    - радиус дуги НЕ ограничивается сверху (метровые дуги остаются дугами).
    
    После основного прохода применяется repair_tangent_breaks: если 
    угол излома касательной на стыке двух соседних результирующих сегментов
    больше tangent_tol_deg градусов — соседние сегменты подменяются 
    объединённой дугой через ВСЕ их точки, чтобы устранить излом.
    
    Args:
        polypath: исходный контур
        tol: допуск отклонения точек от подобранной кривой, мм
        min_chain: минимум сегментов в цепочке для объединения
        tangent_tol_deg: макс. допустимый излом касательной между 
                         соседними сегментами в градусах
        short_seg: порог «короткого» сегмента (мм). Line >= этого не 
                   объединяется. По умолчанию 1.0. Для сглаженных под 
                   фрезу полилиний (много Line 1-5мм, аппроксимирующих 
                   круг) полезно передать 10-20мм.
    
    Returns:
        Новый Polypath.
    """
    segs = polypath.segments
    if not segs:
        return polypath
    
    def _circle_through_3(p1, p2, p3):
        ax, ay = p1; bx, by = p2; cx, cy = p3
        d = 2 * (ax*(by-cy) + bx*(cy-ay) + cx*(ay-by))
        if abs(d) < 1e-9: return None
        ux = ((ax*ax+ay*ay)*(by-cy) + (bx*bx+by*by)*(cy-ay) + (cx*cx+cy*cy)*(ay-by)) / d
        uy = ((ax*ax+ay*ay)*(cx-bx) + (bx*bx+by*by)*(ax-cx) + (cx*cx+cy*cy)*(bx-ax)) / d
        return (ux, uy, math.hypot(ax-ux, ay-uy))
    
    def _all_on_line(pts, tol):
        """Все ли точки на прямой (первая, последняя) с допуском."""
        if len(pts) < 3: return True
        p1, p2 = pts[0], pts[-1]
        dx, dy = p2[0]-p1[0], p2[1]-p1[1]
        L = math.hypot(dx, dy)
        if L < 1e-9: return False
        for px, py in pts[1:-1]:
            dist = abs((px-p1[0])*dy - (py-p1[1])*dx) / L
            if dist > tol: return False
        return True
    
    def _all_on_circle(pts, cx, cy, r, tol):
        for px, py in pts:
            if abs(math.hypot(px-cx, py-cy) - r) > tol:
                return False
        return True
    
    def _seg_length(s):
        if isinstance(s, Line):
            return math.hypot(s.b[0]-s.a[0], s.b[1]-s.a[1])
        elif isinstance(s, Arc):
            a0 = math.atan2(s.a[1]-s.center[1], s.a[0]-s.center[0])
            a1 = math.atan2(s.b[1]-s.center[1], s.b[0]-s.center[0])
            if s.ccw and a1<a0: a1 += 2*math.pi
            if not s.ccw and a1>a0: a1 -= 2*math.pi
            return s.radius * abs(a1-a0)
        return 0
    
    def _arc_from_pts(p1, p_mid, p3, circ):
        cx, cy, r = circ
        a1 = math.atan2(p1[1]-cy, p1[0]-cx)
        am = math.atan2(p_mid[1]-cy, p_mid[0]-cx)
        a3 = math.atan2(p3[1]-cy, p3[0]-cx)
        def _norm(a, ref):
            while a < ref: a += 2*math.pi
            return a
        ccw = _norm(am, a1) < _norm(a3, a1)
        return Arc(a=p1, b=p3, center=(cx, cy), ccw=ccw)
    
    def _arc_from_start_tangent(p_start, p_end, tan_start, p_mid_hint=None):
        """Дуга из p_start в p_end с касательной tan_start в p_start.
        
        Центр лежит на нормали к tan_start через p_start, на расстоянии R
        от обоих точек. Возвращает Arc или None если точки коллинеарны.
        """
        nx, ny = -tan_start[1], tan_start[0]  # нормаль (поворот +90°)
        dx, dy = p_start[0] - p_end[0], p_start[1] - p_end[1]
        d_sq = dx*dx + dy*dy
        if d_sq < 1e-12:
            return None
        dn = dx*nx + dy*ny
        if abs(dn) < 1e-9:
            return None  # коллинеарны → Line
        t = -d_sq / (2.0 * dn)
        cx = p_start[0] + t * nx
        cy = p_start[1] + t * ny
        r = abs(t)
        if r < 0.05:
            return None
        ccw_default = (t > 0)
        if p_mid_hint is not None:
            a1 = math.atan2(p_start[1]-cy, p_start[0]-cx)
            am = math.atan2(p_mid_hint[1]-cy, p_mid_hint[0]-cx)
            a3 = math.atan2(p_end[1]-cy, p_end[0]-cx)
            def _norm_a(a, ref):
                while a < ref: a += 2*math.pi
                return a
            ccw = _norm_a(am, a1) < _norm_a(a3, a1)
        else:
            ccw = ccw_default
        return Arc(a=p_start, b=p_end, center=(cx, cy), ccw=ccw)
    
    SHORT_SEG = short_seg  # сегменты короче — кандидаты на объединение
    tangent_cos_tol = math.cos(math.radians(tangent_tol_deg))
    
    result: List[Segment] = []
    i = 0
    n = len(segs)
    while i < n:
        cur = segs[i]
        cur_L = _seg_length(cur)
        # Длинные одиночные сегменты — не трогаем
        if cur_L >= SHORT_SEG:
            result.append(cur)
            i += 1
            continue
        
        # ── 3-point arc fitting (исходный простой метод) ──
        # Найдём максимальную цепочку коротких сегментов начиная с i
        j = i + 1
        while j < n and _seg_length(segs[j]) < SHORT_SEG:
            j += 1
        max_end = j
        
        best_seg = None
        best_end = i + 1
        
        for jj in range(i + min_chain, max_end + 1):
            chain = segs[i:jj]
            pts = [s.a for s in chain] + [chain[-1].b]
            
            # Прямая?
            if _all_on_line(pts, tol):
                if all(isinstance(s, Line) for s in chain):
                    best_seg = Line(a=pts[0], b=pts[-1])
                    best_end = jj
                    continue
            
            # Дуга через первую/среднюю/последнюю точку
            p1, p_mid, p3 = pts[0], pts[len(pts)//2], pts[-1]
            circ = _circle_through_3(p1, p_mid, p3)
            if circ is None: 
                break
            cx, cy, r = circ
            if r < 0.05: 
                break
            if not _all_on_circle(pts, cx, cy, r, tol):
                break
            
            # Не создавать крошечные дуги из чейна Lines — это численный шум,
            # который потом детектор воспринимает как «острый угол». 
            # Lines с почти-коллинеарными точками → должны оставаться Lines.
            chain_has_arc = any(isinstance(s, Arc) for s in chain)
            if r < 1.0 and not chain_has_arc:
                break
            
            candidate = _arc_from_pts(p1, p_mid, p3, circ)
            
            # ── Tangent check: candidate's start tangent vs previous segment ──
            # Это предотвращает создание arc'ов с тангенциальным разрывом > tol
            # с предыдущим merged segment, что и было корневой причиной 28° breaks
            if result:
                prev_tan = None
                prev = result[-1]
                if isinstance(prev, Arc):
                    pcx, pcy = prev.center
                    prx, pry = prev.b[0]-pcx, prev.b[1]-pcy
                    pL = math.hypot(prx, pry)
                    if pL > 1e-9:
                        if prev.ccw:
                            prev_tan = (-pry/pL, prx/pL)
                        else:
                            prev_tan = (pry/pL, -prx/pL)
                elif isinstance(prev, Line):
                    pdx = prev.b[0] - prev.a[0]
                    pdy = prev.b[1] - prev.a[1]
                    pL = math.hypot(pdx, pdy)
                    if pL > 1e-9:
                        prev_tan = (pdx/pL, pdy/pL)
                
                if prev_tan:
                    # candidate start tangent
                    crx, cry = p1[0]-cx, p1[1]-cy
                    cL = math.hypot(crx, cry)
                    if cL > 1e-9:
                        if candidate.ccw:
                            cand_tan = (-cry/cL, crx/cL)
                        else:
                            cand_tan = (cry/cL, -crx/cL)
                        dot_p = prev_tan[0]*cand_tan[0] + prev_tan[1]*cand_tan[1]
                        if dot_p < tangent_cos_tol:
                            break  # Будет излом > tangent_tol_deg → не удлинять
            
            best_seg = candidate
            best_end = jj
            continue
        
        if best_seg is not None and best_end > i + 1:
            result.append(best_seg)
            i = best_end
        else:
            result.append(cur)
            i += 1
    
    # ── Постобработка: устранение изломов касательной ──
    # На изогнутых контурах (волнистая линия) после merge могут возникнуть
    # ИЗЛОМЫ — в точке стыка двух дуг касательная не непрерывна. 
    # В Illustrator такая фигура отрисовывается плавно, а у нас стыки видны
    # как углы. Чтобы это исправить, проходим по парам соседних сегментов и
    # если угол излома > tangent_tol_deg, пробуем заменить их одной дугой 
    # через все точки.
    def _tangent_at_end(seg):
        if isinstance(seg, Line):
            dx, dy = seg.b[0]-seg.a[0], seg.b[1]-seg.a[1]
            L = math.hypot(dx, dy)
            if L < 1e-9: return None
            return (dx/L, dy/L)
        elif isinstance(seg, Arc):
            # Касательная в точке b дуги: перпендикуляр к радиус-вектору 
            # (cx,cy)->b, направление по обходу
            cx, cy = seg.center
            rx, ry = seg.b[0]-cx, seg.b[1]-cy
            L = math.hypot(rx, ry)
            if L < 1e-9: return None
            # Перпендикуляр: (-ry, rx) для CCW, (ry, -rx) для CW
            if seg.ccw:
                return (-ry/L, rx/L)
            else:
                return (ry/L, -rx/L)
        return None
    
    def _tangent_at_start(seg):
        if isinstance(seg, Line):
            dx, dy = seg.b[0]-seg.a[0], seg.b[1]-seg.a[1]
            L = math.hypot(dx, dy)
            if L < 1e-9: return None
            return (dx/L, dy/L)
        elif isinstance(seg, Arc):
            cx, cy = seg.center
            rx, ry = seg.a[0]-cx, seg.a[1]-cy
            L = math.hypot(rx, ry)
            if L < 1e-9: return None
            if seg.ccw:
                return (-ry/L, rx/L)
            else:
                return (ry/L, -rx/L)
        return None
    
    def _angle_between(t1, t2):
        """Угол между двумя единичными векторами в градусах."""
        if t1 is None or t2 is None: return 0
        dot = max(-1.0, min(1.0, t1[0]*t2[0] + t1[1]*t2[1]))
        return math.degrees(math.acos(dot))
    
    repair_tol = tol * 10  # макс отклонение середины дуги при рефите (0.2мм)
    
    # ── C1-repair: корректировка центров дуг для касательной непрерывности ──
    # На стыках двух Arc вычисляем среднюю касательную и перефитиваем
    # БОЛЕЕ КОРОТКУЮ дугу (меньше искажение) чтобы её касательная на стыке
    # совпала со средней. Эндпоинты НЕ двигаются — меняется только центр/радиус.
    
    def _refit_arc_end_tan(a_pt, b_pt, desired_tan_at_b, original_arc):
        """Перефитить дугу A→B: касательная в B = desired_tan_at_b."""
        tx, ty = desired_tan_at_b
        nx, ny = -ty, tx  # нормаль (лево)
        dx, dy = b_pt[0] - a_pt[0], b_pt[1] - a_pt[1]
        d_sq = dx*dx + dy*dy
        if d_sq < 1e-12: return None
        dn = dx*nx + dy*ny
        if abs(dn) < 1e-9: return None
        t = -d_sq / (2.0 * dn)
        cx = b_pt[0] + t * nx
        cy = b_pt[1] + t * ny
        r = abs(t)
        if r < 0.05 or r > 1e6: return None
        # Определяем CCW по середине оригинальной дуги
        orig_mid = original_arc.point_at(0.5)
        a1 = math.atan2(a_pt[1]-cy, a_pt[0]-cx)
        am = math.atan2(orig_mid[1]-cy, orig_mid[0]-cx)
        a3 = math.atan2(b_pt[1]-cy, b_pt[0]-cx)
        def _na(a, ref):
            while a < ref: a += 2*math.pi
            return a
        ccw = _na(am, a1) < _na(a3, a1)
        new_arc = Arc(a=a_pt, b=b_pt, center=(cx, cy), ccw=ccw)
        # Проверка: середина нового не далеко от середины старого
        new_mid = new_arc.point_at(0.5)
        dev = math.hypot(new_mid[0]-orig_mid[0], new_mid[1]-orig_mid[1])
        chord = math.hypot(b_pt[0]-a_pt[0], b_pt[1]-a_pt[1])
        local_tol = max(repair_tol, chord * 0.15)
        if dev > local_tol: return None
        return new_arc
    
    def _refit_arc_start_tan(a_pt, b_pt, desired_tan_at_a, original_arc):
        """Перефитить дугу A→B: касательная в A = desired_tan_at_a."""
        tx, ty = desired_tan_at_a
        nx, ny = -ty, tx
        dx, dy = a_pt[0] - b_pt[0], a_pt[1] - b_pt[1]
        d_sq = dx*dx + dy*dy
        if d_sq < 1e-12: return None
        dn = dx*nx + dy*ny
        if abs(dn) < 1e-9: return None
        t = -d_sq / (2.0 * dn)
        cx = a_pt[0] + t * nx
        cy = a_pt[1] + t * ny
        r = abs(t)
        if r < 0.05 or r > 1e6: return None
        orig_mid = original_arc.point_at(0.5)
        a1 = math.atan2(a_pt[1]-cy, a_pt[0]-cx)
        am = math.atan2(orig_mid[1]-cy, orig_mid[0]-cx)
        a3 = math.atan2(b_pt[1]-cy, b_pt[0]-cx)
        def _na(a, ref):
            while a < ref: a += 2*math.pi
            return a
        ccw = _na(am, a1) < _na(a3, a1)
        new_arc = Arc(a=a_pt, b=b_pt, center=(cx, cy), ccw=ccw)
        new_mid = new_arc.point_at(0.5)
        dev = math.hypot(new_mid[0]-orig_mid[0], new_mid[1]-orig_mid[1])
        chord = math.hypot(b_pt[0]-a_pt[0], b_pt[1]-a_pt[1])
        local_tol = max(repair_tol, chord * 0.15)
        if dev > local_tol: return None
        return new_arc
    
    # ── Промоутинг коротких Line в Arc для C1-непрерывности ──
    # Короткие Line между двумя Arc — артефакт biarc разбиения.
    # Заменяем их дугой с касательной из предыдущей Arc.
    # ВАЖНО: создаваемая arc должна иметь R > 0.7мм (corner threshold), 
    # иначе detector углов её ложно классифицирует как «острый угол».
    LINE_PROMOTE_MAX = 5.0  # мм — макс длина Line для промоутинга
    MIN_PROMOTE_R = 0.7  # минимальный радиус для безопасного промоутинга
    for idx in range(len(result)):
        seg = result[idx]
        if not isinstance(seg, Line): continue
        if seg.length() > LINE_PROMOTE_MAX: continue
        n_res = len(result)
        prev_seg = result[(idx-1) % n_res]
        next_seg = result[(idx+1) % n_res]
        if not (isinstance(prev_seg, Arc) or isinstance(next_seg, Arc)):
            continue
        p_start = seg.a
        p_end = seg.b
        p_mid = ((p_start[0]+p_end[0])/2, (p_start[1]+p_end[1])/2)
        new_arc = None
        if isinstance(prev_seg, Arc):
            prev_tan = prev_seg.tangent_at_end()
            if prev_tan:
                new_arc = _arc_from_start_tangent(p_start, p_end, prev_tan, p_mid)
        if new_arc is None and isinstance(next_seg, Arc):
            next_tan = next_seg.tangent_at_start()
            if next_tan:
                nx, ny = -next_tan[1], next_tan[0]
                dx, dy = p_end[0]-p_start[0], p_end[1]-p_start[1]
                d_sq = dx*dx + dy*dy
                dn = dx*nx + dy*ny
                if abs(dn) > 1e-9:
                    t = -d_sq / (2.0 * dn)
                    cx = p_end[0] + t * nx
                    cy = p_end[1] + t * ny
                    r = abs(t)
                    if r >= 0.05 and r < 1e6:
                        a1 = math.atan2(p_start[1]-cy, p_start[0]-cx)
                        am = math.atan2(p_mid[1]-cy, p_mid[0]-cx)
                        a3 = math.atan2(p_end[1]-cy, p_end[0]-cx)
                        def _na2(a, ref):
                            while a < ref: a += 2*math.pi
                            return a
                        ccw = _na2(am, a1) < _na2(a3, a1)
                        new_arc = Arc(a=p_start, b=p_end, center=(cx, cy), ccw=ccw)
        if new_arc and new_arc.radius < 1e6:
            arc_mid = new_arc.point_at(0.5)
            dev = math.hypot(arc_mid[0]-p_mid[0], arc_mid[1]-p_mid[1])
            if dev < repair_tol:
                result[idx] = new_arc
    
    
    # ── Final C1-smoothing: dampened iterative repair ──
    # Для оставшихся изломов: рефитим короткую дугу так чтобы её tangent
    # на стыке двигалась на DAMPING к касательной соседа. С damping=0.5
    # это сходится за 10-15 итераций без осцилляции.
    # ВАЖНО: реальные углы (>= CORNER_THRESHOLD) защищаем — не трогаем их.
    DAMPING = 0.5
    CORNER_PROTECT_DEG = 30.0  # стыки с break > этого не трогаем
    
    if polypath.closed and len(result) > 2:
        n_res = len(result)
        # Помечаем реальные углы — их касательные не двигаем
        protected = set()
        for idx in range(n_res):
            s0 = result[idx]; s1 = result[(idx+1)%n_res]
            t0 = _tangent_at_end(s0); t1 = _tangent_at_start(s1)
            if t0 and t1:
                dot = max(-1, min(1, t0[0]*t1[0]+t0[1]*t1[1]))
                ang = math.degrees(math.acos(dot))
                if ang > CORNER_PROTECT_DEG:
                    protected.add(idx)
        
        for _pass in range(15):
            improved_pass = False
            for idx in range(n_res):
                if idx in protected: continue
                cur = result[idx]
                nxt = result[(idx+1) % n_res]
                if not (isinstance(cur, Arc) and isinstance(nxt, Arc)):
                    continue
                t_end = _tangent_at_end(cur)
                t_start = _tangent_at_start(nxt)
                if not t_end or not t_start: continue
                cos_a = t_end[0]*t_start[0] + t_end[1]*t_start[1]
                if cos_a >= tangent_cos_tol: continue
                
                cur_len = cur.length()
                nxt_len = nxt.length()
                
                if cur_len <= nxt_len:
                    tx = (1-DAMPING)*t_end[0] + DAMPING*t_start[0]
                    ty = (1-DAMPING)*t_end[1] + DAMPING*t_start[1]
                    tL = math.hypot(tx, ty)
                    if tL < 1e-9: continue
                    target = (tx/tL, ty/tL)
                    new_arc = _refit_arc_end_tan(cur.a, cur.b, target, cur)
                    if new_arc:
                        result[idx] = new_arc
                        improved_pass = True
                else:
                    tx = DAMPING*t_end[0] + (1-DAMPING)*t_start[0]
                    ty = DAMPING*t_end[1] + (1-DAMPING)*t_start[1]
                    tL = math.hypot(tx, ty)
                    if tL < 1e-9: continue
                    target = (tx/tL, ty/tL)
                    new_arc = _refit_arc_start_tan(nxt.a, nxt.b, target, nxt)
                    if new_arc:
                        result[(idx+1) % n_res] = new_arc
                        improved_pass = True
            
            if not improved_pass:
                break
    
    return Polypath(segments=result, closed=polypath.closed)


def merge_collinear_lines(polypath: Polypath, 
                            angle_tol_deg: float = 1.0,
                            min_segment_len: float = 0.01
                            ) -> Polypath:
    """Объединяет последовательные G1-линии с малым углом перегиба в одну.
    
    Зачем: post-processor выгружает каждый сегмент Line как G1 X Y, а NUM-
    контроллер на каждом стыке проверяет «не угол ли это?» и при ненулевом 
    угле перегиба замедляется. Когда контур разбит на сотни почти-коллинеарных
    отрезков (из биарк-фита или path_offset аппроксимации), машина видит 
    «море углов» и движется рывками, хотя визуально путь гладкий.
    
    После этой функции коллинеарные линии (угол перегиба < angle_tol_deg) 
    становятся одной длинной линией → один G1 → нет угла → нет торможения.
    
    Дуги (Arc) не трогаются — для них есть своя G2/G3 команда. Объединяются 
    только пары Line-Line.
    
    Args:
        polypath: контур с возможно избыточным разбиением на короткие Line'ы
        angle_tol_deg: порог угла перегиба (1° по умолчанию). Меньше — точнее
            но меньше объединений. Больше — больше объединений но точность 
            страдает.
        min_segment_len: если сегмент короче этого, считаем его «дрожанием» 
            и пропускаем (он всё равно объединится с соседним).
    
    Returns:
        Новый Polypath с объединёнными коллинеарными линиями.
    """
    if not polypath or not polypath.segments:
        return polypath
    
    angle_tol_rad = math.radians(angle_tol_deg)
    cos_tol = math.cos(angle_tol_rad)
    
    result = []
    pending_line = None  # накапливаемая Line, готовая к расширению
    
    for seg in polypath.segments:
        if isinstance(seg, Line):
            if pending_line is None:
                pending_line = Line(a=seg.a, b=seg.b)
                continue
            # Сравниваем направление накопленной линии и текущей
            ax = pending_line.b[0] - pending_line.a[0]
            ay = pending_line.b[1] - pending_line.a[1]
            bx = seg.b[0] - seg.a[0]
            by = seg.b[1] - seg.a[1]
            la = math.hypot(ax, ay)
            lb = math.hypot(bx, by)
            if la < 1e-9 or lb < 1e-9:
                # Вырожденный сегмент — просто продляем конец
                pending_line = Line(a=pending_line.a, b=seg.b)
                continue
            # cos угла между направлениями
            cos_angle = (ax*bx + ay*by) / (la * lb)
            if cos_angle >= cos_tol:
                # Угол ≤ tol → коллинеарные, объединяем
                pending_line = Line(a=pending_line.a, b=seg.b)
            else:
                # Реальный угол → фиксируем накопленную линию
                result.append(pending_line)
                pending_line = Line(a=seg.a, b=seg.b)
        else:
            # Не Line (Arc) — фиксируем накопленное, добавляем как есть
            if pending_line is not None:
                result.append(pending_line)
                pending_line = None
            result.append(seg)
    
    if pending_line is not None:
        result.append(pending_line)
    
    # ВАЖНО: НЕ сливаем first и last сегменты замкнутого контура даже если 
    # они коллинеарны. Иначе теряется явная точка старта, которую юзер 
    # мог намеренно поставить в середине прямой грани (например, sdvig 
    # -5мм от RT угла на верхней грани прямоугольника). После сдвига 
    # верхняя грань разбилась на два коллинеарных куска, а слияние 
    # first+last вернуло бы старт на противоположный конец.
    # 
    # Если контур ПРАВДА идеально гладкий (round shape), тут не будет 
    # first/last Line-Line пары для слияния, так что this не мешает.
    
    return Polypath(segments=result, closed=polypath.closed)


def repair_arc_tangency(polypath: Polypath,
                          angle_tol_deg: float = 5.0
                          ) -> Polypath:
    """Восстанавливает C1-непрерывность (касательность) на стыках Arc-Arc.
    
    Биарк-фит при импорте AI часто даёт стыки арок с углом перегиба 
    0.1°-5° — визуально гладко, но NUM-контроллер каждый такой стык 
    считает «углом» и тормозит до 10% от подачи + добавляет корнер-arc'и 
    через COR(...) команду. На контуре из 30 арок это 20 таких ложных 
    углов = 20 замедлений + 20 «лидов в материал» каждые 1-2мм.
    
    Эта функция при стыке с углом < angle_tol_deg слегка модифицирует ВТОРУЮ 
    арку: её центр сдвигается чтобы новая касательная в начале совпала с 
    касательной первой арки в конце. Радиус сохраняется. Геометрия меняется 
    на доли процента — невидимо глазу, но для NC-кода контур становится 
    гладким (один непрерывный путь без углов).
    
    Большие углы (> angle_tol_deg) НЕ трогаются — это легитимные 
    геометрические углы (например, в 3D-ножах с острыми переходами).
    
    Args:
        polypath: контур
        angle_tol_deg: до какого угла считать «биарк-шум» (5° default)
    
    Returns:
        Новый Polypath с C1-восстановленными арками.
    """
    if not polypath or len(polypath.segments) < 2:
        return polypath
    
    angle_tol_rad = math.radians(angle_tol_deg)
    cos_tol = math.cos(angle_tol_rad)
    
    result = list(polypath.segments)
    n = len(result)
    
    # Идём по парам соседей. Для замкнутого контура — включаем стык 
    # последнего и первого.
    range_pairs = range(n - 1)
    if polypath.closed:
        range_pairs = range(n)
    
    for i in range_pairs:
        s1 = result[i]
        s2 = result[(i + 1) % n]
        if not (isinstance(s1, Arc) and isinstance(s2, Arc)):
            continue
        
        # Касательная в конце s1 и начале s2
        try:
            t1 = s1.tangent_at_end()
            t2 = s2.tangent_at_start()
        except Exception:
            continue
        l1 = math.hypot(t1[0], t1[1])
        l2 = math.hypot(t2[0], t2[1])
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        cos_a = (t1[0]*t2[0] + t1[1]*t2[1]) / (l1 * l2)
        cos_a = max(-1.0, min(1.0, cos_a))
        
        # Уже тангенциально (< 0.1°) — не трогаем
        if cos_a > math.cos(math.radians(0.1)):
            continue
        # Слишком большой угол — легитимный, не трогаем
        if cos_a < cos_tol:
            continue
        
        # Угол в диапазоне 0.1° - angle_tol_deg → ремонт
        # Корректируем s2: новый центр такой, чтобы новая касательная 
        # в начале совпала с t1 (касательной в конце s1).
        # 
        # Касательная к арке в точке P относительно центра C:
        #   t = perp(P - C) (перпендикуляр к радиус-вектору)
        # Нужно: t = t1 (направление касательной первой арки в конце)
        # Значит P - C = perp(t1) (или -perp в зависимости от направления арки)
        # Центр C = P - perp(t1) * R  (или + perp)
        # Где R — радиус s2.
        # Направление perp выбираем такое чтобы новый центр был ближе 
        # к исходному (минимум смещения).
        
        try:
            P = s2.a  # точка стыковки
            R = s2.radius
            # Перпендикуляр слева от t1 (CCW поворот на 90°)
            perp_left = (-t1[1] / l1, t1[0] / l1)
            # Перпендикуляр справа от t1 (CW поворот на 90°)
            perp_right = (t1[1] / l1, -t1[0] / l1)
            
            cand_center_left = (P[0] + perp_left[0] * R, P[1] + perp_left[1] * R)
            cand_center_right = (P[0] + perp_right[0] * R, P[1] + perp_right[1] * R)
            
            old_center = s2.center
            d_left = math.hypot(cand_center_left[0] - old_center[0], 
                                 cand_center_left[1] - old_center[1])
            d_right = math.hypot(cand_center_right[0] - old_center[0],
                                  cand_center_right[1] - old_center[1])
            new_center = cand_center_left if d_left < d_right else cand_center_right
            
            # Конечная точка дуги обязана остаться на новой окружности —
            # сдвинем её радиально. Это меняет конечную точку на ту же 
            # окружность но с тем же углом (от нового центра).
            old_end = s2.b
            ce_dx = old_end[0] - new_center[0]
            ce_dy = old_end[1] - new_center[1]
            ce_len = math.hypot(ce_dx, ce_dy)
            if ce_len < 1e-9:
                continue
            new_end = (new_center[0] + ce_dx * R / ce_len,
                       new_center[1] + ce_dy * R / ce_len)
            
            # Заменяем s2 (а заодно s2.a в следующей паре если есть)
            new_s2 = Arc(a=P, b=new_end, center=new_center, ccw=s2.ccw)
            result[(i + 1) % n] = new_s2
            
            # Подгоняем начало следующего за s2 чтобы соответствовало new_end
            j = (i + 2) % n
            if j != (i + 1) % n and j != i:  # не вырожденный случай
                nxt = result[j]
                if isinstance(nxt, (Line, Arc)) and hasattr(nxt, 'a'):
                    # Сдвигаем начало следующего на new_end
                    if isinstance(nxt, Line):
                        result[j] = Line(a=new_end, b=nxt.b)
                    elif isinstance(nxt, Arc):
                        result[j] = Arc(a=new_end, b=nxt.b, center=nxt.center, ccw=nxt.ccw)
        except Exception:
            continue
    
    return Polypath(segments=result, closed=polypath.closed)


def repair_c1_iterative(polypath: Polypath,
                          tangent_tol_deg: float = 0.3,
                          max_iterations: int = 20,
                          damping: float = 0.5,
                          corner_protect_deg: float = 30.0,
                          repair_tol: float = 0.2
                          ) -> Polypath:
    """Восстановление C1-непрерывности (касательности) на стыках Arc-Arc.
    
    Биарк-фит из AI оставляет стыки с углом 0.1°-5° — визуально гладко, 
    но NUM-контроллер каждый такой стык считает «углом» и тормозит до 10% 
    подачи. Эта функция итеративно перерасчитывает ЦЕНТР более короткой из 
    двух арок чтобы её касательная на стыке двигалась к среднему направлению. 
    
    Эндпоинты арок ФИКСИРОВАНЫ — двигается только центр. Это безопасно: 
    стыки с соседними сегментами не нарушаются (точки переходов не меняются).
    
    Итеративный подход с damping=0.5 сходится за 10-20 итераций.
    
    Args:
        polypath: контур (должен быть замкнутым для лучшего эффекта)
        tangent_tol_deg: стыки с углом ≤ этого считаются уже OK
        max_iterations: максимум итераций
        damping: коэффициент сходимости (0.5 = пол-пути за итерацию)
        corner_protect_deg: стыки с углом ≥ этого — легитимные геометр. 
            углы, не трогаем
        repair_tol: допуск на отклонение середины арки от исходной (мм). 
            Если рефит даёт сильное отклонение — арка не заменяется.
    
    Returns:
        Новый Polypath с восстановленной C1-непрерывностью где возможно.
    """
    if not polypath or len(polypath.segments) < 2:
        return polypath
    
    result = list(polypath.segments)
    n = len(result)
    
    def _tan_end(seg):
        if isinstance(seg, Line):
            dx, dy = seg.b[0]-seg.a[0], seg.b[1]-seg.a[1]
            L = math.hypot(dx, dy)
            if L < 1e-9: return None
            return (dx/L, dy/L)
        elif isinstance(seg, Arc):
            cx, cy = seg.center
            rx, ry = seg.b[0]-cx, seg.b[1]-cy
            L = math.hypot(rx, ry)
            if L < 1e-9: return None
            return (-ry/L, rx/L) if seg.ccw else (ry/L, -rx/L)
        return None
    
    def _tan_start(seg):
        if isinstance(seg, Line):
            dx, dy = seg.b[0]-seg.a[0], seg.b[1]-seg.a[1]
            L = math.hypot(dx, dy)
            if L < 1e-9: return None
            return (dx/L, dy/L)
        elif isinstance(seg, Arc):
            cx, cy = seg.center
            rx, ry = seg.a[0]-cx, seg.a[1]-cy
            L = math.hypot(rx, ry)
            if L < 1e-9: return None
            return (-ry/L, rx/L) if seg.ccw else (ry/L, -rx/L)
        return None
    
    def _refit_end_tan(a_pt, b_pt, desired_tan_at_b, original_arc):
        """Перефитить дугу A→B: касательная в B = desired_tan_at_b. 
        Эндпоинты не двигаются."""
        tx, ty = desired_tan_at_b
        nx, ny = -ty, tx
        dx, dy = b_pt[0] - a_pt[0], b_pt[1] - a_pt[1]
        d_sq = dx*dx + dy*dy
        if d_sq < 1e-12: return None
        dn = dx*nx + dy*ny
        if abs(dn) < 1e-9: return None
        t = -d_sq / (2.0 * dn)
        cx = b_pt[0] + t * nx
        cy = b_pt[1] + t * ny
        r = abs(t)
        if r < 0.05 or r > 1e6: return None
        orig_mid = original_arc.point_at(0.5)
        a1 = math.atan2(a_pt[1]-cy, a_pt[0]-cx)
        am = math.atan2(orig_mid[1]-cy, orig_mid[0]-cx)
        a3 = math.atan2(b_pt[1]-cy, b_pt[0]-cx)
        def _na(a, ref):
            while a < ref: a += 2*math.pi
            return a
        ccw = _na(am, a1) < _na(a3, a1)
        new_arc = Arc(a=a_pt, b=b_pt, center=(cx, cy), ccw=ccw)
        new_mid = new_arc.point_at(0.5)
        dev = math.hypot(new_mid[0]-orig_mid[0], new_mid[1]-orig_mid[1])
        chord = math.hypot(b_pt[0]-a_pt[0], b_pt[1]-a_pt[1])
        local_tol = max(repair_tol, chord * 0.15)
        if dev > local_tol: return None
        return new_arc
    
    def _refit_start_tan(a_pt, b_pt, desired_tan_at_a, original_arc):
        """Перефитить дугу A→B: касательная в A = desired_tan_at_a."""
        tx, ty = desired_tan_at_a
        nx, ny = -ty, tx
        dx, dy = a_pt[0] - b_pt[0], a_pt[1] - b_pt[1]
        d_sq = dx*dx + dy*dy
        if d_sq < 1e-12: return None
        dn = dx*nx + dy*ny
        if abs(dn) < 1e-9: return None
        t = -d_sq / (2.0 * dn)
        cx = a_pt[0] + t * nx
        cy = a_pt[1] + t * ny
        r = abs(t)
        if r < 0.05 or r > 1e6: return None
        orig_mid = original_arc.point_at(0.5)
        a1 = math.atan2(a_pt[1]-cy, a_pt[0]-cx)
        am = math.atan2(orig_mid[1]-cy, orig_mid[0]-cx)
        a3 = math.atan2(b_pt[1]-cy, b_pt[0]-cx)
        def _na(a, ref):
            while a < ref: a += 2*math.pi
            return a
        ccw = _na(am, a1) < _na(a3, a1)
        new_arc = Arc(a=a_pt, b=b_pt, center=(cx, cy), ccw=ccw)
        new_mid = new_arc.point_at(0.5)
        dev = math.hypot(new_mid[0]-orig_mid[0], new_mid[1]-orig_mid[1])
        chord = math.hypot(b_pt[0]-a_pt[0], b_pt[1]-a_pt[1])
        local_tol = max(repair_tol, chord * 0.15)
        if dev > local_tol: return None
        return new_arc
    
    tangent_cos_tol = math.cos(math.radians(tangent_tol_deg))
    corner_cos = math.cos(math.radians(corner_protect_deg))
    
    # Помечаем реальные углы — их не трогаем
    protected = set()
    iter_range = range(n) if polypath.closed else range(n - 1)
    for idx in iter_range:
        s0 = result[idx]
        s1 = result[(idx + 1) % n]
        t0 = _tan_end(s0); t1 = _tan_start(s1)
        if t0 and t1:
            dot = max(-1, min(1, t0[0]*t1[0] + t0[1]*t1[1]))
            if dot < corner_cos:  # угол > corner_protect_deg
                protected.add(idx)
    
    for _pass in range(max_iterations):
        improved = False
        for idx in iter_range:
            if idx in protected: continue
            cur = result[idx]
            nxt = result[(idx + 1) % n]
            if not (isinstance(cur, Arc) and isinstance(nxt, Arc)):
                continue
            t_end = _tan_end(cur)
            t_start = _tan_start(nxt)
            if not t_end or not t_start: continue
            cos_a = t_end[0]*t_start[0] + t_end[1]*t_start[1]
            if cos_a >= tangent_cos_tol: continue  # уже OK
            
            # Двигаем тангенс более КОРОТКОЙ арки — она меньше пострадает.
            cur_len = cur.length()
            nxt_len = nxt.length()
            
            if cur_len <= nxt_len:
                tx = (1-damping)*t_end[0] + damping*t_start[0]
                ty = (1-damping)*t_end[1] + damping*t_start[1]
                tL = math.hypot(tx, ty)
                if tL < 1e-9: continue
                target = (tx/tL, ty/tL)
                new_arc = _refit_end_tan(cur.a, cur.b, target, cur)
                if new_arc:
                    result[idx] = new_arc
                    improved = True
            else:
                tx = damping*t_end[0] + (1-damping)*t_start[0]
                ty = damping*t_end[1] + (1-damping)*t_start[1]
                tL = math.hypot(tx, ty)
                if tL < 1e-9: continue
                target = (tx/tL, ty/tL)
                new_arc = _refit_start_tan(nxt.a, nxt.b, target, nxt)
                if new_arc:
                    result[(idx + 1) % n] = new_arc
                    improved = True
        
        if not improved:
            break
    
    return Polypath(segments=result, closed=polypath.closed)
