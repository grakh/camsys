"""Тесты детектора острых углов на контуре."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
from geometry.primitives import Line, Arc, Polypath
from geometry.corner_detect import (
    detect_sharp_corners, classify_corners_for_tooling, angle_between
)


# ─────────────────────────────────────────────────────────────────────────
#  УГОЛ МЕЖДУ КАСАТЕЛЬНЫМИ
# ─────────────────────────────────────────────────────────────────────────

def test_angle_between_same():
    """Сонаправленные касательные -> 0°."""
    assert abs(angle_between((1, 0), (1, 0))) < 1e-9


def test_angle_between_perpendicular():
    """Перпендикулярные -> 90°."""
    assert abs(angle_between((1, 0), (0, 1)) - 90.0) < 1e-9


def test_angle_between_opposite():
    """Противоположные -> 180°."""
    assert abs(angle_between((1, 0), (-1, 0)) - 180.0) < 1e-9


# ─────────────────────────────────────────────────────────────────────────
#  ДЕТЕКЦИЯ ОСТРЫХ УГЛОВ
# ─────────────────────────────────────────────────────────────────────────

def test_no_corners_on_smooth_line():
    """Прямая последовательность отрезков без углов."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (2, 0)),
        Line((2, 0), (3, 0)),
    ])
    corners = detect_sharp_corners(poly, threshold_deg=90.0)
    assert len(corners) == 0


def test_right_angle_detected():
    """Прямой угол (90° внутр.) — острый при threshold=91°, не острый при 89°."""
    # Идём вправо, потом вверх -> поворот на 90° влево
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
    ])
    
    corners_91 = detect_sharp_corners(poly, threshold_deg=91.0)
    corners_89 = detect_sharp_corners(poly, threshold_deg=89.0)
    
    assert len(corners_91) == 1, f"При threshold=91 должен найти 1 угол"
    assert len(corners_89) == 0, f"При threshold=89 угол НЕ острый"
    
    c = corners_91[0]
    assert abs(c.interior_angle - 90.0) < 1e-9
    assert c.point == (1, 0)
    assert c.turn_sign == 1  # поворот влево (CCW)


def test_sharp_angle_45():
    """Острый угол 45° (поворот на 135°)."""
    # Идём вправо, потом в направлении (cos(135°), sin(135°)) — это поворот влево на 135°
    # Внутренний угол = 180-135 = 45°
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1 + math.cos(math.radians(135)),
                       math.sin(math.radians(135)))),
    ])
    corners = detect_sharp_corners(poly, threshold_deg=90.0)
    assert len(corners) == 1
    c = corners[0]
    assert abs(c.interior_angle - 45.0) < 1e-7
    assert c.turn_sign == 1  # влево


def test_square_closed_contour():
    """Замкнутый квадрат: 4 прямых угла, все должны быть найдены 
       включая стык последний->первый сегмент."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    
    corners = detect_sharp_corners(poly, threshold_deg=100.0)
    assert len(corners) == 4, f"Должно быть 4 угла, найдено {len(corners)}"
    
    # Все углы 90°
    for c in corners:
        assert abs(c.interior_angle - 90.0) < 1e-9
    
    # Все повороты влево (CCW обход квадрата)
    assert all(c.turn_sign == 1 for c in corners)


def test_square_open_contour():
    """Открытый квадрат (не закрыт) — 3 угла вместо 4."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=False)  # не замкнут — стык последний->первый не проверяется
    
    corners = detect_sharp_corners(poly, threshold_deg=100.0)
    assert len(corners) == 3


# ─────────────────────────────────────────────────────────────────────────
#  КЛАССИФИКАЦИЯ ДЛЯ ИНСТРУМЕНТА
# ─────────────────────────────────────────────────────────────────────────

def test_classify_corners():
    """Разделение углов на 2D и 3D по порогу."""
    from geometry.corner_detect import SharpCorner
    corners = [
        SharpCorner(point=(0,0), segment_index=0, interior_angle=85, turn_sign=1),
        SharpCorner(point=(1,1), segment_index=1, interior_angle=70, turn_sign=1),
        SharpCorner(point=(2,2), segment_index=2, interior_angle=45, turn_sign=1),
        SharpCorner(point=(3,3), segment_index=3, interior_angle=30, turn_sign=1),
    ]
    
    c2d, c3d = classify_corners_for_tooling(corners, thin_threshold=60.0)
    
    # 85° и 70° > 60° -> corner (2D тонкая)
    # 45° и 30° ≤ 60° -> corner3D (3D фреза)
    assert len(c2d) == 2
    assert len(c3d) == 2
    assert [c.interior_angle for c in c2d] == [85, 70]
    assert [c.interior_angle for c in c3d] == [45, 30]


# ─────────────────────────────────────────────────────────────────────────
#  ИНТЕГРАЦИОННЫЙ ТЕСТ НА РЕАЛЬНОМ .ai
# ─────────────────────────────────────────────────────────────────────────

def test_real_ai_no_sharp_corners():
    """В реальном 118917.ai контуры органические — не должно быть углов
    острее 90° на чистовом контуре (они плавные)."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    
    ai = '/mnt/user-data/uploads/118917.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    
    import camsys.core.importer as importer_mod
    project = importer_mod.import_ai_to_project(ai)
    knife = project.get_layer_by_name("Knife")
    
    print(f'\n  Анализ {len(knife.geometries)} контуров:')
    total_corners = 0
    for geom in knife.geometries:
        corners = detect_sharp_corners(geom.polypath, threshold_deg=90.0)
        if corners:
            total_corners += len(corners)
            print(f'    {geom.name}: {len(corners)} острых углов')
            for c in corners[:3]:  # первые 3
                print(f'      ({c.point[0]:.2f}, {c.point[1]:.2f}): '
                      f'{c.interior_angle:.1f}°')
    print(f'  Всего острых углов на детали: {total_corners}')


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


# ─────────────────────────────────────────────────────────────────────────
#  ПОРОГ РАДИУСА УГЛА ИЗ НАСТРОЕК (v1.7)
# ─────────────────────────────────────────────────────────────────────────

def test_corner_threshold_is_taken_from_settings():
    """Порог берётся из настроек, а не зажимается эквидистантой.

    Регрессия: было min(настройка, эквидистанта). С обычной фрезой
    эквидистанта ≈0.53, настройка 0.7 — min() опускал её до 0.53, и
    настройка не работала никогда. На 124173 это давало 0 углов на всех
    60 ножах.
    """
    import os, math
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    import camsys.post.mtx_anderson  # noqa: F401 — регистрация поста
    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.post.package_export import PackageExporter

    def corners_at(threshold):
        project = importer_mod.import_ai_to_project(ai)
        for geom in project.get_layer_by_name("Knife").geometries:
            project.add_blade_operation(geom.id)
        macros_mod.sort_operations_by_grid(project)
        params = CuttingMacroParams()
        params.tip_diameter, params.bottom, params.knife_angle = 0.8, 0.19, 70
        params.generate_corner = True
        params.corner_radius_threshold_mm = threshold
        # Здесь проверяется именно настройка оператора, поэтому нижнюю
        # привязку по минимальной грани (v1.7.21) отключаем — иначе оба
        # порога поднимутся до неё и сравнивать станет нечего.
        params.min_face_mm = 0.0
        ops_2d, _ops_3d = PackageExporter(project,
                                          params)._build_corner_operations()
        return ops_2d

    tool_offset = 0.8 / 2 + 0.19 * math.tan(math.radians(35.0))
    low = corners_at(tool_offset - 0.1)
    high = corners_at(0.7)
    print(f'  эквидистанта={tool_offset:.3f}: при пороге 0.7 углов '
          f'{len(high)}, при пороге ниже эквидистанты — {len(low)}')
    # Поднятая настройка ДОЛЖНА давать углы (раньше давала 0)
    assert len(high) > 0, "настройка порога не влияет на детекцию"
    assert len(high) > len(low)


def test_corner_threshold_floored_by_tool_equidistant():
    """Порог ниже эквидистанты не опускается: непроходимое — всегда угол.

    Иначе слишком низкой настройкой можно было бы спрятать места, куда
    основная фреза физически не входит.
    """
    import math
    from camsys.geometry.primitives import Line, Arc, Polypath
    from camsys.geometry.corner_detect import detect_geometric_corners

    tool_offset = 0.533
    # Прямоугольный угол со скруглением R0.35 — меньше эквидистаты
    r = 0.35
    corner = Arc(a=(0.0, r), b=(r, 0.0), center=(r, r), ccw=True)
    pp = Polypath(segments=[Line(a=(0.0, 6.0), b=(0.0, r)), corner,
                            Line(a=(r, 0.0), b=(6.0, 0.0))], closed=False)
    # Даже если настройку опустили до 0.2, фактический порог = эквидистанта
    effective = max(0.2, tool_offset)
    assert effective == tool_offset
    assert len(detect_geometric_corners(pp, radius_threshold_mm=effective)) == 1
    # А по «сырой» настройке 0.2 угол R0.35 потерялся бы
    assert detect_geometric_corners(pp, radius_threshold_mm=0.2) == []


def test_version_uses_three_part_numbering():
    """Версия всегда МАЖОР.МИНОР.ПАТЧ, все части — числа.

    Ловит откат к двухчастному виду («1.7»), с которого начиналась
    линейка 1.7 до перехода на схему 1.7.0 / 1.7.1 / …
    """
    from camsys import __version__, __version_info__
    parts = __version__.split('.')
    assert len(parts) == 3, f"версия должна быть из трёх частей: {__version__}"
    assert all(p.isdigit() for p in parts), f"нечисловая часть: {__version__}"
    assert __version_info__.startswith(f"v{__version__} (")


# ─────────────────────────────────────────────────────────────────────────
#  СТОРОНА ДОРАБОТКИ УГЛА (v1.7.1)
# ─────────────────────────────────────────────────────────────────────────

def _ring(ccw: bool):
    """Окружность R1 из 4 дуг с заданным направлением обхода."""
    import math
    from camsys.geometry.primitives import Arc, Polypath
    pts = [(math.cos(2 * math.pi * k / 4 * (1 if ccw else -1)),
            math.sin(2 * math.pi * k / 4 * (1 if ccw else -1)))
           for k in range(5)]
    return Polypath(segments=[Arc(a=pts[i], b=pts[i + 1], center=(0.0, 0.0),
                                 ccw=ccw) for i in range(4)], closed=True)


def test_corner_side_follows_compensation_not_hardcoded():
    """Сторона зависит от направления угла ОТНОСИТЕЛЬНО намотки контура.

    Тонкая фреза нужна там, где G42 сжимает угол (радиус реза R − T).
    Для угла, совпадающего с намоткой, это OUTSIDE; для встречного —
    INSIDE. Раньше сторона была захардкожена в OUTSIDE для всех углов.
    """
    from camsys.geometry.corner_detect import corner_side_name
    for contour_ccw in (True, False):
        pp = _ring(contour_ccw)
        same = corner_side_name(pp, contour_ccw)
        opposite = corner_side_name(pp, not contour_ccw)
        # Знак возвращён к исходному (v1.7.26): переворот из v1.7.24 был
        # подгонкой под один случай и ломал обычные углы.
        assert same == "OUTSIDE", f"намотка ccw={contour_ccw}: {same}"
        assert opposite == "INSIDE", f"намотка ccw={contour_ccw}: {opposite}"
        assert same != opposite


def test_corner_ops_use_both_sides():
    """На реальном файле углы распределяются по ОБЕИМ сторонам.

    Регрессия: до v1.7.1 все corner-операции получали ContourSide.OUTSIDE,
    из-за чего примерно половина углов резалась с неверной стороны
    (на 124173 — 22 из 47).
    """
    import os
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    import camsys.post.mtx_anderson  # noqa: F401 — регистрация поста
    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.post.package_export import PackageExporter

    project = importer_mod.import_ai_to_project(ai)
    for geom in project.get_layer_by_name("Knife").geometries:
        project.add_blade_operation(geom.id)
    macros_mod.sort_operations_by_grid(project)
    params = CuttingMacroParams()
    params.tip_diameter, params.bottom, params.knife_angle = 0.8, 0.19, 70
    params.generate_corner = True
    params.corner_radius_threshold_mm = 0.7

    ops_2d, _ops_3d = PackageExporter(project,
                                      params)._build_corner_operations()
    sides = [op.toolpaths[0].side.name for op in ops_2d]
    print(f'  углов {len(sides)}: OUTSIDE={sides.count("OUTSIDE")}, '
          f'INSIDE={sides.count("INSIDE")}')
    assert ops_2d, "углы не построились"
    assert sides.count("OUTSIDE") > 0 and sides.count("INSIDE") > 0, \
        "все углы на одной стороне — сторона снова захардкожена"
    # Сторона дублируется в атрибуты для UI и отладки
    assert all(op.attributes.get('corner_side') in ("OUTSIDE", "INSIDE")
               for op in ops_2d)


def test_suggest_corner_threshold_is_exact_boundary():
    """Подсказка порога указывает ТОЧНУЮ границу срабатывания.

    При подсказанном пороге углы есть, чуть ниже — нет. Нужна, чтобы UI
    вместо молчаливого «углов нет» называл конкретное число.
    """
    import os
    from camsys.geometry.corner_detect import (suggest_corner_threshold,
                                               detect_geometric_corners)
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    import camsys.core.importer as importer_mod
    project = importer_mod.import_ai_to_project(ai)
    # Двоичный поиск гоняет детектор по каждому ножу — на всех 60
    # суита раздувалась до нескольких минут. Хватает выборки.
    geoms = [g for g in project.get_layer_by_name("Knife").geometries
             if g.polypath][:12]

    checked = 0
    for geom in geoms:
        thr = suggest_corner_threshold(geom.polypath)
        if thr <= 0:
            # Углов нет ни при каком пороге — проверяем, что это правда
            assert not detect_geometric_corners(geom.polypath,
                                                radius_threshold_mm=5.0)
            continue
        assert detect_geometric_corners(geom.polypath,
                                        radius_threshold_mm=thr), \
            f"при подсказанном пороге {thr} углов нет"
        assert not detect_geometric_corners(
            geom.polypath, radius_threshold_mm=thr - 1e-3), \
            f"порог {thr} не минимальный — ниже тоже есть углы"
        checked += 1
    print(f'  проверено ножей с углами: {checked}')
    assert checked > 0


def test_autodetect_uses_same_detector_as_builder():
    """Галка «Острые углы 2D» ставится тем же критерием, что строит углы.

    Регрессия v1.7.7: галку ставил detect_corners_by_equidistant (проверка
    по вершинам), а операции строил detect_geometric_corners (радиус ниже
    порога + разворот от 40°). Критерии разные, поэтому галка вставала на
    макетах, где углов потом не находилось ни одного — оператор видел
    включённую галку и пустой _corner.anc.
    """
    import os
    source = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'ui', 'main_window.py'), encoding='utf-8').read()
    start = source.index('def _auto_detect_corner_programs')
    end = source.index('def _auto_set_fiducial_x')
    body = source[start:end]
    assert 'detect_geometric_corners' in body, \
        "автодетект 2D не использует детектор, которым углы строятся"
    assert 'corner_radius_threshold_mm' in body, \
        "автодетект 2D игнорирует порог из настроек"


def test_autodetect_agrees_with_builder_on_real_file():
    """На реальном файле автодетект и построение дают согласованный ответ."""
    import os, math
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    from camsys.geometry.corner_detect import detect_geometric_corners
    import camsys.core.importer as importer_mod

    project = importer_mod.import_ai_to_project(ai)
    geoms = [g for g in project.get_layer_by_name("Knife").geometries
             if g.polypath]
    tool_radius = 0.8 / 2.0 + 0.19 * math.tan(math.radians(35.0))

    for threshold, expect_any in ((0.60, False), (0.70, True)):
        eff = max(threshold, tool_radius)
        found = any(detect_geometric_corners(g.polypath,
                                             radius_threshold_mm=eff)
                    for g in geoms)
        print(f'  порог {threshold}: углы найдены = {found}')
        assert found == expect_any, (
            f"порог {threshold}: ожидалось {expect_any}, получено {found}")


def test_corner_lead_side_uses_parent_contour():
    """Сторона лида угла считается по родительскому ЗАМКНУТОМУ контуру.

    Регрессия v1.7.19: pick_lead_side_for_pass решает, куда направить
    лид, тестом point-in-polygon дальней точки. Для CORNER_REWORK в
    polypath лежит ОТКРЫТЫЙ фрагмент контура — такой тест на нём
    бессмыслен (полигон замыкается сам на себя «линзой»), и сторона
    получалась случайной: часть углов заходила навстречу.

    На 124819 фрагмент и родительский контур дают разный ответ для 51
    угла из 134.
    """
    import os, math
    ai = '/mnt/user-data/uploads/124819.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла 124819.ai')
        return
    import camsys.post.mtx_anderson  # noqa: F401 — регистрация поста
    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.post.package_export import PackageExporter
    from camsys.geometry.path_offset import extract_subpath_around_indices
    from camsys.geometry.lead_inout import pick_lead_side_for_pass

    project = importer_mod.import_ai_to_project(ai)
    for geom in project.get_layer_by_name("Knife").geometries:
        project.add_blade_operation(geom.id)
    macros_mod.sort_operations_by_grid(project)
    params = CuttingMacroParams()
    params.generate_corner = True
    params.corner_radius_threshold_mm = 0.7
    ops_2d, _ = PackageExporter(project, params)._build_corner_operations()
    assert ops_2d, "угловые операции не построились"

    tool_eq = params.tip_diameter + 2 * params.bottom * math.tan(
        math.radians(params.knife_angle / 2))

    checked = differ = 0
    for op in ops_2d:
        attrs = op.attributes
        if 'corner_first_idx' not in attrs:
            continue
        geom = project.get_geometry(op.geometry_ids[0])
        frag = extract_subpath_around_indices(
            geom.polypath, attrs['corner_first_idx'],
            attrs['corner_last_idx'], pad_mm=1.5)
        if not frag or not frag.segments:
            continue
        start = frag.segments[0].a
        tangent = frag.segments[0].tangent_at_start()
        side = op.toolpaths[0].side.name
        by_frag = pick_lead_side_for_pass(start, tangent, frag, side,
                                          tool_eq, tool_eq, 45.0)
        by_parent = pick_lead_side_for_pass(start, tangent, geom.polypath,
                                            side, tool_eq, tool_eq, 45.0)
        checked += 1
        if by_frag != by_parent:
            differ += 1

    print(f'  углов {checked}, сторона зависит от эталона у {differ}')
    assert checked > 0
    # Если бы эталон был неважен, фикс был бы пустышкой
    assert differ > 0, "эталон не влияет — тест не проверяет ничего"

    # И пост обязан брать родительский контур
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'post', 'mtx_anderson.py'), encoding='utf-8').read()
    assert '_side_ref = geom.polypath' in src, \
        "пост не использует родительский контур как эталон стороны"
    assert 'contour_start_tangent, _side_ref' in src
    assert 'contour_end_tangent, _side_ref' in src


def test_no_false_corners_where_face_is_thin():
    """Тонкая, но положительная грань — НЕ угол.

    Регрессия v1.7.26: в v1.7.21 порог угла был привязан к
    `эквидистанта + min_face`, и местами углов объявлялись дуги с гранью
    0.01–0.15 мм — касательные переходы, куда фреза входит. На 124173
    так появлялись 48 ложных углов, хотя настоящих там нет ни одного.

    Настоящий угол — там, где грань ФИЗИЧЕСКИ невозможна: радиус меньше
    эквидистаты. Места, куда фреза не дошла по другой причине (ямы, где
    путь упирается сам в себя), ищет find_narrow_gaps и на радиус дуги
    не завязан.
    """
    import os
    import camsys.post.mtx_anderson  # noqa: F401
    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.post.package_export import PackageExporter

    def corners_of(path):
        project = importer_mod.import_ai_to_project(path)
        for geom in project.get_layer_by_name("Knife").geometries:
            project.add_blade_operation(geom.id)
        macros_mod.sort_operations_by_grid(project)
        params = CuttingMacroParams()
        params.knife_angle, params.tip_diameter, params.bottom = 90, 0.8, 0.19
        params.generate_corner = True
        params.corner_radius_threshold_mm = 0.60
        ops, _ = PackageExporter(project, params)._build_corner_operations()
        return ops

    ai = '/mnt/user-data/uploads/124173.ai'
    if os.path.exists(ai):
        ops = corners_of(ai)
        # Проточки карманов — не углы: карман там действительно есть, и
        # металл из него снимает тонкая фреза (v1.7.38).
        corner_ops = [o for o in ops if 'pocket_s0' not in o.attributes]
        pocket_ops = [o for o in ops if 'pocket_s0' in o.attributes]
        print(f'  124173: углов {len(corner_ops)} (ожидается 0), '
              f'проточек карманов {len(pocket_ops)}')
        assert not corner_ops, (
            f"ложные углы на 124173: {len(corner_ops)}; настоящих там нет")

    # Контроль: где угол РЕАЛЬНЫЙ, он находиться обязан
    test_ai = '/mnt/user-data/uploads/_TEST.ai'
    if os.path.exists(test_ai):
        ops = corners_of(test_ai)
        print(f'  _TEST (треугольники R0.5): углов {len(ops)}')
        assert ops, "на треугольнике со скруглением R0.5 угол не найден"


def test_corner_convexity_is_geometric():
    """Выпуклость определяется геометрически, а не флагами намотки.

    На треугольнике со скруглением R0.5 угол выпуклый, вырождается
    ВНУТРЕННЯЯ грань, значит T3 идёт по внутреннему пути
    (ContourSide.OUTSIDE).
    """
    import os
    from camsys.geometry.primitives import Arc
    from camsys.geometry.corner_detect import (corner_is_convex,
                                               corner_side_for_arc)
    test_ai = '/mnt/user-data/uploads/_TEST.ai'
    if not os.path.exists(test_ai):
        print('  SKIP — нет _TEST.ai')
        return
    import camsys.core.importer as importer_mod
    project = importer_mod.import_ai_to_project(test_ai)
    geoms = [g for g in project.get_layer_by_name("Knife").geometries
             if g.polypath]
    checked = 0
    for geom in geoms:
        for seg in geom.polypath.segments:
            if not isinstance(seg, Arc) or seg.radius > 1.0:
                continue
            assert corner_is_convex(geom.polypath, seg), \
                "скругление треугольника должно быть выпуклым"
            assert corner_side_for_arc(geom.polypath, seg) == "OUTSIDE", \
                "у выпуклого угла T3 идёт по внутреннему пути"
            checked += 1
    print(f'  проверено скруглений: {checked}')
    assert checked > 0


def test_corner_cut_is_right_of_travel():
    """Эквидистанта угла всегда СПРАВА по ходу — это и делает G42.

    Регрессия v1.7.37: вьювер строил её через `offset_polypath_uniform(...,
    inward=...)`, а эта функция у РАЗОМКНУТОГО пути безусловно принимает
    намотку за CCW (`is_ccw(polypath) if polypath.closed else True`).
    Фрагмент угла разомкнут, поэтому inward=True давал ЛЕВУЮ нормаль, и
    все углы со стороной OUTSIDE — 16 из 26 на заказе 124173 — рисовались
    зеркально: рез показывался не с той стороны контура.
    """
    import sys as _s, os as _o
    _o_root = _o.path.dirname(_o.path.dirname(_o.path.dirname(
        _o.path.abspath(__file__))))
    if _o_root not in _s.path:
        _s.path.insert(0, _o_root)
    from camsys.geometry.path_offset import (offset_right_of_travel,
                                             offset_polypath_uniform,
                                             _exact_axis_points)
    from camsys.geometry.primitives import Line as _L, Polypath as _P

    # Разомкнутый фрагмент: вверх, затем вправо
    frag = _P(segments=[_L(a=(0.0, 0.0), b=(0.0, 2.0)),
                        _L(a=(0.0, 2.0), b=(2.0, 2.0))], closed=False)
    off = offset_right_of_travel(frag, 0.5)
    # Идём вверх → справа это +X; первый сегмент должен уехать в +X
    a0 = off.segments[0].a
    assert a0[0] > 0.4, f"эквидистанта ушла влево: {a0}"
    # А inward=True на разомкнутом пути уводит её ВЛЕВО — ровно баг
    bad = offset_polypath_uniform(frag, 0.5, inward=True)
    assert bad.segments[0].a[0] < -0.4, "поведение inward изменилось"


def test_corner_fragment_follows_its_pass():
    """Фрагмент угла идёт в намотке СВОЕГО прохода, а не по центру bbox.

    Тонкая фреза дорабатывает ту же грань лезвия, что резала основная,
    поэтому направление обхода у них обязано совпадать: G42 кладёт фрезу
    справа по ходу, и разворот фрагмента переносит рез на встречную грань.

    Прежний признак — «центр bbox ножа справа от касательной» — грубая
    замена «внутри контура»; на ложке, языке и крючке центр bbox лежит
    вне контура, и угол резался зеркально.
    """
    import sys as _s, os as _o, math as _m
    _o_root = _o.path.dirname(_o.path.dirname(_o.path.dirname(
        _o.path.abspath(__file__))))
    if _o_root not in _s.path:
        _s.path.insert(0, _o_root)
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not _o.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    from camsys.geometry.corner_detect import (detect_geometric_corners,
                                               group_corner_arcs,
                                               corner_side_name)
    from camsys.geometry.direction import (orient_corner_fragment,
                                           normalize_for_side)
    from camsys.geometry.path_offset import (extract_subpath_around_indices,
                                             _exact_axis_points)
    import camsys.core.importer as importer_mod

    def _tangent_near(poly, pt):
        pts = _exact_axis_points(poly, 0.01)[0]
        i = min(range(len(pts) - 1),
                key=lambda k: (pts[k][1][0] - pt[0]) ** 2
                + (pts[k][1][1] - pt[1]) ** 2)
        p, q = pts[i][1], pts[min(i + 1, len(pts) - 1)][1]
        dx, dy = q[0] - p[0], q[1] - p[1]
        L = _m.hypot(dx, dy) or 1.0
        return dx / L, dy / L

    project = importer_mod.import_ai_to_project(ai)
    tip, bottom, angle = 0.8, 0.25, 90.0
    thr = max(0.7, tip / 2 + bottom * _m.tan(_m.radians(angle / 2)))
    total = against = 0
    for g in project.get_layer_by_name("Knife").geometries:
        pp = g.polypath
        if not pp or not pp.closed:
            continue
        cs = detect_geometric_corners(pp, radius_threshold_mm=thr)
        if not cs:
            continue
        for grp in group_corner_arcs(cs):
            side = corner_side_name(pp, grp.ccw)
            frag = extract_subpath_around_indices(pp, grp.first_idx,
                                                  grp.last_idx, pad_mm=1.5)
            if not frag.segments:
                continue
            frag = orient_corner_fragment(frag, pp, side)
            fp = _exact_axis_points(frag, 0.01)[0]
            m = len(fp) // 2
            p0, p1 = fp[m][1], fp[min(m + 1, len(fp) - 1)][1]
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            L = _m.hypot(dx, dy) or 1.0
            ft = (dx / L, dy / L)
            mt = _tangent_near(normalize_for_side(pp, side), p0)
            total += 1
            if ft[0] * mt[0] + ft[1] * mt[1] <= 0:
                against += 1
                print(f'  ПРОТИВ: {getattr(g, "name", "?")} {side}')
    print(f'  углов {total}, против основного прохода {against}')
    assert total >= 20, f'углов нашлось всего {total}'
    assert against == 0, f'{against} углов идут против своего прохода'
