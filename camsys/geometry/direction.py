"""
geometry/direction.py — определение и нормализация направления обхода
замкнутого контура.

Алгоритм определения CW/CCW:
    Используем формулу площади со знаком (shoelace formula).
    Для полигона со знаком: A = 1/2 · sum((x_i · y_{i+1}) − (x_{i+1} · y_i))
    Если A > 0 → обход CCW (против часовой)
    Если A < 0 → обход CW (по часовой)
    
    Для контура из Line + Arc сегментов:
    - Line дискретизирован как отрезок (a → b)
    - Arc — берём начальную и конечную точки, плюс точку посередине дуги
      для приблизительного учёта вклада дуги в площадь.

Нормализация:
    - Если хотим OUTSIDE → нужно CCW (по правилу: металл справа от пути).
    - Если хотим INSIDE  → нужно CW.
    - Реверс: меняем порядок сегментов и направление каждого сегмента
      (поменять a и b у Line, перевернуть ccw флаг у Arc).
"""

from __future__ import annotations
from typing import List
import math
from dataclasses import replace

from .primitives import Line, Arc, Polypath, Segment, vec_dist


# ─────────────────────────────────────────────────────────────────────────
#  ПЛОЩАДЬ КОНТУРА СО ЗНАКОМ
# ─────────────────────────────────────────────────────────────────────────

def signed_area(polypath: Polypath, arc_samples: int = 8) -> float:
    """Площадь со знаком замкнутого контура.
    
    Положительная = CCW (против часовой)
    Отрицательная = CW (по часовой)
    
    Для Line: точная формула трапеции.
    Для Arc: дискретизация на arc_samples отрезков для приближения.
    
    Возвращает 0 если контур пуст или не замкнут.
    """
    if not polypath or not polypath.segments:
        return 0.0
    
    # Сначала развернём контур в список точек
    points: List[tuple] = []
    
    first_seg = polypath.segments[0]
    points.append(first_seg.a)
    
    for seg in polypath.segments:
        if isinstance(seg, Line):
            points.append(seg.b)
        elif isinstance(seg, Arc):
            # Дискретизация дуги на отрезки
            for i in range(1, arc_samples + 1):
                t = i / arc_samples
                pt = seg.point_at(t)
                points.append(pt)
    
    # Замыкаем если контур помечен как closed и последняя != первой
    if polypath.closed and vec_dist(points[0], points[-1]) > 1e-9:
        points.append(points[0])
    
    # Shoelace formula
    a = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        a += (x1 * y2) - (x2 * y1)
    
    return a / 2.0


def is_ccw(polypath: Polypath) -> bool:
    """True если контур обходится против часовой стрелки."""
    return signed_area(polypath) > 0


def is_cw(polypath: Polypath) -> bool:
    """True если контур обходится по часовой стрелке."""
    return signed_area(polypath) < 0


# ─────────────────────────────────────────────────────────────────────────
#  РЕВЕРС НАПРАВЛЕНИЯ
# ─────────────────────────────────────────────────────────────────────────

def reverse_segment(seg: Segment) -> Segment:
    """Развернуть направление одного сегмента."""
    if isinstance(seg, Line):
        return Line(a=seg.b, b=seg.a)
    elif isinstance(seg, Arc):
        return Arc(
            a=seg.b,
            b=seg.a,
            center=seg.center,
            ccw=not seg.ccw,  # CCW дуга в обратном направлении = CW
        )
    raise TypeError(f"Unsupported segment type: {type(seg).__name__}")


def reverse_polypath(polypath: Polypath) -> Polypath:
    """Возвращает новый Polypath с обратным направлением обхода.
    
    Не модифицирует исходный.
    """
    return Polypath(
        segments=[reverse_segment(s) for s in reversed(polypath.segments)],
        closed=polypath.closed,
    )


# ─────────────────────────────────────────────────────────────────────────
#  НОРМАЛИЗАЦИЯ ПОД СТОРОНУ ОБРАБОТКИ
# ─────────────────────────────────────────────────────────────────────────

def ensure_ccw(polypath: Polypath) -> Polypath:
    """Гарантирует, что контур обходится против часовой.
    
    Если он уже CCW — возвращает исходный.
    Если CW — реверсит.
    """
    if is_ccw(polypath):
        return polypath
    return reverse_polypath(polypath)


def ensure_cw(polypath: Polypath) -> Polypath:
    """Гарантирует, что контур обходится по часовой."""
    if is_cw(polypath):
        return polypath
    return reverse_polypath(polypath)


def normalize_for_side(polypath: Polypath, side: str) -> Polypath:
    """Возвращает контур с направлением, согласованным со стороной обработки.
    
    Правило (зафиксировано пользователем как производственный стандарт):
        внутренний рез → CW  (по часовой)
        внешний рез   → CCW (против часовой)
    
    После фикса конвенции компенсации (оба прохода G41):
        ContourSide.OUTSIDE — это ВНУТРЕННИЙ рез → CW
        ContourSide.INSIDE  — это ВНЕШНИЙ рез   → CCW
    
    Args:
        polypath: исходный контур (направление любое)
        side: 'OUTSIDE' / 'INSIDE' (case-insensitive)
    
    Returns:
        Контур с правильным направлением (новый объект, исходный не меняется).
    """
    s = side.upper()
    if s == 'OUTSIDE':
        # OUTSIDE = внутренний рез → CW
        return ensure_cw(polypath)
    elif s == 'INSIDE':
        # INSIDE = внешний рез → CCW
        return ensure_ccw(polypath)
    else:
        # Для открытых путей (LEFT/RIGHT) направление определяется художником
        # и не нормализуется автоматически
        return polypath
