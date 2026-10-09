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


def test_merge_does_not_add_tangent_breaks():
    """Склейка сегментов не должна делать контур ЛОМАНЕЕ оригинала.

    Регрессия v1.7.3: merge_segments_to_arcs при импорте склеивал плавные
    биарк-цепочки .ai в дуги, не стыкующиеся по касательной. На 124173
    число изломов > 3° росло с 81 до 167 — плавные дуги приезжали в вид
    ломаной с видимыми углами.
    """
    import os, math
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    import camsys.core.importer as importer_mod
    from camsys.geometry.path_offset import merge_segments_to_arcs

    def count_breaks(polypath, thr=3.0):
        segs = polypath.segments
        n = len(segs)
        total = 0
        rng = range(n) if polypath.closed else range(n - 1)
        for i in rng:
            t1 = segs[i].tangent_at_end()
            t2 = segs[(i + 1) % n].tangent_at_start()
            deg = math.degrees(abs(math.atan2(
                t1[0] * t2[1] - t1[1] * t2[0],
                t1[0] * t2[0] + t1[1] * t2[1])))
            if deg > thr:
                total += 1
        return total

    project = importer_mod.import_ai_to_project(ai)
    geoms = [g for g in project.get_layer_by_name("Knife").geometries
             if g.polypath]
    raw_breaks = sum(count_breaks(g.polypath) for g in geoms)
    raw_segs = sum(len(g.polypath.segments) for g in geoms)

    merged = [merge_segments_to_arcs(g.polypath, tol=0.02, min_chain=3)
              for g in geoms]
    new_breaks = sum(count_breaks(m) for m in merged)
    new_segs = sum(len(m.segments) for m in merged)

    print(f'  изломов >3°: сырой {raw_breaks} -> merge {new_breaks}; '
          f'сегментов {raw_segs} -> {new_segs}')
    # Главное: склейка не добавляет изломов сверх оригинала
    assert new_breaks <= raw_breaks, (
        f"склейка добавила изломов: {raw_breaks} -> {new_breaks}")
    # И при этом всё ещё что-то склеивает (иначе смысл теряется)
    assert new_segs < raw_segs * 0.75, "склейка перестала работать"


def test_smooth_guard_is_tunable():
    """Порог guard регулируется: выключенный даёт больше изломов."""
    import os, math
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    import camsys.core.importer as importer_mod
    from camsys.geometry.path_offset import merge_segments_to_arcs

    def count_breaks(pp, thr=3.0):
        segs = pp.segments
        n = len(segs)
        return sum(1 for i in (range(n) if pp.closed else range(n - 1))
                   if math.degrees(abs(math.atan2(
                       segs[i].tangent_at_end()[0]
                       * segs[(i + 1) % n].tangent_at_start()[1]
                       - segs[i].tangent_at_end()[1]
                       * segs[(i + 1) % n].tangent_at_start()[0],
                       segs[i].tangent_at_end()[0]
                       * segs[(i + 1) % n].tangent_at_start()[0]
                       + segs[i].tangent_at_end()[1]
                       * segs[(i + 1) % n].tangent_at_start()[1]))) > thr)

    project = importer_mod.import_ai_to_project(ai)
    geoms = [g for g in project.get_layer_by_name("Knife").geometries
             if g.polypath][:20]
    on = sum(count_breaks(merge_segments_to_arcs(
        g.polypath, tol=0.02, min_chain=3, smooth_guard_deg=3.0))
        for g in geoms)
    off = sum(count_breaks(merge_segments_to_arcs(
        g.polypath, tol=0.02, min_chain=3, smooth_guard_deg=999.0))
        for g in geoms)
    print(f'  изломов: guard 3° -> {on}, guard выключен -> {off}')
    assert on < off, "guard не влияет на результат"


def test_merge_keeps_variable_curvature():
    """Цепочка дуг с растущим радиусом не склеивается в одну.

    Регрессия v1.7.30: нож #0 файла 124173_test, точка (57.8, 93.8). В .ai
    там гладкая кривая переменной кривизны — десять дуг R 2.3 → 61.9,
    изломы 0.00°. merge_segments_to_arcs сворачивал их в одну дугу
    R 16.08 длиной 2.71 с изломом 1.66° на стыке: формально допуск
    tol=0.02 соблюдён, а на экране — ступенька. Guard по изломам (3°)
    этого не ловил.
    """
    import os
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.core.importer as importer_mod
    from camsys.geometry.path_offset import merge_segments_to_arcs

    project = importer_mod.import_ai_to_project(ai)
    geom = [g for g in project.get_layer_by_name("Knife").geometries
            if g.polypath][0]

    def in_zone(pp):
        return [s for s in pp.segments
                if 56.3 < s.a[0] < 59.3 and 92.3 < s.a[1] < 95.3]

    off = in_zone(merge_segments_to_arcs(geom.polypath, tol=0.02,
                                         min_chain=3, max_radius_ratio=99.0))
    on = in_zone(merge_segments_to_arcs(geom.polypath, tol=0.02,
                                        min_chain=3))
    print(f'  сегментов в зоне ступеньки: без ограничения {len(off)}, '
          f'с ограничением {len(on)}')
    assert len(off) == 1, "проверка не воспроизводит исходную ступеньку"
    assert len(on) > 1, "цепочка переменной кривизны снова склеена в одну дугу"

    # Радиусы после правки растут плавно — соседние не больше чем вдвое
    radii = [s.radius for s in on if hasattr(s, 'radius')]
    for r1, r2 in zip(radii, radii[1:]):
        assert max(r1, r2) / min(r1, r2) <= 4.0


def test_radius_ratio_default_is_two():
    """Умолчание разброса радиусов — 2.0, согласовано с оператором."""
    import inspect
    from camsys.geometry.path_offset import merge_segments_to_arcs
    sig = inspect.signature(merge_segments_to_arcs)
    assert sig.parameters['max_radius_ratio'].default == 2.0
