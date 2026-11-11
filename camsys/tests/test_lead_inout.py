"""Тесты построения Lead-In / Lead-Out (тангенциальная дуга + прямая).

Геометрия захода/отхода в постпроцессоре MTX выводится через обычные
G2/G3 с явным указанием радиуса и направления, а не через специальные
G12/G13 (которые давали неоднозначное направление дуги)."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
from geometry.lead_inout import build_lead_in, build_lead_out, LeadGeometry
from geometry.primitives import Line, Arc, vec_dist, EPS


def approx(a, b, tol=1e-6):
    return abs(a - b) < tol


# ─────────────────────────────────────────────────────────────────────────
#  ВХОД: ДУГА КАСАЕТСЯ КОНТУРА В ТОЧКЕ СТЫКА
# ─────────────────────────────────────────────────────────────────────────

def test_lead_in_arc_touches_start():
    """Дуга захода должна заканчиваться ровно в start_point контура."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)  # контур идёт вправо
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=1.0,
                          approach_angle_deg=45.0)
    
    assert lead.arc is not None
    assert vec_dist(lead.arc.b, start) < 1e-9


def test_lead_in_arc_tangent_at_contour():
    """Касательная дуги в точке start_point должна совпадать с касательной 
    контура (т.е. это плавный заход, не излом)."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=1.0,
                          approach_angle_deg=45.0)
    
    # Касательная дуги в её конце (b)
    arc = lead.arc
    arc_tang_at_end = arc.tangent_at_end()
    
    # Должна быть параллельна tangent с тем же направлением
    dot = arc_tang_at_end[0]*tangent[0] + arc_tang_at_end[1]*tangent[1]
    # cos угла ≈ 1 → касательные сонаправлены
    assert dot > 0.999, f"Касательная дуги {arc_tang_at_end} не совпадает " \
                        f"с касательной контура {tangent}, dot={dot:.6f}"


def test_lead_in_line_meets_arc():
    """Конец прямой должен совпадать с началом дуги (стык line→arc)."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=1.0,
                          approach_angle_deg=45.0)
    
    assert vec_dist(lead.line.b, lead.arc.a) < 1e-9


def test_lead_in_line_length():
    """Длина прямой = заявленной."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    line_length = 3.5
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=line_length, arc_radius=1.0,
                          approach_angle_deg=45.0)
    
    actual_length = vec_dist(lead.line.a, lead.line.b)
    assert approx(actual_length, line_length, tol=1e-9)


def test_lead_in_arc_radius():
    """Радиус дуги = заявленному."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    arc_radius = 1.7
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=arc_radius,
                          approach_angle_deg=45.0)
    
    actual_radius = vec_dist(lead.arc.center, lead.arc.a)
    assert approx(actual_radius, arc_radius, tol=1e-9)
    # И до точки b тоже должно быть R
    actual_radius2 = vec_dist(lead.arc.center, lead.arc.b)
    assert approx(actual_radius2, arc_radius, tol=1e-9)


def test_lead_in_left_vs_right_side():
    """Lead-In с left и right должны быть зеркальны относительно касательной."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    
    lead_L = build_lead_in(start, tangent, side='left',
                            line_length=2.0, arc_radius=1.0,
                            approach_angle_deg=45.0)
    lead_R = build_lead_in(start, tangent, side='right',
                            line_length=2.0, arc_radius=1.0,
                            approach_angle_deg=45.0)
    
    # Точки старта прямых должны быть зеркальны по оси X (касательной)
    # Left → выше оси, Right → ниже
    Lstart = lead_L.line.a
    Rstart = lead_R.line.a
    
    assert approx(Lstart[0], Rstart[0], tol=1e-9), \
        "X-координаты должны совпадать при зеркале"
    assert approx(Lstart[1], -Rstart[1], tol=1e-9), \
        "Y-координаты должны быть противоположны"


# ─────────────────────────────────────────────────────────────────────────
#  ВЫХОД (LEAD-OUT) — ЗЕРКАЛО ВХОДА
# ─────────────────────────────────────────────────────────────────────────

def test_lead_out_arc_touches_end():
    """Дуга выхода должна начинаться ровно в end_point контура."""
    end = (5.0, 5.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_out(end, tangent, side='left',
                           line_length=2.0, arc_radius=1.0,
                           retract_angle_deg=45.0)
    
    assert vec_dist(lead.arc.a, end) < 1e-9


def test_lead_out_tangent_at_contour():
    """Касательная дуги выхода в точке end_point = касательная контура."""
    end = (5.0, 5.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_out(end, tangent, side='left',
                           line_length=2.0, arc_radius=1.0,
                           retract_angle_deg=45.0)
    
    # Касательная дуги в её начале
    arc = lead.arc
    arc_tang_at_start = arc.tangent_at_start()
    
    dot = arc_tang_at_start[0]*tangent[0] + arc_tang_at_start[1]*tangent[1]
    assert dot > 0.999, f"Касательная дуги выхода {arc_tang_at_start} " \
                        f"не совпадает с касательной {tangent}"


def test_lead_out_arc_meets_line():
    """Дуга → линия: end дуги = начало линии."""
    end = (5.0, 5.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_out(end, tangent, side='left',
                           line_length=2.0, arc_radius=1.0)
    
    assert vec_dist(lead.arc.b, lead.line.a) < 1e-9


# ─────────────────────────────────────────────────────────────────────────
#  ГРАНИЧНЫЕ СЛУЧАИ
# ─────────────────────────────────────────────────────────────────────────

def test_lead_in_zero_approach_angle():
    """Угол 0° = прямая параллельна касательной (вырожденный случай).
    Дуга должна быть бесконечно малой / контролируемо обрабатываться."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    
    # Угол 0 граничный случай — функция не должна падать
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=1.0,
                          approach_angle_deg=0.0)
    # На малом угле дуга почти не разворачивается, прямая почти касательна
    assert lead.arc is not None
    assert lead.line is not None


def test_lead_in_90deg():
    """Угол 90° — заход перпендикулярно касательной."""
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    
    lead = build_lead_in(start, tangent, side='left',
                          line_length=2.0, arc_radius=1.0,
                          approach_angle_deg=90.0)
    
    # Прямая должна быть перпендикулярна к tangent
    line_vec = (lead.line.b[0] - lead.line.a[0],
                lead.line.b[1] - lead.line.a[1])
    dot = line_vec[0]*tangent[0] + line_vec[1]*tangent[1]
    L = math.hypot(line_vec[0], line_vec[1])
    # cos(угла между line и tangent) ≈ 0 для 90°
    cos_angle = dot / L
    assert abs(cos_angle) < 1e-6


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


def test_sample_cache_survives_id_reuse():
    """Кэш выборки контура держит сам объект, а не только его id.

    Регрессия v1.7.40: точки контура запоминались по `id(polypath)`, а
    Python отдаёт адрес освобождённого объекта следующему. В экспорте,
    где полипасов создаётся много, замер лида начинал считаться ПО
    ЧУЖОМУ контуру — и подбор длины давал бессмыслицу (10 лидов из 54
    «не удалось» на ровном месте).

    Лечится тем, что в кэше вместе с точками лежит ССЫЛКА на полипас:
    пока запись жива, адрес не может достаться другому объекту.
    """
    from camsys.geometry.lead_inout import _cached_points, _SAMPLE_CACHE
    from camsys.geometry.primitives import Line, Polypath

    _SAMPLE_CACHE.clear()
    poly = Polypath(segments=[Line(a=(0.0, 0.0), b=(10.0, 0.0))], closed=False)
    pts = _cached_points(poly, 0.02)
    assert pts, 'выборка пустая'
    assert len(_SAMPLE_CACHE) == 1
    stored = next(iter(_SAMPLE_CACHE.values()))
    assert isinstance(stored, tuple) and len(stored) == 2, (
        'в кэше лежат только точки — ключ по id снова небезопасен')
    assert stored[0] is poly, 'кэш не держит ссылку на контур'
    assert stored[1] is pts
    # повторный вызов отдаёт то же самое, без пересчёта
    assert _cached_points(poly, 0.02) is pts


def test_preview_position_reproduces_bit_exactly():
    """Пост обязан повторить позицию вьювера БИТ В БИТ.

    Вьювер считает заход и отдаёт его посту, пост ставит старт туда и
    перепроверяет. Пока позиция передавалась КООРДИНАТОЙ, пост приходил
    в неё другой функцией (`shift_start_to_point` вместо
    `shift_start_along_contour`), и лид расходился в 11-м знаке —
    0.08 нм. На границе проверки этого хватало, чтобы вердикт
    перевернулся: вьювер считал позицию чистой, пост на той же точке
    видел коллизию и уезжал на 5 мм. В картинке заход стоял в одном
    месте, в программе — в другом (124173, Knife_26, 2.2 мм).

    Поэтому передаётся СДВИГ, и пост применяет его той же функцией.
    Здесь это и проверяется: повторный проход с полученным сдвигом даёт
    тот же старт и тот же лид до последнего бита.
    """
    import math
    from camsys.geometry.primitives import Polypath, Line, Arc
    from camsys.geometry.lead_collision import (
        PassLeadParams, plan_pass_leads, build_contours_cache)

    # Контур-«таблетка»: две прямые и две полуокружности.
    r, L = 6.0, 20.0
    segs = [
        Line(a=(0.0, -r), b=(L, -r)),
        Arc(a=(L, -r), b=(L, r), center=(L, 0.0), ccw=True),
        Line(a=(L, r), b=(0.0, r)),
        Arc(a=(0.0, r), b=(0.0, -r), center=(0.0, 0.0), ccw=True),
    ]
    pp = Polypath(segments=segs, closed=True)

    class _G:
        id = 'g1'
        is_closed = True
        polypath = pp

    g = _G()
    cl, cb = build_contours_cache([g])

    def _params(**kw):
        return PassLeadParams(
            pass_side='OUTSIDE', is_corner_rework=False,
            tool_offset=0.65, lead_scale=1.3,
            entry_enabled=True, entry_angle=45.0,
            entry_length=1.0, entry_radius=1.0,
            exit_enabled=True, exit_angle=45.0,
            exit_length=1.0, exit_radius=1.0, **kw)

    first = plan_pass_leads(pp, g, _params(), cl, cb)
    again = plan_pass_leads(
        pp, g, _params(entry_shift_hint=first.entry_shift), cl, cb)

    a1 = first.axis.segments[0].a
    a2 = again.axis.segments[0].a
    print(f'  сдвиг {first.entry_shift:.4f} мм; старт '
          f'({a1[0]!r}, {a1[1]!r}) → ({a2[0]!r}, {a2[1]!r})')
    assert a1 == a2, (
        f'старт не повторился: {a1} против {a2} — пост придёт не туда, '
        f'куда пришёл вьювер')

    for name, l1, l2 in (('заход', first.lead_in, again.lead_in),
                         ('выход', first.lead_out, again.lead_out)):
        assert (l1 is None) == (l2 is None), f'{name}: есть/нет не совпало'
        if l1 is None:
            continue
        assert len(l1.segments) == len(l2.segments), \
            f'{name}: число сегментов не совпало'
        for k, (s1, s2) in enumerate(zip(l1.segments, l2.segments)):
            assert s1.a == s2.a and s1.b == s2.b, (
                f'{name}, сегмент {k}: координаты разошлись — '
                f'{s1.a},{s1.b} против {s2.a},{s2.b}')
            if hasattr(s1, 'radius'):
                assert s1.radius == s2.radius and s1.ccw == s2.ccw, (
                    f'{name}, сегмент {k}: дуга разошлась')
    # И вердикт проверки обязан быть тем же — иначе пост «исправит»
    # позицию, которую вьювер уже признал чистой.
    assert first.lead_in_collision == again.lead_in_collision, \
        'вердикт по заходу изменился при повторе'
    assert first.lead_out_collision == again.lead_out_collision, \
        'вердикт по выходу изменился при повторе'


def test_shallow_lead_does_not_run_along_its_own_blade():
    """Пологий заход не идёт вдоль собственного лезвия.

    Угол захода 10° юзер ставит, когда лид не влезает («иногда даже 10
    приходится ставить»). При таком угле длина прямой = боковой зазор /
    sin(10°), то есть лид почти ложится на контур — и идёт вдоль него.

    Проверка этого не видела. Для СВОЕГО контура она мерила ОСЕВУЮ и
    освобождала зону по РАЗМЕРУ ЛИДА (0.8 его габарита): у пологого
    захода лид длинный, зона выходила в несколько миллиметров, и весь
    опасный участок оказывался внутри неё. На 124173, Knife_28 (круг
    R 4.9) заход шёл по собственному лезвию с зазором 0.000 мм при
    нужных 0.850 — и ни одна проверка не срабатывала.

    Теперь путь фрезы меряется до ТОЧНОЙ геометрии своего контура (дуги
    аналитически, без ломаной с хордой 0.5 мм, которая на скруглении
    R 0.3 врёт на 130 мкм), освобождается только окрестность стыка.
    """
    import math
    from camsys.geometry.primitives import Polypath, Arc
    from camsys.geometry.lead_collision import (
        PassLeadParams, plan_pass_leads, build_contours_cache,
        own_contour_clearance)
    from camsys.geometry.path_offset import _exact_axis_points

    # Круг R 4.9 — как Knife_28 заказа 124173.
    R, C = 4.9, (0.0, 0.0)
    pp = Polypath(segments=[
        Arc(a=(R, 0.0), b=(-R, 0.0), center=C, ccw=True),
        Arc(a=(-R, 0.0), b=(R, 0.0), center=C, ccw=True),
    ], closed=True)

    class _G:
        id = 'g1'
        is_closed = True
        polypath = pp

    g = _G()
    cl, cb = build_contours_cache([g])
    half = 0.85          # пятка 1.2 при 90°/0.25
    from camsys.geometry.direction import normalize_for_side

    worst = float('inf')
    worst_at = None
    for side in ('OUTSIDE', 'INSIDE'):
        # Намотку задаёт та же функция, что и в программе: без неё лид
        # строится в другую сторону и тест меряет не то.
        axis = normalize_for_side(pp, side)
        for ang in (10.0, 20.0, 45.0, 90.0):
            prm = PassLeadParams(
                pass_side=side, is_corner_rework=False,
                tool_offset=half, lead_scale=half * 2,
                entry_enabled=True, entry_angle=ang,
                entry_length=1.0, entry_radius=1.0,
                exit_enabled=True, exit_angle=ang,
                exit_length=1.0, exit_radius=1.0)
            res = plan_pass_leads(axis, g, prm, cl, cb)
            for name, lead, flagged in (
                    ('заход', res.lead_in, res.lead_in_collision),
                    ('выход', res.lead_out, res.lead_out_collision)):
                if not lead or not lead.segments:
                    continue
                pts = [q for _t, q in _exact_axis_points(lead, 0.01)[0]]
                cut = []
                for i in range(len(pts) - 1):
                    x0, y0 = pts[i]
                    x1, y1 = pts[i + 1]
                    dx, dy = x1 - x0, y1 - y0
                    L = math.hypot(dx, dy)
                    if L < 1e-12:
                        continue
                    cut.append((x0 + dy / L * half, y0 - dx / L * half))
                join = lead.segments[-1].b if name == 'заход' \
                    else lead.segments[0].a
                d = own_contour_clearance(cut, pp, join, half * 0.5)
                # На ИДЕАЛЬНОМ круге сдвиг старта не меняет ничего
                # (вращательная симметрия), а угол подбор только
                # понижает — значит при 10° чистого решения может не
                # быть вовсе. Требование тогда другое: не молча.
                if d < half - 0.05 and not flagged:
                    worst = min(worst, d)
                    worst_at = (side, ang, name)
                elif d < worst and flagged is False:
                    worst, worst_at = d, (side, ang, name)
    print(f'  худший НЕПОМЕЧЕННЫЙ зазор до своего лезвия {worst:.3f} мм '
          f'(надо {half:.3f}) {worst_at}')
    assert worst >= half - 0.05, (
        f'лид идёт по собственному лезвию и НЕ помечен коллизией: '
        f'зазор {worst:.3f} при нужных {half:.3f} ({worst_at})')


# ── v1.7.69: автоподбор внутреннего лида по ножу, а не по проекту ──

def _two_knife_project(lead_mode=0, flags=True, only=None):
    """Маленький кружок Ø3 и большой прямоугольник 40×30 в одном проекте."""
    from camsys.geometry.primitives import Line, Arc, Polypath
    from camsys.core.project import Project, Geometry
    prj = Project(name="MIX")
    lay = prj.add_layer("Knife")
    r = 1.5
    circ = Polypath(segments=[
        Arc(a=(r, 0.0), b=(-r, 0.0), center=(0.0, 0.0), ccw=True),
        Arc(a=(-r, 0.0), b=(r, 0.0), center=(0.0, 0.0), ccw=True)],
        closed=True)
    V = [(20.0, 0.0), (60.0, 0.0), (60.0, 30.0), (20.0, 30.0)]
    rect = Polypath(segments=[Line(a=V[i], b=V[(i + 1) % 4])
                              for i in range(4)], closed=True)
    for name, poly in (("Knife_1", circ), ("Knife_2", rect)):
        if only and name != only:
            continue
        g = Geometry(name=name, polypath=poly, source_layer="Knife")
        lay.geometries.append(g)
        prj.add_blade_operation(g.id)
    prj.attributes['lead_mode'] = lead_mode
    if flags:
        prj.attributes['lead_auto_inner'] = {'angle': True, 'length': True}
    return prj


def test_auto_inner_lead_is_per_knife():
    """Кружок получает свой 90°, прямоугольник — умолчание 45° / 1.0.

    Раньше подбор брал самый мелкий нож проекта и ставил его угол всем:
    «надо - не надо лепит 90 везде, даже в других заказах».
    """
    from camsys.geometry.lead_inout import auto_inner_lead_for_op
    prj = _two_knife_project()
    circ_op, rect_op = prj.operations[0], prj.operations[1]
    a = auto_inner_lead_for_op(prj, circ_op, 'OUTSIDE', 0.8)
    b = auto_inner_lead_for_op(prj, rect_op, 'OUTSIDE', 0.8)
    print(f'  кружок {a}, прямоугольник {b}')
    # Ø3: 90° и длина 0.8 — наибольшая, при которой заход влезает
    # (1.0 уходит в противоположную стенку, v1.7.71)
    assert a == {'angle': 90.0, 'length': 0.8}, a
    assert b == {'angle': 45.0, 'length': 1.0}
    # Внешний рез автоподбор не трогает
    assert auto_inner_lead_for_op(prj, circ_op, 'INSIDE', 0.8) is None
    # Ручная правка ножа побеждает
    circ_op.attributes['lead_override'] = {'lead_inside': {'angle': 30}}
    assert auto_inner_lead_for_op(prj, circ_op, 'OUTSIDE', 0.8) is None
    # «Все элементы» — поля панели как есть
    prj2 = _two_knife_project(lead_mode=1)
    assert auto_inner_lead_for_op(
        prj2, prj2.operations[0], 'OUTSIDE', 0.8) is None
    # Юзер трогал угол — угол его, длину ещё подбираем
    prj3 = _two_knife_project()
    prj3.attributes['lead_auto_inner'] = {'angle': False, 'length': True}
    c = auto_inner_lead_for_op(prj3, prj3.operations[0], 'OUTSIDE', 0.8)
    assert 'angle' not in c and 'length' in c


def test_auto_lead_small_knife_does_not_touch_big_one():
    """Прямоугольник в программе режется ровно как без автоподбора,
    а кружок — как если бы ему одному поставили 90° в поле панели."""
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.post.package_export import PackageExporter
    import camsys.post.mtx_anderson  # noqa

    def prog(prj, angle=45.0, length=1.0):
        p = CuttingMacroParams()
        p.knife_angle, p.tip_diameter, p.bottom = 90.0, 0.8, 0.25
        p.lead_inside.angle, p.lead_inside.length = angle, length
        files = PackageExporter(prj, p).generate(only_stages=('rough_all',))
        return [ln for ln in list(files.values())[0].splitlines()
                if 'NC-Program created on' not in ln]

    rect_auto = prog(_two_knife_project(flags=True, only='Knife_2'))
    rect_plain = prog(_two_knife_project(flags=False, only='Knife_2'))
    assert rect_auto == rect_plain, 'автоподбор изменил прямоугольник'
    circ_auto = prog(_two_knife_project(flags=True, only='Knife_1'))
    circ_plain = prog(_two_knife_project(flags=False, only='Knife_1'))
    circ_90 = prog(_two_knife_project(flags=False, only='Knife_1'),
                   angle=90.0, length=0.8)
    assert circ_auto != circ_plain, 'кружку автоподбор не дал своего лида'
    assert circ_auto == circ_90, 'кружок получил не то, что даёт поле 90°'


def test_auto_lead_viewer_matches_post_rule():
    """Вьювер рисует то же правило: прямоугольник как без автоподбора,
    кружок — как с полем 90°."""
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    try:
        from PySide6 import QtWidgets
    except Exception:
        print('  SKIP — нет PySide6')
        return
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.ui.viewer_2d import CamScene, add_toolpaths_to_scene

    keep = []

    def boxes(prj, angle=45.0, length=1.0):
        p = CuttingMacroParams()
        p.knife_angle, p.tip_diameter, p.bottom = 90.0, 0.8, 0.25
        p.lead_inside.angle = angle
        p.lead_inside.length = length
        sc = CamScene()
        keep.append(sc)
        items = add_toolpaths_to_scene(
            sc, prj, {'tool_radius': 0.4, 'tool_equidistant': 1.3}, p)
        names = {o.id: prj.get_geometry(o.geometry_ids[0]).name
                 for o in prj.operations}
        out = {}
        for i in items:
            oid = getattr(i, 'op_id', None)
            if oid not in names:
                continue
            r = i.boundingRect()
            out.setdefault(names[oid], []).append(
                (str(getattr(i, 'kind', '')), round(r.x(), 4),
                 round(r.y(), 4), round(r.width(), 4), round(r.height(), 4)))
        return out

    auto = boxes(_two_knife_project(flags=True))
    plain = boxes(_two_knife_project(flags=False))
    at90 = boxes(_two_knife_project(flags=False), angle=90.0, length=0.8)
    assert auto['Knife_2'] == plain['Knife_2'], 'прямоугольник изменился'
    assert auto['Knife_1'] != plain['Knife_1'], 'кружок без своего лида'
    assert auto['Knife_1'] == at90['Knife_1'], 'кружок не как с полем 90°'


def test_auto_inner_lead_fits_the_hole():
    """Длина автоподбора — наибольшая, при которой заход влезает в
    кружок (сверено с полным построением во вьювере), а одинаковые
    кружки на границе 90°/60° получают ОДИН угол."""
    import math as _m
    from camsys.geometry.primitives import Arc as _A, Polypath as _P
    from camsys.geometry.lead_inout import auto_inner_lead

    def circ(D, cx=0.0, cy=0.0):
        r = D / 2.0
        return _P(segments=[
            _A(a=(cx + r, cy), b=(cx - r, cy), center=(cx, cy), ccw=True),
            _A(a=(cx - r, cy), b=(cx + r, cy), center=(cx, cy), ccw=True)],
            closed=True)
    got = {D: auto_inner_lead(circ(D), 0.8, 1.3) for D in (2, 3, 4, 8)}
    print(f'  {got}')
    assert got[2] == (90.0, 0.4)
    assert got[3] == (90.0, 0.8)
    assert got[4] == (60.0, 1.0)
    assert got[8] == (45.0, 1.0)
    # Ø4 в разных местах листа (шум габарита) — один угол
    angles = {auto_inner_lead(circ(4.0, x, y), 0.8, 1.3)[0]
              for x, y in ((636.117, 84.0), (603.1, 51.0), (64.0, 479.0),
                           (381.6, 479.0), (643.8, 412.9))}
    assert len(angles) == 1, angles


def test_lead_check_measures_cutter_like_the_control():
    """Путь фрезы по лиду — дуга концентрическая, как у стойки: короткий
    заход в кружок Ø8 больше не «коллизия» (0.636 вместо 0.650 было
    ошибкой замера хордами, v1.7.71)."""
    import math as _m
    from camsys.geometry.primitives import Arc as _A, Polypath as _P
    from camsys.geometry.lead_inout import _inner_lead_fits
    for m in (1.0, 0.8, 0.6, 0.4, 0.2, 0.1):
        assert _inner_lead_fits(4.0, 45.0, m, 1.3), m
    assert not _inner_lead_fits(1.5, 90.0, 1.0, 1.3)
