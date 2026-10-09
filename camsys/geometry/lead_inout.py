"""
geometry/lead_inout.py — построение геометрии заходов/выходов на контур.

Lead-In/Out (по образцу диалога Альфакама):
    - LINE + ARC tangential: 
        1) прямая длиной L (Tool Rad × N) под углом α к касательной контура
        2) дуга радиуса R (Tool Rad × N), касательная и к контуру, и к прямой
      Это превращается в G1 (Line) + G12/G13 (Arc) в .anc.
    - ARC only: только тангенциальная дуга, без прямой.
    - LINE only: только прямая под углом, без дуги (не плавный заход).
    - NONE: без захода — прямо в начало контура.

Геометрия захода:
    - Дуга проходит через точку start контура и через конец прямой
    - В точке start дуга касается касательной к контуру
    - Прямая идёт от внешней точки к началу дуги под углом α к этой касательной
    - Сторона захода (со стороны металла или со стороны от металла) определяется
      направлением, при этом entry-дуга не должна «врезаться» в контур
"""

from __future__ import annotations
from typing import Optional, Tuple
from dataclasses import dataclass
import math

from .primitives import Line, Arc, Polypath, Segment, Point, EPS


@dataclass
class LeadGeometry:
    """Геометрия захода или выхода: линия + дуга, или только что-то одно.
    
    Поля могут быть None — например, при стиле ARC only line == None.
    """
    line: Optional[Line] = None
    arc: Optional[Arc] = None
    
    def start_point(self) -> Optional[Point]:
        """Самая внешняя точка захода — куда G0-позиционирование."""
        if self.line is not None:
            return self.line.a
        if self.arc is not None:
            return self.arc.a
        return None
    
    def end_point(self) -> Optional[Point]:
        """Точка стыка с контуром."""
        if self.arc is not None:
            return self.arc.b
        if self.line is not None:
            return self.line.b
        return None


# ─────────────────────────────────────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЕ ВЕКТОРНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────

def _rotate(v: Point, angle_rad: float) -> Point:
    """Поворот вектора на angle_rad против часовой."""
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return (v[0]*c - v[1]*s, v[0]*s + v[1]*c)


def _scale(v: Point, k: float) -> Point:
    return (v[0]*k, v[1]*k)


def _add(a: Point, b: Point) -> Point:
    return (a[0]+b[0], a[1]+b[1])


def _norm(v: Point) -> Point:
    L = math.hypot(v[0], v[1])
    if L < EPS:
        return (0.0, 0.0)
    return (v[0]/L, v[1]/L)


# ─────────────────────────────────────────────────────────────────────────
#  ГЕОМЕТРИЯ ЗАХОДА: LINE + TANGENT ARC
# ─────────────────────────────────────────────────────────────────────────

def build_lead_in(start_point: Point,
                  tangent: Point,
                  side: str,
                  line_length: float,
                  arc_radius: float,
                  approach_angle_deg: float = 45.0,
                  style: str = 'line_arc',
                  ) -> LeadGeometry:
    """Строит геометрию захода (Lead-In) на контур.
    
    Тип захода (style):
      - 'line_arc' (по умолчанию): дуга + прямая, касательная к контуру 
        (G12/G13 совместимо).
      - 'line' (Альфакам-стиль): только прямая под углом к касательной,
        без дуги. Заход — острая «галочка» к точке контура.
    
    Сторона захода (side='left'|'right') определяет в какую сторону 
    отклоняется прямая от касательной.
    """
    t = _norm(tangent)
    
    if style == 'line':
        # АЛЬФАКАМ-стиль: одна прямая под углом approach_angle к касательной.
        # Внешняя точка слева/справа от продолжения касательной назад (-t).
        # При side='left' и t=(1,0): "слева" по ходу касательной = сверху, 
        # значит внешняя точка должна быть в верхне-ЛЕВОМ углу.
        # Вектор -t = (-1,0); чтобы поднять его в верхне-левую четверть,
        # надо повернуть на угол -approach (CW), т.е. sign=-1.
        approach_rad = math.radians(approach_angle_deg)
        cos_a = math.cos(approach_rad)
        sin_a = math.sin(approach_rad)
        if side.lower() == 'left':
            sign = -1.0
        else:
            sign = 1.0
        bx = -t[0]; by = -t[1]
        rotated_x = bx * cos_a - by * (sign * sin_a)
        rotated_y = bx * (sign * sin_a) + by * cos_a
        line_start = (
            start_point[0] + line_length * rotated_x,
            start_point[1] + line_length * rotated_y,
        )
        line = Line(a=line_start, b=start_point)
        return LeadGeometry(line=line, arc=None)
    
    # style == 'line_arc' — дуга + прямая (старая логика)
    
    # Перпендикуляр к касательной в сторону side
    # +90° поворот = (-t.y, t.x) → это «лево» относительно направления t
    if side.lower() == 'left':
        n = (-t[1], t[0])
        # При заходе слева от контура (идущего вправо) дуга центром выше,
        # обход по дуге CCW: внешняя точка → точка стыка с касательной к контуру
        ccw_arc = True
    else:
        n = (t[1], -t[0])
        # При заходе справа — дуга центром ниже, CW обход
        ccw_arc = False
    
    # Центр заходной дуги: на расстоянии arc_radius от start_point
    # в направлении n (внутрь стороны захода)
    center = _add(start_point, _scale(n, arc_radius))
    
    # Точка стыка дуги и прямой: дуга проходит через угол (180° - approach_angle)
    # от радиус-вектора (n) при подходе к контуру.
    # Удобнее: точка стыка = center + arc_radius * rotated(-n, ±arc_angle)
    # где arc_angle — угол по дуге от точки start_point до стыка.
    
    # Для симметричного захода: arc охватывает угол 
    # (90° - approach_angle/2) ... здесь упрощённо берём arc_angle = approach_angle
    # → прямая выходит под углом approach_angle к касательной.
    
    approach_rad = math.radians(approach_angle_deg)
    
    # Радиус-вектор от центра дуги к start_point — это -n
    minus_n = (-n[0], -n[1])
    
    # Поворачиваем радиус-вектор на угол захода в сторону, противоположную 
    # направлению касательной (заход идёт «навстречу» обходу контура).
    # Знак угла зависит от side:
    if side.lower() == 'left':
        # n указывает влево от t. Заходим против t → поворот minus_n на 
        # -approach_rad (против часовой если n=влево)
        rotate_angle = -approach_rad
    else:
        rotate_angle = approach_rad
    
    rotated_radial = _rotate(minus_n, rotate_angle)
    arc_start = _add(center, _scale(rotated_radial, arc_radius))
    
    # Касательная к дуге в точке arc_start = perp(rotated_radial), 
    # направление зависит от ccw_arc
    if ccw_arc:
        arc_tangent_at_start = (-rotated_radial[1], rotated_radial[0])
    else:
        arc_tangent_at_start = (rotated_radial[1], -rotated_radial[0])
    arc_tangent_at_start = _norm(arc_tangent_at_start)
    
    # Прямая идёт от внешней точки к arc_start.
    # Направление прямой — ПРОТИВ касательной дуги (приходим к стыку)
    # → внешняя точка = arc_start - line_length * arc_tangent_at_start
    line_start = (
        arc_start[0] - line_length * arc_tangent_at_start[0],
        arc_start[1] - line_length * arc_tangent_at_start[1],
    )
    
    line = Line(a=line_start, b=arc_start)
    arc = Arc(a=arc_start, b=start_point, center=center, ccw=ccw_arc)
    
    return LeadGeometry(line=line, arc=arc)


def _polypath_centroid(polypath):
    xs = [s.a[0] for s in polypath.segments]
    ys = [s.a[1] for s in polypath.segments]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def pick_lead_side_for_pass(point: Point, tangent: Point, polypath,
                            pass_side: str,
                            line_length: float, arc_radius: float,
                            angle_deg: float, is_exit: bool = False) -> str:
    """Выбирает сторону завитка lead-in/out так, чтобы заходы ДВУХ проходов
    разводились в разные стороны (не пересекали друг друга и канал между
    резами).

    Правило (после фикса конвенции компенсации, оба G41):
      - pass_side='INSIDE' (CW, режет ВНЕШНИЙ контур) — заход НАРУЖУ контура
      - pass_side='OUTSIDE' (CCW, режет ВНУТРЕННИЙ) — заход ВНУТРЬ контура

    Строим завиток обеими сторонами, смотрим где оказалась дальняя точка
    (point-in-polygon). Берём ту, где её положение совпадает с нужным.
    Если ни одна сторона не даёт точного совпадения (бывает на длинных
    прямых сторонах) — выбираем по расстоянию до центра: для «наружу»
    берём ту что дальше, для «внутрь» — что ближе.
    """
    from .path_offset import _point_in_polypath
    builder = build_lead_out if is_exit else build_lead_in
    # Правило (внимание к терминологии! pass_side обозначает СТОРОНУ 
    # компенсации, а не физический смысл реза):
    #   pass_side='OUTSIDE' = ВНУТРЕННИЙ путь (бирюзовый в превью):
    #       смещается ВНУТРЬ контура к центру. Заход → ИЗНУТРИ контура
    #       (с той же стороны куда смещён путь).
    #   pass_side='INSIDE' = ВНЕШНИЙ путь (красный в превью):
    #       смещается НАРУЖУ от центра. Заход → СНАРУЖИ контура.
    # 
    # Логика: лезвие фрезы движется по смещённому пути; если оно входит 
    # на путь с противоположной стороны, то рассекает соседнюю стенку 
    # ножа. Поэтому заход должен идти С ТОЙ ЖЕ СТОРОНЫ контура куда 
    # смещён сам путь.
    #
    # Сейчас (после неоднократных переименований side/проход) сторона 
    # смещения совпадает со словом want_outside так: 
    #   OUTSIDE (внутр.путь, бирюзовый, смещ. внутрь) → want_outside=False
    #   INSIDE  (внеш.путь, красный, смещ. наружу)    → want_outside=True
    want_outside = (str(pass_side).upper() == 'INSIDE')

    def far_point(ls):
        lg = builder(point, tangent, ls, line_length, arc_radius, angle_deg)
        if is_exit:
            return lg.line.b if lg.line else (lg.arc.b if lg.arc else point)
        return lg.line.a if lg.line else (lg.arc.a if lg.arc else point)

    cx, cy = _polypath_centroid(polypath)
    info = {}
    for ls in ('left', 'right'):
        far = far_point(ls)
        info[ls] = {
            'outside': not _point_in_polypath(far, polypath),
            'dist': (far[0] - cx) ** 2 + (far[1] - cy) ** 2,
        }
    # Совпадает с нужным режимом?
    matches = [ls for ls in ('left', 'right') if info[ls]['outside'] == want_outside]
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 2:
        # Обе подходят — берём ту, что «глубже» в нужную сторону
        if want_outside:
            return 'left' if info['left']['dist'] >= info['right']['dist'] else 'right'
        else:
            return 'left' if info['left']['dist'] <= info['right']['dist'] else 'right'
    # Ни одна не подходит (вырожденный случай на прямой) — fallback по расстоянию
    if want_outside:
        return 'left' if info['left']['dist'] >= info['right']['dist'] else 'right'
    else:
        return 'left' if info['left']['dist'] <= info['right']['dist'] else 'right'


def pick_outward_lead_side(point: Point, tangent: Point, polypath,
                           line_length: float, arc_radius: float,
                           angle_deg: float, is_exit: bool = False) -> str:
    """LEGACY: оставлено для обратной совместимости. Эквивалентно
    pick_lead_side_for_pass(... pass_side='INSIDE' ...) — обе стороны наружу.
    Не использовать в новом коде, использовать pick_lead_side_for_pass."""
    return pick_lead_side_for_pass(point, tangent, polypath, 'INSIDE',
                                   line_length, arc_radius, angle_deg, is_exit)


def build_lead_out(end_point: Point,
                   tangent: Point,
                   side: str,
                   line_length: float,
                   arc_radius: float,
                   retract_angle_deg: float = 45.0,
                   style: str = 'line_arc',
                   ) -> LeadGeometry:
    """Строит геометрию выхода (Lead-Out) с контура.
    
    Тип выхода (style):
      - 'line_arc' (по умолчанию): дуга + прямая.
      - 'line' (Альфакам-стиль): только прямая под углом к касательной.
    
    Это «отражение» Lead-In: 
        1) дуга, касательная к контуру в точке end_point
        2) прямая, уводящая инструмент в сторону
    
    Args:
        end_point: точка отхода с контура (где контур заканчивается)
        tangent: единичный касательный вектор к контуру в end_point
                 (направлен в сторону движения)
        side: 'left' или 'right' — с какой стороны выходим
        line_length: длина прямого участка
        arc_radius: радиус дуги выхода
        retract_angle_deg: угол отхода
    """
    t = _norm(tangent)
    
    if style == 'line':
        # АЛЬФАКАМ-стиль: одна прямая от end_point наружу.
        # Касательная направлена «вперёд». Внешняя точка = поворот t на 
        # angle в side (для side=left = CCW = вверх; зеркально входу).
        retract_rad = math.radians(retract_angle_deg)
        cos_a = math.cos(retract_rad)
        sin_a = math.sin(retract_rad)
        if side.lower() == 'left':
            sign = 1.0
        else:
            sign = -1.0
        # Поворот вектора t на sign*retract_angle
        bx = t[0]; by = t[1]
        rotated_x = bx * cos_a - by * (sign * sin_a)
        rotated_y = bx * (sign * sin_a) + by * cos_a
        line_end = (
            end_point[0] + line_length * rotated_x,
            end_point[1] + line_length * rotated_y,
        )
        line = Line(a=end_point, b=line_end)
        return LeadGeometry(line=line, arc=None)
    
    # style == 'line_arc' — старая логика с дугой
    
    if side.lower() == 'left':
        n = (-t[1], t[0])
        # Lead-out с левой стороны: дуга идёт CCW от точки на контуре наружу
        ccw_arc = True
    else:
        n = (t[1], -t[0])
        ccw_arc = False
    
    center = _add(end_point, _scale(n, arc_radius))
    
    retract_rad = math.radians(retract_angle_deg)
    minus_n = (-n[0], -n[1])
    
    # При выходе поворачиваем радиус-вектор В НАПРАВЛЕНИИ движения
    if side.lower() == 'left':
        rotate_angle = retract_rad
    else:
        rotate_angle = -retract_rad
    
    rotated_radial = _rotate(minus_n, rotate_angle)
    arc_end = _add(center, _scale(rotated_radial, arc_radius))
    
    if ccw_arc:
        arc_tangent_at_end = (-rotated_radial[1], rotated_radial[0])
    else:
        arc_tangent_at_end = (rotated_radial[1], -rotated_radial[0])
    arc_tangent_at_end = _norm(arc_tangent_at_end)
    
    line_end = (
        arc_end[0] + line_length * arc_tangent_at_end[0],
        arc_end[1] + line_length * arc_tangent_at_end[1],
    )
    
    arc = Arc(a=end_point, b=arc_end, center=center, ccw=ccw_arc)
    line = Line(a=arc_end, b=line_end)
    
    return LeadGeometry(line=line, arc=arc)


_SAMPLE_CACHE: dict = {}


def _cached_points(poly, tol: float):
    """Точки контура с запоминанием: один и тот же нож пересэмплировался
    для КАЖДОГО лида и каждой пробы — на заказе это минута на пустом
    месте (v1.7.39)."""
    from .path_offset import _exact_axis_points
    key = (id(poly), tol, len(getattr(poly, 'segments', ()) or ()))
    hit = _SAMPLE_CACHE.get(key)
    if hit is not None:
        return hit[1]
    try:
        got = _exact_axis_points(poly, tol)[0]
    except Exception:
        got = []
    if len(_SAMPLE_CACHE) > 512:
        _SAMPLE_CACHE.clear()
    # ВМЕСТЕ с точками держим сам объект: ключ — его id, а освобождённый
    # объект отдаёт свой адрес следующему, и кэш начинает возвращать
    # ТОЧКИ ЧУЖОГО КОНТУРА. В экспорте, где полипасов создаётся много,
    # замер лида из-за этого считался не по тому ножу (v1.7.40).
    _SAMPLE_CACHE[key] = (poly, got)
    return got


def pick_lead_side_by_clearance(point: Point, tangent: Point, contour,
                                cut_half_width: float,
                                line_length: float, arc_radius: float,
                                angle_deg: float, is_exit: bool = False,
                                style: str = 'line_arc',
                                prefer: str = 'right',
                                own_wall_mm: float = 2.0,
                                min_clearance_mm: float = 0.03,
                                neighbours=None
                                ) -> Tuple[str, float, float]:
    """Сторона лида доработки — ПО ЗАМЕРУ ЗАЗОРА до ЧУЖОЙ стенки (v1.7.39).

    Эмиттер пишет G42, поэтому фреза снимает металл справа по ходу, и
    заходить она обязана оттуда же — по уже снятому металлу. Но в узкой
    щели справа может просто не быть места: лид ложится на встречную
    стенку и срезает её. Так вышло на Knife_1 заказа 124173 — щель между
    вершинами 1.3 мм при ширине реза T3 1.1 мм.

    Поэтому сторона не назначается правилом, а выбирается замером.

    Мерить «до контура» в лоб бессмысленно: лид по построению выходит ИЗ
    контура и у точки стыка касается его — ноль получается у обеих
    сторон. Считается расстояние только до тех точек контура, которые
    удалены от точки стыка ПО ДЛИНЕ КОНТУРА больше чем на `own_wall_mm`:
    своя стенка исключается, встречная — нет.

    `neighbours` — контуры СОСЕДНИХ ножей. Их обязательно учитывать: щель,
    в которую заходит фреза, чаще всего образована не своим контуром, а
    вершиной соседнего лезвия, и лид, замеренный только по своему ножу,
    спокойно срезает соседа (Knife_1 заказа 124173).

    Сторона по умолчанию — справа по ходу, и она остаётся, пока зазор
    не меньше `min_clearance_mm`. На другую переходим только там, где
    справа места нет: «где зазор больше» — негодное правило, оно гоняет
    лид туда-сюда и на свободных местах.

    Returns:
        (сторона, зазор в мм, множитель длины). Зазор — расстояние до
        чужой стенки минус половина ширины реза; отрицательный значит
        «режет». Множитель < 1 — лид пришлось укоротить, чтобы он
        поместился на своей стороне.
    """
    from .path_offset import _exact_axis_points
    builder = build_lead_out if is_exit else build_lead_in

    cpts = _cached_points(contour, 0.02)
    ctot = cpts[-1][0] if cpts else 0.0
    if not cpts or ctot <= 0:
        return prefer, 0.0, 1.0

    # положение точки стыка на контуре
    s_att = min(cpts, key=lambda z: (z[1][0] - point[0]) ** 2
                + (z[1][1] - point[1]) ** 2)[0]
    far = [q for s, q in cpts
           if min((s - s_att) % ctot, (s_att - s) % ctot) > own_wall_mm]
    # Соседние ножи — только те, что рядом: дальние лишь замедляют замер.
    reach = line_length + arc_radius + cut_half_width + 1.0
    for nb in (neighbours or ()):
        for _s, q in _cached_points(nb, 0.05):
            if (abs(q[0] - point[0]) < reach
                    and abs(q[1] - point[1]) < reach):
                far.append(q)
    # Дальше точки лида в любом случае не достанут, а перебор по всему
    # контуру съедал минуту на заказ (v1.7.39).
    far = [q for q in far
           if abs(q[0] - point[0]) < reach and abs(q[1] - point[1]) < reach]
    if not far:
        # Рядом вообще ничего нет — места сколько угодно.
        return prefer, reach, 1.0
    # Сетка: без неё замер квадратичный и экспорт тормозит.
    cell = max(0.25, cut_half_width)
    grid = {}
    for q in far:
        grid.setdefault((int(q[0] / cell), int(q[1] / cell)), []).append(q)

    def clearance(side: str, shrink: float = 1.0) -> float:
        _ll, _ar = line_length * shrink, arc_radius * shrink
        try:
            g = builder(point, tangent, side, _ll, _ar,
                        angle_deg, style=style)
        except TypeError:
            try:
                g = builder(point, tangent, side, _ll, _ar,
                            angle_deg)
            except Exception:
                return -1e9
        except Exception:
            return -1e9
        segs = [x for x in (getattr(g, 'line', None), getattr(g, 'arc', None))
                if x is not None]
        if not segs:
            return -1e9
        try:
            lpts = [q for _s, q in _exact_axis_points(
                Polypath(segments=segs, closed=False), 0.02)[0]]
        except Exception:
            return -1e9
        worst = reach ** 2
        for p in lpts:
            gx, gy = int(p[0] / cell), int(p[1] / cell)
            r = int(math.sqrt(worst) / cell) + 1
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    for q in grid.get((gx + dx, gy + dy), ()):
                        d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
                        if d < worst:
                            worst = d
        return math.sqrt(worst) - cut_half_width

    other = 'left' if prefer == 'right' else 'right'
    c_pref = clearance(prefer)
    if c_pref >= min_clearance_mm:
        return prefer, c_pref, 1.0
    # Не помещается — сначала УКОРАЧИВАЕМ на своей стороне. Перенос на
    # другую сторону уводит лид поверх собственной стенки, а это хуже:
    # слева по ходу стоит металл, который этот проход не снимает.
    for k in (0.8, 0.6, 0.45, 0.3, 0.2):
        c = clearance(prefer, k)
        if c >= min_clearance_mm:
            return prefer, c, k
    c_other = clearance(other)
    if c_other > c_pref + 1e-9:
        return other, c_other, 1.0
    return prefer, c_pref, 1.0


def _seg_cross(a, b, c, d) -> bool:
    """Пересекаются ли отрезки ab и cd (строго, без касаний в концах)."""
    def cr(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1, d2 = cr(c, d, a), cr(c, d, b)
    d3, d4 = cr(a, b, c), cr(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def fit_rework_lead(point: Point, tangent: Point, contour,
                    cut_half_width: float,
                    line_length: float, arc_radius: float,
                    angle_deg: float, is_exit: bool = False,
                    style: str = 'line_arc',
                    prefer: str = 'right',
                    neighbours=None,
                    margin_mm: float = 0.05,
                    attach_eps: float = 0.02,
                    path_is_cutter: bool = False):
    """Подбирает лид доработки так, чтобы фреза НЕ доставала до геометрии.

    Два условия (v1.7.40):

    1. Лид не пересекает контур ножа. Прежний замер исключал «свою»
       стенку на 2 мм вокруг точки стыка — а лид пересекал именно её,
       уходя поверх вершины лезвия. Теперь своя стенка проверяется, из
       неё исключён только стык (`attach_eps`).

    2. До ЧУЖИХ стенок (дальше по контуру, плюс соседние ножи) остаётся
       не меньше `cut_half_width + margin_mm`.

    Если условия не выполняются, лид сначала УКОРАЧИВАЕТСЯ, а затем
    становится более пологим (угол захода уменьшается): пологий лид
    уходит вдоль пути и не перелезает через стенку. Сторона реза
    меняется в последнюю очередь — слева по ходу стоит металл, который
    этот проход не снимает.

    `path_is_cutter` — переданный лид УЖЕ является путём фрезы (так его
    строит вьювер: от эквидистанты). Тогда смещать его второй раз не надо
    и зазор считается прямо до стенок. Пост передаёт осевую и оставляет
    False.

    Returns:
        (сторона, множитель длины, угол захода, зазор в мм).
    """
    builder = build_lead_out if is_exit else build_lead_in
    cpts = _cached_points(contour, 0.02)
    if not cpts:
        return prefer, 1.0, angle_deg, 0.0
    ctot = cpts[-1][0]
    s_att = min(cpts, key=lambda z: (z[1][0] - point[0]) ** 2
                + (z[1][1] - point[1]) ** 2)[0]

    # Контуры для проверки на пересечение — КАЖДЫЙ отдельной ломаной.
    walls = [[q for _s, q in cpts]]
    # Чужие стенки — для замера зазора
    reach = line_length + arc_radius + cut_half_width + 1.0
    far = [q for s, q in cpts
           if min((s - s_att) % ctot, (s_att - s) % ctot) > 2.0
           and abs(q[0] - point[0]) < reach and abs(q[1] - point[1]) < reach]
    # Соседние ножи идут ТОЛЬКО в замер зазора. В проверку на пересечение
    # их добавлять нельзя: точки разных контуров, склеенные в одну
    # ломаную, дают фиктивные отрезки через весь лист, и «пересечение»
    # находится всегда (v1.7.40).
    for nb in (neighbours or ()):
        pts_nb = _cached_points(nb, 0.05)
        near_nb = False
        for _s, q in pts_nb:
            if abs(q[0] - point[0]) < reach and abs(q[1] - point[1]) < reach:
                far.append(q)
                near_nb = True
        if near_nb:
            walls.append([q for _s, q in pts_nb])

    def probe(side, shrink, ang):
        try:
            g = builder(point, tangent, side, line_length * shrink,
                        arc_radius * shrink, ang, style=style)
        except TypeError:
            try:
                g = builder(point, tangent, side, line_length * shrink,
                            arc_radius * shrink, ang)
            except Exception:
                return None
        except Exception:
            return None
        segs = [x for x in (getattr(g, 'line', None), getattr(g, 'arc', None))
                if x is not None]
        if not segs:
            return None
        try:
            return [q for _s, q in _cached_points(
                Polypath(segments=segs, closed=False), 0.02)]
        except Exception:
            return None

    def cutter(lpts):
        """Путь ФРЕЗЫ: осевая, смещённая вправо по ходу (G42)."""
        if path_is_cutter:
            return lpts
        out = []
        for i in range(len(lpts) - 1):
            x0, y0 = lpts[i]
            x1, y1 = lpts[i + 1]
            dx, dy = x1 - x0, y1 - y0
            L = math.hypot(dx, dy)
            if L < 1e-12:
                continue
            nx, ny = dy / L, -dx / L
            out.append((x0 + nx * cut_half_width, y0 + ny * cut_half_width))
            out.append((x1 + nx * cut_half_width, y1 + ny * cut_half_width))
        return out

    def ok(lpts):
        if not lpts:
            return None
        # 1. ПУТЬ ФРЕЗЫ не пересекает геометрию. Мерить по осевой нельзя:
        #    фреза идёт на полуширину реза в сторону, и в узком месте
        #    через лезвие перелезает именно она, а осевая проходит мимо.
        #    Окрестность стыка не считается — там фреза и должна лежать
        #    на контуре, она его и обрабатывает.
        cpath = cutter(lpts)
        eps = max(attach_eps, cut_half_width * 1.2)
        for i in range(len(cpath) - 1):
            a, b = cpath[i], cpath[i + 1]
            if (math.hypot(a[0] - point[0], a[1] - point[1]) < eps
                    or math.hypot(b[0] - point[0], b[1] - point[1]) < eps):
                continue
            for wl in walls:
                for k in range(len(wl) - 1):
                    c, d = wl[k], wl[k + 1]
                    if abs(c[0] - a[0]) > reach or abs(c[1] - a[1]) > reach:
                        continue
                    if _seg_cross(a, b, c, d):
                        return None
        # 2. зазор до чужих стенок
        worst = reach
        for p in lpts:
            for q in far:
                d = math.hypot(q[0] - p[0], q[1] - p[1])
                if d < worst:
                    worst = d
        clr = worst - (0.0 if path_is_cutter else cut_half_width)
        return clr if clr >= margin_mm else None

    other = 'left' if prefer == 'right' else 'right'
    for side in (prefer, other):
        for ang in (angle_deg, angle_deg * 0.7, angle_deg * 0.5,
                    angle_deg * 0.35):
            for shrink in (1.0, 0.8, 0.6, 0.45, 0.3, 0.2, 0.12):
                clr = ok(probe(side, shrink, ang))
                if clr is not None:
                    return side, shrink, ang, clr
    return prefer, 0.12, angle_deg * 0.35, -1.0
