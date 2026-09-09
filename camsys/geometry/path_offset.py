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


def shift_start_to_point(polypath: Polypath,
                         target,
                         samples_per_seg: int = 32) -> Polypath:
    """Сдвигает начало контура к точке на контуре, ближайшей к `target`.

    Используется, когда точка касания лид-in известна извне (например,
    извлечена из .anc-файла эмиттера) — приводим полипас к тому же
    состоянию, что был бы после явных shift'ов viewer'а, но гарантированно
    в ту же точку что видит эмиттер.

    Двухэтапный поиск:
      1. Грубый сэмпл (`samples_per_seg` точек на сегмент) — находим
         лучший сегмент и параметр t.
      2. Бинарное уточнение вокруг найденной точки на ~40 итераций —
         точность до 1e-6 длины сегмента (микроны для типового ножа).
    Уточнение критично для контуров, состоящих целиком из дуг
    (звёздочки, ёлки, круги): большие дуги имеют длину десятки мм,
    сэмпл 1/32 = 1-2мм промах, из-за чего лид рисуется под кривым углом
    и визуально пересекает соседний контур.

    Args:
        polypath: закрытый Polypath.
        target: (tx, ty) — искомая точка.
        samples_per_seg: N — плотность грубого сэмплирования (32 хватает).

    Returns:
        Новый Polypath, стартующий в ближайшей точке контура к target.
    """
    if not polypath.segments:
        return polypath
    tx, ty = target
    # Этап 1: грубый сэмпл
    best_seg_idx = 0
    best_seg_t = 0.0
    best_dist_sq = float('inf')
    for i, seg in enumerate(polypath.segments):
        for k in range(samples_per_seg + 1):
            t = k / samples_per_seg
            px, py = seg.point_at(t)
            d = (px - tx) * (px - tx) + (py - ty) * (py - ty)
            if d < best_dist_sq:
                best_dist_sq = d
                best_seg_idx = i
                best_seg_t = t
    # Этап 2: бинарное уточнение на найденном сегменте
    #   ищем локальный минимум расстояния к target внутри окна
    #   [t - 1/N, t + 1/N] через 40 итераций золотого сечения.
    seg = polypath.segments[best_seg_idx]
    window = 1.0 / samples_per_seg
    lo = max(0.0, best_seg_t - window)
    hi = min(1.0, best_seg_t + window)
    # золотое сечение
    phi = (5 ** 0.5 - 1) / 2  # ≈ 0.618
    def _dist_sq(t):
        px, py = seg.point_at(t)
        return (px - tx) ** 2 + (py - ty) ** 2
    for _ in range(40):
        a = hi - phi * (hi - lo)
        b = lo + phi * (hi - lo)
        if _dist_sq(a) < _dist_sq(b):
            hi = b
        else:
            lo = a
    best_seg_t = (lo + hi) / 2.0
    # Arc-length от start до найденной точки
    total = 0.0
    for i in range(best_seg_idx):
        total += polypath.segments[i].length()
    total += polypath.segments[best_seg_idx].length() * best_seg_t
    return shift_start_along_contour(polypath, total)


def _point_on_arc_span(arc, pt, tol: float = 1e-6) -> bool:
    """Точка pt лежит в угловом секторе дуги (грубо, для отбора)."""
    import math as _m
    cx, cy = arc.center
    a0 = _m.atan2(arc.a[1] - cy, arc.a[0] - cx)
    a1 = _m.atan2(arc.b[1] - cy, arc.b[0] - cx)
    ap = _m.atan2(pt[1] - cy, pt[0] - cx)
    if arc.ccw:
        if a1 < a0:
            a1 += 2 * _m.pi
        while ap < a0:
            ap += 2 * _m.pi
        return a0 - tol <= ap <= a1 + tol
    else:
        if a1 > a0:
            a1 -= 2 * _m.pi
        while ap > a0:
            ap -= 2 * _m.pi
        return a1 - tol <= ap <= a0 + tol


def shift_start_from_diagonal_zero(polypath: Polypath,
                                   offset: float) -> Polypath:
    """Смещение старта по alphacam-модели: 0 = точка, где ДИАГОНАЛЬ из
    верхне-правого угла bbox к центру пересекает контур (на скруглённом
    углу — его 45°-точка, однозначная для ОБОИХ проходов). От неё:
    offset<0 = влево по ВЕРХНЕЙ грани, offset>0 = вниз по ПРАВОЙ грани.

    Снимает неоднозначность вершины (в самой вершине два прохода выбирали
    разные грани и расходились). Диагональная точка не на вершине, поэтому
    оба прохода садятся в одну точку и совпадают.
    """
    if not polypath or not polypath.segments:
        return polypath
    import math as _m
    xs = [c for s in polypath.segments for c in (s.a[0], s.b[0])]
    ys = [c for s in polypath.segments for c in (s.a[1], s.b[1])]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    P = (maxx, maxy)  # верхне-правый угол bbox
    C = ((minx + maxx) / 2.0, (miny + maxy) / 2.0)  # центр
    dx, dy = C[0] - P[0], C[1] - P[1]
    L = _m.hypot(dx, dy) or 1.0
    dx, dy = dx / L, dy / L
    # первое пересечение луча (P → центр) с контуром
    best = None  # (t, point)
    for s in polypath.segments:
        if isinstance(s, Arc):
            cx, cy = s.center
            fx, fy = P[0] - cx, P[1] - cy
            B = 2 * (fx * dx + fy * dy)
            Cc = fx * fx + fy * fy - s.radius**2
            disc = B * B - 4 * Cc
            if disc < 0:
                continue
            sd = _m.sqrt(disc)
            for t in ((-B - sd) / 2.0, (-B + sd) / 2.0):
                if t > 1e-6:
                    pt = (P[0] + t * dx, P[1] + t * dy)
                    if _point_on_arc_span(s, pt) and (
                            best is None or t < best[0]):
                        best = (t, pt)
        else:
            ax, ay = s.a; bx, by = s.b
            ex, ey = bx - ax, by - ay
            den = dx * ey - dy * ex
            if abs(den) < 1e-12:
                continue
            t = ((ax - P[0]) * ey - (ay - P[1]) * ex) / den
            u = ((ax - P[0]) * dy - (ay - P[1]) * dx) / den
            if t > 1e-6 and -1e-6 <= u <= 1 + 1e-6 and (
                    best is None or t < best[0]):
                best = (t, (P[0] + t * dx, P[1] + t * dy))
    zero_pt = best[1] if best else P
    pp = shift_start_to_point(polypath, zero_pt)
    if abs(offset) < 1e-9:
        return pp
    base = pp.segments[0].a
    # off<0 → X уменьшается (влево по верху); off>0 → Y уменьшается (вниз)
    chosen = None
    chosen_score = None
    for sgn in (1.0, -1.0):
        cand = shift_start_along_contour(pp, sgn * abs(offset))
        s0 = cand.segments[0].a
        score = (base[0] - s0[0]) if offset < 0 else (base[1] - s0[1])
        if chosen_score is None or score > chosen_score:
            chosen_score = score
            chosen = cand
    return chosen if chosen is not None else pp


def shift_start_to_top_x(polypath: Polypath, offset_from_rt: float,
                         top_tol: float = 2.0) -> Polypath:
    """Сдвигает старт вдоль ВЕРХНЕЙ грани на offset_from_rt от верхне-правого
    угла КОНТУРА (0 = угол, <0 = влево).

    Для смещения лида «по координате от угла»: 0 = правый-верхний угол,
    отрицательное = влево вдоль верха. В отличие от shift_start_along_contour
    (двигает по периметру и заворачивает за угол вниз по стороне — каша на
    прямоугольниках), здесь старт всегда остаётся на ВЕРХНЕЙ грани, а X
    зажимается в её пределах (target_x правее угла → угол; левее конца
    верха → конец верха).
    """
    if not polypath or not polypath.segments:
        return polypath
    import math as _m
    ymax = max(max(s.a[1], s.b[1]) for s in polypath.segments)
    top_pts = []
    for s in polypath.segments:
        for p in (s.a, s.b):
            if p[1] > ymax - top_tol:
                top_pts.append(p)
    if not top_pts:
        return polypath
    top_min_x = min(p[0] for p in top_pts)
    top_max_x = max(p[0] for p in top_pts)   # верхне-правый угол контура

    # ── ПОЛОЖИТЕЛЬНЫЙ offset: вниз по ПРАВОЙ грани (так было исторически) ──
    if offset_from_rt > 1e-9:
        xmax = max(max(s.a[0], s.b[0]) for s in polypath.segments)
        right_pts = [p for s in polypath.segments for p in (s.a, s.b)
                     if p[0] > xmax - top_tol]
        if right_pts:
            right_ymax = max(p[1] for p in right_pts)  # верх правой грани
            right_ymin = min(p[1] for p in right_pts)
            ty = max(right_ymin, min(right_ymax - offset_from_rt, right_ymax))
            best_r = None  # (x, point) — берём самую правую точку на ty
            for s in polypath.segments:
                if max(s.a[0], s.b[0]) <= xmax - top_tol:
                    continue
                if isinstance(s, Arc):
                    cx, cy = s.center
                    dy2 = s.radius**2 - (ty - cy)**2
                    if dy2 < 0:
                        continue
                    for xx in (cx + _m.sqrt(dy2), cx - _m.sqrt(dy2)):
                        if xx <= xmax - top_tol:
                            continue
                        pt = (xx, ty)
                        if _point_on_arc_span(s, pt) and (
                                best_r is None or xx > best_r[0]):
                            best_r = (xx, pt)
                else:
                    ax, ay = s.a; bx, by = s.b
                    lo, hi = min(ay, by), max(ay, by)
                    if lo - 1e-6 <= ty <= hi + 1e-6 and abs(by - ay) > 1e-12:
                        t = (ty - ay) / (by - ay)
                        xx = ax + t * (bx - ax)
                        if xx <= xmax - top_tol:
                            continue
                        if best_r is None or xx > best_r[0]:
                            best_r = (xx, (xx, ty))
            if best_r is not None:
                return shift_start_to_point(polypath, best_r[1])
        # если правой грани не нашли — падаем в угол
        return shift_start_to_point(polypath, max(top_pts, key=lambda p: p[0]))

    tx = max(top_min_x, min(top_max_x + offset_from_rt, top_max_x))
    # У самого угла (offset≈0) — ставим точно в верхне-правую точку.
    if tx >= top_max_x - 1e-6:
        _rt = max(top_pts, key=lambda p: p[0])
        return shift_start_to_point(polypath, _rt)

    best = None  # (y, point)
    for s in polypath.segments:
        if max(s.a[1], s.b[1]) <= ymax - top_tol:
            continue
        if isinstance(s, Arc):
            cx, cy = s.center
            dx2 = s.radius**2 - (tx - cx)**2
            if dx2 < 0:
                continue
            for yy in (cy + _m.sqrt(dx2), cy - _m.sqrt(dx2)):
                if yy <= ymax - top_tol:
                    continue  # точка не у верха
                pt = (tx, yy)
                if _point_on_arc_span(s, pt):
                    if best is None or yy > best[0]:
                        best = (yy, pt)
        else:
            ax, ay = s.a; bx, by = s.b
            lo, hi = min(ax, bx), max(ax, bx)
            if lo - 1e-6 <= tx <= hi + 1e-6 and abs(bx - ax) > 1e-12:
                t = (tx - ax) / (bx - ax)
                yy = ay + t * (by - ay)
                if yy <= ymax - top_tol:
                    continue  # точка не у верха (напр. правая вертикаль)
                if best is None or yy > best[0]:
                    best = (yy, (tx, yy))
    if best is None:
        return polypath
    return shift_start_to_point(polypath, best[1])


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


def offset_polypath_toward_body(polypath: Polypath, offset: float,
                                parent_is_cw: bool) -> Polypath:
    """Оффсет открытого фрагмента в сторону ТЕЛА контура по ЛОКАЛЬНОЙ
    нормали (winding-based). В отличие от offset_polypath_toward_center,
    корректно работает на ВОГНУТЫХ углах — там глобальный центр bbox
    смотрит наружу от локального тела, а локальная нормаль всегда верна.

    Направление тела определяется намоткой родительского контура:
      - CW-намотка (parent_is_cw=True): тело СПРАВА от направления
        обхода. Right-нормаль = (dy, -dx)/|d| для линии; для дуги —
        к центру если ccw, от центра если cw.
      - CCW-намотка: тело слева, нормаль инвертируется.

    Args:
        polypath: открытый фрагмент (corner subpath).
        offset: величина смещения (положительная, всегда к телу).
        parent_is_cw: намотка родительского контура (shoelace > 0).

    Returns:
        Смещённый Polypath.
    """
    if not polypath or not polypath.segments or abs(offset) < 1e-9:
        return polypath
    import math
    d_abs = abs(offset)
    # Знак: для CW тело справа → сдвигаем по right-нормали (dy,-dx).
    # right-normal-sign: +1 = right perp, -1 = left perp.
    right_sign = 1.0 if parent_is_cw else -1.0
    new_segments: List[Segment] = []
    for seg in polypath.segments:
        if isinstance(seg, Line):
            dx = seg.b[0] - seg.a[0]
            dy = seg.b[1] - seg.a[1]
            d = math.hypot(dx, dy)
            if d < 1e-9:
                new_segments.append(seg)
                continue
            # right perp = (dy, -dx)/d
            nx = right_sign * dy / d
            ny = right_sign * (-dx) / d
            a2 = (seg.a[0] + nx * d_abs, seg.a[1] + ny * d_abs)
            b2 = (seg.b[0] + nx * d_abs, seg.b[1] + ny * d_abs)
            new_segments.append(Line(a2, b2))
        elif isinstance(seg, Arc):
            # Для дуги сдвиг к телу = изменение радиуса. Тело справа (CW):
            #   - ccw-дуга: центр слева от движения → тело справа = наружу
            #     от центра → R растёт.
            #   - cw-дуга: центр справа → тело справа = к центру → R
            #     уменьшается.
            cx, cy = seg.center
            r = math.hypot(seg.a[0] - cx, seg.a[1] - cy)
            # Определяем, с какой стороны от направления движения центр.
            # Для ccw центр слева, для cw центр справа.
            body_is_toward_center = (seg.ccw and not parent_is_cw) or \
                                     (not seg.ccw and parent_is_cw)
            if body_is_toward_center:
                new_r = r - d_abs
            else:
                new_r = r + d_abs
            if new_r < 1e-6:
                # Вырожденная дуга — заменяем линией
                new_segments.append(Line(seg.a, seg.b))
                continue
            scale = new_r / r
            a2 = (cx + (seg.a[0] - cx) * scale, cy + (seg.a[1] - cy) * scale)
            b2 = (cx + (seg.b[0] - cx) * scale, cy + (seg.b[1] - cy) * scale)
            new_segments.append(Arc(a2, b2, (cx, cy), seg.ccw))
        else:
            new_segments.append(seg)
    return Polypath(new_segments)


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


def find_equidistant_kinks(equidistant: Polypath,
                           source_polypath: Polypath = None,
                           small_arc_max_mm: float = 2.0,
                           cusp_angle_deg: float = 60.0,
                           max_loop_segs: int = 6):
    """Ищет ИЗЛОМЫ на уже построенной эквидистанте — точки, где нужен
    угловой рез. Возвращает список изломов с классификацией 2D/3D.

    Подход (по идее юзера): эквидистанта уже построена от КОНКРЕТНОГО
    пути (внутр./внешн.), значит направление смещения зашито в ней.
    Излом = место, где эквидистанта:
      • САМОПЕРЕСЕКАЕТСЯ (петля) — фреза не проходит, острый внешний
        угол свернулся в петлю, ИЛИ
      • даёт ОСТРИЁ (cusp) — резкий разворот направления > порога.
    Там нужен угловой рез. Сторона/направление лида уже правильные,
    т.к. взят путь с определённым смещением.

    Классификация 2D/3D — по исходной геометрии рядом с изломом:
      • есть дуга малого R (< small_arc_max_mm) → 2D (скругление)
      • иначе → 3D (острый стык)

    Args:
        equidistant: построенная эквидистанта пути (offset).
        source_polypath: исходный контур (для классификации 2D/3D).
        small_arc_max_mm: порог радиуса дуги для 2D.
        cusp_angle_deg: порог остриё (поворот направления, град).
        max_loop_segs: макс. длина петли самопересечения.

    Returns:
        list of dict: [{'point': (x,y), 'kind': '2D'|'3D',
                        'type': 'loop'|'cusp'}]
    """
    result = []
    if not equidistant or len(equidistant.segments) < 2:
        return result
    segs = equidistant.segments
    n = len(segs)

    # ── 1. САМОПЕРЕСЕЧЕНИЯ (петли) ──
    seen_pts = []
    for i in range(n):
        s1 = segs[i]
        for delta in range(2, min(max_loop_segs + 1, n - 1)):
            j = (i + delta) % n
            if j == i:
                continue
            if (j + 1) % n == i:
                continue
            s2 = segs[j]
            hit = _segment_chord_intersect(s1, s2)
            if hit is not None:
                _t1, _t2, ix, iy = hit
                # дедуп близких точек
                if any(math.hypot(ix - px, iy - py) < 0.05
                       for px, py in seen_pts):
                    continue
                seen_pts.append((ix, iy))
                # касательные эквидистанты в точке петли — направление
                # для лида (заход по s1, выход по s2).
                try:
                    _tin = s1.tangent_at_end()
                    _tout = s2.tangent_at_start()
                except Exception:
                    _tin = _tout = (1.0, 0.0)
                result.append({'point': (ix, iy), 'type': 'loop',
                               'kind': None,
                               'seg_i': i, 'seg_j': j,
                               'tan_in': _tin, 'tan_out': _tout})

    # ── 2. ОСТРИЯ (cusp): резкий разворот направления в вершине ──
    verts = list(range(n - 1))
    if equidistant.closed:
        verts.append(n - 1)
    for vi in verts:
        s1 = segs[vi]
        s2 = segs[(vi + 1) % n]
        try:
            t1 = s1.tangent_at_end()
            t2 = s2.tangent_at_start()
        except Exception:
            continue
        d1 = math.hypot(t1[0], t1[1])
        d2 = math.hypot(t2[0], t2[1])
        if d1 < 1e-9 or d2 < 1e-9:
            continue
        t1 = (t1[0] / d1, t1[1] / d1)
        t2 = (t2[0] / d2, t2[1] / d2)
        dot = max(-1.0, min(1.0, t1[0] * t2[0] + t1[1] * t2[1]))
        turn = math.degrees(math.acos(dot))
        if turn >= cusp_angle_deg:
            pt = s1.b
            if any(math.hypot(pt[0] - px, pt[1] - py) < 0.05
                   for px, py in seen_pts):
                continue
            seen_pts.append(pt)
            result.append({'point': pt, 'type': 'cusp', 'kind': None,
                           'seg_i': vi, 'seg_j': (vi + 1) % n,
                           'tan_in': t1, 'tan_out': t2})

    # ── 3. Классификация 2D/3D по исходной геометрии рядом с изломом ──
    if source_polypath and source_polypath.segments:
        for kink in result:
            kx, ky = kink['point']
            near_small_arc = False
            for s in source_polypath.segments:
                if isinstance(s, Arc) and s.radius < small_arc_max_mm:
                    # дистанция от излома до дуги (грубо — до концов/центра)
                    for probe in (s.a, s.b):
                        if math.hypot(kx - probe[0], ky - probe[1]) < \
                                small_arc_max_mm + 1.0:
                            near_small_arc = True
                            break
                if near_small_arc:
                    break
            kink['kind'] = '2D' if near_small_arc else '3D'
    else:
        for kink in result:
            kink['kind'] = '3D'

    return result


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


def _ang_in_sweep(seg, px, py, edge_tol: float = 1e-7) -> bool:
    """Точка (px,py) лежит на дуге seg СТРОГО внутри её сектора (не на концах)?"""
    cx, cy = seg.center
    a0 = math.atan2(seg.a[1] - cy, seg.a[0] - cx)
    a1 = math.atan2(seg.b[1] - cy, seg.b[0] - cx)
    if seg.ccw and a1 < a0:
        a1 += 2 * math.pi
    if (not seg.ccw) and a1 > a0:
        a1 -= 2 * math.pi
    ap = math.atan2(py - cy, px - cx)
    if seg.ccw:
        while ap < a0:
            ap += 2 * math.pi
        while ap > a0 + 2 * math.pi:
            ap -= 2 * math.pi
        return a0 + edge_tol < ap < a1 - edge_tol
    else:
        while ap > a0:
            ap -= 2 * math.pi
        while ap < a0 - 2 * math.pi:
            ap += 2 * math.pi
        return a1 + edge_tol < ap < a0 - edge_tol


def _seg_true_hits(s1, s2):
    """Точки ИСТИННОГО пересечения двух сегментов (Line/Arc), строго ВНУТРИ
    обоих (общие концы исключены). Работает с реальной геометрией дуг
    (arc-arc через пересечение окружностей, line-arc через квадратное
    уравнение), а не с хордами — поэтому ловит петли из-за кривизны."""
    hits = []
    is1a = isinstance(s1, Arc); is2a = isinstance(s2, Arc)
    if not is1a and not is2a:
        x1, y1 = s1.a; x2, y2 = s1.b; x3, y3 = s2.a; x4, y4 = s2.b
        d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
        if abs(d) < 1e-15:
            return hits
        t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
        u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
        if 1e-7 < t < 1 - 1e-7 and 1e-7 < u < 1 - 1e-7:
            hits.append((x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
        return hits
    if is1a and is2a:
        c1 = s1.center; r1 = s1.radius; c2 = s2.center; r2 = s2.radius
        dx = c2[0] - c1[0]; dy = c2[1] - c1[1]
        d = math.hypot(dx, dy)
        if d < 1e-12 or d > r1 + r2 or d < abs(r1 - r2):
            return hits
        a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
        h2 = r1 * r1 - a * a
        if h2 < 0:
            return hits
        h = math.sqrt(max(0.0, h2))
        xm = c1[0] + a * dx / d; ym = c1[1] + a * dy / d
        for sgn in (1, -1):
            px = xm + sgn * h * (-dy) / d
            py = ym + sgn * h * (dx) / d
            if _ang_in_sweep(s1, px, py) and _ang_in_sweep(s2, px, py):
                hits.append((px, py))
        return hits
    # line-arc
    line, arc = (s1, s2) if not is1a else (s2, s1)
    ax, ay = line.a; bx, by = line.b
    cx, cy = arc.center; r = arc.radius
    dx = bx - ax; dy = by - ay
    fx = ax - cx; fy = ay - cy
    A = dx * dx + dy * dy
    B = 2 * (fx * dx + fy * dy)
    Cc = fx * fx + fy * fy - r * r
    disc = B * B - 4 * A * Cc
    if A < 1e-18 or disc < 0:
        return hits
    sd = math.sqrt(disc)
    for t in ((-B - sd) / (2 * A), (-B + sd) / (2 * A)):
        if 1e-7 < t < 1 - 1e-7:
            px = ax + t * dx; py = ay + t * dy
            if _ang_in_sweep(arc, px, py):
                hits.append((px, py))
    return hits


def _truncate_to_point(seg, X, keep_start: bool):
    """Обрезает сегмент до точки X (X лежит на сегменте). Для Arc центр/ccw/
    радиус сохраняются, меняется только один конец."""
    if isinstance(seg, Arc):
        if keep_start:
            return Arc(a=seg.a, b=X, center=seg.center, ccw=seg.ccw)
        return Arc(a=X, b=seg.b, center=seg.center, ccw=seg.ccw)
    if keep_start:
        return Line(a=seg.a, b=X)
    return Line(a=X, b=seg.b)


def despike_polypath(polypath: Polypath, max_pass: int = 80,
                     seg_window: int = 10) -> Polypath:
    """Убирает петли/шипы самопересечения на пути, СОХРАНЯЯ его концы.

    Зачем отдельно от trim_self_intersections: тот ищет пересечения по
    ХОРДАМ дуг и не видит петли, возникающие из-за самой КРИВИЗНЫ. На
    corner-эквидистанте viewer'а (пооссегментный оффсет сырого биарк-
    фрагмента) на стыках соседних offset-дуг и на остриях образуются
    микро-петли: сегмент k+1 стартует на 0.02–0.04мм «позади» конца k,
    плюс попадаются вырожденные дуги (R≈0, длина 0). Здесь используется
    ИСТИННОЕ пересечение дуг (arc-arc / line-arc), а не сэмплинг — поэтому
    ловятся даже крошечные петли на остриях, которые полилинейный подход
    пропускает.

    Алгоритм: ищем пару сегментов (в пределах seg_window по индексу — петли
    локальны), у которых есть истинное внутреннее пересечение X; обрезаем
    первый сегмент до X, второй — от X, выбрасываем сегменты между ними;
    повторяем. Обрезаются только ВНУТРЕННИЕ концы, поэтому первая и
    последняя точки пути неизменны (к ним привязаны lead-in/lead-out).

    ВАЖНО: это чисто визуальный артефакт РЕНДЕРА пути фрезы. В .anc угол
    пишется сырым фрагментом + G42 (машинная компенсация), станок сам
    считает эквидистанту без петель — эмиссия .anc НЕ затрагивается.

    seg_window ограничивает поиск локальными парами, что делает проход
    линейным по числу сегментов (быстро даже на крупных углах).
    """
    if not polypath or len(polypath.segments) < 3:
        return polypath
    segs = list(polypath.segments)
    for _ in range(max_pass):
        n = len(segs)
        found = None
        for i in range(n):
            jmax = min(n, i + 1 + seg_window)
            for j in range(i + 1, jmax):
                if polypath.closed and i == 0 and j == n - 1:
                    continue
                hs = _seg_true_hits(segs[i], segs[j])
                if hs:
                    found = (i, j, hs[0])
                    break
            if found:
                break
        if not found:
            break
        i, j, X = found
        si = _truncate_to_point(segs[i], X, keep_start=True)
        sj = _truncate_to_point(segs[j], X, keep_start=False)
        segs = segs[:i] + [si, sj] + segs[j + 1:]
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
    tool_offset) была физически проходима фрезой.

    ПРИНЦИП (переработан): shapely-морфология используется ТОЛЬКО как
    ДЕТЕКТОР тугих мест. Контур патчится точечно:
      - Сегменты, которые морфология НЕ изменила (расстояние всех сэмплов
        до morph-границы < tol) — остаются ОРИГИНАЛЬНЫМИ Line/Arc.
        Окружность остаётся окружностью (одна G2/G3 дуга в .anc), прямые
        остаются прямыми. НИКАКИХ «мелких волн» на гладких местах.
      - Непрерывные кластеры изменённых сегментов (реальные углы и
        вогнутости/выпуклости туже радиуса фрезы) — заменяются участком
        morph-границы (короткая ломаная скругления).

    Морфология (соответствует направлению offset стороны):
      INSIDE  смещается НАРУЖУ (+) → замыкание buffer(+T,-T)
      OUTSIDE смещается ВНУТРЬ (−) → размыкание buffer(-T,+T)

    Требует shapely. Если его нет — возвращает исходный контур.

    Args:
        polypath: осевая (уже с нужной намоткой под сторону)
        tool_offset: эквидистанта фрезы (мм)
        side: 'OUTSIDE' или 'INSIDE'
        chord_err_mm: точность дискретизации дуг при сэмплировании

    Returns:
        Polypath: оригинальные сегменты + локальные заплатки в тугих местах.
    """
    if not polypath or len(polypath.segments) < 3 or tool_offset <= 1e-6:
        return polypath
    try:
        from shapely.geometry import Polygon, Point
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
        # ЗАМЫКАНИЕ (closing): закрывает узкие ВЫЕМКИ (пазы/щели уже 2T),
        # в которые фреза, идущая центром по осевой, физически не входит
        # (тело задевает противоположную стенку). Направление side здесь
        # НЕ важно: заплатки берутся ТОЛЬКО в зонах, помеченных
        # физическим детектором ниже; проходимые места (выступы, острые
        # языки, реальные углы) детектор не метит и closing их не тронет.
        corrected = poly.buffer(T, join_style=1, quad_segs=24).buffer(
            -T, join_style=1, quad_segs=24)
        if corrected.is_empty:
            return polypath
        if corrected.geom_type == 'MultiPolygon':
            corrected = max(corrected.geoms, key=lambda p: p.area)
        ring = corrected.exterior
    except Exception:
        return polypath

    # ── Классификация сегментов: физическая проходимость фрезы ──
    # НОЖЕВОЙ рез: центр фрезы идёт ПО осевой. Точка осевой проходима,
    # если диск R=T с центром в ней не задевает ДРУГИЕ участки контура.
    # «Другие» = точки контура на ДУГОВОМ расстоянии больше 1.5T от
    # текущей (циклически). Так:
    #   - реальные углы и острые «языки» НЕ детектятся (вторая сторона —
    #     ближняя по дуге окрестность) — их дорабатывает corner-программа,
    #     станок (SCLN) сам обрезает петли компенсации;
    #   - узкие щели/пазы уже 2T детектятся: противоположная стенка
    #     геометрически рядом, но по дуге далеко.
    # Реализация: равномерный сэмпл контура (шаг ~T/3) + grid-hash для
    # быстрого поиска соседей по расстоянию.
    import math as _md
    n = len(polypath.segments)
    step = max(0.05, T / 3.0)
    samples = []  # (x, y, s_arc, seg_idx)
    s_acc = 0.0
    for si, seg in enumerate(polypath.segments):
        L = seg.length()
        if L < 1e-9:
            continue
        k_n = max(1, int(L / step))
        for k in range(k_n):
            t = k / k_n
            px, py = seg.point_at(t)
            samples.append((px, py, s_acc + t * L, si))
        s_acc += L
    total_len = s_acc
    M = len(samples)
    if M < 8:
        return polypath

    cell = 2.0 * T
    grid = {}
    for idx, (px, py, _sv, _si) in enumerate(samples):
        key = (int(px // cell), int(py // cell))
        grid.setdefault(key, []).append(idx)

    excl_win = 1.5 * T
    tol_hit = T * 0.98
    changed = [False] * n
    for idx, (px, py, sv, si) in enumerate(samples):
        if changed[si]:
            continue
        cx, cy = int(px // cell), int(py // cell)
        hit = False
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for j in grid.get((gx, gy), ()):
                    if j == idx:
                        continue
                    qx, qy, sq, _sj = samples[j]
                    ds = abs(sv - sq)
                    ds = min(ds, total_len - ds)
                    if ds < excl_win:
                        continue
                    if _md.hypot(px - qx, py - qy) < tol_hit:
                        hit = True
                        break
                if hit:
                    break
            if hit:
                break
        if hit:
            changed[si] = True

    # ── Слияние кластеров через короткие «мосты» ──
    # Нетронутый участок короче 2·excl_win, зажатый между двумя
    # changed-зонами (типичный случай: ДНО узкого паза между его
    # стенками — само дно ничего «чужого» рядом не видит, т.к. стенки
    # близки по дуге и исключены), включается в кластер — иначе заплатка
    # рвётся на две половинки по краям паза, а дно остаётся.
    if any(changed) and not all(changed):
        seg_lens = [seg.length() for seg in polypath.segments]
        merged = True
        while merged:
            merged = False
            i2 = 0
            while i2 < n:
                if changed[i2]:
                    i2 += 1
                    continue
                # Скан непрерывного нетронутого блока
                j2 = i2
                blk_len = 0.0
                while j2 < n and not changed[j2]:
                    blk_len += seg_lens[j2]
                    j2 += 1
                left_ch = changed[(i2 - 1) % n]
                right_ch = changed[j2 % n]
                if left_ch and right_ch and blk_len < 2.0 * excl_win \
                        and not (i2 == 0 and j2 == n):
                    for k2 in range(i2, j2):
                        changed[k2] = True
                    merged = True
                i2 = j2

    if not any(changed):
        return polypath          # Всё проходимо — контур не трогаем ВООБЩЕ
    if all(changed):
        # Весь контур туже фрезы — деградация до старого поведения:
        # полная замена morph-ломаной.
        coords = list(ring.coords)
        if coords and coords[0] == coords[-1]:
            coords = coords[:-1]
        if len(coords) < 3:
            return polypath

        def _sa(c):
            a = 0.0
            for j in range(len(c)):
                x1, y1 = c[j]
                x2, y2 = c[(j + 1) % len(c)]
                a += x1 * y2 - x2 * y1
            return a / 2.0
        orig_a = _sa([s.a for s in polypath.segments])
        if (_sa(coords) > 0) != (orig_a > 0):
            coords = list(reversed(coords))
        start = polypath.segments[0].a
        bi = min(range(len(coords)),
                 key=lambda j: (coords[j][0] - start[0]) ** 2
                 + (coords[j][1] - start[1]) ** 2)
        coords = coords[bi:] + coords[:bi]
        return Polypath(segments=[Line(a=coords[j],
                                       b=coords[(j + 1) % len(coords)])
                                  for j in range(len(coords))], closed=True)

    # ── Точечный патчинг кластеров ──
    # Ротация к нетронутому сегменту, чтобы кластеры не пересекали границу
    # списка.
    first_ok = next(i for i in range(n) if not changed[i])
    order = list(range(first_ok, n)) + list(range(0, first_ok))

    # Подпуть morph-границы между двумя точками (вдоль ring, короткой
    # дорогой в направлении намотки полипаса).
    ring_coords = list(ring.coords)
    if ring_coords and ring_coords[0] == ring_coords[-1]:
        ring_coords = ring_coords[:-1]
    m = len(ring_coords)

    def _nearest_ring_idx(pt):
        return min(range(m), key=lambda j: (ring_coords[j][0] - pt[0]) ** 2
                   + (ring_coords[j][1] - pt[1]) ** 2)

    def _sa2(c):
        a = 0.0
        for j in range(len(c)):
            x1, y1 = c[j]
            x2, y2 = c[(j + 1) % len(c)]
            a += x1 * y2 - x2 * y1
        return a / 2.0
    ring_ccw = _sa2(ring_coords) > 0
    poly_ccw = _sa2([s.a for s in polypath.segments]) > 0
    step = 1 if (ring_ccw == poly_ccw) else -1

    def _ring_subpath(pt_a, pt_b):
        """Точки ring между pt_a и pt_b по ходу намотки полипаса."""
        ia = _nearest_ring_idx(pt_a)
        ib = _nearest_ring_idx(pt_b)
        out = []
        j = ia
        guard = 0
        while j != ib and guard <= m:
            out.append(ring_coords[j])
            j = (j + step) % m
            guard += 1
        out.append(ring_coords[ib])
        # Прореживание: замена плотной дискретизации буфера (шаг ~0.1мм)
        # на разумную ломаную. RDP-лайт через shapely.
        try:
            from shapely.geometry import LineString
            ls = LineString(out).simplify(0.01, preserve_topology=False)
            out = list(ls.coords)
        except Exception:
            pass
        return out

    new_segs: List[Segment] = []
    i_pos = 0
    while i_pos < n:
        idx = order[i_pos]
        if not changed[idx]:
            new_segs.append(polypath.segments[idx])
            i_pos += 1
            continue
        # Начало кластера изменённых сегментов
        j_pos = i_pos
        while j_pos < n and changed[order[j_pos]]:
            j_pos += 1
        # Точки стыковки: конец предыдущего нетронутого сегмента (или
        # start первого изменённого) → начало следующего нетронутого.
        pt_a = polypath.segments[order[i_pos]].a
        pt_b = (polypath.segments[order[j_pos]].a if j_pos < n
                else polypath.segments[order[0]].a)
        patch = _ring_subpath(pt_a, pt_b)
        # Пришиваем: линия от pt_a к первой точке patch, дальше по patch,
        # затем линия к pt_b (микро-стыки < tol — визуально нулевые).
        chain = [pt_a] + patch + [pt_b]
        for k in range(len(chain) - 1):
            a, b = chain[k], chain[k + 1]
            if (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 < 1e-12:
                continue
            new_segs.append(Line(a=a, b=b))
        i_pos = j_pos

    if len(new_segs) < 3:
        return polypath
    return Polypath(segments=new_segs, closed=True)


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
