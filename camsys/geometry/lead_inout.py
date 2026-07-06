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
