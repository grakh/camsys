"""Тесты выгрузки геометрии в DXF и PDF (v1.7.51).

Смысл выгрузки: если на станке что-то пошло не так, деталь надо доделать
отдельно. Для этого нужна ТА ЖЕ геометрия, по которой считался рез, —
после биарк-фита и слияния дуг. По исходному `.ai` в размер не попасть:
альфа при своём импорте раскладывает кривые иначе.
"""

import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from camsys.geometry.primitives import Line, Arc, Polypath
from camsys.io_.dxf_export import (
    build_dxf, parse_dxf_entities, project_bounds)
from camsys.io_.pdf_export import build_pdf, PT_PER_MM


def _demo_project():
    """Проект из двух контуров: прямые, дуга по часовой и против."""
    from camsys.core.project import Project, Geometry, Fiducial
    prj = Project(name="EXPORT_TEST")
    lay = prj.add_layer("Knife")

    # Скруглённый прямоугольник: 4 прямые + 4 дуги (все выпуклые, CCW).
    r = 2.0
    x0, y0, x1, y1 = 10.0, 20.0, 40.0, 35.0
    segs = [
        Line(a=(x0 + r, y0), b=(x1 - r, y0)),
        Arc(a=(x1 - r, y0), b=(x1, y0 + r), center=(x1 - r, y0 + r), ccw=True),
        Line(a=(x1, y0 + r), b=(x1, y1 - r)),
        Arc(a=(x1, y1 - r), b=(x1 - r, y1), center=(x1 - r, y1 - r), ccw=True),
        Line(a=(x1 - r, y1), b=(x0 + r, y1)),
        Arc(a=(x0 + r, y1), b=(x0, y1 - r), center=(x0 + r, y1 - r), ccw=True),
        Line(a=(x0, y1 - r), b=(x0, y0 + r)),
        Arc(a=(x0, y0 + r), b=(x0 + r, y0), center=(x0 + r, y0 + r), ccw=True),
    ]
    lay.geometries.append(
        Geometry(name="Knife_1", polypath=Polypath(segments=segs, closed=True),
                 source_layer="Knife"))

    # Второй контур с дугами ПО ЧАСОВОЙ — проверяем перестановку углов
    # при записи в DXF (там дуга всегда против часовой).
    segs2 = [
        Arc(a=(60.0, 20.0), b=(60.0, 30.0), center=(60.0, 25.0), ccw=False),
        Arc(a=(60.0, 30.0), b=(60.0, 20.0), center=(60.0, 25.0), ccw=False),
    ]
    lay.geometries.append(
        Geometry(name="Knife_2", polypath=Polypath(segments=segs2, closed=True),
                 source_layer="Knife"))

    prj.fiducials.append(Fiducial(x=5.0, y=5.0, name="FID1"))
    prj.fiducials.append(Fiducial(x=70.0, y=5.0, name="FID2"))
    return prj


def _all_segments(prj):
    out = []
    for layer in prj.layers.values():
        for g in layer.geometries:
            if g.polypath and g.polypath.segments:
                out += list(g.polypath.segments)
    return out


def _arc_from_bulge(p0, p1, bulge):
    """Восстанавливает дугу из пары вершин и bulge — обратная операция
    к записи. Возвращает (радиус, центр, ccw)."""
    th = 4.0 * math.atan(bulge)
    d = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    r = d / (2 * math.sin(abs(th) / 2))
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    h = math.sqrt(max(0.0, r * r - (d / 2) ** 2))
    ux, uy = -(p1[1] - p0[1]) / d, (p1[0] - p0[0]) / d
    sgn = 1.0 if (abs(th) <= math.pi) == (th > 0) else -1.0
    return r, (mx + sgn * h * ux, my + sgn * h * uy), th > 0


def _segments_of(polys):
    """Разворачивает полилинии обратно в сегменты (a, b, bulge)."""
    out = []
    for e in polys:
        vs = e['vertices']
        n = len(vs)
        last = n if e.get('closed') else n - 1
        for k in range(last):
            a = (vs[k]['10'], vs[k]['20'])
            b = (vs[(k + 1) % n]['10'], vs[(k + 1) % n]['20'])
            out.append((a, b, vs[k].get('42', 0.0)))
    return out


def test_dxf_writes_contour_as_one_entity():
    """Контур — ОДНА полилиния, а не россыпь отдельных сегментов.

    Сначала каждый сегмент писался своей сущностью LINE/ARC. Файл
    читался, но альфа видела не контур, а сотни отдельных элементов, на
    каждом своя стрелка направления — работать с таким нельзя
    (v1.7.52). Полилиния держит контур целым.
    """
    prj = _demo_project()
    ents = parse_dxf_entities(build_dxf(prj))
    polys = [e for e in ents if e['type'] == 'POLYLINE']
    singles = [e for e in ents if e['type'] in ('LINE', 'ARC')]
    n_src = sum(1 for _ in _all_segments(prj))
    print(f'  сегментов {n_src} -> полилиний {len(polys)}, '
          f'одиночных сущностей {len(singles)}')
    assert len(polys) == 2, (
        f'контуров в проекте 2, а полилиний {len(polys)}')
    assert not singles, (
        f'{len(singles)} сегментов выгружены по отдельности — '
        f'альфа соберёт из них россыпь')
    assert all(e.get('closed') for e in polys), (
        'замкнутый контур выгружен разомкнутым')
    circles = [e for e in ents if e['type'] == 'CIRCLE']
    assert len(circles) == len(prj.fiducials), (
        'реперы не выгружены — без них в чужом CAM не привязаться')


def test_dxf_keeps_arcs_as_arcs():
    """Дуга остаётся дугой: у её вершины есть bulge, у прямой — нет.

    bulge = tan(Δθ/4) — это ТОЧНОЕ представление дуги окружности, а не
    приближение. Именно поэтому DXF, а не PDF: в PDF дуги нет вообще.
    """
    prj = _demo_project()
    polys = [e for e in parse_dxf_entities(build_dxf(prj))
             if e['type'] == 'POLYLINE']
    segs = _segments_of(polys)
    src = _all_segments(prj)
    n_arc_src = sum(1 for s in src if isinstance(s, Arc))
    n_bulge = sum(1 for _a, _b, bl in segs if abs(bl) > 1e-12)
    print(f'  сегментов {len(segs)} (ожидалось {len(src)}), '
          f'с дугой {n_bulge} (ожидалось {n_arc_src})')
    assert len(segs) == len(src), 'число сегментов изменилось'
    assert n_bulge == n_arc_src, 'дуги потеряны или появились лишние'
    for (_a, _b, bl), sg in zip(segs, src):
        if isinstance(sg, Line):
            assert abs(bl) <= 1e-12, 'прямая выгружена с кривизной'


def test_dxf_geometry_is_exact():
    """Выгрузка в DXF не двигает геометрию.

    Допуск 0.001 мм = 1 мкм: это допуск биарк-фита и половина допуска на
    разброс высоты вершины (±2 мкм — брак изделия). Реально расхождение
    на шесть порядков меньше — только округление записи.

    Меряется отклонение САМОЙ КРИВОЙ, а не радиуса: на пологой дуге
    радиус огромен, и абсолютная ошибка радиуса там ничего не говорит.
    """
    prj = _demo_project()
    polys = [e for e in parse_dxf_entities(build_dxf(prj))
             if e['type'] == 'POLYLINE']
    segs = _segments_of(polys)
    src = _all_segments(prj)

    worst_pt = 0.0
    worst_curve = 0.0
    for (a, b, bl), sg in zip(segs, src):
        worst_pt = max(worst_pt,
                       math.hypot(a[0] - sg.a[0], a[1] - sg.a[1]),
                       math.hypot(b[0] - sg.b[0], b[1] - sg.b[1]))
        if not isinstance(sg, Arc):
            continue
        r, c, ccw = _arc_from_bulge(a, b, bl)
        assert ccw == sg.ccw, 'дуга выгружена в обратную сторону'
        a0 = math.atan2(a[1] - c[1], a[0] - c[0])
        a1 = math.atan2(b[1] - c[1], b[0] - c[0])
        sw = (a1 - a0) % (2 * math.pi)
        if not ccw:
            sw -= 2 * math.pi
        for t in range(21):
            ang = a0 + sw * t / 20.0
            p = (c[0] + r * math.cos(ang), c[1] + r * math.sin(ang))
            worst_curve = max(worst_curve, abs(
                math.hypot(p[0] - sg.center[0], p[1] - sg.center[1])
                - sg.radius))
    print(f'  вершины {worst_pt * 1e6:.3f} нм, кривая '
          f'{worst_curve * 1e6:.3f} нм')
    assert worst_pt < 0.001, f'вершины уехали на {worst_pt * 1000:.4f} мкм'
    assert worst_curve < 0.001, (
        f'кривая уехала на {worst_curve * 1000:.4f} мкм')


def test_dxf_has_layers_and_millimetres():
    """Слои объявлены, единицы — миллиметры.

    Без объявления слоёв чужой CAM свалит всё в один, а без единиц
    масштаб может уехать в дюймы.
    """
    text = build_dxf(_demo_project())
    assert "$INSUNITS" in text, 'не указаны единицы'
    i = text.find("$INSUNITS")
    assert text[i:i + 60].split()[2] == '4', 'единицы не миллиметры'
    assert "\r\nLAYER\r\n" in text, 'нет таблицы слоёв'
    assert "Knife" in text, 'слой ножей не объявлен'
    assert text.rstrip().endswith("EOF"), 'файл не закрыт'


def test_pdf_arc_error_is_measured_and_small():
    """В PDF дуга приближается Безье, и приближение КОНТРОЛИРУЕТСЯ.

    У PDF нет дуги окружности. Экспортёр режет дугу, пока ЗАМЕРЕННОЕ
    отклонение не станет меньше допуска. Проверяем это на дуге большого
    радиуса — там приближение всего одной кривой дало бы десятки микрон.
    """
    from camsys.io_.pdf_export import _arc_bezier_pieces
    big = Arc(a=(100.0, 0.0), b=(0.0, 100.0), center=(0.0, 0.0), ccw=True)
    for tol in (0.01, 0.0005):
        pieces = _arc_bezier_pieces(big, tol)
        worst = 0.0
        for p0, p1, p2, p3 in pieces:
            for i in range(1, 32):
                t = i / 32.0
                mt = 1 - t
                x = (mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0]
                     + 3 * mt * t * t * p2[0] + t ** 3 * p3[0])
                y = (mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1]
                     + 3 * mt * t * t * p2[1] + t ** 3 * p3[1])
                worst = max(worst, abs(math.hypot(x, y) - 100.0))
        print(f'  R100, допуск {tol * 1000:.2f} мкм: кусков {len(pieces)}, '
              f'отклонение {worst * 1000:.4f} мкм')
        assert worst <= tol * 1.5, (
            f'приближение дуги {worst * 1000:.3f} мкм при допуске '
            f'{tol * 1000:.3f} мкм')


def test_pdf_is_readable_by_our_own_importer():
    """PDF читается НАШИМ же импортёром, со слоями.

    Если свой экспорт не грузится обратно, проверить его нечем. Слои
    пишутся как OCG — той же конструкцией, какую читает `ai_parser`.
    """
    import tempfile
    from camsys.io_.ai_parser import list_layers, get_layer_paths
    prj = _demo_project()
    data = build_pdf(prj)
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as fh:
        fh.write(data)
        path = fh.name
    try:
        layers = list_layers(path)
        print(f'  PDF {len(data)} байт, слои: {layers}')
        assert 'Knife' in layers, f'слой ножей не виден: {layers}'
        paths = get_layer_paths(path, 'Knife')
        assert len(paths) == 2, f'контуров в Knife {len(paths)}, ожидалось 2'
        # Координаты обязаны быть АБСОЛЮТНЫМИ — теми же, что в `.anc`,
        # иначе в чужом CAM система координат поедет.
        xs = [p[0] for pth in paths for p in pth.all_anchors()]
        ys = [p[1] for pth in paths for p in pth.all_anchors()]
        assert 9.0 < min(xs) < 11.0, f'X уехал: min {min(xs):.2f}, ждали ~10'
        assert 19.0 < min(ys) < 21.0, f'Y уехал: min {min(ys):.2f}, ждали ~20'
    finally:
        os.unlink(path)


def test_export_covers_everything_in_bounds():
    """Габариты считаются с учётом выпуклости дуг, а не только их концов.

    Иначе дуга, выпирающая за свои концы, окажется за краем листа PDF.
    """
    prj = _demo_project()
    x0, y0, x1, y1 = project_bounds(prj)
    print(f'  габариты: X {x0:.2f}…{x1:.2f}, Y {y0:.2f}…{y1:.2f}')
    assert x0 <= 5.0 and y0 <= 5.0, 'реперы не учтены в габаритах'

    # Без реперов габарит обязан давать сама дуга: круг с центром
    # (60, 25) и радиусом 5 доходит до X=65, хотя ОБА его конца лежат
    # на X=60. По концам сегментов габарит получился бы 60 — и правый
    # край детали вышел бы за лист.
    prj.fiducials.clear()
    bx0, by0, bx1, by1 = project_bounds(prj)
    print(f'  без реперов: X {bx0:.2f}…{bx1:.2f}, Y {by0:.2f}…{by1:.2f}')
    assert bx1 >= 65.0 - 1e-9, (
        f'выпуклость дуги не учтена: правый край {bx1:.2f}, ждали 65')
    assert by0 <= 20.0 + 1e-9, (
        f'низ дуги не учтён: {by0:.2f}, ждали 20')
