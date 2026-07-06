"""
geometry/biarc.py — биарковая аппроксимация кривых Безье.

Алгоритм по статье "Biarcs" (Bolton 1975, упрощённый):

Дано: кривая Безье B с тангенциальными единичными векторами T₀ в P₀ и T₁ в P₃.
Цель: найти точку сочленения J и общий тангенс T_J такие, что:
    Arc1: от P₀ с тангенсом T₀ в P₀ и T_J в J
    Arc2: от J с тангенсом T_J в J и T₁ в P₃
гладко стыкуются (G1-непрерывность) и приближают Безье с ошибкой ≤ tolerance.

Стандартная формула для биарка с равными "длинами":
    α = угол между T₀ и хордой
    β = угол между T₁ и хордой
    d = длина хорды P₀→P₃
    
    Для случая, когда d_T₀ = d_T₁ = d_T (длина каждой дуги по тангенсу):
        d_T = d / (cos(α/2) + cos(β/2))  -- упрощённо
    
    Точка сочленения J = P₀ + d_T * T₀ + ... (через геометрическое построение)

В реализации используем стандартный метод "equal chord biarc":
    1. Хорда H = P₃ - P₀
    2. Если T₀ + T₁ почти параллельны H → биарк вырождается в одну дугу
       (или вообще в прямую если T₀ || T₁ || H)
    3. Иначе: J на пересечении биссектрис

Источники: Schneider "A Tribute to Schoenberg" (1988), Šír et al. (2006).

Если отклонение биарка от Безье > tolerance — рекурсивно делим Безье пополам.
"""

from __future__ import annotations
from typing import List, Optional, Tuple
import math
from .primitives import (
    Bezier, Line, Arc, Segment, Point,
    vec_add, vec_sub, vec_mul, vec_dot, vec_cross, vec_len, vec_norm,
    vec_perp, vec_dist, EPS
)


DEFAULT_TOLERANCE = 0.001   # мм; 1 мкм по умолчанию (для микронной обработки достаточно)
MAX_SUBDIVISION = 32        # глубина рекурсии при делении Безье
ERROR_SAMPLES = 8           # сколько точек брать для проверки отклонения


# ─────────────────────────────────────────────────────────────────────────
#  ОДИНОЧНАЯ ДУГА ПО 2 ТОЧКАМ + КАСАТЕЛЬНОЙ В НАЧАЛЕ
# ─────────────────────────────────────────────────────────────────────────

def arc_from_tangent(p_start: Point, p_end: Point,
                     t_start: Point) -> Optional[Segment]:
    """Построить дугу/линию, проходящую через p_start и p_end,
    с заданной касательной t_start в p_start.
    
    Возвращает Arc или Line (если точки коллинеарны касательной).
    """
    chord = vec_sub(p_end, p_start)
    chord_len = vec_len(chord)
    if chord_len < EPS:
        return None  # вырождение
    
    # Если касательная сонаправлена с хордой → линия
    cross = vec_cross(t_start, chord)
    if abs(cross) < EPS * chord_len:
        # Проверим что направление совпадает (не назад)
        if vec_dot(t_start, chord) > 0:
            return Line(p_start, p_end)
        return None  # назад — невозможная дуга
    
    # Радиус дуги через точки A, B и касательную T в A:
    #   Центр C на перпендикуляре к T в A: C = A + R·n (n — ед. перпендикуляр к T).
    #   Условие |C - B| = R даёт: R = |chord|² / (2 · |n · chord|).
    #   |n · chord| = |T × chord| (модуль псевдоскалярного произведения).
    r = (chord_len * chord_len) / (2 * abs(cross))
    
    # Перпендикуляр к касательной — в сторону хорды
    perp = vec_perp(t_start)  # +90° от касательной (CCW)
    side = vec_dot(perp, chord)  # >0 если хорда слева от касательной
    if side < 0:
        perp = (-perp[0], -perp[1])
    
    center = vec_add(p_start, vec_mul(perp, r))
    
    # Определить направление: CCW если cross > 0, CW иначе
    ccw = cross > 0
    
    return Arc(p_start, p_end, center, ccw)


# ─────────────────────────────────────────────────────────────────────────
#  БИАРК: ОДИН СЕГМЕНТ БЕЗЬЕ → ДВЕ ДУГИ
# ─────────────────────────────────────────────────────────────────────────

def _equal_chord_biarc(p0: Point, t0: Point,
                       p3: Point, t1: Point) -> Optional[Tuple[Segment, Segment]]:
    """Построить биарк с правильной C1-непрерывностью.
    
    ИДЕЯ (правильная):
      T_J = (T0 + T1) / |T0 + T1|  — общая касательная в точке соединения 
      (симметричный «равноугловой» биарк).
      
      Тогда хорды двух арок имеют направление углового бисектора 
      соответствующих касательных:
        chord1_dir = (T0 + T_J) / |T0 + T_J|
        chord2_dir = (T_J + T1) / |T_J + T1|
      
      И должно выполняться:
        L1 * chord1_dir + L2 * chord2_dir = P3 - P0  (векторное уравнение)
      
      Решаем линейную систему 2x2 относительно (L1, L2). При L1,L2 > 0:
        J = P0 + L1 * chord1_dir
      
      Затем строим арки через arc_from_tangent — каждая получит правильную 
      касательную (по построению с T0 и T1 на концах + T_J посередине).
    
    Возвращает (Arc1, Arc2) или None если конструкция невозможна 
    (T0+T1 ≈ 0, или хорды линейно зависимы, или L < 0).
    """
    chord = vec_sub(p3, p0)
    chord_len = vec_len(chord)
    
    if chord_len < EPS:
        return None
    
    # Общая касательная в точке соединения — симметричная.
    t_sum = (t0[0] + t1[0], t0[1] + t1[1])
    t_sum_len = vec_len(t_sum)
    if t_sum_len < EPS:
        # T0 и T1 антипараллельны → биарк вырождается, нужно разбивать
        return None
    t_j = (t_sum[0] / t_sum_len, t_sum[1] / t_sum_len)
    
    # Направления хорд двух арок — углового бисектора пар касательных.
    chord1_raw = (t0[0] + t_j[0], t0[1] + t_j[1])
    chord1_len = vec_len(chord1_raw)
    if chord1_len < EPS:
        return None
    chord1_dir = (chord1_raw[0] / chord1_len, chord1_raw[1] / chord1_len)
    
    chord2_raw = (t_j[0] + t1[0], t_j[1] + t1[1])
    chord2_len = vec_len(chord2_raw)
    if chord2_len < EPS:
        return None
    chord2_dir = (chord2_raw[0] / chord2_len, chord2_raw[1] / chord2_len)
    
    # Линейная система:
    #   L1 * chord1_dir.x + L2 * chord2_dir.x = chord.x
    #   L1 * chord1_dir.y + L2 * chord2_dir.y = chord.y
    det = chord1_dir[0] * chord2_dir[1] - chord1_dir[1] * chord2_dir[0]
    if abs(det) < EPS:
        # Хорды параллельны (T0 ≈ T1) → можно одной дугой, не биарком
        return None
    
    L1 = (chord[0] * chord2_dir[1] - chord[1] * chord2_dir[0]) / det
    L2 = (chord1_dir[0] * chord[1] - chord1_dir[1] * chord[0]) / det
    
    if L1 <= 0 or L2 <= 0:
        # Точка соединения «за спиной» — биарк невозможен в этой конфигурации
        return None
    
    # Точка соединения
    j = (p0[0] + L1 * chord1_dir[0], p0[1] + L1 * chord1_dir[1])
    
    # Первая дуга: P0 → J с касательной T0 в P0.
    # Касательная в конце автоматически получится T_J по построению 
    # (потому что chord1_dir — угловой бисектор T0 и T_J, и арка отражает 
    # T0 относительно хорды → получаем T_J).
    arc1 = arc_from_tangent(p0, j, t0)
    if arc1 is None:
        return None
    
    # Вторая дуга: J → P3 с касательной T_J в J.
    # Аналогично касательная в конце автоматически получится T1.
    arc2 = arc_from_tangent(j, p3, t_j)
    if arc2 is None:
        return None
    
    return (arc1, arc2)


# ─────────────────────────────────────────────────────────────────────────
#  ИЗМЕРЕНИЕ ОТКЛОНЕНИЯ
# ─────────────────────────────────────────────────────────────────────────

def _point_to_segment_distance(p: Point, seg: Segment) -> float:
    """Расстояние от точки до сегмента (Line или Arc)."""
    if isinstance(seg, Line):
        a, b = seg.a, seg.b
        ab = vec_sub(b, a)
        ap = vec_sub(p, a)
        l2 = vec_dot(ab, ab)
        if l2 < EPS:
            return vec_dist(p, a)
        t = max(0.0, min(1.0, vec_dot(ap, ab) / l2))
        proj = (a[0] + t*ab[0], a[1] + t*ab[1])
        return vec_dist(p, proj)
    else:  # Arc
        # Расстояние от точки до окружности = ||p - center| - R|
        # Но дуга — только часть окружности. Если проекция на окружность
        # лежит внутри дугового угла — это и есть кратчайшее. Иначе — до ближайшего конца.
        cp = vec_sub(p, seg.center)
        cp_len = vec_len(cp)
        if cp_len < EPS:
            return seg.radius  # точка в центре — расстояние = радиус
        # Проверим, лежит ли проекция в пределах дуги
        ang_p = math.atan2(cp[1], cp[0])
        va = vec_sub(seg.a, seg.center)
        vb = vec_sub(seg.b, seg.center)
        ang_a = math.atan2(va[1], va[0])
        ang_b = math.atan2(vb[1], vb[0])
        # Нормализуем по направлению
        if seg.ccw:
            # Идём от ang_a к ang_b в направлении CCW
            d_total = (ang_b - ang_a) % (2*math.pi)
            d_p = (ang_p - ang_a) % (2*math.pi)
        else:
            d_total = (ang_a - ang_b) % (2*math.pi)
            d_p = (ang_a - ang_p) % (2*math.pi)
        if 0 <= d_p <= d_total:
            return abs(cp_len - seg.radius)
        else:
            # Берём ближайший конец дуги
            return min(vec_dist(p, seg.a), vec_dist(p, seg.b))


def _max_deviation(bez: Bezier, segs: List[Segment], n_samples: int = ERROR_SAMPLES) -> float:
    """Максимальное отклонение точек Безье от ближайшего сегмента биарка."""
    max_err = 0.0
    for i in range(1, n_samples):  # пропускаем концы — они совпадают
        t = i / n_samples
        p = bez.point_at(t)
        d = min(_point_to_segment_distance(p, s) for s in segs)
        if d > max_err:
            max_err = d
    return max_err


# ─────────────────────────────────────────────────────────────────────────
#  ОСНОВНАЯ ФУНКЦИЯ: BEZIER → СПИСОК СЕГМЕНТОВ (LINE/ARC)
# ─────────────────────────────────────────────────────────────────────────

def fit_bezier(bez: Bezier, tolerance: float = DEFAULT_TOLERANCE,
               _depth: int = 0) -> List[Segment]:
    """Аппроксимирует кубическую Безье последовательностью отрезков и дуг.
    
    tolerance: максимально допустимое отклонение от исходной кривой (мм).
    Возвращает список Segment (Line/Arc), последовательно образующих путь.
    """
    # Случай 1: вырожденная кривая (точка)
    if bez.is_degenerate():
        return []
    
    # Случай 2: кривая — фактически прямая
    if bez.is_line(tol=tolerance):
        # Только если длина не нулевая
        if vec_dist(bez.p0, bez.p3) > EPS:
            return [Line(bez.p0, bez.p3)]
        return []
    
    # Получаем касательные в начале и конце
    t0 = bez.tangent_at(0.0)
    t1 = bez.tangent_at(1.0)
    
    # Случай 3: попытка собрать биарк
    biarc = _equal_chord_biarc(bez.p0, t0, bez.p3, t1)
    
    if biarc is not None:
        segs = list(biarc)
        err = _max_deviation(bez, segs)
        if err <= tolerance:
            return segs
    
    # Случай 4: либо биарк не построился, либо отклонение слишком велико → делим
    if _depth >= MAX_SUBDIVISION:
        # Защита от бесконечной рекурсии — отдаём что получилось (или хорду)
        return [Line(bez.p0, bez.p3)]
    
    left, right = bez.split(0.5)
    return fit_bezier(left, tolerance, _depth + 1) + \
           fit_bezier(right, tolerance, _depth + 1)
