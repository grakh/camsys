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


def test_dxf_keeps_arcs_as_arcs():
    """В DXF дуга остаётся дугой — ни одна не раскладывается в отрезки.

    Это и есть причина выбрать DXF, а не PDF: в PDF дуги окружности нет
    вообще, только Безье, и чужой импортёр приближает их заново.
    """
    prj = _demo_project()
    ents = parse_dxf_entities(build_dxf(prj))
    src = _all_segments(prj)
    n_arc_src = sum(1 for s in src if isinstance(s, Arc))
    n_line_src = sum(1 for s in src if isinstance(s, Line))
    n_arc = sum(1 for e in ents if e['type'] == 'ARC')
    n_line = sum(1 for e in ents if e['type'] == 'LINE')
    n_circle = sum(1 for e in ents if e['type'] == 'CIRCLE')
    print(f'  дуг {n_arc_src} -> {n_arc}, прямых {n_line_src} -> {n_line}, '
          f'реперов {n_circle}')
    assert n_arc == n_arc_src, 'дуги потерялись или размножились'
    assert n_line == n_line_src, 'прямые потерялись или размножились'
    assert n_circle == len(prj.fiducials), (
        'реперы не выгружены — без них в чужом CAM не привязаться')


def test_dxf_geometry_is_exact():
    """Выгрузка в DXF не двигает геометрию.

    Допуск 0.001 мм = 1 мкм: это допуск биарк-фита и половина допуска на
    разброс высоты вершины (±2 мкм — брак изделия). Реально расхождение
    на три порядка меньше — только округление записи.
    """
    prj = _demo_project()
    ents = parse_dxf_entities(build_dxf(prj))
    src = _all_segments(prj)
    src_l = [s for s in src if isinstance(s, Line)]
    src_a = [s for s in src if isinstance(s, Arc)]
    got_l = [e for e in ents if e['type'] == 'LINE']
    got_a = [e for e in ents if e['type'] == 'ARC']

    worst = 0.0
    for sg, e in zip(src_l, got_l):
        worst = max(worst, abs(e['10'] - sg.a[0]), abs(e['20'] - sg.a[1]),
                    abs(e['11'] - sg.b[0]), abs(e['21'] - sg.b[1]))
    for sg, e in zip(src_a, got_a):
        cx, cy, r = e['10'], e['20'], e['40']
        a0, a1 = math.radians(e['50']), math.radians(e['51'])
        p0 = (cx + r * math.cos(a0), cy + r * math.sin(a0))
        p1 = (cx + r * math.cos(a1), cy + r * math.sin(a1))
        # В DXF дуга идёт ПРОТИВ часовой от начального угла к конечному,
        # поэтому у нашей дуги по часовой концы переставлены местами.
        want0, want1 = (sg.a, sg.b) if sg.ccw else (sg.b, sg.a)
        worst = max(worst, abs(r - sg.radius),
                    math.hypot(p0[0] - want0[0], p0[1] - want0[1]),
                    math.hypot(p1[0] - want1[0], p1[1] - want1[1]))
    print(f'  худшее расхождение: {worst * 1e6:.3f} нм')
    assert worst < 0.001, f'геометрия уехала на {worst * 1000:.4f} мкм'


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
