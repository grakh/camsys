"""Тесты geometry/path_offset — смещение точки старта вдоль контура."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geometry.primitives import Line, Arc, Polypath, vec_dist, EPS
from geometry.path_offset import (
    polypath_total_length, point_and_tangent_at_distance,
    shift_start_along_contour,
)


def approx(a, b, tol=1e-6):
    return abs(a - b) < tol


def test_total_length_square():
    """Длина квадрата 1×1 = 4."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    assert approx(polypath_total_length(poly), 4.0)


def test_point_at_distance_zero():
    """В точке 0 — начало контура."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    result = point_and_tangent_at_distance(poly, 0.0)
    assert result is not None
    point, tangent, idx, t = result
    assert vec_dist(point, (0, 0)) < EPS
    assert idx == 0


def test_point_at_distance_half():
    """В точке 0.5 квадрата — середина первого ребра."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    result = point_and_tangent_at_distance(poly, 0.5)
    point, tangent, idx, t = result
    assert vec_dist(point, (0.5, 0)) < EPS
    assert idx == 0
    assert approx(t, 0.5)


def test_point_at_distance_full_edge():
    """В точке ровно 1.0 — на стыке первого и второго ребра."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    result = point_and_tangent_at_distance(poly, 1.0)
    point, tangent, idx, t = result
    assert vec_dist(point, (1, 0)) < EPS


def test_point_at_distance_negative_closed():
    """Отрицательное расстояние на замкнутом контуре = циклический сдвиг назад."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    # -0.5 от начала = 0.5 единиц назад = (0, 0.5) (на последнем ребре)
    result = point_and_tangent_at_distance(poly, -0.5)
    point, tangent, idx, t = result
    assert vec_dist(point, (0, 0.5)) < EPS, f"Получили {point}"


def test_shift_start_zero_no_change():
    """Сдвиг 0 = тот же контур."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    shifted = shift_start_along_contour(poly, 0.0)
    assert len(shifted.segments) == len(poly.segments)
    assert vec_dist(shifted.segments[0].a, poly.segments[0].a) < EPS


def test_shift_start_half_edge():
    """Сдвиг на 0.5 = новое начало в середине первого ребра, контур замыкается."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    shifted = shift_start_along_contour(poly, 0.5)
    
    # Новое начало — (0.5, 0)
    assert vec_dist(shifted.segments[0].a, (0.5, 0)) < EPS
    
    # Длина контура должна сохраниться
    assert approx(polypath_total_length(shifted), 
                  polypath_total_length(poly), tol=1e-6)
    
    # Конец последнего сегмента возвращается в (0.5, 0)
    last = shifted.segments[-1]
    assert vec_dist(last.b, (0.5, 0)) < EPS


def test_shift_start_to_vertex():
    """Сдвиг на 1.0 — стартуем точно с угла (1, 0)."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    shifted = shift_start_along_contour(poly, 1.0)
    
    assert vec_dist(shifted.segments[0].a, (1, 0)) < EPS
    # Контур должен иметь по-прежнему 4 ребра
    assert len(shifted.segments) == 4


def test_shift_start_negative():
    """Сдвиг -0.5 = циклически на 0.5 назад = (0, 0.5)."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    shifted = shift_start_along_contour(poly, -0.5)
    assert vec_dist(shifted.segments[0].a, (0, 0.5)) < EPS


def test_shift_does_not_mutate():
    """Сдвиг не модифицирует исходный."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    
    orig_first_a = poly.segments[0].a
    _ = shift_start_along_contour(poly, 0.5)
    assert poly.segments[0].a == orig_first_a


def test_two_shifts_different_start_points():
    """Два разных смещения должны дать разные точки старта.
    Это и есть случай INSIDE с offset=-5 vs OUTSIDE с offset=-4."""
    poly = Polypath(segments=[
        Line((0, 0), (10, 0)),  # длинное ребро для удобства
        Line((10, 0), (10, 1)),
        Line((10, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    
    shifted_minus_5 = shift_start_along_contour(poly, -5.0)
    shifted_minus_4 = shift_start_along_contour(poly, -4.0)
    
    p_5 = shifted_minus_5.segments[0].a
    p_4 = shifted_minus_4.segments[0].a
    
    # Точки должны различаться
    assert vec_dist(p_5, p_4) > EPS
    # На прямой длиной 10 от начала, при сдвиге -5 (=5 назад от старта)
    # точка лежит на последнем сегменте (0,1)→(0,0): 22 - 5 = 17 = 1+10+1+5 → (0, 0.0)? 
    # Длина контура = 10+1+10+1 = 22. shift -5 → distance 17. 
    #   После 10 (первое ребро) accum=10, +1 (второе) = 11, +10 (третье) = 21, 
    #   distance=17 на третьем ребре: 17-11 = 6 от начала третьего ребра (10,1)→(0,1)
    #   → точка (10-6, 1) = (4, 1)
    # Аналогично для shift -4: distance 18 → 18-11=7 на третьем ребре → (3, 1)
    # Между ними расстояние = 1 (как разница в offset)
    assert approx(vec_dist(p_5, p_4), 1.0, tol=1e-6)


# ─────────────────────────────────────────────────────────────────────────
#  ТЕСТЫ ВЫБОРА СТАРТОВОЙ ТОЧКИ У УГЛА BBOX
# ─────────────────────────────────────────────────────────────────────────
from geometry.path_offset import (polypath_bbox, point_to_segment_distance,
                                    find_closest_point_on_polypath,
                                    distance_along_polypath, shift_start_to_corner,
                                    lead_distance_to_contour)


def test_bbox_square():
    """Bbox квадрата 0..10 × 0..10."""
    poly = Polypath(segments=[
        Line((0, 0), (10, 0)), Line((10, 0), (10, 10)),
        Line((10, 10), (0, 10)), Line((0, 10), (0, 0)),
    ], closed=True)
    bb = polypath_bbox(poly)
    assert bb == (0, 0, 10, 10), f"Bbox: {bb}"


def test_point_to_line_distance():
    """Точка (5, 3) к линии y=0: расстояние 3."""
    seg = Line((0, 0), (10, 0))
    d, pt = point_to_segment_distance((5, 3), seg)
    assert approx(d, 3.0)
    assert approx(pt[0], 5.0) and approx(pt[1], 0.0)


def test_closest_point_on_polypath():
    """На квадрате (0,0)-(10,10) ближайшая к (15, 5) точка = (10, 5)."""
    poly = Polypath(segments=[
        Line((0, 0), (10, 0)), Line((10, 0), (10, 10)),
        Line((10, 10), (0, 10)), Line((0, 10), (0, 0)),
    ], closed=True)
    pt, idx, t, d = find_closest_point_on_polypath(poly, (15, 5))
    assert approx(pt[0], 10) and approx(pt[1], 5), f"Точка: {pt}"
    assert approx(d, 5)


def test_shift_to_RT_corner():
    """shift_start_to_corner('RT') ставит начало в правый верхний угол."""
    poly = Polypath(segments=[
        Line((0, 0), (10, 0)), Line((10, 0), (10, 10)),
        Line((10, 10), (0, 10)), Line((0, 10), (0, 0)),
    ], closed=True)
    # RT угол bbox = (10, 10). Ближайшая точка контура — вершина (10,10).
    shifted = shift_start_to_corner(poly, "RT")
    assert vec_dist(shifted.segments[0].a, (10, 10)) < EPS, \
        f"Старт: {shifted.segments[0].a}"


def test_shift_to_LB_corner():
    """shift_start_to_corner('LB') ставит начало в левый нижний угол."""
    poly = Polypath(segments=[
        Line((5, 5), (15, 5)), Line((15, 5), (15, 15)),
        Line((15, 15), (5, 15)), Line((5, 15), (5, 5)),
    ], closed=True)
    # LB = (5, 5)
    shifted = shift_start_to_corner(poly, "LB")
    assert vec_dist(shifted.segments[0].a, (5, 5)) < EPS, \
        f"Старт: {shifted.segments[0].a}"


def test_lead_distance_far_from_contour():
    """Заход далеко от контура — большое расстояние."""
    poly = Polypath(segments=[
        Line((0, 0), (10, 0)), Line((10, 0), (10, 10)),
        Line((10, 10), (0, 10)), Line((0, 10), (0, 0)),
    ], closed=True)
    # Заход у точки (0,0), направлен от контура вниз-влево
    from geometry.primitives import Line as Ln
    lead = [Ln((-5, -5), (-1, -1))]
    d = lead_distance_to_contour(lead, poly)
    assert d > 0.5, f"Расстояние: {d}"


def test_lead_distance_passes_through_contour():
    """Заход пересекает контур — расстояние близко к 0."""
    poly = Polypath(segments=[
        Line((0, 0), (10, 0)), Line((10, 0), (10, 10)),
        Line((10, 10), (0, 10)), Line((0, 10), (0, 0)),
    ], closed=True)
    # Заход пересекает левую сторону квадрата (x=0): идёт из (-5, 5) в (5, 5)
    from geometry.primitives import Line as Ln
    lead = [Ln((-5, 5), (5, 5))]
    d = lead_distance_to_contour(lead, poly, skip_first_n=0, skip_last_n=0)
    assert d < 0.5, f"Должно быть пересечение, d={d}"


if __name__ == "__main__":
    import inspect
    tests = [(n,f) for n,f in inspect.getmembers(sys.modules[__name__])
             if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for n, f in tests:
        try:
            f()
            passed += 1
            print(f"  [OK] {n}")
        except Exception as e:
            failed.append((n, e))
            print(f"  [FAIL] {n}: {e!r}")
            import traceback; traceback.print_exc()
    print(f"\n{passed}/{len(tests)} тестов пройдено")
    sys.exit(0 if not failed else 1)
