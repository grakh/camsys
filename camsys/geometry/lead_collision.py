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


# Радиус вокруг точки стыковки, в котором пересечение лида с контуром
# считается касанием, а не коллизией: там они сходятся по построению.
JOIN_TOUCH_MM = 0.08


def lead_crosses_contours(lead_poly: Optional[Polypath],
                           contours_lines: List[Tuple[str, List[Tuple[Point, Point]]]],
                           own_geom_id: str,
                           entry_point: Point,
                           tool_offset: float,
                           safety_factor: float = 1.2,
                           skip_radius: Optional[float] = None,
                           contours_bboxes: Optional[List[Tuple[str, Tuple]]] = None,
                           path_is_cutter: bool = False
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
        path_is_cutter: переданный лид УЖЕ построен на эквидистанте, то
            есть сам является путём фрезы. Так его строит вьювер: он
            передаёт сюда `polypath_offset`, а не осевую. Тогда смещать
            второй раз нельзя — получится двойное смещение (v1.7.56).
            Пост передаёт ОСЕВУЮ и оставляет флаг выключенным.
    
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
    # ── ПРОВЕРЯЕМ ПУТЬ ФРЕЗЫ, А НЕ ОСЕВУЮ (v1.7.55) ──
    # В `.anc` пишется ОСЕВАЯ плюс G41/G42, а металл снимает фреза,
    # центр которой стойка отводит на полуэквидистанту вправо по ходу.
    # Проверка мерила осевую и требовала от неё tool_offset*safety —
    # но центр фрезы при этом оказывается на tool_offset БЛИЖЕ к
    # соседнему лезвию. На заказе 126387 с пяткой 1.2 осевая проходила
    # в 1.139 мм (проверка требовала 1.020 и была довольна), а центр
    # фрезы — в 0.551 при нужных 0.850: врезание в соседнее лезвие на
    # 299 мкм, и так у 77 заходов из 198. Флаг коллизии не поднялся ни
    # разу, поэтому автоподбор ничего и не пробовал.
    #
    # Тот же класс ошибки был в лидах доработки и починен в v1.7.45.
    lead_lines = [(lead_pts[i], lead_pts[i + 1])
                  for i in range(len(lead_pts) - 1)]
    if path_is_cutter:
        cut_lines = lead_lines
    else:
        cut_lines = []
        for _i in range(len(lead_pts) - 1):
            _x0, _y0 = lead_pts[_i]
            _x1, _y1 = lead_pts[_i + 1]
            _dx, _dy = _x1 - _x0, _y1 - _y0
            _L = math.hypot(_dx, _dy)
            if _L < 1e-12:
                continue
            _nx, _ny = _dy / _L * tool_offset, -_dx / _L * tool_offset
            cut_lines.append(((_x0 + _nx, _y0 + _ny),
                              (_x1 + _nx, _y1 + _ny)))
    if not lead_lines or not cut_lines:
        return False
    # Центр фрезы не должен подходить к ЧУЖОМУ лезвию ближе своего же
    # радиуса; запас 50 мкм — как у лидов доработки.
    #
    # К СВОЕМУ контуру это требование неприменимо: лид к нему и
    # пристыкован, фреза идёт ровно по его эквидистанте — она его и
    # режет. Поэтому свой контур проверяется по-старому, по осевой.
    cut_clear = tool_offset + 0.05
    
    # Lead bbox — для пре-фильтра далёких контуров
    xs = [p[0] for p in lead_pts]
    ys = [p[1] for p in lead_pts]
    # Габарит берём по ОСЕВОЙ и расширяем на смещение фрезы: путь центра
    # уходит от осевой на tool_offset в сторону.
    _bb_pad = 0.0 if path_is_cutter else tool_offset
    lead_bb = (min(xs) - _bb_pad, min(ys) - _bb_pad,
               max(xs) + _bb_pad, max(ys) + _bb_pad)
    
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
        # ── ПРОПУСК ПО ДЛИНЕ ВДОЛЬ КОНТУРА (v1.7.16) ──
        # На своём контуре нельзя пропускать сегменты по ЕВКЛИДОВУ
        # расстоянию от точки стыковки: на мелком элементе
        # противоположная сторона перешейка оказывается ближе
        # own_skip_radius (≈1.2 мм, а после увеличения лидов вдвое —
        # ≈2.4 мм), и реальное пересечение молча пропускалось. Лезвие
        # при этом режет само себя.
        #
        # Пропускать надо только то, что рядом ПО КОНТУРУ — там arc
        # лида тангенциально прижат к нему по построению. Всё, что
        # далеко по контуру, но близко геометрически, проверяем.
        _skip_idx = set()
        if is_own and c_lines:
            _cum = [0.0]
            for (ca_, cb_) in c_lines:
                _cum.append(_cum[-1] + math.hypot(cb_[0] - ca_[0],
                                                  cb_[1] - ca_[1]))
            _total = _cum[-1]
            # позиция точки стыковки вдоль контура
            _best_i, _best_d = 0, float('inf')
            for _i, (ca_, cb_) in enumerate(c_lines):
                _d = _point_to_segment_distance(entry_point, ca_, cb_)
                if _d < _best_d:
                    _best_d, _best_i = _d, _i
            _s0 = (_cum[_best_i] + _cum[_best_i + 1]) * 0.5
            for _i in range(len(c_lines)):
                _sm = (_cum[_i] + _cum[_i + 1]) * 0.5
                _ds = abs(_sm - _s0)
                if _total > 0:
                    _ds = min(_ds, _total - _ds)   # замкнутый контур
                if _ds < own_skip_radius:
                    _skip_idx.add(_i)

        for _ci, (ca, cb) in enumerate(c_lines):
            # В «зоне тангенциального касания» пропускаем только
            # distance-чек: дуга лида там прижата к контуру по построению.
            # Явное ПЕРЕСЕЧЕНИЕ остаётся ошибкой и в этой зоне (v1.7.18) —
            # на 124819 выход резал свою осевую в 0.45 мм по контуру от
            # стыковки, при пороге пропуска 1.79 мм.
            _near_join = is_own and _ci in _skip_idx
            
            for (la, lb) in (lead_lines if is_own else cut_lines):
                # 1. Явное пересечение — реальная коллизия, всегда проверяется
                if _segments_intersect_2d(la, lb, ca, cb):
                    if _near_join:
                        # У самой точки стыковки лид и контур сходятся —
                        # это касание, а не пересечение. Игнорируем только
                        # его, в пределах JOIN_TOUCH_MM.
                        _mx = (la[0] + lb[0]) * 0.5
                        _my = (la[1] + lb[1]) * 0.5
                        if math.hypot(_mx - entry_point[0],
                                      _my - entry_point[1]) < JOIN_TOUCH_MM:
                            continue
                    return True
                # 2. Distance-check (внутри зоны эквидистанты соседа) — 
                # для ЧУЖИХ контуров + для СВОЕГО контура на далёких 
                # сегментах (>= own_skip_radius от точки стыковки).
                # На своём это ловит случай когда lead-line проходит близко 
                # к своей же offset-toolpath на противоположной стороне 
                # выпуклого изгиба (тангенс срезает материал).
                if _near_join:
                    continue
                _lim = safe_offset if is_own else cut_clear
                for p in (la, lb, ((la[0]+lb[0])/2, (la[1]+lb[1])/2)):
                    if _point_to_segment_distance(p, ca, cb) < _lim:
                        return True
    return False


# Совместимость со старым API:
def leads_cross_each_other(lead_in: Optional[Polypath],
                           lead_out: Optional[Polypath],
                           skip_mm: float = 0.25) -> bool:
    """Пересекаются ли заход и выход между собой (v1.7.17).

    Заход и выход строились независимо и сверялись только с контурами
    ножей — друг с другом их не сравнивал никто. На мелких элементах они
    расходятся от почти одной точки и успевают пересечься.

    У замкнутого контура без перекрытия обе стыковки лежат в одной точке,
    и рядом с ней дуги неизбежно идут вплотную. Поэтому пересечения
    ближе skip_mm к любой из точек стыковки не считаем.
    """
    if not lead_in or not lead_out:
        return False
    if not lead_in.segments or not lead_out.segments:
        return False
    a_join = lead_in.segments[-1].b     # заход ВХОДИТ в контур концом
    b_join = lead_out.segments[0].a     # выход НАЧИНАЕТСЯ на контуре
    a_lines = polypath_to_lines(lead_in, max_chord=0.2)
    b_lines = polypath_to_lines(lead_out, max_chord=0.2)
    for (a1, a2) in a_lines:
        for (b1, b2) in b_lines:
            if not _segments_intersect_2d(a1, a2, b1, b2):
                continue
            # Пересечение рядом со стыковкой — это касание у контура
            mid = ((a1[0] + a2[0]) * 0.5, (a1[1] + a2[1]) * 0.5)
            if min(math.hypot(mid[0] - a_join[0], mid[1] - a_join[1]),
                   math.hypot(mid[0] - b_join[0], mid[1] - b_join[1])) < skip_mm:
                continue
            return True
    return False


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
        # Ранее пробовали линейно 2%, 4%, ..., 98% и возвращали ПЕРВЫЙ
        # непадающий. Проблема: если малые прямые сдвиги (+2..+20%) 
        # конфликтуют с соседним ножом, а обратный сдвиг −2% (= +98%) 
        # был бы чистым, алгоритм уходил вперёд и находил «первое 
        # непадающее» — визуально «заход улетает на противоположную 
        # сторону контура».
        # Фикс: сортируем fractions по min(f, 1-f) → после 0.02 сразу 
        # 0.98 (те же 2%, но в другую сторону), после 0.04 → 0.96 и т.д. 
        # Первый успех = МИНИМАЛЬНОЕ отклонение в любую сторону.
        fractions = tuple(i/100 for i in range(2, 100, 2))  # 2%, 4%, ..., 98%
        # Ключ: (|нормализованный сдвиг|, сам fraction) — тьюпл-ключ
        # исключает флоат-неоднозначности в min(f, 1-f) для пар вида
        # (0.08, 0.92) из-за представления в двоичной плавающей точке.
        # Вторичный ключ (fraction asc) гарантирует детерминированный порядок
        # в паре: сначала +f, потом −f. Целочисленный расчёт — надёжнее.
        def _shift_key(f):
            pct = round(f * 100)  # 2, 4, ..., 98 — целые
            norm_pct = min(pct, 100 - pct)  # 2, 2, 4, 4, ...
            return (norm_pct, pct)
        fractions = sorted(fractions, key=_shift_key)
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
                  safety_factor: float = 1.5,
                  fallback_safety_factor: float = 1.2,
                  exit_request: Optional[LeadGeometryRequest] = None,
                  overlap: float = 0.0,
                  path_is_cutter: bool = False,
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
                contours_bboxes=contours_bboxes, path_is_cutter=path_is_cutter)
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
            if lead_crosses_contours(
                    lead_out, contours_lines, own_geom_id, join_pt,
                    tool_offset=tool_offset, safety_factor=sf,
                    contours_bboxes=contours_bboxes, path_is_cutter=path_is_cutter):
                return True
            # Заход и выход не должны пересекаться между собой (v1.7.17)
            return leads_cross_each_other(lead_in, lead_out)
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
            # Взаимное пересечение лидов — отдельный штраф (v1.7.17):
            # без него позиция с крестом считалась бы идеальной.
            cross_pen = tool_offset if leads_cross_each_other(
                lead_in, lead_out) else 0.0
            return max(score_in, score_out, cross_pen)
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
                   fallback_safety_factor: float = 1.0,
                   path_is_cutter: bool = False
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
        contours_bboxes=contours_bboxes, path_is_cutter=path_is_cutter)
    
    # Мягкая — для определения «реально ли есть пересечение» (а не просто
    # тесный зазор в пределах safety запаса)
    if coll and fallback_safety_factor < safety_factor:
        coll = lead_crosses_contours(
            lead, contours_lines, own_geom_id, join_pt,
            tool_offset=tool_offset, safety_factor=fallback_safety_factor,
            contours_bboxes=contours_bboxes, path_is_cutter=path_is_cutter)
    
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


# ════════════════════════════════════════════════════════════════════════
# ЕДИНОЕ ПЛАНИРОВАНИЕ ЛИДОВ ОДНОГО ПРОХОДА (v1.7.58)
# ════════════════════════════════════════════════════════════════════════
#
# До v1.7.58 этот блок существовал ДВАЖДЫ: в посте и во вьювере. Они
# расходились молча и повторно — v1.7.11 (масштаб лида), v1.7.29
# (эквидистанта тонкой фрезы), v1.7.39 (множители и запас угла), v1.7.43
# и v1.7.45 (какая кривая идёт в замер), v1.7.55 и v1.7.56 (что считает
# проверка коллизий). Каждый раз это обнаруживалось так: в программе
# одно, на картинке другое.
#
# ГЛАВНОЕ РАСХОЖДЕНИЕ, из-за которого блок и сведён: лид строится на
# ОСЕВОЙ, потому что в `.anc` уходит осевая, а путь фрезы из неё делает
# стойка (G41/G42). Вьювер же строил лид на ЭКВИДИСТАНТЕ — это ДРУГАЯ
# кривая, и её смещение вправо не совпадает с тем, что едет станок. На
# заказе 126387 с пяткой 1.2 так рисовалось 126 лидов из 396, худший
# подходил к лезвию на 0.302 мм при нужных 0.850.
#
# Теперь обе стороны зовут `plan_pass_leads` и обе дают лид НА ОСЕВОЙ.
# Вьювер рисует его смещение вправо по ходу — то есть ровно ту кривую,
# которую построит стойка.

# Минимальный радиус дуги лида ПОСЛЕ компенсации, мм (v1.7.59).
#
# В `.anc` уходит осевая, дугу лида стойка по G42 пересчитывает в
# концентрическую радиусом R − d (лид всегда загибает В СТОРОНУ
# инструмента). При R <= d центр инструмента должен вывернуться через
# центр дуги — такой дуги не существует, и стойке её не выполнить.
#
# Замерено на корнер-программе 124173 (T3, d = 0.55): из 27 заходов
# радиус после компенсации был 0.0 или −0.22 у ВСЕХ 27, из 27 выходов —
# у трёх. Причина: радиус дуги считался как `множитель × эквидистанта`,
# а у доработки множитель 0.5 — то есть ровно d, единственное значение,
# на котором компенсация схлопывается.
#
# Порог 0.1 мм выбран так, чтобы программы, которые стойка и раньше
# выполняла, не изменились ни на знак: у основного прохода при
# множителе 1.0 радиус после компенсации 0.65 мм, при 0.6 — 0.13 мм.
LEAD_MIN_COMP_RADIUS = 0.1


@dataclass
class PassLeadParams:
    """Всё, что нужно для планирования захода и выхода одного прохода.

    Значения уже разрешены вызывающей стороной: пост читает их из
    `tp.entry`/`tp.exit` (их заполняет `_apply_lead_params` из
    `CuttingMacroParams`), вьювер — из полей текущей панели UI. Дальше
    геометрия считается ОДНИМ кодом.
    """
    pass_side: str              # tp.side.name — INSIDE/OUTSIDE/LEFT/RIGHT
    is_corner_rework: bool      # проход доработки угла/кармана
    tool_offset: float          # полуэквидистанта фрезы ЭТОГО прохода
    lead_scale: float           # эквидистанта фрезы (масштаб множителей)
    length_mult: float = 1.0    # множитель длины (у доработки 0.5)
    radius_mult: float = 1.0    # множитель радиуса
    extra_mm: float = 0.0       # запас прямой части (доработка)

    entry_enabled: bool = False
    entry_style: str = 'line_arc'
    entry_angle: float = 45.0
    entry_length: float = 1.0   # множитель «x_tool_rad»
    entry_radius: float = 1.0

    exit_enabled: bool = False
    exit_style: str = 'line_arc'
    exit_angle: float = 45.0
    exit_length: float = 1.0
    exit_radius: float = 1.0

    overlap: float = 0.0        # перебег, мм (применяется ПОСЛЕ подбора)
    no_avoid: bool = False      # ручная позиция — автоподбор не двигает
    entry_hint: Optional[Point] = None   # готовая точка захода


@dataclass
class PassLeadResult:
    """Результат планирования: ОСЕВАЯ и лиды НА ОСЕВОЙ."""
    axis: Polypath                      # контур после re-root и перебега
    # Контур после re-root, но ДО перебега — ещё замкнутый. Нужен
    # вьюверу: эквидистанту считают по ЗАМКНУТОМУ контуру (там намотка
    # определена однозначно), а перебег разомкнул бы его и нормаль могла
    # уйти в другую сторону.
    axis_rerooted: Optional[Polypath] = None
    lead_in: Optional[Polypath] = None
    lead_out: Optional[Polypath] = None
    lead_in_geom: object = None         # .line/.arc для эмиссии NC
    lead_out_geom: object = None
    lead_in_collision: bool = False
    lead_out_collision: bool = False
    lead_side: str = 'right'
    lead_out_side: str = 'right'
    clr_in: float = 0.0                 # зазор лида доработки, мм (<0 — тесно)
    clr_out: float = 0.0


def _lead_line_length(user_factor: float, angle_deg: float,
                      p: PassLeadParams) -> float:
    """Длина прямой части лида по формуле альфы.

    lateral_clearance = user_factor × эквидистанта, затем делится на
    sin(угла захода): чем положе заход, тем длиннее прямая при том же
    боковом зазоре. Множители «x_tool_rad» альфа считает ОТ
    ЭКВИДИСТАНТЫ, а не от её половины (замерено сличением с эталоном на
    124536, v1.7.11).
    """
    ang_rad = math.radians(max(5.0, min(175.0, angle_deg)))
    sin_a = math.sin(ang_rad)
    if sin_a < 0.05:
        sin_a = 0.05            # защита от углов около 0 и 180
    return (user_factor * p.length_mult * p.lead_scale + p.extra_mm) / sin_a


def plan_pass_leads(axis: Polypath,
                    own_geom,
                    p: PassLeadParams,
                    contours_lines: List[Tuple[str, List]],
                    contours_bboxes: List[Tuple[str, Tuple]],
                    neighbours=(),
                    log: Optional[Callable[[str], None]] = None,
                    ) -> PassLeadResult:
    """Планирует заход и выход прохода НА ОСЕВОЙ. Общая точка для поста
    и вьювера.

    Args:
        axis: программная осевая прохода — уже нормализованная по
            направлению, со сдвинутым стартом, с поднятыми радиусами и
            слитыми коллинеарными линиями. Именно она уходит в `.anc`.
        own_geom: Geometry обрабатываемого ножа (нужен `.id` и
            `.polypath` как эталон «внутри/снаружи»).
        p: разрешённые параметры лидов.
        contours_lines, contours_bboxes: кеш `build_contours_cache`.
        neighbours: полипасы СОСЕДНИХ ножей — для замера лида доработки.
        log: куда писать «места нет, нужна ручная правка».

    Returns:
        PassLeadResult. Лиды — в координатах ОСЕВОЙ. Путь фрезы = их
        смещение на `tool_offset` вправо по ходу (так делает стойка по
        G42); вьювер для рисования зовёт `offset_right_of_travel`.
    """
    from .lead_inout import fit_rework_lead, pick_lead_side_for_pass

    res = PassLeadResult(axis=axis)
    if not axis or not axis.segments:
        return res

    want_in = bool(p.entry_enabled)
    want_out = bool(p.exit_enabled)

    start_pt = axis.segments[0].a
    start_tan = axis.segments[0].tangent_at_start()
    end_pt = axis.segments[-1].b
    end_tan = axis.segments[-1].tangent_at_end()

    # Эталон «внутри/снаружи»: у доработки в axis лежит ОТКРЫТЫЙ
    # фрагмент, point-in-polygon на нём бессмыслен — берём родительский
    # замкнутый контур ножа (v1.7.19).
    side_ref = axis
    own_pp = getattr(own_geom, 'polypath', None)
    if p.is_corner_rework and own_pp is not None and own_pp.closed:
        side_ref = own_pp

    ll_in = _lead_line_length(p.entry_length, p.entry_angle, p)
    ar_in = p.entry_radius * p.radius_mult * p.lead_scale
    ang_in = p.entry_angle
    shrink_in = 1.0

    ll_out = _lead_line_length(p.exit_length, p.exit_angle, p)
    ar_out = p.exit_radius * p.radius_mult * p.lead_scale
    ang_out = p.exit_angle
    shrink_out = 1.0

    # ── СТОРОНА ЗАХОДА ──
    if p.is_corner_rework:
        # Лид доработки — ПО ЗАМЕРУ ЗАЗОРА (v1.7.39). Эмиттер пишет G42,
        # фреза снимает металл справа по ходу, заходить обязана оттуда
        # же. Но в узкой щели справа может не быть места: лид ложится на
        # встречную стенку. Поэтому сторону не назначаем правилом, а
        # меряем обеими и берём ту, где зазор больше.
        cut_half = (p.lead_scale / 2.0 if p.lead_scale > 0 else 0.5)
        res.lead_side, shrink_in, _a, res.clr_in = fit_rework_lead(
            start_pt, start_tan, own_pp, cut_half, ll_in, ar_in, ang_in,
            is_exit=False, neighbours=list(neighbours),
            style=p.entry_style)
        if _a is not None:
            # Подобранный угол меняет и длину прямой: при том же боковом
            # зазоре длина делится на sin(угла). Пересчитываем, иначе
            # лид строится под один угол, а мерился под другой.
            ang_in = _a
            ll_in = _lead_line_length(p.entry_length, ang_in, p)
        # Укорочение применяется к ИТОГОВОЙ длине и радиусу (v1.7.45):
        # запас прямой части уже внутри ll_in, и он тоже обязан
        # укорачиваться — иначе лид выходит длиннее программного.
        ll_in *= shrink_in
        ar_in *= shrink_in
        if res.clr_in < 0 and log is not None:
            log(f"ЛИД ДОРАБОТКИ: заход подходит к металлу ближе "
                f"полуширины реза на {abs(res.clr_in) * 1000:.0f} мкм "
                f"({start_pt[0]:.1f} {start_pt[1]:.1f}) — места нет ни "
                f"длиной, ни углом, нужна ручная правка")
    elif p.pass_side in ("INSIDE", "OUTSIDE"):
        res.lead_side = pick_lead_side_for_pass(
            start_pt, start_tan, side_ref, p.pass_side,
            ll_in, ar_in, ang_in, is_exit=False)
    elif p.pass_side == "LEFT":
        res.lead_side = "left"
    else:
        res.lead_side = "right"

    # ── СТОРОНА ВЫХОДА — отдельно, по КОНЦЕВОЙ касательной ──
    if p.is_corner_rework:
        cut_half = (p.lead_scale / 2.0 if p.lead_scale > 0 else 0.5)
        res.lead_out_side, shrink_out, _a, res.clr_out = fit_rework_lead(
            end_pt, end_tan, own_pp, cut_half, ll_out, ar_out, ang_out,
            is_exit=True, neighbours=list(neighbours), style=p.exit_style)
        if _a is not None:
            ang_out = _a
            ll_out = _lead_line_length(p.exit_length, ang_out, p)
        if res.clr_out < 0 and log is not None:
            log(f"ЛИД ДОРАБОТКИ: выход подходит к металлу ближе "
                f"полуширины реза на {abs(res.clr_out) * 1000:.0f} мкм "
                f"({end_pt[0]:.1f} {end_pt[1]:.1f}) — места нет ни "
                f"длиной, ни углом, нужна ручная правка")
    elif p.pass_side in ("INSIDE", "OUTSIDE"):
        res.lead_out_side = pick_lead_side_for_pass(
            end_pt, end_tan, side_ref, p.pass_side,
            ll_out, ar_out, ang_out, is_exit=True)
    else:
        res.lead_out_side = res.lead_side

    # ── ДУГА ЛИДА ОБЯЗАНА ПЕРЕЖИТЬ КОМПЕНСАЦИЮ ──
    # Радиус считается от эквидистанты фрезы ЭТОГО прохода: у доработки
    # режет тонкая T3, и компенсацию стойка делает по ней, а не по
    # основной (p.tool_offset там намеренно завышен, см. комментарий во
    # вьювере).
    _d_cut = (p.lead_scale / 2.0) if p.lead_scale > 0 else p.tool_offset
    _r_min = _d_cut + LEAD_MIN_COMP_RADIUS
    if ar_in > 0 and ar_in < _r_min:
        if log is not None:
            log(f"ЛИД: радиус дуги захода {ar_in:.3f} мм при радиусе "
                f"инструмента {_d_cut:.3f} — после компенсации "
                f"{ar_in - _d_cut:+.3f} мм, стойка такую дугу не "
                f"выполнит; поднят до {_r_min:.3f} мм")
        ar_in = _r_min
    if ar_out > 0 and ar_out * shrink_out < _r_min:
        if log is not None:
            log(f"ЛИД: радиус дуги выхода {ar_out * shrink_out:.3f} мм "
                f"при радиусе инструмента {_d_cut:.3f} — после "
                f"компенсации {ar_out * shrink_out - _d_cut:+.3f} мм, "
                f"стойка такую дугу не выполнит; поднят до "
                f"{_r_min:.3f} мм")
        ar_out = _r_min / (shrink_out if shrink_out > 1e-9 else 1.0)

    # ── ЗАПРОСЫ НА ПОСТРОЕНИЕ ──
    # INSIDE/OUTSIDE: сторону НЕ фиксируем — автоподбор пересчитает её на
    # каждой кандидатной позиции (после сдвига старта сторона меняется).
    # Доработка и LEFT/RIGHT: сторона замерена/задана, фиксируем.
    _fix = (p.is_corner_rework or p.pass_side not in ("INSIDE", "OUTSIDE"))
    req_in = LeadGeometryRequest(
        is_entry=True, pass_side=p.pass_side, angle_deg=ang_in,
        line_length=ll_in, arc_radius=ar_in, style=p.entry_style,
        forced_side=(res.lead_side if _fix else None))
    req_out = LeadGeometryRequest(
        is_entry=False, pass_side=p.pass_side, angle_deg=ang_out,
        line_length=ll_out * shrink_out,
        arc_radius=ar_out * shrink_out, style=p.exit_style,
        forced_side=(res.lead_out_side if _fix else None))

    # ── ЗАХОД ──
    if want_in:
        # Готовая точка захода (вьювер посчитал её первым). Ставим старт
        # туда, но автоподбор НЕ выключаем: он начинает с этой позиции,
        # поэтому безопасная точка остаётся на месте, а небезопасная
        # исправляется (v1.7.57).
        if p.entry_hint and axis.closed:
            try:
                from .path_offset import shift_start_to_point
                before_n = len(axis.segments)
                before_len = sum(s.length() for s in axis.segments)
                cand = shift_start_to_point(
                    axis, (float(p.entry_hint[0]), float(p.entry_hint[1])))
                ok = (cand and cand.segments and cand.closed
                      and abs(len(cand.segments) - before_n) <= 2)
                if ok:
                    after_len = sum(s.length() for s in cand.segments)
                    ok = abs(after_len - before_len) <= max(
                        0.01, before_len * 0.01)
                if ok:
                    axis = cand
            except Exception:
                pass
        # ── ВЫХОД В ПОДБОРЕ ЗАХОДА: НЕ ТОТ ЖЕ, ЧТО БУДЕТ ПОСТРОЕН ──
        # Здесь сознательно оставлено расхождение, которое было в посте
        # до v1.7.58: подбор позиции захода проверяет выход БЕЗ
        # укорочения и БЕЗ жёсткой стороны, а строится потом выход С
        # ними. Логически это неверно — проверяется не тот объект,
        # который поедет, — но это НЕ дефект картинки, и трогать его
        # внутри сведения вьювера с постом нельзя: замерено на 124173,
        # что согласованный вариант УХУДШАЕТ два захода угла (зазор до
        # металла 0.547 → 0.502 и 0.547 → 0.429 мм), потому что
        # «лишняя» проверка выхода работала как дополнительный фильтр и
        # уводила заход на лучший угол. Разбирать это надо отдельно и с
        # замерами на станке, а не попутно. Поэтому: подбору отдаём
        # ТАКОЙ ЖЕ запрос, как отдавал прежний пост.
        _exit_in_search = None
        if want_out:
            _exit_in_search = LeadGeometryRequest(
                is_entry=False, pass_side=p.pass_side, angle_deg=ang_out,
                line_length=ll_out, arc_radius=ar_out, style=p.exit_style,
                forced_side=(None if p.pass_side in ("INSIDE", "OUTSIDE")
                             else res.lead_out_side))
        axis, res.lead_in, res.lead_in_collision, res.lead_in_geom = \
            plan_lead_in(
                axis, req_in, contours_lines, contours_bboxes,
                own_geom.id, p.tool_offset,
                auto_avoid=(not p.no_avoid), safety_factor=1.2,
                exit_request=_exit_in_search,
                overlap=p.overlap)

    # ── ПЕРЕБЕГ — ПОСЛЕ подбора ──
    # Применённый раньше, он разомкнул бы контур, и сдвиг старта тащил
    # бы перебег вместе с собой.
    res.axis_rerooted = axis
    if p.overlap > 1e-9 and axis.closed:
        from .path_offset import apply_overlap
        axis = apply_overlap(axis, p.overlap)

    # ── ВЫХОД — из ФИНАЛЬНОГО конца контура ──
    # Без автоподбора: позиция жёстко задана концом осевой.
    if want_out and axis.segments:
        res.lead_out, res.lead_out_collision, res.lead_out_geom = \
            plan_lead_out(
                axis, req_out, contours_lines, contours_bboxes,
                own_geom.id, p.tool_offset, safety_factor=1.2)

    res.axis = axis
    return res
