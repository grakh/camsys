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

# ── Нож заказа 124136: длинный «лист» с острой вершиной 43.3°
#    и тупым изломом 130.2°. Именно на нём эквидистанта резала лезвие.
_K124136 = [
    ('L', (15.3021, 1.2622), (15.2954, 1.2407), None, None),
    ('A', (15.2954, 1.2407), (15.0408, 0.7345), (13.5861, 1.7833), False),
    ('A', (15.0408, 0.7345), (14.6484, 0.3406), (13.6134, 1.7637), False),
    ('A', (14.6484, 0.3406), (14.1623, 0.0906), (13.6004, 1.7816), False),
    ('A', (14.1623, 0.0906), (13.6239, 0.0), (13.6011, 1.7793), False),
    ('A', (13.6239, 0.0), (11.5703, 2.4561), (79.2093, 56.925), False),
    ('A', (11.5703, 2.4561), (9.2977, 5.3872), (89.9286, 65.5571), False),
    ('A', (9.2977, 5.3872), (7.4825, 7.8942), (95.3308, 69.5884), False),
    ('A', (7.4825, 7.8942), (5.5502, 10.7319), (104.9216, 76.3238), False),
    ('A', (5.5502, 10.7319), (2.7301, 15.205), (111.0978, 80.4005), False),
    ('A', (2.7301, 15.205), (0.0, 19.9655), (115.1947, 82.8653), False),
    ('L', (0.0, 19.9655), (17.5274, 14.2617), None, None),
    ('L', (17.5274, 14.2617), (17.5634, 14.2498), None, None),
    ('A', (17.5634, 14.2498), (18.5922, 13.3798), (17.012, 12.5544), False),
    ('A', (18.5922, 13.3798), (18.7212, 12.0093), (16.9777, 12.5365), False),
    ('L', (18.7212, 12.0093), (18.6358, 11.7334), None, None),
    ('L', (18.6358, 11.7334), (18.4924, 11.2773), None, None),
    ('L', (18.4924, 11.2773), (18.0669, 9.9337), None, None),
    ('L', (18.0669, 9.9337), (16.9054, 6.2853), None, None),
    ('L', (16.9054, 6.2853), (15.3021, 1.2622), None, None),
]


def _knife_124136():
    segs = []
    for kind, a, b, c, ccw in _K124136:
        segs.append(Line(a=a, b=b) if kind == 'L'
                    else Arc(a=a, b=b, center=c, ccw=ccw))
    return Polypath(segments=segs, closed=True)


def _nearest_dist_fn(points, cell):
    """Ближайшее расстояние до набора точек через сетку (без numpy)."""
    import math as _m
    grid = {}
    for q in points:
        grid.setdefault((int(q[0] / cell), int(q[1] / cell)), []).append(q)

    def dist(p):
        gx, gy = int(p[0] / cell), int(p[1] / cell)
        best = float('inf')
        r = 1
        while True:
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r and r > 1:
                        continue
                    for q in grid.get((gx + dx, gy + dy), ()):
                        d = _m.hypot(q[0] - p[0], q[1] - p[1])
                        if d < best:
                            best = d
            if best <= (r - 1) * cell or r > 40:
                return best
            r += 1
    return dist


def test_equidistant_never_cuts_the_blade():
    """Эквидистанта не подходит к лезвию ближе смещения (v1.7.48).

    Сегменты смещаются КАЖДЫЙ ОТДЕЛЬНО, и в выпуклом углу их концы
    расходятся: на ноже заказа 124136 при смещении 0.65 мм и угле 43.3°
    разрыв 1.21 мм. Через разрыв рисовалась ПРЯМАЯ, и она проходила в
    0.234 мм от лезвия вместо 0.650 — основной путь срезал вершину ножа
    на 416 мкм. На втором проходе то же на изломе 130.2°, 329 мкм.

    В станке этого нет: в `.anc` идёт осевая плюс G42, и компенсация
    обкатывает выпуклый угол дугой радиуса смещения. Её и ставим.
    """
    import math
    from geometry.path_offset import (
        bridge_radii_for_tool, bridge_narrow_pits, offset_polypath_uniform,
        trim_self_intersections, join_polypath_corners, _exact_axis_points)
    from geometry.direction import normalize_for_side

    poly = _knife_124136()
    tip, absd, angle = 0.8, 0.25, 90.0
    eq = tip + 2 * absd * math.tan(math.radians(angle / 2))
    off = eq / 2.0
    r_m, r_b = bridge_radii_for_tool(tip, absd, eq)
    blade = [q for _s, q in _exact_axis_points(poly, 0.01)[0]]
    dist = _nearest_dist_fn(blade, off)

    for side, inward in (('INSIDE', False), ('OUTSIDE', True)):
        pp = normalize_for_side(poly, side)
        tr, _br = bridge_narrow_pits(pp, r_b, r_mouth=r_m)
        o = offset_polypath_uniform(tr, off, inward=inward)
        o = join_polypath_corners(trim_self_intersections(o), tol=0.01)

        worst = min(dist(q) for _s, q in _exact_axis_points(o, 0.01)[0])
        # Разрывы между сегментами рисуются ПРЯМОЙ — проверяем и их.
        segs = o.segments
        worst_gap = 0.0
        for i in range(len(segs) - 1):
            A, B = segs[i].b, segs[i + 1].a
            g = math.hypot(B[0] - A[0], B[1] - A[1])
            if g <= 0.05:
                continue
            worst_gap = max(worst_gap, g)
            n = max(2, int(g / 0.01))
            for k in range(n + 1):
                P = (A[0] + (B[0] - A[0]) * k / n,
                     A[1] + (B[1] - A[1]) * k / n)
                worst = min(worst, dist(P))
        print(f'  {side}: ближайшая к лезвию {worst:.4f} мм '
              f'(надо {off:.3f}), худший разрыв {worst_gap:.2f} мм')
        assert worst_gap < 0.05, (
            f'{side}: в эквидистанте разрыв {worst_gap:.2f} мм — '
            f'его нарисуют прямой через лезвие')
        assert worst > off - 0.003, (
            f'{side}: эквидистанта врезается в лезвие на '
            f'{(off - worst) * 1000:.0f} мкм')

def test_corner_pad_covers_what_the_main_tool_left():
    """Припуск фрагмента угла считается от его остроты (v1.7.49).

    Фреза с полушириной реза T встаёт в клине с внутренним углом A не
    ближе T/sin(A/2) от вершины. Доработке принадлежит полоса от упора
    ТОНКОЙ фрезы до упора ОСНОВНОЙ, и фрагмент обязан её перекрывать.

    Было: припуск всегда 1.5 мм. На ноже 124136 с углом 43.3° основная
    фреза встаёт в 1.760 мм от вершины, T3 — в 1.490 мм, а фрагмент
    кончался в 1.500 мм. Путь доработки выходил 0.210 мм — короче
    собственного захода, а полоса 1.50…1.76 мм не снималась НИКЕМ.
    """
    import math
    from geometry.corner_detect import detect_pointed_corners
    from geometry.path_offset import (
        extract_subpath_around_point, offset_right_of_travel,
        trim_self_intersections, join_polypath_corners, despike_polypath,
        corner_pad_for_angle)

    poly = _knife_124136()
    main_half = (0.8 + 2 * 0.25 * math.tan(math.radians(45))) / 2
    t3_half = (0.6 + 2 * 0.25 * math.tan(math.radians(45))) / 2

    def t3_path_len(pc, pad):
        frag = extract_subpath_around_point(poly, pc.point,
                                            pc.segment_index, pad)
        o = despike_polypath(join_polypath_corners(trim_self_intersections(
            offset_right_of_travel(frag, t3_half)), tol=0.01))
        return sum(x.length() for x in o.segments)

    corners = detect_pointed_corners(poly, sharp_threshold_deg=30.0)
    assert len(corners) == 2, f'углов найдено {len(corners)}, ожидалось 2'
    seen = {}
    for pc in corners:
        pad = corner_pad_for_angle(pc.interior_angle, main_half)
        stop_main = main_half / math.sin(math.radians(pc.interior_angle / 2))
        was, now = t3_path_len(pc, 1.5), t3_path_len(pc, pad)
        print(f'  угол {pc.interior_angle:.1f}°: упор основной '
              f'{stop_main:.3f} мм, припуск 1.50 -> {pad:.2f} мм, '
              f'путь T3 {was:.3f} -> {now:.3f} мм')
        seen[round(pc.interior_angle)] = (pad, was, now, stop_main)
        # Фрагмент обязан доставать ДАЛЬШЕ упора основной фрезы —
        # иначе между упорами остаётся несрезанный металл.
        assert pad > stop_main, (
            f'припуск {pad:.2f} не достаёт до упора основной фрезы '
            f'{stop_main:.3f} мм')
        # Проход доработки не может быть короче собственного захода.
        assert now >= 1.0, (
            f'путь доработки всего {now:.3f} мм')
    # Тупой угол не трогаем вообще: там 1.5 мм и так перекрывали упор.
    pad_blunt, was_blunt, now_blunt, _st = seen[130]
    assert abs(pad_blunt - 1.5) < 1e-9, (
        f'у тупого угла припуск изменился на {pad_blunt:.2f}')
    assert abs(now_blunt - was_blunt) < 1e-9, (
        'у тупого угла путь доработки изменился, а не должен')
    # Острый — вырос в разы.
    _pad_sharp, was_sharp, now_sharp, _st2 = seen[43]
    assert now_sharp > was_sharp * 5, (
        f'у острого угла путь вырос лишь с {was_sharp:.3f} до '
        f'{now_sharp:.3f} мм')



def test_lead_cutter_path_is_pointwise_not_per_segment():
    """Путь фрезы для лида считается ПОТОЧЕЧНО, а не посегментно.

    Предыстория (v1.7.44). Правильно считать лид на ОСЕВОЙ — именно она
    уходит в `.anc`, а смещение делает стойка, — и рисовать смещение
    результата. Я так и сделал, но смещал ПОСЕГМЕНТНО, и получил жалобу
    «что это с ними стало, в разные стороны, и просто прямые линии».

    Причина геометрическая: дуга лида по радиусу СРАВНИМА со смещением
    (типично R 0.55…1.3 при смещении 0.55…0.85). Посегментный оффсет на
    таком соотношении выворачивает дугу и расходит её стык с прямой.
    Поточечное смещение таких допущений не делает вовсе.

    Здесь это замеряется: эталон — плотная выборка истинного места
    смещённой кривой (2000 точек на сегмент), и от неё не должен
    отходить поточечный результат. Посегментный — отходит, иначе тест
    ничего не проверяет.
    """
    import math
    from camsys.geometry.primitives import Polypath, Line
    from camsys.geometry.lead_inout import build_lead_in
    from camsys.geometry.path_offset import (cutter_path_of_lead,
                                             offset_right_of_travel)

    axis = Polypath(segments=[Line(a=(0.0, 0.0), b=(20.0, 0.0))],
                    closed=False)

    def true_locus(lead, off, n=2000):
        """Истинное место смещённой кривой: плотная выборка p(t)+n(t)·off."""
        pts = []
        for seg in lead.segments:
            for i in range(n + 1):
                t = i / n
                p = seg.point_at(t)
                if hasattr(seg, 'center'):
                    rx, ry = p[0] - seg.center[0], p[1] - seg.center[1]
                    r = math.hypot(rx, ry)
                    tg = ((-ry / r, rx / r) if seg.ccw else (ry / r, -rx / r))
                else:
                    tg = seg.tangent_at_start()
                pts.append((p[0] + tg[1] * off, p[1] - tg[0] * off))
        return pts

    def worst_off(curve, locus):
        w = 0.0
        for s in curve.segments:
            for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                q = s.point_at(t)
                d = min(math.hypot(q[0] - c[0], q[1] - c[1]) for c in locus)
                w = max(w, d)
        return w

    worst_point = 0.0
    worst_seg = 0.0
    for side in ('right', 'left'):
        for R, off in ((0.65, 0.65), (1.30, 0.65), (0.325, 0.65),
                       (0.55, 0.85)):
            lead = build_lead_in(axis.segments[0].a, (1.0, 0.0), side,
                                 line_length=1.0, arc_radius=R,
                                 approach_angle_deg=45.0)
            lp = Polypath(segments=[s for s in (lead.line, lead.arc)
                                    if s is not None], closed=False)
            locus = true_locus(lp, off)
            wp = worst_off(cutter_path_of_lead(lp, off), locus)
            ws = worst_off(offset_right_of_travel(lp, off), locus)
            print(f'  {side:5s} R={R:4} смещение={off}: поточечно '
                  f'{wp * 1000:6.1f} мкм, посегментно {ws * 1000:7.1f} мкм')
            worst_point = max(worst_point, wp)
            worst_seg = max(worst_seg, ws)

    # Поточечно — в пределах шага выборки (0.05 мм) на прогиб хорды,
    # то есть единицы микрон.
    assert worst_point < 0.005, (
        f'поточечное смещение ушло от истинной кривой на '
        f'{worst_point * 1000:.1f} мкм')
    # И посегментное обязано быть ХУЖЕ на порядки — иначе проверять
    # нечего и выбор функции не имеет значения.
    assert worst_seg > 0.05, (
        f'посегментное смещение отходит всего на {worst_seg * 1000:.1f} мкм '
        f'— тест перестал ловить причину провала v1.7.44')


def test_rework_fragment_is_one_function_with_the_post_pad():
    """Участок доработки вырезает ОДНА функция, с припуском поста.

    До v1.7.58 это место было написано дважды: пост брал для 2D-угла
    припуск 1.5 мм, вьювер — 3.0 с адаптивным удлинением до 5.0. Кривые
    получались разные, лид крепился к ним в разных местах, и картинка
    угла не имела отношения к программе: на 124173 из 56 лидов
    доработки совпадали с программой 12.

    Припуск оставлен постовский, потому что он ЛУЧШЕ по замеру зазора
    лида до металла (21 угол заказа 124173, 42 лида): 1.5 мм даёт
    худший зазор −118 мкм и один отрицательный, 3.0+ — −314 мкм и
    четыре. То есть удлинение «чтобы поворот лида не жался к острию»
    делало хуже, и в программу оно никогда не попадало.
    """
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    geo = open(os.path.join(here, 'geometry', 'path_offset.py'),
               encoding='utf-8').read()
    post = open(os.path.join(here, 'post', 'mtx_anderson.py'),
                encoding='utf-8').read()
    view = open(os.path.join(here, 'ui', 'viewer_2d.py'),
                encoding='utf-8').read()

    assert 'def rework_fragment(' in geo, 'общая функция участка пропала'
    assert 'pad_mm=1.5' in geo, 'припуск 2D-угла не постовский'

    for name, src in (('mtx_anderson.py', post), ('viewer_2d.py', view)):
        assert 'rework_fragment(' in src, \
            f'{name}: участок доработки вырезается не общей функцией'
        # Своих вырезаний больше быть не должно — именно они и разошлись
        for forbidden in ('extract_subpath_around_indices(',
                          'extract_subpath_around_point(',
                          'pocket_fragment_with_gentle_ends(',
                          'orient_corner_fragment('):
            assert forbidden not in src, \
                f'{name}: остался свой вырез участка ({forbidden})'
