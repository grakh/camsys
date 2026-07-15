"""
geometry/lead_collision.py — проверка коллизии lead-in/out с обстоятельствами,
АВТОПОДБОР позиции старта, и ВЫСОКОУРОВНЕВЫЕ функции построения lead'ов.

ИСПОЛЬЗУЕТСЯ ОБЕИМИ СТОРОНАМИ — viewer_2d.py и mtx_anderson.py — чтобы 
гарантировать что превью и сгенерированный NC-код выдают ОДИНАКОВЫЕ lead'ы.

Высокоуровневый API:
    plan_lead_in(polypath, request, contours_lines, ...) 
        → (возможно сдвинутый polypath, lead_in, collision_flag)
    
    plan_lead_out(polypath, request, contours_lines, ...) 
        → (lead_out, collision_flag)

Низкоуровневый API:
    build_lead_for_polypath() — построение line+arc на старте/конце polypath
    lead_crosses_contours()   — проверка коллизии (intersection + distance)
    auto_avoid_collision()    — перебор сдвигов/углов/укорачивания
    polypath_to_lines()       — line-only апроксимация контура
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Tuple, Optional, Callable

from .primitives import Polypath, Segment, Line, Arc, Point


# (Алгоритм сдвигов теперь использует +5мм и доли периметра — см. 
# auto_avoid_collision. Старая константа _AUTO_SHIFT_CANDIDATES удалена.)


# ════════════════════════════════════════════════════════════════════════
# Высокоуровневое описание lead'а (запрос на построение)
# ════════════════════════════════════════════════════════════════════════

@dataclass
class LeadGeometryRequest:
    """Параметры одного захода/выхода, нужные для его построения.
    
    Используется и viewer'ом и постом — чтобы передать в plan_lead_*() 
    одинаковые данные и получить идентичные результаты.
    """
    # True = lead-in (от внешней точки К старту контура)
    # False = lead-out (ОТ конца контура к внешней точке)
    is_entry: bool
    
    # Имя стороны прохода для логики выбора стороны 
    # (передаётся в pick_lead_side_for_pass)
    pass_side: str  # "INSIDE", "OUTSIDE" и т.п.
    
    # Геометрические параметры lead'а (АБСОЛЮТНЫЕ значения в мм)
    angle_deg: float
    line_length: float
    arc_radius: float
    style: str  # "line_arc" или "line"
    
    # Опциональное жёсткое указание стороны (left/right) — иначе авто-подбор.
    # Используется для 3D/2D corner-прохода где сторона выбирается по bbox.
    forced_side: Optional[str] = None


# ════════════════════════════════════════════════════════════════════════
# Утилиты геометрии (line-only апроксимация, пересечение, расстояние)
# ════════════════════════════════════════════════════════════════════════


def _segment_to_polyline(seg: Segment, max_chord: float = 0.5) -> List[Point]:
    """Преобразует сегмент в ломаную с шагом ≤ max_chord мм.
    Line → 2 точки. Arc → сэмплирование по углу."""
    if isinstance(seg, Line):
        return [seg.a, seg.b]
    if isinstance(seg, Arc):
        L = seg.length()
        n = max(3, int(math.ceil(L / max_chord)) + 1)
        return [seg.point_at(i / (n - 1)) for i in range(n)]
    return [seg.a, seg.b]


def polypath_to_lines(polypath: Polypath, max_chord: float = 0.5) -> List[Tuple[Point, Point]]:
    """Упрощённая line-only апроксимация контура: список пар (a, b) точек.
    
    Дуги аппроксимируются короткими линиями (chord <= max_chord мм). 
    Это быстрая геометрия для проверки коллизий: одна полная итерация по 
    точкам контура, никаких трансцендентных функций при пересечениях.
    
    Точность апроксимации лимитирована max_chord — но при tool_offset 
    запасе в 0.5мм это незначительно (хорда дуги отступает не более чем 
    на max_chord/8 от истинной дуги, ≈0.06мм при max_chord=0.5).
    """
    lines = []
    for seg in polypath.segments:
        pts = _segment_to_polyline(seg, max_chord)
        for i in range(len(pts) - 1):
            lines.append((pts[i], pts[i+1]))
    return lines


def lines_bbox(lines: List[Tuple[Point, Point]]) -> Tuple[float, float, float, float]:
    """bbox набора line-сегментов: (x0, y0, x1, y1)."""
    if not lines:
        return (0, 0, 0, 0)
    xs = [p[0] for L in lines for p in L]
    ys = [p[1] for L in lines for p in L]
    return (min(xs), min(ys), max(xs), max(ys))


def lead_bbox(lead_poly: Polypath, max_chord: float = 0.3
              ) -> Tuple[float, float, float, float]:
    """bbox сегментов lead'а после сэмплирования."""
    xs, ys = [], []
    for s in lead_poly.segments:
        pts = _segment_to_polyline(s, max_chord)
        for p in pts:
            xs.append(p[0]); ys.append(p[1])
    if not xs:
        return (0, 0, 0, 0)
    return (min(xs), min(ys), max(xs), max(ys))


def _bboxes_overlap(b1, b2, margin=0.0) -> bool:
    """True если два bbox'а пересекаются (с возможной маржой)."""
    return not (b1[2] + margin < b2[0] or b2[2] + margin < b1[0]
                or b1[3] + margin < b2[1] or b2[3] + margin < b1[1])


def _segments_intersect_2d(a1: Point, a2: Point, 
                            b1: Point, b2: Point) -> bool:
    """True если два отрезка [a1,a2] и [b1,b2] геометрически пересекаются.
    Касание концом — НЕ считаем пересечением."""
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1 = cross(b1, b2, a1)
    d2 = cross(b1, b2, a2)
    d3 = cross(a1, a2, b1)
    d4 = cross(a1, a2, b2)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return False


def _point_to_segment_distance(p: Point, a: Point, b: Point) -> float:
    """Точное минимальное расстояние от точки p до отрезка [a, b]."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx*dx + dy*dy
    if L2 < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
    t = max(0.0, min(1.0, t))
    fx, fy = a[0] + t*dx, a[1] + t*dy
    return math.hypot(p[0] - fx, p[1] - fy)


def lead_crosses_contours(lead_poly: Optional[Polypath],
                           contours_lines: List[Tuple[str, List[Tuple[Point, Point]]]],
                           own_geom_id: str,
                           entry_point: Point,
                           tool_offset: float,
                           safety_factor: float = 1.2,
                           skip_radius: Optional[float] = None,
                           contours_bboxes: Optional[List[Tuple[str, Tuple]]] = None
                           ) -> bool:
    """Проверка коллизии lead'а с упрощёнными line-only контурами.
    
    Двухуровневая фильтрация для скорости:
        1. BBOX: если lead bbox + safe_offset не пересекается с bbox контура — 
           пропускаем (мгновенно)
        2. Геометрия: для оставшихся контуров — line-vs-line проверка
    
    Коллизия = ОДНО из:
        a) Lead-сегмент пересекает контур-сегмент
        b) Точка lead'а ближе safe_offset = tool_offset * safety_factor 
           к контур-сегменту (точка зашла в расширенную зону эквидистанты)
    
    Args:
        lead_poly: проверяемый lead (line + arc)
        contours_lines: кеш line-only апроксимации контуров [(id, lines), ...]
        own_geom_id: ID своего контура
        entry_point: точка стыковки lead'а с контуром
        tool_offset: реальное смещение фрезы (мм)
        safety_factor: множитель к tool_offset для запаса (1.2 = +20%, чтобы 
            lead не подходил вплотную к зоне эквидистанты — это покрывает 
            погрешности апроксимации арков ломаной и даёт минимальный отступ
            тулпасов друг от друга)
        skip_radius: радиус игнорирования сегментов около entry_point 
            на СВОЕМ контуре. None → 2 * safe_offset.
        contours_bboxes: опц. bbox-индекс [(id, (x0,y0,x1,y1))] для prefilter.
    
    Returns:
        True если коллизия обнаружена
    """
    if not lead_poly or not lead_poly.segments or not contours_lines:
        return False
    safe_offset = tool_offset * safety_factor
    # skip_radius — радиус игнорирования сегментов собственного контура 
    # у точки стыковки lead'а с контуром. Поскольку distance-check для 
    # собственного контура отключён (см. ниже), нам нужно пропустить только 
    # сегменты ВПРИТЫК к точке стыковки (entry_point на контуре, дистанция 
    # ~0). Маленький порог 0.1мм покрывает округление floating-point и 
    # позволяет детектить реальные пересечения с любыми удалёнными участ.
    if skip_radius is None:
        # Автоподбор skip_radius под размер lead'а: сегменты собственного 
        # контура ближе чем lead_span*0.8 от точки стыковки не проверяем — 
        # это «зона тангенциального касания», arc lead'а по определению 
        # близок к контуру там.
        lead_span_tmp = max(1.0, 
            (lead_poly.segments[0].a[0] if lead_poly.segments else 0) -
            (entry_point[0]))
        # Упрощённо: используем 0.8 * длину линии lead'а (line + arc ≈ 
        # 1.5-2× длины line'а). Считаем через bbox после построения.
        skip_radius = 0.1  # временно — переопределится ниже
    
    # Lead → плоская ломаная
    lead_pts = []
    for s in lead_poly.segments:
        pts = _segment_to_polyline(s, max_chord=0.3)
        if lead_pts and pts and lead_pts[-1] == pts[0]:
            lead_pts.extend(pts[1:])
        else:
            lead_pts.extend(pts)
    if len(lead_pts) < 2:
        return False
    lead_lines = [(lead_pts[i], lead_pts[i+1]) for i in range(len(lead_pts)-1)]
    
    # Lead bbox — для пре-фильтра далёких контуров
    xs = [p[0] for p in lead_pts]
    ys = [p[1] for p in lead_pts]
    lead_bb = (min(xs), min(ys), max(xs), max(ys))
    
    # Адаптивный skip_radius для собственного контура: 0.8 × размер lead'а
    lead_span = max(lead_bb[2]-lead_bb[0], lead_bb[3]-lead_bb[1])
    own_skip_radius = max(skip_radius, lead_span * 0.8)
    
    # Подготавливаем bbox-индекс если не задан
    if contours_bboxes is None:
        contours_bboxes = []
        for cid, c_lines in contours_lines:
            if not c_lines:
                contours_bboxes.append((cid, (0,0,0,0)))
                continue
            xs2 = [p[0] for L in c_lines for p in L]
            ys2 = [p[1] for L in c_lines for p in L]
            contours_bboxes.append((cid, (min(xs2), min(ys2), max(xs2), max(ys2))))
    
    bbox_dict = dict(contours_bboxes)
    
    for cid, c_lines in contours_lines:
        # Пре-фильтр: пропускаем далёкие ножи (lead bbox + safe_offset 
        # не пересекается с контур bbox + safe_offset).
        c_bb = bbox_dict.get(cid)
        if c_bb is not None:
            if not _bboxes_overlap(lead_bb, c_bb, margin=safe_offset):
                continue
        
        is_own = (cid == own_geom_id)
        for (ca, cb) in c_lines:
            # На СВОЕМ контуре скипаем сегменты которые ПРОХОДЯТ через 
            # окрестность точки стыковки lead'а.
            if is_own:
                d_min = _point_to_segment_distance(entry_point, ca, cb)
                if d_min < own_skip_radius:
                    continue
            
            for (la, lb) in lead_lines:
                # 1. Явное пересечение — реальная коллизия, всегда проверяется
                if _segments_intersect_2d(la, lb, ca, cb):
                    return True
                # 2. Distance-check (внутри зоны эквидистанты соседа) — 
                # для ЧУЖИХ контуров + для СВОЕГО контура на далёких 
                # сегментах (>= own_skip_radius от точки стыковки).
                # На своём это ловит случай когда lead-line проходит близко 
                # к своей же offset-toolpath на противоположной стороне 
                # выпуклого изгиба (тангенс срезает материал).
                for p in (la, lb, ((la[0]+lb[0])/2, (la[1]+lb[1])/2)):
                    if _point_to_segment_distance(p, ca, cb) < safe_offset:
                        return True
    return False


# Совместимость со старым API:
def lead_collides_with_neighbors(lead_poly, neighbor_bboxes, tool_offset,
                                  sample_per_seg=8):
    """Устаревший bbox-чек. Использовать lead_crosses_contours."""
    if not lead_poly or not lead_poly.segments or not neighbor_bboxes:
        return False
    for seg in lead_poly.segments:
        for i in range(sample_per_seg):
            t = i / max(1, sample_per_seg - 1)
            p = seg.point_at(t)
            for bb in neighbor_bboxes:
                x0, y0, x1, y1 = bb
                if (x0 - tool_offset <= p[0] <= x1 + tool_offset 
                        and y0 - tool_offset <= p[1] <= y1 + tool_offset):
                    return True
    return False


def lead_self_collides(lead_poly, own_polypath, tool_offset, entry_point,
                       skip_radius=3.0, sample_per_seg=8):
    """Устаревший distance-чек. Использовать lead_crosses_contours."""
    if not lead_poly or not own_polypath:
        return False
    lines = polypath_to_lines(own_polypath)
    return lead_crosses_contours(
        lead_poly, [("self", lines)], "self", entry_point, tool_offset, skip_radius)


def _polypath_perimeter(polypath: Polypath) -> float:
    """Суммарная длина всех сегментов polypath'а (мм)."""
    total = 0.0
    for seg in polypath.segments:
        try:
            total += seg.length()
        except Exception:
            continue
    return total


def _lead_violation_score(lead: Optional[Polypath],
                            contours_lines, own_geom_id, entry_pt,
                            tool_offset, safety_factor,
                            contours_bboxes) -> float:
    """Оценка «насколько плохо» lead конфликтует с препятствиями.
    
    Возвращает положительное число (большее = хуже):
        0.0 — нет коллизии вообще (валидная позиция)
        > 0 — глубина проникновения в зону эквидистанты (мм). Чем больше — 
              тем глубже lead в чужой зоне или ближе к контуру.
    
    Используется для выбора «наименее плохого» кандидата когда ни один не 
    прошёл строгую/мягкую проверки. На этом кандидате потом варьируется 
    угол/длина.
    """
    if not lead or not lead.segments:
        return float('inf')
    
    safe_offset = tool_offset * safety_factor
    
    # Lead → ломаная (line+arc флэт)
    lead_pts = []
    for s in lead.segments:
        pts = _segment_to_polyline(s, max_chord=0.3)
        if lead_pts and pts and lead_pts[-1] == pts[0]:
            lead_pts.extend(pts[1:])
        else:
            lead_pts.extend(pts)
    if len(lead_pts) < 2:
        return float('inf')
    lead_lines = [(lead_pts[i], lead_pts[i+1]) for i in range(len(lead_pts)-1)]
    lead_bb_xs = [p[0] for p in lead_pts]
    lead_bb_ys = [p[1] for p in lead_pts]
    lead_bb = (min(lead_bb_xs), min(lead_bb_ys), max(lead_bb_xs), max(lead_bb_ys))
    
    bbox_dict = dict(contours_bboxes) if contours_bboxes else {}
    
    max_violation = 0.0
    for cid, c_lines in contours_lines:
        c_bb = bbox_dict.get(cid)
        if c_bb is not None and not _bboxes_overlap(lead_bb, c_bb, margin=safe_offset):
            continue
        is_own = (cid == own_geom_id)
        for (ca, cb) in c_lines:
            if is_own:
                d_min_to_entry = _point_to_segment_distance(entry_pt, ca, cb)
                if d_min_to_entry < 0.1:  # skip near join point
                    continue
            
            for (la, lb) in lead_lines:
                # Реальное пересечение = очень большой score
                if _segments_intersect_2d(la, lb, ca, cb):
                    return 1e6
                if is_own:
                    continue  # для своего без distance-check
                # Распределение зазора
                for p in (la, lb, ((la[0]+lb[0])/2, (la[1]+lb[1])/2)):
                    d = _point_to_segment_distance(p, ca, cb)
                    violation = safe_offset - d
                    if violation > max_violation:
                        max_violation = violation
    return max_violation


def auto_avoid_collision(start_polypath: Polypath,
                          build_lead_fn: Callable,
                          check_collision_fn: Callable,
                          side_name: str,
                          base_angle_deg: float = 45.0,
                          base_line_length: float = 1.0,
                          violation_scorer: Optional[Callable] = None,
                          try_angles: bool = True,
                          min_angle_deg: float = 30.0,
                          min_line_length: float = 0.6
                          ) -> Tuple[Polypath, object, bool]:
    """Подбирает позицию + угол + длину чтобы избежать коллизии lead'а.
    
    АЛГОРИТМ (по запросу пользователя):
        Этап 1 — СДВИГИ позиции старта (БЕЗ изменения угла/длины):
            а) исходная позиция
            б) +5мм по контуру (как было изначально)
            в) 30%, 60%, 90% периметра контура — равномерно по контуру
            
        Этап 2 — если ни один сдвиг не прошёл, найти позицию с НАИМЕНЬШИМ 
            пересечением, на ней начать варьировать:
                — угол: уменьшение до min_angle_deg (default 30°)
                — длину: укорачивание до min_line_length (default 0.6мм)
        
        Этап 3 — не помогло → возвращаем «наименее плохой» с collision=True 
            (красный в viewer).
    
    OVERLAP не трогаем — это пользовательская настройка.
    
    Args:
        start_polypath: контур со стартом в user-position (закрытый)
        build_lead_fn: callable(polypath, angle, shrink) → lead Polypath
        check_collision_fn: callable(lead, polypath) → bool (True = коллизия)
        side_name: "INSIDE" / "OUTSIDE"
        base_angle_deg: базовый угол захода
        base_line_length: базовая длина линии lead'а (для расчёта shrink-минимума)
        violation_scorer: callable(lead, polypath) → float — оценка глубины 
            коллизии. Если None — используется check_collision_fn (бинарно).
        try_angles: если False — только сдвиги
        min_angle_deg: минимальный угол при варьировании (default 30°)
        min_line_length: минимальная длина line (default 0.6мм)
    
    Returns:
        (best_polypath, best_lead, collision_flag)
    """
    from .path_offset import shift_start_along_contour
    
    def _try(pp, angle, shrink):
        try:
            lead = build_lead_fn(pp, angle, shrink)
            if lead and not check_collision_fn(lead, pp):
                return lead, True
            return lead, False
        except Exception:
            return None, False
    
    def _score(pp, lead):
        if violation_scorer is None:
            return 0.0 if not check_collision_fn(lead, pp) else 1.0
        return violation_scorer(lead, pp)
    
    # ── Этап 1а: исходная позиция, базовые параметры ──
    lead, ok = _try(start_polypath, base_angle_deg, 1.0)
    if ok:
        return start_polypath, lead, False
    
    candidates = [(0.0, start_polypath, lead, _score(start_polypath, lead) if lead else float('inf'))]
    
    # ── Этап 1б: малые фиксированные сдвиги с БАЗОВЫМИ параметрами ──
    for shift_mm in (5.0, -5.0, 10.0, -10.0):
        try:
            pp = shift_start_along_contour(start_polypath, shift_mm)
            lead_s, ok = _try(pp, base_angle_deg, 1.0)
            if ok:
                return pp, lead_s, False
            if lead_s:
                candidates.append((shift_mm, pp, lead_s, _score(pp, lead_s)))
        except Exception:
            continue
    
    # ── Этап 1в: сдвиги по периметру каждые 2% ──
    # Очень плотный сэмплинг: 49 позиций дают почти сплошное покрытие 
    # контура. Для крупного ножа (200мм): шаг = 4мм. Для мелкого (30мм): 
    # шаг = 0.6мм. Плотнее чем 2% нецелесообразно — соседние позиции 
    # почти совпадают.
    perimeter = _polypath_perimeter(start_polypath)
    if perimeter > 5.0:
        fractions = tuple(i/100 for i in range(2, 100, 2))  # 2%, 4%, ..., 98%
        for fraction in fractions:
            try:
                shift = perimeter * fraction
                pp = shift_start_along_contour(start_polypath, shift)
                lead_f, ok = _try(pp, base_angle_deg, 1.0)
                if ok:
                    return pp, lead_f, False
                if lead_f:
                    candidates.append((shift, pp, lead_f, _score(pp, lead_f)))
            except Exception:
                continue
    
    if not try_angles:
        candidates.sort(key=lambda c: c[3])
        _shift, pp, lead, _v = candidates[0]
        return pp, lead, True
    
    # ── Этап 2: на «наименее плохой» позиции варьировать УГОЛ и ДЛИНУ ──
    candidates.sort(key=lambda c: c[3])
    best_shift, best_pp, best_lead, best_v = candidates[0]
    
    # Варьируем УГОЛ (от base до min_angle_deg, шаг 5°)
    if base_angle_deg > min_angle_deg + 1:
        for delta in range(5, int(base_angle_deg - min_angle_deg) + 1, 5):
            ang = base_angle_deg - delta
            if ang < min_angle_deg:
                break
            cand_lead, ok = _try(best_pp, ang, 1.0)
            if ok:
                return best_pp, cand_lead, False
    
    # Варьируем ДЛИНУ через shrink (line_length × shrink ≥ min_line_length)
    if base_line_length > min_line_length:
        min_shrink = max(0.1, min_line_length / base_line_length)
        shrink = 0.9
        while shrink >= min_shrink:
            cand_lead, ok = _try(best_pp, base_angle_deg, shrink)
            if ok:
                return best_pp, cand_lead, False
            shrink -= 0.1
    
    # ── Этап 3: ничего не помогло → возвращаем «наименее плохой» ──
    return best_pp, best_lead, True




# ════════════════════════════════════════════════════════════════════════
# Высокоуровневые функции: единая точка построения lead'а
# для viewer и post-processor
# ════════════════════════════════════════════════════════════════════════

def build_lead_for_polypath(polypath: Polypath, 
                             request: LeadGeometryRequest,
                             angle_override: Optional[float] = None,
                             shrink: float = 1.0
                             ) -> Optional[Polypath]:
    """Построение lead'а на старте (is_entry=True) или конце (is_entry=False)
    polypath'а согласно request'у.
    
    Возвращает Polypath из 2-х сегментов:
        Для lead-in:  [line, arc]   — линия идёт от воздуха к arc, arc к контуру
        Для lead-out: [arc, line]   — arc от контура, линия в воздух
    
    Args:
        polypath: контур с заданной точкой старта/конца
        request: параметры lead'а (angle, length, style и т.д.)
        angle_override: если задан — заменяет request.angle_deg (для авто-подбора)
        shrink: множитель длины и радиуса (1.0 = базовый, <1 = укорочен)
    
    Returns:
        Polypath или None при ошибке построения
    """
    from .lead_inout import build_lead_in, build_lead_out, pick_lead_side_for_pass
    
    if not polypath or not polypath.segments:
        return None
    
    ang = angle_override if angle_override is not None else request.angle_deg
    line_len = request.line_length * shrink
    arc_r = request.arc_radius * shrink
    
    if request.is_entry:
        sp = polypath.segments[0].a
        tan = polypath.segments[0].tangent_at_start()
        if request.forced_side is not None:
            side = request.forced_side
        else:
            side = pick_lead_side_for_pass(
                sp, tan, polypath, request.pass_side,
                line_len, arc_r, ang, is_exit=False)
        try:
            g = build_lead_in(
                start_point=sp, tangent=tan, side=side,
                line_length=line_len, arc_radius=arc_r,
                approach_angle_deg=ang, style=request.style)
            return Polypath(segments=[g.line, g.arc], closed=False)
        except Exception:
            return None
    else:
        # Lead-out
        ep = polypath.segments[-1].b
        tan = polypath.segments[-1].tangent_at_end()
        if request.forced_side is not None:
            side = request.forced_side
        else:
            side = pick_lead_side_for_pass(
                ep, tan, polypath, request.pass_side,
                line_len, arc_r, ang, is_exit=True)
        try:
            g = build_lead_out(
                end_point=ep, tangent=tan, side=side,
                line_length=line_len, arc_radius=arc_r,
                retract_angle_deg=ang, style=request.style)
            return Polypath(segments=[g.arc, g.line], closed=False)
        except Exception:
            return None


def plan_lead_in(polypath: Polypath,
                  request: LeadGeometryRequest,
                  contours_lines: List[Tuple[str, List]],
                  contours_bboxes: List[Tuple[str, Tuple]],
                  own_geom_id: str,
                  tool_offset: float,
                  auto_avoid: bool = True,
                  safety_factor: float = 3.0,
                  fallback_safety_factor: float = 1.2,
                  exit_request: Optional[LeadGeometryRequest] = None,
                  overlap: float = 0.0,
                  ) -> Tuple[Polypath, Optional[Polypath], bool, object]:
    """ПЛАНИРОВАНИЕ lead-in с ТРЁХФАЗНЫМ подбором безопасности.
    
    Если передан exit_request, то на каждом кандидате-сдвиге проверяются 
    ОБЕ коллизии (lead-in И lead-out). Валидная позиция = обе без коллизий. 
    Это нужно потому что lead-in и lead-out на закрытом контуре с overlap=0 
    крепятся в ОДНОЙ точке контура — сдвиг старта двигает и точку lead-out. 
    Без этой проверки алгоритм мог остановиться на позиции где lead-in OK 
    но lead-out коллизирует.
    
    Args:
        exit_request: параметры lead-out'а (опционально). Если задан, при 
            поиске сдвига валидность = "нет коллизии для обоих".
        overlap: значение overlap'а — на закрытом контуре lead-out позиция 
            = start + overlap вдоль контура.
    """
    def _make_check(sf):
        def _check(lead, pp):
            if not lead or not lead.segments:
                return False
            entry_pt = lead.segments[-1].b
            return lead_crosses_contours(
                lead, contours_lines, own_geom_id, entry_pt,
                tool_offset=tool_offset, safety_factor=sf,
                contours_bboxes=contours_bboxes)
        return _check
    
    def _make_scorer(sf):
        def _scorer(lead, pp):
            if not lead or not lead.segments:
                return float('inf')
            entry_pt = lead.segments[-1].b
            return _lead_violation_score(
                lead, contours_lines, own_geom_id, entry_pt,
                tool_offset, sf, contours_bboxes)
        return _scorer
    
    def _build(pp, angle, shrink):
        return build_lead_for_polypath(pp, request, angle, shrink)
    
    # Комбинированная проверка: lead-in + (опционально) lead-out.
    # При exit_request на каждом сдвиге строится ещё и lead-out (из конца 
    # polypath'а + overlap) и проверяется его коллизия. Обе должны быть 
    # False для «валидной» позиции.
    def _make_combined_check(sf):
        base_check_in = _make_check(sf)
        if exit_request is None:
            return base_check_in
        
        def _combined(lead_in, pp):
            if base_check_in(lead_in, pp):
                return True  # lead-in коллизирует
            # Для замкнутого контура: end = start; для закрытого + overlap 
            # end смещён на overlap вдоль контура.
            pp_for_out = pp
            if overlap > 1e-9 and pp.closed:
                from .path_offset import apply_overlap
                try:
                    pp_for_out = apply_overlap(pp, overlap)
                except Exception:
                    pass
            lead_out = build_lead_for_polypath(pp_for_out, exit_request)
            if lead_out is None or not lead_out.segments:
                return False  # если не построилось — не наша забота
            join_pt = lead_out.segments[0].a
            return lead_crosses_contours(
                lead_out, contours_lines, own_geom_id, join_pt,
                tool_offset=tool_offset, safety_factor=sf,
                contours_bboxes=contours_bboxes)
        return _combined
    
    def _make_combined_scorer(sf):
        base_scorer = _make_scorer(sf)
        if exit_request is None:
            return base_scorer
        
        def _combined(lead_in, pp):
            score_in = base_scorer(lead_in, pp) if lead_in else float('inf')
            # Оценка lead-out на этой же позиции
            pp_for_out = pp
            if overlap > 1e-9 and pp.closed:
                from .path_offset import apply_overlap
                try:
                    pp_for_out = apply_overlap(pp, overlap)
                except Exception:
                    pass
            lead_out = build_lead_for_polypath(pp_for_out, exit_request)
            if lead_out is None or not lead_out.segments:
                return score_in
            join_pt = lead_out.segments[0].a
            score_out = _lead_violation_score(
                lead_out, contours_lines, own_geom_id, join_pt,
                tool_offset, sf, contours_bboxes)
            return max(score_in, score_out)  # худшая из двух
        return _combined
    
    # auto_avoid=False — просто строим как есть
    if not auto_avoid:
        lead = _build(polypath, request.angle_deg, 1.0)
        coll = _make_combined_check(safety_factor)(lead, polypath) if lead else False
        geom = _polypath_to_lead_geometry(lead, request.is_entry) if lead else None
        return polypath, lead, coll, geom
    
    # ФАЗЫ 1-3 ниже сами разберутся: если позиция юзера уже чиста по строгой 
    # мере — Phase 1 вернёт её первой. Если нет — попробуют сдвиги. Если 
    # и Phase 1 не смог — Phase 2 с мягкой мерой. Если ничего — Phase 3 (RED).
    
    # Реальное пересечение есть — начинаем автоподбор через фазы
    # ФАЗА 1: строгая
    final_pp, lead, coll = auto_avoid_collision(
        polypath, _build, _make_combined_check(safety_factor), request.pass_side,
        base_angle_deg=request.angle_deg,
        base_line_length=request.line_length,
        violation_scorer=_make_combined_scorer(safety_factor),
        try_angles=True)
    if not coll:
        geom = _polypath_to_lead_geometry(lead, request.is_entry) if lead else None
        return final_pp, lead, coll, geom
    
    # ФАЗА 2: мягкая
    if fallback_safety_factor < safety_factor:
        final_pp2, lead2, coll2 = auto_avoid_collision(
            polypath, _build, _make_combined_check(fallback_safety_factor),
            request.pass_side,
            base_angle_deg=request.angle_deg,
            base_line_length=request.line_length,
            violation_scorer=_make_combined_scorer(fallback_safety_factor),
            try_angles=True)
        if not coll2:
            geom = _polypath_to_lead_geometry(lead2, request.is_entry)
            return final_pp2, lead2, coll2, geom
        final_pp, lead, coll = final_pp2, lead2, coll2
    
    # ФАЗА 3: intersection-only
    final_pp3, lead3, coll3 = auto_avoid_collision(
        polypath, _build, _make_combined_check(0.01), request.pass_side,
        base_angle_deg=request.angle_deg,
        base_line_length=request.line_length,
        violation_scorer=_make_combined_scorer(0.01),
        try_angles=True)
    if not coll3:
        geom = _polypath_to_lead_geometry(lead3, request.is_entry)
        return final_pp3, lead3, coll3, geom
    
    # ФАЗА 4: противоположная сторона lead-in
    if request.forced_side is None:
        from .lead_inout import pick_lead_side_for_pass
        try:
            sp = polypath.segments[0].a
            tan = polypath.segments[0].tangent_at_start()
            auto_side = pick_lead_side_for_pass(
                sp, tan, polypath, request.pass_side,
                request.line_length, request.arc_radius, 
                request.angle_deg, is_exit=False)
            opposite_side = "left" if auto_side == "right" else "right"
        except Exception:
            opposite_side = "left"
        
        request_flipped = LeadGeometryRequest(
            is_entry=request.is_entry, pass_side=request.pass_side,
            angle_deg=request.angle_deg, line_length=request.line_length,
            arc_radius=request.arc_radius, style=request.style,
            forced_side=opposite_side)
        
        def _build_flipped(pp, angle, shrink):
            return build_lead_for_polypath(pp, request_flipped, angle, shrink)
        
        for sf_try in (safety_factor, fallback_safety_factor, 0.01):
            if sf_try < 0.005: continue
            final_pp4, lead4, coll4 = auto_avoid_collision(
                polypath, _build_flipped, _make_combined_check(sf_try),
                request.pass_side,
                base_angle_deg=request.angle_deg,
                base_line_length=request.line_length,
                violation_scorer=_make_combined_scorer(sf_try),
                try_angles=True)
            if not coll4:
                geom = _polypath_to_lead_geometry(lead4, request.is_entry)
                return final_pp4, lead4, coll4, geom
    
    geom = _polypath_to_lead_geometry(lead, request.is_entry) if lead else None
    return final_pp, lead, coll, geom


def plan_lead_out(polypath: Polypath,
                   request: LeadGeometryRequest,
                   contours_lines: List[Tuple[str, List]],
                   contours_bboxes: List[Tuple[str, Tuple]],
                   own_geom_id: str,
                   tool_offset: float,
                   safety_factor: float = 1.2,
                   fallback_safety_factor: float = 1.0
                   ) -> Tuple[Optional[Polypath], bool, object]:
    """ПЛАНИРОВАНИЕ lead-out: построение + проверка коллизии.
    
    БЕЗ автоподбора — позиция жёстко определена концом polypath'а.
    Двухпроходная проверка safety_factor: строгая → мягкая.
    Если хотя бы мягкая прошла — коллизии нет.
    
    Returns:
        (lead_out_poly, collision_flag, lead_geometry)
    """
    lead = build_lead_for_polypath(polypath, request)
    if not lead or not lead.segments:
        return None, False, None
    
    join_pt = lead.segments[0].a
    
    # Строгая проверка
    coll = lead_crosses_contours(
        lead, contours_lines, own_geom_id, join_pt,
        tool_offset=tool_offset, safety_factor=safety_factor,
        contours_bboxes=contours_bboxes)
    
    # Мягкая — для определения «реально ли есть пересечение» (а не просто
    # тесный зазор в пределах safety запаса)
    if coll and fallback_safety_factor < safety_factor:
        coll = lead_crosses_contours(
            lead, contours_lines, own_geom_id, join_pt,
            tool_offset=tool_offset, safety_factor=fallback_safety_factor,
            contours_bboxes=contours_bboxes)
    
    geom = _polypath_to_lead_geometry(lead, request.is_entry)
    return lead, coll, geom


def _polypath_to_lead_geometry(lead_poly: Polypath, is_entry: bool):
    """Конвертирует lead-Polypath в LeadGeometry-совместимый объект 
    с атрибутами .line и .arc (как у camsys.geometry.lead_inout.LeadGeometry).
    
    Для lead-in:  Polypath[line, arc] → geom.line=segments[0], geom.arc=segments[1]
    Для lead-out: Polypath[arc, line] → geom.arc=segments[0], geom.line=segments[1]
    
    Это даёт пост-процессору доступ к .line и .arc для генерации NC-команд
    без необходимости знать про порядок сегментов в Polypath.
    """
    from .lead_inout import LeadGeometry
    if not lead_poly or not lead_poly.segments:
        return None
    segs = lead_poly.segments
    if is_entry:
        line = segs[0] if len(segs) > 0 else None
        arc = segs[1] if len(segs) > 1 else None
    else:
        arc = segs[0] if len(segs) > 0 else None
        line = segs[1] if len(segs) > 1 else None
    return LeadGeometry(line=line, arc=arc)


def build_contours_cache(geometries: List, 
                          max_chord: float = 0.5
                          ) -> Tuple[List[Tuple[str, List]], List[Tuple[str, Tuple]]]:
    """Кеш для быстрой проверки коллизий: line-only апроксимация + bbox 
    каждого замкнутого ножа.
    
    Должен строиться один раз на пайплайн рендеринга/экспорта и переиспользоваться 
    для всех toolpath'ов в данном проекте.
    
    Args:
        geometries: список Geometry-объектов (например knife_layer.geometries)
        max_chord: шаг апроксимации дуг ломаной (мм)
    
    Returns:
        (contours_lines, contours_bboxes) — для передачи в plan_lead_*
    """
    contours_lines = []
    contours_bboxes = []
    for g in geometries:
        if not getattr(g, 'is_closed', False): continue
        try:
            lines = polypath_to_lines(g.polypath, max_chord=max_chord)
            contours_lines.append((g.id, lines))
            # bbox через те же line-точки
            if lines:
                xs = [p[0] for L in lines for p in L]
                ys = [p[1] for L in lines for p in L]
                contours_bboxes.append((g.id, (min(xs), min(ys), max(xs), max(ys))))
        except Exception:
            continue
    return contours_lines, contours_bboxes
