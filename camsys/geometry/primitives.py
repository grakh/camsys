"""
geometry/primitives.py — базовые гео-примитивы для CAM.

Внутреннее представление путей:
    Segment  — общий тип сегмента (Line | Arc)
    Line     — отрезок A→B
    Arc      — дуга со стартом, концом, центром и направлением
    Bezier   — кубическая кривая Безье (4 контрольные точки)
    Polypath — последовательность сегментов (Line/Arc) одного пути

Все координаты в миллиметрах, тип float (64-бит).
Точность: внутренние сравнения с EPS = 1e-9 мм (пикометры),
финальный вывод в .anc — 5 знаков после запятой (10 нм).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Union, Optional
import math


EPS = 1e-9  # пикометры — для геометрических сравнений
Point = Tuple[float, float]


# ─────────────────────────────────────────────────────────────────────────
#  ВЕКТОРНАЯ АРИФМЕТИКА
# ─────────────────────────────────────────────────────────────────────────

def vec_add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def vec_sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def vec_mul(a: Point, k: float) -> Point:
    return (a[0] * k, a[1] * k)


def vec_dot(a: Point, b: Point) -> float:
    return a[0]*b[0] + a[1]*b[1]


def vec_cross(a: Point, b: Point) -> float:
    """2D-кросс: положительный = b слева от a (CCW)."""
    return a[0]*b[1] - a[1]*b[0]


def vec_len(a: Point) -> float:
    return math.hypot(a[0], a[1])


def vec_norm(a: Point) -> Point:
    l = vec_len(a)
    if l < EPS:
        return (0.0, 0.0)
    return (a[0]/l, a[1]/l)


def vec_perp(a: Point) -> Point:
    """Перпендикуляр, повёрнутый против часовой на 90°."""
    return (-a[1], a[0])


def vec_dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ─────────────────────────────────────────────────────────────────────────
#  СЕГМЕНТЫ
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Line:
    """Отрезок A → B."""
    a: Point
    b: Point

    def length(self) -> float:
        return vec_dist(self.a, self.b)

    def tangent_at_start(self) -> Point:
        """Единичный касательный вектор в начале."""
        return vec_norm(vec_sub(self.b, self.a))

    def tangent_at_end(self) -> Point:
        return self.tangent_at_start()

    def point_at(self, t: float) -> Point:
        """t ∈ [0,1]: точка на отрезке."""
        return (self.a[0] + t * (self.b[0] - self.a[0]),
                self.a[1] + t * (self.b[1] - self.a[1]))


@dataclass
class Arc:
    """Дуга со стартом, концом, центром и направлением (CCW=True / CW=False)."""
    a: Point
    b: Point
    center: Point
    ccw: bool  # True = против часовой (G3), False = по часовой (G2)

    @property
    def radius(self) -> float:
        return vec_dist(self.center, self.a)

    def length(self) -> float:
        r = self.radius
        if r < EPS:
            return 0.0
        # Угол от center→A до center→B, со знаком
        va = vec_sub(self.a, self.center)
        vb = vec_sub(self.b, self.center)
        ang_a = math.atan2(va[1], va[0])
        ang_b = math.atan2(vb[1], vb[0])
        delta = ang_b - ang_a
        if self.ccw:
            # CCW: delta должна быть положительной
            while delta < 0:
                delta += 2*math.pi
        else:
            # CW: delta должна быть отрицательной
            while delta > 0:
                delta -= 2*math.pi
            delta = -delta
        return r * delta

    def tangent_at_start(self) -> Point:
        """Единичный касательный вектор в начале дуги."""
        radial = vec_norm(vec_sub(self.a, self.center))
        # Касательная = перпендикуляр к радиусу. CCW = +90°, CW = -90°
        if self.ccw:
            return (-radial[1], radial[0])
        else:
            return (radial[1], -radial[0])

    def tangent_at_end(self) -> Point:
        radial = vec_norm(vec_sub(self.b, self.center))
        if self.ccw:
            return (-radial[1], radial[0])
        else:
            return (radial[1], -radial[0])

    def point_at(self, t: float) -> Point:
        """t ∈ [0,1]: точка на дуге по параметру длины."""
        r = self.radius
        if r < EPS:
            return self.a
        va = vec_sub(self.a, self.center)
        vb = vec_sub(self.b, self.center)
        ang_a = math.atan2(va[1], va[0])
        ang_b = math.atan2(vb[1], vb[0])
        delta = ang_b - ang_a
        if self.ccw:
            while delta < 0:
                delta += 2*math.pi
        else:
            while delta > 0:
                delta -= 2*math.pi
        ang = ang_a + delta * t
        return (self.center[0] + r * math.cos(ang),
                self.center[1] + r * math.sin(ang))


Segment = Union[Line, Arc]


# ─────────────────────────────────────────────────────────────────────────
#  КУБИЧЕСКАЯ КРИВАЯ БЕЗЬЕ
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Bezier:
    """Кубическая кривая Безье: P0 → P1 (control) → P2 (control) → P3."""
    p0: Point
    p1: Point
    p2: Point
    p3: Point

    def point_at(self, t: float) -> Point:
        """B(t) = (1-t)³ P0 + 3(1-t)²t P1 + 3(1-t)t² P2 + t³ P3"""
        u = 1.0 - t
        u2 = u * u
        t2 = t * t
        x = u2*u*self.p0[0] + 3*u2*t*self.p1[0] + 3*u*t2*self.p2[0] + t2*t*self.p3[0]
        y = u2*u*self.p0[1] + 3*u2*t*self.p1[1] + 3*u*t2*self.p2[1] + t2*t*self.p3[1]
        return (x, y)

    def tangent_at(self, t: float) -> Point:
        """B'(t) = 3(1-t)² (P1-P0) + 6(1-t)t (P2-P1) + 3t² (P3-P2)"""
        u = 1.0 - t
        dx = 3*u*u*(self.p1[0]-self.p0[0]) + 6*u*t*(self.p2[0]-self.p1[0]) + 3*t*t*(self.p3[0]-self.p2[0])
        dy = 3*u*u*(self.p1[1]-self.p0[1]) + 6*u*t*(self.p2[1]-self.p1[1]) + 3*t*t*(self.p3[1]-self.p2[1])
        l = math.hypot(dx, dy)
        if l < EPS:
            # Вырожденная касательная — берём фолбэк через хорду
            dx = self.p3[0] - self.p0[0]
            dy = self.p3[1] - self.p0[1]
            l = math.hypot(dx, dy)
            if l < EPS:
                return (1.0, 0.0)
        return (dx/l, dy/l)

    def split(self, t: float) -> Tuple["Bezier", "Bezier"]:
        """Алгоритм де Кастельжо: разделить кривую в параметре t на две."""
        u = 1.0 - t
        # Линейные интерполяции
        p01 = (u*self.p0[0] + t*self.p1[0], u*self.p0[1] + t*self.p1[1])
        p12 = (u*self.p1[0] + t*self.p2[0], u*self.p1[1] + t*self.p2[1])
        p23 = (u*self.p2[0] + t*self.p3[0], u*self.p2[1] + t*self.p3[1])
        p012 = (u*p01[0] + t*p12[0], u*p01[1] + t*p12[1])
        p123 = (u*p12[0] + t*p23[0], u*p12[1] + t*p23[1])
        m = (u*p012[0] + t*p123[0], u*p012[1] + t*p123[1])
        return (
            Bezier(self.p0, p01, p012, m),
            Bezier(m, p123, p23, self.p3),
        )

    def is_degenerate(self, tol: float = EPS) -> bool:
        """Все 4 точки совпадают — фактически точка."""
        return (vec_dist(self.p0, self.p3) < tol and
                vec_dist(self.p1, self.p3) < tol and
                vec_dist(self.p2, self.p3) < tol)

    def is_line(self, tol: float = 1e-7) -> bool:
        """Контрольные точки лежат на хорде — это прямая."""
        chord = vec_sub(self.p3, self.p0)
        chord_len = vec_len(chord)
        if chord_len < EPS:
            return self.is_degenerate(tol)
        n = vec_perp(vec_norm(chord))  # нормаль к хорде
        d1 = abs(vec_dot(vec_sub(self.p1, self.p0), n))
        d2 = abs(vec_dot(vec_sub(self.p2, self.p0), n))
        return d1 < tol and d2 < tol


# ─────────────────────────────────────────────────────────────────────────
#  ПУТЬ: последовательность Line/Arc
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Polypath:
    """Путь — последовательность Line и Arc сегментов.
    Это окончательное представление, которое идёт в .anc."""
    segments: List[Segment] = field(default_factory=list)
    closed: bool = False

    def start_point(self) -> Optional[Point]:
        if not self.segments:
            return None
        s = self.segments[0]
        return s.a

    def end_point(self) -> Optional[Point]:
        if not self.segments:
            return None
        s = self.segments[-1]
        return s.b

    def length(self) -> float:
        return sum(s.length() for s in self.segments)
