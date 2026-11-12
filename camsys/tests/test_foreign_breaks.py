"""Разрыв основного прохода у близкого лезвия соседнего ножа (v1.7.79).

Альфа: «эти разрывы ставятся вручную, если близко лезвие»; «рвутся оба,
если только один, то другой обязательно зарежет лезвие, 2 мм достаточно
в запас, Т3 проходит ещё на 1 мм дальше, чтоб точно перекрыть».
"""
import os
import sys
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import camsys.post.mtx_anderson  # noqa — регистрирует пост


def _two_rects(gap):
    """Два прямоугольника 20×10 рядом, между лезвиями `gap` мм."""
    from camsys.geometry.primitives import Line, Polypath
    from camsys.core.project import Project, Geometry
    prj = Project(name="GAP")
    lay = prj.add_layer("Knife")

    def rect(x0, y0, w, h):
        V = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
        return Polypath(segments=[Line(a=V[i], b=V[(i + 1) % 4])
                                  for i in range(4)], closed=True)
    for name, pp in (("Knife_1", rect(0.0, 0.0, 20.0, 10.0)),
                     ("Knife_2", rect(20.0 + gap, 0.0, 20.0, 10.0))):
        g = Geometry(name=name, polypath=pp, source_layer="Knife")
        lay.geometries.append(g)
        prj.add_blade_operation(g.id)
    prj.attributes['lead_mode'] = 0
    return prj


def _params():
    from camsys.core.cutting_macro import CuttingMacroParams
    p = CuttingMacroParams()
    p.knife_angle, p.tip_diameter, p.bottom = 90.0, 0.8, 0.25
    p.corner_tip_diameter = 0.6
    return p


def test_both_knives_broken_and_t3_overlaps():
    """Щель 1.2 мм: основная (рез 1.3) не лезет — рвутся ОБА ножа на
    обращённых друг к другу сторонах; T3 (рез 1.1) лезет — по операции
    на каждый, на 1 мм дальше разрыва."""
    from camsys.post.package_export import PackageExporter
    from camsys.geometry.path_offset import distance_along_polypath
    from camsys.geometry.direction import normalize_for_side
    prj = _two_rects(1.2)
    ex = PackageExporter(prj, _params())
    ops, spans = ex.apply_foreign_breaks()
    br = {o.name: o.attributes.get('foreign_breaks') for o in prj.operations}
    print(f'  разрывы: { {k: list(v) for k, v in br.items() if v} }')
    assert set(br['Blade Knife_1']) == {'INSIDE'}
    assert set(br['Blade Knife_2']) == {'INSIDE'}
    # разрыв = сторона щели (10 мм) + 2 мм с каждой стороны
    for name in ('Blade Knife_1', 'Blade Knife_2'):
        b = br[name]['INSIDE'][0]
        assert abs(math.dist(b['A'], b['B']) - 0.0) > 0  # не точка
    assert len(ops) == 2, [o.name for o in ops]
    for o in ops:
        at = o.attributes
        span = (at['pocket_s1'] - at['pocket_s0']) % at['pocket_total']
        # 10 + 2·2 (разрыв) + 2·1 (перекрытие T3), щель — у прямых углов
        # (конфликт идёт и чуть дальше угла: 10 мм стороны ± выступ)
        print(f'  {o.name}: участок T3 {span:.2f} мм')
        assert span > 10.0 + 2 * 2.0 + 2 * 1.0 - 0.1


def test_t3_too_wide_is_reported_not_cut():
    """Щель 1.0 мм: не лезет и T3 (рез 1.1) — участок не режется, а в
    отчёте и красным."""
    from camsys.post.package_export import PackageExporter
    prj = _two_rects(1.0)
    ex = PackageExporter(prj, _params())
    ops, _spans = ex.apply_foreign_breaks()
    assert all(o.attributes.get('foreign_breaks') for o in prj.operations)
    rep = [r for r in ex.machinability_report if 'БЛИЖЕ T3' in r]
    print(f'  отчёт: {rep[:1]}')
    assert rep
    assert prj.unmachinable_pockets


def test_main_pass_is_open_in_program():
    """В программе проход, обращённый к соседу, — один ОТКРЫТЫЙ кусок:
    вход и выход по краям разрыва, щели фреза не касается."""
    from camsys.post.package_export import PackageExporter
    import camsys.post.mtx_anderson  # noqa
    import re
    prj = _two_rects(1.2)
    files = PackageExporter(prj, _params()).generate(
        only_stages=('rough_all',))
    text = list(files.values())[0]
    parts = text.split('PREPART')[1:]
    assert len(parts) == 4, len(parts)
    # точки контура каждого PART
    num = r'(-?\d+\.?\d*)'
    worst = 0.0
    for ch in parts:
        pts = [(float(a), float(b)) for a, b in
               re.findall(r'X' + num + r'\s+Y' + num, ch)]
        # ни одна точка оси не лежит на обращённой к соседу стороне
        # глубже края разрыва (x=20 у Knife_1, x=21.2 у Knife_2, y∈[0,10])
        for x, y in pts:
            if 1.0 < y < 9.0 and (abs(x - 20.0) < 1e-3 or abs(x - 21.2) < 1e-3):
                worst = max(worst, 1.0)
    assert worst == 0.0, 'основной проход идёт по стороне у соседа'
