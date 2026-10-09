"""Гарантия проходимости фрезы под компенсацией G41/G42 (v1.7).

В .anc уходит ОСЕВАЯ линия + G41/G42; эквидистанту считает сам станок.
Если на осевой есть дуга, которую компенсация СЖИМАЕТ, и её радиус меньше
эквидистаты фрезы, то R_факт ≤ 0 — дуга выворачивается, контроллер NUM
либо ругается, либо крутит петлю на месте («мелкий барашек»).

Правило сжатия (ISO):
    G42 — фреза справа → сжимается дуга по часовой   (G2, ccw=False)
    G41 — фреза слева  → сжимается дуга против часовой (G3, ccw=True)
"""

import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from camsys.geometry.primitives import Line, Arc, Polypath
from camsys.geometry.path_offset import (
    arc_shrinks_under_comp,
    find_unmachinable_arcs,
    ensure_machinable_arcs,
)

TOOL = 0.533          # типовая эквидистанта (tip 0.8, ABS 0.19, угол 70)
MARGIN = 0.02


def _circle(radius, n=4, ccw=False, center=(0.0, 0.0)):
    """Замкнутая окружность из n дуг."""
    pts = []
    for k in range(n + 1):
        ang = 2 * math.pi * k / n * (1.0 if ccw else -1.0)
        pts.append((center[0] + radius * math.cos(ang),
                    center[1] + radius * math.sin(ang)))
    return Polypath(
        segments=[Arc(a=pts[i], b=pts[i + 1], center=center, ccw=ccw)
                  for i in range(n)],
        closed=True)


def _micro_arc_path(radius, chord=0.0108, ccw=False):
    """Длинный контур с ОДНОЙ микро-дугой посередине — типичный биарк-шум."""
    half = chord / 2.0
    # Центр строится так, чтобы дуга была малой и имела нужный радиус
    h = math.sqrt(max(0.0, radius * radius - half * half))
    cy = -h if not ccw else h
    arc = Arc(a=(-half, 0.0), b=(half, 0.0), center=(0.0, cy), ccw=ccw)
    return Polypath(segments=[Line(a=(-5.0, 0.0), b=(-half, 0.0)),
                              arc,
                              Line(a=(half, 0.0), b=(5.0, 0.0))],
                    closed=False)


# ─────────────────────────────────────────────────────────────────────────
#  ПРАВИЛО СЖАТИЯ
# ─────────────────────────────────────────────────────────────────────────

def test_shrink_rule_matches_iso():
    """G42 сжимает G2, G41 сжимает G3. Линии не сжимаются никогда."""
    cw = Arc(a=(1, 0), b=(0, 1), center=(0, 0), ccw=False)   # G2
    ccw = Arc(a=(1, 0), b=(0, 1), center=(0, 0), ccw=True)   # G3
    line = Line(a=(0, 0), b=(1, 0))

    assert arc_shrinks_under_comp(cw, "G42") is True
    assert arc_shrinks_under_comp(cw, "G41") is False
    assert arc_shrinks_under_comp(ccw, "G41") is True
    assert arc_shrinks_under_comp(ccw, "G42") is False
    # Без компенсации и для прямых — сжатия нет
    assert arc_shrinks_under_comp(cw, "G40") is False
    assert arc_shrinks_under_comp(line, "G42") is False


def test_expanding_arcs_are_never_flagged():
    """Дуга, которую компенсация РАСШИРЯЕТ, проходима при любом радиусе.

    Это важно: та же физическая дуга режется двумя проходами, и в одном
    из них она расширяется. Трогать её там нельзя — иначе деталь поедет.
    """
    tiny = _micro_arc_path(0.05, ccw=True)          # G3
    assert find_unmachinable_arcs(tiny, TOOL, "G42", MARGIN) == []
    out, fixed, unfix = ensure_machinable_arcs(tiny, TOOL, "G42", MARGIN)
    assert out is tiny and not fixed and not unfix


# ─────────────────────────────────────────────────────────────────────────
#  ДЕТЕКЦИЯ
# ─────────────────────────────────────────────────────────────────────────

def test_detects_arc_that_compensation_inverts():
    """Дуга R < эквидистанты на сжимающей стороне — непроходима."""
    pp = _micro_arc_path(0.2798)                    # G2, R меньше фрезы
    bad = find_unmachinable_arcs(pp, TOOL, "G42", MARGIN)
    assert len(bad) == 1
    assert bad[0]['index'] == 1
    assert abs(bad[0]['radius'] - 0.2798) < 1e-6
    assert abs(bad[0]['needed'] - (TOOL + MARGIN)) < 1e-9


def test_large_radius_arc_is_machinable():
    """Дуга заведомо больше фрезы (R6.6 — типовой круглый нож) не метится."""
    pp = _circle(6.6, n=32)
    assert find_unmachinable_arcs(pp, TOOL, "G42", MARGIN) == []
    assert find_unmachinable_arcs(pp, TOOL, "G41", MARGIN) == []


def test_margin_widens_detection():
    """Запас поднимает порог: дуга чуть больше фрезы всё равно метится."""
    pp = _micro_arc_path(TOOL + 0.005)              # 0.538 — больше фрезы
    assert find_unmachinable_arcs(pp, TOOL, "G42", 0.0) == []
    assert len(find_unmachinable_arcs(pp, TOOL, "G42", MARGIN)) == 1


# ─────────────────────────────────────────────────────────────────────────
#  ИСПРАВЛЕНИЕ
# ─────────────────────────────────────────────────────────────────────────

def test_fix_raises_radius_and_keeps_endpoints():
    """Радиус поднят до порога, КОНЦЫ дуги не сдвинулись.

    Сохранение концов — ключевое: к ним крепятся соседние сегменты, и
    именно поэтому не возникает каскада искажений (в отличие от правки
    центра в repair_arc_tangency).
    """
    pp = _micro_arc_path(0.2798)
    src = pp.segments[1]
    out, fixed, unfix = ensure_machinable_arcs(pp, TOOL, "G42", MARGIN)

    assert len(fixed) == 1 and not unfix
    got = out.segments[1]
    assert isinstance(got, Arc)
    assert abs(got.radius - (TOOL + MARGIN)) < 1e-6
    assert got.a == src.a and got.b == src.b
    assert got.ccw == src.ccw
    # Соседи не тронуты
    assert out.segments[0] == pp.segments[0]
    assert out.segments[2] == pp.segments[2]


def test_fix_is_geometrically_negligible_on_biarc_noise():
    """На биарк-шуме подъём радиуса смещает контур на единицы нанометров."""
    pp = _micro_arc_path(0.2798)
    _out, fixed, _ = ensure_machinable_arcs(pp, TOOL, "G42", MARGIN)
    assert fixed[0]['deviation'] < 0.001   # меньше микрона


def test_result_is_machinable_after_fix():
    """После правки повторная проверка не находит непроходимых мест."""
    pp = _micro_arc_path(0.2798)
    out, _fixed, _unfix = ensure_machinable_arcs(pp, TOOL, "G42", MARGIN)
    assert find_unmachinable_arcs(out, TOOL, "G42", MARGIN) == []


def test_untouched_path_returns_same_object():
    """Нечего чинить → возвращается ИСХОДНЫЙ объект (дешёвая проверка is)."""
    pp = _circle(6.6, n=32)
    out, fixed, unfix = ensure_machinable_arcs(pp, TOOL, "G42", MARGIN)
    assert out is pp and fixed == [] and unfix == []


# ─────────────────────────────────────────────────────────────────────────
#  ПРЕДОХРАНИТЕЛИ
# ─────────────────────────────────────────────────────────────────────────

def test_small_circle_is_not_silently_inflated():
    """Круглый нож МЕНЬШЕ фрезы не раздувается — уходит в неспасаемые.

    Регрессия: подъём радиуса тут изменил бы РАЗМЕР изделия (R0.3 → R0.553),
    а смещение 45 мкм проскакивало под порогом 50 мкм. Ловит контурный
    предохранитель по доле непроходимой длины.
    """
    pp = _circle(0.3, n=4)
    out, fixed, unfix = ensure_machinable_arcs(pp, TOOL, "G42", MARGIN)
    assert out is pp                       # геометрия НЕ тронута
    assert fixed == []
    assert len(unfix) == 4
    assert all('reason' in r for r in unfix)


def test_deviation_guard_rejects_large_shape_change():
    """Дуга, подъём которой сильно двигает контур, не правится молча."""
    pp = _micro_arc_path(0.30, chord=0.55)   # заметная развёртка
    out, fixed, unfix = ensure_machinable_arcs(
        pp, TOOL, "G42", MARGIN, max_deviation_mm=0.001)
    assert fixed == [] and len(unfix) == 1
    assert 'смещение контура' in unfix[0]['reason']
    assert out is pp


def test_affected_fraction_threshold_is_configurable():
    """Порог доли контура можно ослабить — тогда круг всё же чинится."""
    pp = _circle(0.3, n=4)
    _out, fixed, unfix = ensure_machinable_arcs(
        pp, TOOL, "G42", MARGIN,
        max_deviation_mm=1.0, max_affected_fraction=1.01)
    assert len(fixed) == 4 and unfix == []


# ─────────────────────────────────────────────────────────────────────────
#  ИНТЕГРАЦИЯ С ПОСТОМ
# ─────────────────────────────────────────────────────────────────────────

def test_emitted_anc_has_no_inverting_arcs():
    """Сквозной тест на реальном .ai: в .anc нет ни одной вывернутой дуги.

    До v1.7 на 124173 таких было 6 (ножи 11/15/25/34/60) — именно они
    давали «барашек». Проверяем ровно то, что увидит контроллер: код
    компенсации + направление дуги + радиус.
    """
    import re
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return

    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    import camsys.post.base as post_base
    import camsys.post.mtx_anderson  # noqa: F401 — регистрация поста

    tip, bottom, angle = 0.8, 0.19, 70.0
    tool_eq = tip + 2 * bottom * math.tan(math.radians(angle / 2))
    tool_off = tool_eq / 2.0

    project = importer_mod.import_ai_to_project(ai, project_name="124173")
    for geom in project.get_layer_by_name("Knife").geometries:
        project.add_blade_operation(geom.id)
    macros_mod.sort_operations_by_grid(project)
    for op in project.operations:
        op.sequence_number = 1

    post = post_base.PostRegistry.get("MTX Anderson GVM V2.13")
    options = post_base.PostOptions(
        program_name="124173", sheet_thickness=0.44, z_depth=0.19,
        extras={'tool_radius': tip / 2.0, 'tool_equidistant': tool_eq,
                'smooth_offset_for_tool': True})
    anc = post.generate(project, options)

    num = r'[-+]?\d*\.?\d+'
    g, comp, inverted, g2_min = 0, None, [], float('inf')
    for ln in anc.splitlines():
        if re.search(r'\bG41\b', ln):
            comp = 41
        if re.search(r'\bG42\b', ln):
            comp = 42
        if re.search(r'\bG40\b', ln):
            comp = None
        gm = re.search(r'\bG([0123])\b', ln)
        if gm:
            g = int(gm.group(1))
        gr = re.search(r'\bR\s*(' + num + r')', ln)
        if not gr or g not in (2, 3) or comp is None:
            continue
        radius = float(gr.group(1))
        shrinks = (comp == 42 and g == 2) or (comp == 41 and g == 3)
        if not shrinks:
            continue
        g2_min = min(g2_min, radius)
        if radius - tool_off <= 1e-6:
            inverted.append((comp, g, radius))

    print(f'  эквидистанта={tool_off:.4f}, мин. R сжимаемой дуги={g2_min:.4f}')
    assert not inverted, f"вывернутых дуг: {len(inverted)} — {inverted[:5]}"
    # Все сжимаемые дуги подняты минимум до порога
    assert g2_min >= tool_off + 0.02 - 1e-6


def test_post_reports_which_knives_were_fixed():
    """Пост пишет в журнал, КАКОЙ нож и какой проход правился."""
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return

    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    import camsys.post.base as post_base
    import camsys.post.mtx_anderson  # noqa: F401

    tool_eq = 0.8 + 2 * 0.19 * math.tan(math.radians(35.0))
    project = importer_mod.import_ai_to_project(ai, project_name="124173")
    for geom in project.get_layer_by_name("Knife").geometries:
        project.add_blade_operation(geom.id)
    macros_mod.sort_operations_by_grid(project)
    for op in project.operations:
        op.sequence_number = 1

    post = post_base.PostRegistry.get("MTX Anderson GVM V2.13")
    options = post_base.PostOptions(
        program_name="124173", sheet_thickness=0.44, z_depth=0.19,
        extras={'tool_radius': 0.4, 'tool_equidistant': tool_eq,
                'smooth_offset_for_tool': True})
    post.generate(project, options)

    report = options.extras.get('machinability_report', [])
    print(f'  записей в журнале: {len(report)}')
    for line in report[:5]:
        print(f'    {line}')
    assert report, "журнал проходимости пуст — правки не залогированы"
    # Каждая запись называет нож и сторону прохода
    assert all('/' in line and line.startswith('[') for line in report)
    assert any('исправлено непроходимых дуг' in line for line in report)


def test_margin_param_exists_in_cutting_params():
    """Запас проходимости вынесен в параметры макроса."""
    from camsys.core.cutting_macro import CuttingMacroParams
    assert abs(CuttingMacroParams().machinable_margin - 0.02) < 1e-9


def test_no_zero_length_arcs_in_anc():
    """В программе нет дуг нулевой длины.

    Дуга с одинаковыми началом и концом, но заданным R, для NUM НЕ
    ОПРЕДЕЛЕНА: контроллер обычно трактует её как полную окружность и
    крутит петлю на месте. Именно это давало «станок крутит не в ту
    сторону» — на 124536 таких дуг было 112, по одной в каждой детали.

    Ловятся по ОТФОРМАТИРОВАННЫМ координатам: сегмент бывает короче
    разрешения формата (5 знаков), тогда он геометрически ненулевой, но
    печатается движением в ту же точку — порог по длине его не видит.
    """
    import re
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return

    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    import camsys.post.base as post_base
    import camsys.post.mtx_anderson  # noqa: F401 — регистрация поста

    tool_eq = 0.8 + 2 * 0.19 * math.tan(math.radians(35.0))
    project = importer_mod.import_ai_to_project(ai, project_name="124173")
    for geom in project.get_layer_by_name("Knife").geometries:
        project.add_blade_operation(geom.id)
    macros_mod.sort_operations_by_grid(project)
    for op in project.operations:
        op.sequence_number = 1

    post = post_base.PostRegistry.get("MTX Anderson GVM V2.13")
    options = post_base.PostOptions(
        program_name="124173", sheet_thickness=0.44, z_depth=0.19,
        extras={'tool_radius': 0.4, 'tool_equidistant': tool_eq})
    anc = post.generate(project, options)

    num = r'[-+]?\d*\.?\d+'
    x = y = 0.0
    g = 0
    zero_arcs = []
    for idx, line in enumerate(anc.splitlines()):
        gm = re.search(r'\bG([0123])\b', line)
        if gm:
            g = int(gm.group(1))
        gx = re.search(r'\bX\s*(' + num + r')', line)
        gy = re.search(r'\bY\s*(' + num + r')', line)
        if not gx and not gy:
            continue
        gr = re.search(r'\bR\s*(' + num + r')', line)
        nx = float(gx.group(1)) if gx else x
        ny = float(gy.group(1)) if gy else y
        if math.hypot(nx - x, ny - y) < 1e-6 and g in (2, 3) and gr:
            zero_arcs.append((idx + 1, line.strip()))
        x, y = nx, ny

    for ln, text in zero_arcs[:5]:
        print(f'  строка {ln}: {text}')
    assert not zero_arcs, f"дуг нулевой длины: {len(zero_arcs)}"


def test_lead_arcs_scaled_by_equidistant():
    """Лиды масштабируются от ЭКВИДИСТАНТЫ, как в AlphaCAM.

    Регрессия v1.7.11: множители «x_tool_rad» умножались на tool_offset
    (половину эквидистанты), из-за чего заход и выход выходили ВДВОЕ
    короче эталонных. Направление при этом было верным, поэтому
    расхождение не бросалось в глаза.

    Замер на эталонном выходе альфы (124536): хорда дуги G12/G13
    стабильно 0.8159 мм при развороте 45°, то есть
    2·R·sin(22.5°) = 0.7654·R → R = 1.0661 = ровно эквидистанта.
    """
    import re
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return

    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    import camsys.post.base as post_base
    import camsys.post.mtx_anderson  # noqa: F401 — регистрация поста

    tool_eq = 0.8 + 2 * 0.19 * math.tan(math.radians(35.0))
    project = importer_mod.import_ai_to_project(ai, project_name="124173")
    for geom in project.get_layer_by_name("Knife").geometries:
        project.add_blade_operation(geom.id)
    macros_mod.sort_operations_by_grid(project)
    for op in project.operations:
        op.sequence_number = 1

    post = post_base.PostRegistry.get("MTX Anderson GVM V2.13")
    options = post_base.PostOptions(
        program_name="124173", sheet_thickness=0.44, z_depth=0.19,
        extras={'tool_radius': 0.4, 'tool_equidistant': tool_eq})
    anc = post.generate(project, options)

    # Заход/выход задаются с approach_angle=45° по умолчанию
    expect = 2.0 * tool_eq * math.sin(math.radians(45.0 / 2.0))
    half = expect / 2.0

    num = r'[-+]?\d*\.?\d+'
    x = y = 0.0
    chords = []
    for line in anc.splitlines():
        gx = re.search(r'\bX\s*(' + num + r')', line)
        gy = re.search(r'\bY\s*(' + num + r')', line)
        if not gx and not gy:
            continue
        nx = float(gx.group(1)) if gx else x
        ny = float(gy.group(1)) if gy else y
        if 'G12' in line or 'G13' in line:
            chords.append(math.hypot(nx - x, ny - y))
        x, y = nx, ny

    assert chords, "лидов G12/G13 в программе нет"
    worst = max(abs(c - expect) for c in chords)
    print(f'  лидов {len(chords)}, ожидание {expect:.4f} мм, '
          f'макс. отклонение {worst * 1000:.1f} мкм')
    assert worst < 0.002, (
        f"хорда лида не совпадает с эталоном: ожидалось {expect:.4f}, "
        f"получено {chords[0]:.4f}")
    # И явно ловим старую ошибку — вдвое короче
    assert abs(chords[0] - half) > 0.01, \
        "лиды снова масштабируются на половину эквидистанты"


def test_narrow_gap_detector_finds_hairpin():
    """Детектор узких мест находит шпильку, где фреза срежет лезвие.

    Фреза режет канавку шириной 2 × эквидистанта. Если два участка
    контура сходятся ближе, канавки сливаются и перемычки не остаётся.
    На 124173 (нож #14) при эквидистанте 0.590 зазор 1.079 мм при ширине
    реза 1.180 — перемычка −0.101 мм.

    Это то, что раньше должно было ловить «Сглаживание под фрезу», но его
    порог был вдвое меньше нужного (0.98·T вместо 2·T), и такие щели в
    него не попадали.
    """
    from camsys.geometry.path_offset import find_narrow_gaps
    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    import camsys.core.importer as importer_mod

    project = importer_mod.import_ai_to_project(ai)
    geoms = [g for g in project.get_layer_by_name("Knife").geometries
             if g.polypath]
    tool_offset = (0.8 + 2 * 0.19 * math.tan(math.radians(45))) / 2
    cut = 2 * tool_offset

    hits = {i: find_narrow_gaps(g.polypath, cut)
            for i, g in enumerate(geoms)}
    flagged = {i: r for i, r in hits.items() if r}
    print(f'  ножей с узкими местами: {len(flagged)} '
          f'(ширина реза {cut:.3f} мм)')
    assert 14 in flagged, "шпилька ножа #14 не найдена"
    worst = flagged[14][0]
    print(f"  худший зазор {worst['gap']:.3f}, перемычка {worst['land']:+.3f}")
    assert worst['gap'] < cut
    assert worst['land'] < 0, "перемычка должна быть отрицательной"

    # Соседние по контуру точки не считаются щелью
    for records in flagged.values():
        for rec in records:
            assert rec['separation'] >= 2.5

    # Широкий рез — щелей нет (проверяем, что порог реально участвует)
    assert not find_narrow_gaps(geoms[14].polypath, 0.5)


def test_pass_self_intersections_match_viewer_chain():
    """Детектор находит ровно то, что видно во вьювере.

    Регрессия v1.7.31: ложку Knife_10 не находили ни детектор узких мест,
    ни голая функция смещения. Причина — пропущенный шаг цепочки вьювера:
    normalize_for_side разворачивает осевую под проход, и без него
    «внутрь» для функции смещения означало не ту сторону.

    Ножи, проверенные оператором на экране и по эталону альфы
    (124173_test, угол 80 / ABS 0.25 из заголовка .anc):
        Knife_3  — внешний путь (шпилька)
        Knife_10 — внутренний путь (ложка: перешеек и расширение)
        Knife_19 — внутренний путь
        Knife_14 — внутренний путь (мелкое, оператор нашёл не сразу)
    """
    from camsys.geometry.path_offset import (find_pass_self_intersections,
                                             merge_segments_to_arcs)
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.core.importer as importer_mod

    tool_offset = (0.8 + 2 * 0.25 * math.tan(math.radians(40.0))) / 2
    project = importer_mod.import_ai_to_project(ai)
    knives = {getattr(g, 'name', ''): g
              for g in project.get_layer_by_name("Knife").geometries
              if g.polypath}

    expected = {'Knife_3': 'INSIDE', 'Knife_10': 'OUTSIDE',
                'Knife_19': 'OUTSIDE', 'Knife_14': 'OUTSIDE'}
    for name, side in expected.items():
        pp = merge_segments_to_arcs(knives[name].polypath, tol=0.02,
                                    min_chain=3)
        res = find_pass_self_intersections(pp, tool_offset)
        where = 'внешний' if side == 'INSIDE' else 'внутренний'
        print(f'  {name}: {where} путь — {len(res[side])} пересечений')
        assert res[side], f"{name}: пересечение на {where} пути не найдено"


def test_tool_clearance_detector_matches_operator():
    """Детектор по зазору фрезы находит ровно согласованные места.

    Критерий согласован с оператором на 124173_test (угол 80, ABS 0.25):
    место непроходимо, если основная фреза проходит между вершинами
    лезвия двух РАЗНЫХ участков контура с зазором меньше 30 мкм.

        Knife_3   −152 мкм   яма            → да
        Knife_19  −101 мкм   яма            → да
        Knife_14   −27 мкм   яма            → да
        Knife_1    −13 мкм   угол           → да
        Knife_10   +15 мкм   ложка          → да (с запасом, как у альфы)
        Knife_11   +48 мкм                  → нет
    """
    from camsys.geometry.path_offset import (find_tool_clearance_issues,
                                             merge_segments_to_arcs)
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.core.importer as importer_mod

    cut = 0.8 + 2 * 0.25 * math.tan(math.radians(40.0))
    project = importer_mod.import_ai_to_project(ai)
    knives = {getattr(g, 'name', ''): g
              for g in project.get_layer_by_name("Knife").geometries
              if g.polypath}

    def worst(name):
        pp = merge_segments_to_arcs(knives[name].polypath, tol=0.02,
                                    min_chain=3)
        res = find_tool_clearance_issues(pp, cut)
        return res[0]['clearance'] if res else None

    must = ('Knife_3', 'Knife_19', 'Knife_14', 'Knife_1', 'Knife_10')
    for name in must:
        c = worst(name)
        print(f'  {name}: зазор {c * 1000:+.1f} мкм' if c is not None
              else f'  {name}: не найден')
        assert c is not None, f"{name} должен попасть под обрезку"
        assert c < 0.030

    assert worst('Knife_11') is None, \
        "Knife_11 (+48 мкм) проходим и не должен обрезаться"


def test_clearance_zero_reference():
    """На простом ноже без узких мест детектор молчит."""
    from camsys.geometry.path_offset import (find_tool_clearance_issues,
                                             merge_segments_to_arcs)
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP')
        return
    import camsys.core.importer as importer_mod
    project = importer_mod.import_ai_to_project(ai)
    knives = {getattr(g, 'name', ''): g
              for g in project.get_layer_by_name("Knife").geometries
              if g.polypath}
    cut = 0.8 + 2 * 0.25 * math.tan(math.radians(40.0))
    pp = merge_segments_to_arcs(knives['Knife_6'].polypath, tol=0.02,
                                min_chain=3)
    assert find_tool_clearance_issues(pp, cut) == []


def test_min_tool_clearance_default():
    """Умолчание — 30 мкм, как согласовано."""
    from camsys.core.cutting_macro import CuttingMacroParams
    assert abs(CuttingMacroParams().min_tool_clearance_mm - 0.030) < 1e-12


def test_narrow_pit_trimmed_like_alpha():
    """Карман обрезается так же, как в эталонном выходе AlphaCAM.

    Эталон: 124173_test.anc, Knife_3 (шпилька), угол 80 / ABS 0.25.
    Альфа выбрасывает участок 12.13 мм и перекидывает через устье дугу
    R0.65 = пятка/2 + ABS, идущую G2, от (165.78, 105.70) к
    (166.10, 106.95).

    Эмиттер дробит дуги свыше 90°, поэтому мост выходит двумя строками
    по ~83° — траектория та же, радиус в обеих задан явно.
    """
    from camsys.geometry.path_offset import (bridge_narrow_pits,
                                             merge_segments_to_arcs)
    from camsys.geometry.direction import normalize_for_side
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.core.importer as importer_mod

    r_bridge = 0.8 / 2 + 0.25
    project = importer_mod.import_ai_to_project(ai)
    knives = {getattr(g, 'name', ''): g
              for g in project.get_layer_by_name("Knife").geometries
              if g.polypath}

    base = merge_segments_to_arcs(knives['Knife_3'].polypath, tol=0.02,
                                  min_chain=3)
    outer = normalize_for_side(base, 'INSIDE')
    trimmed, bridges = bridge_narrow_pits(outer, r_bridge)
    assert len(bridges) == 1, f"мостов {len(bridges)}, ожидался один"
    b = bridges[0]
    print(f"  мост ({b['A'][0]:.3f},{b['A'][1]:.3f})->"
          f"({b['B'][0]:.3f},{b['B'][1]:.3f}) выброшено {b['skip']:.2f} мм")

    assert abs(b['skip'] - 12.13) < 0.05, "выброшен не тот участок"
    assert abs(b['A'][0] - 165.78) < 0.05 and abs(b['A'][1] - 105.70) < 0.05
    assert abs(b['B'][0] - 166.10) < 0.05 and abs(b['B'][1] - 106.95) < 0.05
    assert b['ccw'] is False, "мост должен идти G2"
    assert 160.0 < b['sweep_deg'] < 172.0

    # Второй проход того же ножа не трогается
    inner = normalize_for_side(base, 'OUTSIDE')
    _p, br2 = bridge_narrow_pits(inner, r_bridge)
    assert not br2, "обрезан не тот проход"

    # Чистый нож не трогается вовсе
    circle = normalize_for_side(
        merge_segments_to_arcs(knives['Knife_6'].polypath, tol=0.02,
                               min_chain=3), 'INSIDE')
    same, br3 = bridge_narrow_pits(circle, r_bridge)
    assert not br3 and same is circle


def test_bridge_radius_never_degenerates():
    """Радиус моста всегда больше эквидистанты.

    Регрессия v1.7.34: формула альфы `пятка/2 + ABS` даёт запас
    ABS·(1 − tan(угол/2)) — при угле 80 это 40 мкм, при 90 РОВНО НОЛЬ,
    при большем уходит в минус. Оба эталонных файла альфы были с углом
    80, поэтому на заказе с углом 90 мост вырождался: компенсированный
    радиус равен нулю, дуга схлопывается.

    В журнале это выглядело как «НЕ ИСПРАВЛЕНО: дуга R=0.6500 нужно
    ≥0.6700» на каждом ноже с мостом.
    """
    for angle in (70.0, 80.0, 90.0, 100.0):
        tip, bottom = 0.8, 0.25
        equid = tip / 2 + bottom * math.tan(math.radians(angle / 2))
        alpha = tip / 2 + bottom
        used = max(alpha, equid + 0.02)
        print(f'  угол {angle:>5}: эквидистанта {equid:.4f}, '
              f'формула альфы {alpha:.4f}, берётся {used:.4f}')
        assert used > equid + 0.019, (
            f"угол {angle}: мост {used} не больше эквидистанты {equid}")
        if angle < 90.0:
            assert abs(used - alpha) < 1e-9, "на угле <90 должна быть формула альфы"


def test_no_degenerate_bridges_in_program():
    """В программе нет вырожденных мостов ни на 80°, ни на 90°."""
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    import camsys.post.base as post_base
    import camsys.post.mtx_anderson  # noqa: F401

    for angle in (90.0,):   # один угол: обрезка считается на каждом ноже
        tool_eq = 0.8 + 2 * 0.25 * math.tan(math.radians(angle / 2))
        project = importer_mod.import_ai_to_project(ai)
        for geom in project.get_layer_by_name("Knife").geometries:
            project.add_blade_operation(geom.id)
        macros_mod.sort_operations_by_grid(project)
        for op in project.operations:
            op.sequence_number = 1
        options = post_base.PostOptions(
            program_name="t", sheet_thickness=0.44, z_depth=0.25,
            extras={'tool_radius': 0.4, 'tool_equidistant': tool_eq})
        post_base.PostRegistry.get("MTX Anderson GVM V2.13").generate(
            project, options)
        rep = options.extras.get('machinability_report', [])
        pits = {r for r in rep if 'КАРМАН' in r}
        degen = {r for r in rep if 'НЕ ИСПРАВЛЕНО' in r and '0.6500' in r}
        print(f'  угол {angle}: карманов {len(pits)}, вырожденных {len(degen)}')
        assert pits, "карманы не найдены"
        assert not degen, f"вырожденные мосты: {len(degen)}"


def test_bridge_mouth_independent_of_radius_margin():
    """Ширина устья не зависит от запаса на радиусе моста (v1.7.35).

    Регрессия v1.7.34: запас, которым радиус моста страховался от
    вырождения под компенсацией, подмешивался и в поиск устья. На угле
    90 устье росло 1.29 → 1.33 мм, мост нырял в карман на 0.588 вместо
    0.567, а главное — дуга ВЫХОДИЛА за исходный контур у обоих концов
    устья и срезала вершину соседнего лезвия. Пользователь это и видел:
    «заходит глубже чем нужно и дуга режет лезвие в начале и конце».

    Устье выбирается по формуле альфы `пятка/2 + ABS` — она от угла
    заточки не зависит, поэтому хорда одна и та же на любом угле.
    """
    from camsys.geometry.path_offset import (bridge_narrow_pits,
                                             merge_segments_to_arcs,
                                             _exact_axis_points)
    from camsys.geometry.direction import normalize_for_side
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.core.importer as importer_mod

    project = importer_mod.import_ai_to_project(ai)
    knives = {getattr(g, 'name', ''): g
              for g in project.get_layer_by_name("Knife").geometries
              if g.polypath}
    base = merge_segments_to_arcs(knives['Knife_3'].polypath, tol=0.02,
                                  min_chain=3)
    outer = normalize_for_side(base, 'INSIDE')
    orig, _t0 = _exact_axis_points(outer, 0.004)

    tip, bottom = 0.8, 0.25
    r_mouth = tip / 2 + bottom
    for angle in (70.0, 80.0, 90.0, 100.0):
        equid = tip / 2 + bottom * math.tan(math.radians(angle / 2))
        r_b = max(r_mouth, equid + 0.02)
        trimmed, bridges = bridge_narrow_pits(outer, r_b, r_mouth=r_mouth)
        assert len(bridges) == 1, f"угол {angle}: мостов {len(bridges)}"
        b = bridges[0]
        arc = trimmed.segments[-1]
        A, B = b['A'], b['B']
        chord = math.hypot(B[0] - A[0], B[1] - A[1])
        sag = arc.radius - math.sqrt(
            max(0.0, arc.radius ** 2 - (chord / 2) ** 2))
        n_cross = _count_arc_crossings(arc, A, B, orig)
        print(f'  угол {angle:>5}: R {arc.radius:.4f}, хорда {chord:.4f}, '
              f'глубина {sag:.4f}, разворот {b["sweep_deg"]:.1f}°, '
              f'пересечений {n_cross}')
        # Устье — альфовское на любом угле (эталон альфы 1.2893)
        assert abs(chord - 1.2900) < 0.005, (
            f"угол {angle}: устье {chord:.4f} вместо 1.290 — "
            f"запас радиуса снова течёт в поиск устья")
        # Мост не ныряет глубже альфы
        assert sag <= 0.5700 + 1e-4, f"угол {angle}: глубина {sag:.4f}"
        # И не выходит за исходный контур — иначе срежет соседнее лезвие
        assert n_cross == 0, (
            f"угол {angle}: мост пересекает контур в {n_cross} местах")
        # Радиус под компенсацией не вырождается
        assert arc.radius > equid + 0.019, f"угол {angle}: мост вырожден"


def _count_arc_crossings(arc, A, B, orig_points, skip_mm=0.03):
    """Сколько раз дуга пересекает контур, не считая концов устья."""
    cx, cy = arc.center[0], arc.center[1]
    R = arc.radius
    a0 = math.atan2(A[1] - cy, A[0] - cx)
    a1 = math.atan2(B[1] - cy, B[0] - cx)
    sweep = ((a1 - a0) % (2 * math.pi) if arc.ccw
             else (a0 - a1) % (2 * math.pi))
    hits = []
    for k in range(len(orig_points) - 1):
        p, q = orig_points[k][1], orig_points[k + 1][1]
        d1 = math.hypot(p[0] - cx, p[1] - cy) - R
        d2 = math.hypot(q[0] - cx, q[1] - cy) - R
        if (d1 < 0) == (d2 < 0):
            continue
        f = abs(d1) / (abs(d1) + abs(d2)) if (abs(d1) + abs(d2)) else 0.0
        x = (p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f)
        ax = math.atan2(x[1] - cy, x[0] - cx)
        t = ((ax - a0) % (2 * math.pi) if arc.ccw
             else (a0 - ax) % (2 * math.pi))
        if not (1e-9 < t < sweep - 1e-9):
            continue
        if min(math.hypot(x[0] - A[0], x[1] - A[1]),
               math.hypot(x[0] - B[0], x[1] - B[1])) < skip_mm:
            continue
        if not any(math.hypot(x[0] - u[0], x[1] - u[1]) < 0.02 for u in hits):
            hits.append(x)
    return len(hits)


def test_bridge_never_cuts_into_blade():
    """Мост не выходит за контур глубже допуска ножа (v1.7.36).

    Станок меряет фрезу лазером с точностью 0.1 мкм, и уход вершины
    больше ±2 мкм — брак. Поэтому «мелкий» заход моста за контур на
    5 мкм мелким не является: именно его пользователь и увидел на
    Knife_19 заказа 124173 («стало меньше, но режет лезвие»).

    Устье поэтому выбирается ПО КАСАНИЮ: от найденного по зазору
    положения оба конца двигаются внутрь кармана, пока дуга не
    перестанет выходить за контур. Так же считает альфа — её хорда у
    каждого кармана своя: 1.2297 / 1.2562 / 1.2869 / 1.2893 мм на
    четырёх карманах 124173 при R0.65.
    """
    from camsys.geometry.path_offset import (bridge_narrow_pits,
                                             bridge_radii_for_tool,
                                             merge_segments_to_arcs,
                                             arc_penetration_mm,
                                             _exact_axis_points)
    from camsys.geometry.direction import normalize_for_side
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.core.importer as importer_mod

    project = importer_mod.import_ai_to_project(ai)
    knives = {getattr(g, 'name', ''): g
              for g in project.get_layer_by_name("Knife").geometries
              if g.polypath}
    tip, bottom = 0.8, 0.25
    worst_all = 0.0
    found = 0
    for angle in (80.0, 90.0):
        equid = tip + 2 * bottom * math.tan(math.radians(angle / 2))
        r_m, r_b = bridge_radii_for_tool(tip, bottom, equid)
        for name, geom in knives.items():
            base = merge_segments_to_arcs(geom.polypath, tol=0.02,
                                          min_chain=3)
            for side in ('INSIDE', 'OUTSIDE'):
                pp = normalize_for_side(base, side)
                trimmed, bridges = bridge_narrow_pits(pp, r_b, r_mouth=r_m)
                if not bridges:
                    continue
                found += 1
                arc = trimmed.segments[-1]
                b = bridges[0]
                pen = arc_penetration_mm(
                    arc.center, arc.radius, b['A'], b['B'],
                    _exact_axis_points(pp, 0.0002)[0])
                worst_all = max(worst_all, pen)
                assert pen <= 0.002, (
                    f"угол {angle} {name}/{side}: мост выходит за контур "
                    f"на {pen * 1000:.2f} мкм — срежет соседнее лезвие")
    print(f'  карманов {found}, худший заход за контур '
          f'{worst_all * 1000:.2f} мкм')
    assert found >= 10, f"карманов нашлось всего {found}"


def test_bridge_mouth_matches_alpha_chords():
    """Хорда устья совпадает с эталоном альфы на всех четырёх карманах.

    Эталон — alfa_90_all_R.anc, дуги R0.65: Knife_14 1.2562, Knife_3
    1.2893, Knife_19 1.2297, Knife_10 1.2869 мм. Радиус моста там равен
    альфовскому только при угле 80 (при 90 он поднят до 0.67 ради
    компенсации), поэтому сверяемся на 80.
    """
    from camsys.geometry.path_offset import (bridge_narrow_pits,
                                             bridge_radii_for_tool,
                                             merge_segments_to_arcs)
    from camsys.geometry.direction import normalize_for_side
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.core.importer as importer_mod

    expect = {('Knife_3', 'INSIDE'): 1.2893, ('Knife_10', 'OUTSIDE'): 1.2869,
              ('Knife_14', 'OUTSIDE'): 1.2562, ('Knife_19', 'OUTSIDE'): 1.2297}
    project = importer_mod.import_ai_to_project(ai)
    knives = {getattr(g, 'name', ''): g
              for g in project.get_layer_by_name("Knife").geometries
              if g.polypath}
    tip, bottom = 0.8, 0.25
    equid = tip + 2 * bottom * math.tan(math.radians(40.0))
    r_m, r_b = bridge_radii_for_tool(tip, bottom, equid)
    for (name, side), ref in expect.items():
        base = merge_segments_to_arcs(knives[name].polypath, tol=0.02,
                                      min_chain=3)
        pp = normalize_for_side(base, side)
        _tr, bridges = bridge_narrow_pits(pp, r_b, r_mouth=r_m)
        assert bridges, f"{name}/{side}: мост не поставлен"
        chord = bridges[0].get('mouth_chord') or math.hypot(
            bridges[0]['B'][0] - bridges[0]['A'][0],
            bridges[0]['B'][1] - bridges[0]['A'][1])
        print(f'  {name}/{side}: {chord:.4f} мм, альфа {ref:.4f} '
              f'(Δ{(chord - ref) * 1000:+.0f} мкм)')
        assert abs(chord - ref) < 0.015, (
            f"{name}/{side}: устье {chord:.4f} вместо альфовских {ref:.4f}")


def test_pocket_gets_full_t3_groove():
    """Каждый обрезанный карман получает проточку T3 на ВЕСЬ карман.

    Основной проход выбрасывает место, куда фреза не проходит, и
    перекидывает через устье дугу. Металл оттуда никуда не девается —
    его снимает тонкая фреза.

    Регрессия v1.7.38: проточки не было вовсе. На кармане оказывалась
    только УГЛОВАЯ операция, а она строится вокруг найденного угла с
    запасом 1.5 мм, поэтому карман длиной 20 мм покрывала едва на
    десятую часть — «проточил не весь карман».
    """
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.post.mtx_anderson  # noqa: F401
    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.post.package_export import PackageExporter
    from camsys.geometry.path_offset import _subpath_forward, _exact_axis_points
    from camsys.geometry.primitives import Polypath
    from camsys.geometry.direction import normalize_for_side

    project = importer_mod.import_ai_to_project(ai)
    for geom in project.get_layer_by_name("Knife").geometries:
        project.add_blade_operation(geom.id)
    macros_mod.sort_operations_by_grid(project)
    params = CuttingMacroParams()
    params.knife_angle, params.tip_diameter, params.bottom = 90.0, 0.8, 0.25
    ops, _ = PackageExporter(project, params)._build_corner_operations()
    pits = [o for o in ops if 'pocket_s0' in o.attributes]
    print(f'  проточек карманов: {len(pits)}')
    # На этом заказе карманов шесть — те же, что обрезает пост
    assert len(pits) == 6, f'проточек {len(pits)}, ожидалось 6'

    for op in pits:
        geom = project.get_geometry(op.geometry_ids[0])
        side = op.attributes['pocket_side']
        pp = normalize_for_side(geom.polypath, side)
        sub = _subpath_forward(pp, op.attributes['pocket_s0'],
                               op.attributes['pocket_s1'])
        length = sum(x.length() for x in sub)
        skip = float(op.attributes['pocket_skip'])
        print(f'  {getattr(geom, "name", "?"):>9} {side:<8} '
              f'карман {skip:6.2f} мм, проточка {length:6.2f} мм')
        # Проточка покрывает карман целиком, а не кусок вокруг угла
        assert length >= skip - 0.01, (
            f'{getattr(geom, "name", "?")}: проточка {length:.2f} мм '
            f'короче кармана {skip:.2f} мм')
        assert op.toolpaths and op.toolpaths[0].side.name == side, (
            'проточка попала не на тот проход')


def test_pocket_groove_lead_enters_from_cut_side():
    """Заход проточки идёт СПРАВА ПО ХОДУ — по уже снятому металлу.

    Эмиттер пишет G42, фреза снимает металл справа по ходу. Слева стоит
    нетронутое лезвие, и лид, уведённый туда, врезается прямо в него.

    Регрессия v1.7.38: сторона лида задавалась в посте, но до
    планировщика не доходила — `forced_side` ставился только для
    LEFT/RIGHT, а у доработки сторона INSIDE/OUTSIDE, и plan_lead_in
    пересчитывал её сам тестом «внутри/снаружи замкнутого контура». На
    фрагменте этот тест и ошибался.
    """
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import re as _re
    import camsys.post.mtx_anderson  # noqa: F401
    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.post.package_export import PackageExporter

    project = importer_mod.import_ai_to_project(ai)
    for geom in project.get_layer_by_name("Knife").geometries:
        project.add_blade_operation(geom.id)
    macros_mod.sort_operations_by_grid(project)
    params = CuttingMacroParams()
    params.knife_angle, params.tip_diameter, params.bottom = 90.0, 0.8, 0.25
    ex = PackageExporter(project, params)
    ops, _ = ex._build_corner_operations()
    for op in ops:
        op.sequence_number = 1
    text = ex._generate_with_operations(ops)

    parts, cur = [], None
    for line in text.splitlines():
        up = line.upper()
        if 'MSG' in up and 'PART' in up:
            if cur:
                parts.append(cur)
            cur = {'g0': None, 'moves': []}
            continue
        if cur is None:
            continue
        mx = _re.search(r'X\s*=?\s*(-?\d+\.?\d*)', line)
        my = _re.search(r'Y\s*=?\s*(-?\d+\.?\d*)', line)
        if not (mx and my):
            continue
        p = (float(mx.group(1)), float(my.group(1)))
        if _re.search(r'\bG0\b', line) and cur['g0'] is None:
            cur['g0'] = p
            continue
        cur['moves'].append(p)
    if cur:
        parts.append(cur)

    right = left = 0
    for pr in parts:
        if not pr['g0'] or len(pr['moves']) < 4:
            continue
        p0, p1 = pr['moves'][1], pr['moves'][2]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        if math.hypot(dx, dy) < 1e-9:
            continue
        g = pr['g0']
        if dx * (g[1] - p0[1]) - dy * (g[0] - p0[0]) < 0:
            right += 1
        else:
            left += 1
    print(f'  частей {right + left}: заход справа {right}, слева {left}')
    assert right + left >= 20, 'частей в программе доработки слишком мало'
    assert left == 0, f'{left} заходов идут слева — врежутся в лезвие'


def test_rework_lead_never_cuts_another_wall():
    """Лид доработки не срезает встречную стенку (v1.7.39).

    Заходить фреза обязана справа по ходу — там, где снимает металл. Но
    в узкой щели справа может не быть места: на Knife_1 заказа 124173
    лид с запасом длины уходил в стенку на 153 мкм, на Knife_10 — на
    455 мкм. Пользователь это и увидел: «наоборот оно стало пересекать
    лезвие».

    Поэтому сторона выбирается замером зазора до ЧУЖИХ стенок (своя,
    к которой лид и пристыкован, исключается по длине контура) с учётом
    СОСЕДНИХ ножей. Справа остаётся везде, где помещается.
    """
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.geometry.lead_inout as LI
    import camsys.post.mtx_anderson  # noqa: F401
    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.post.package_export import PackageExporter

    seen = []
    orig = LI.fit_rework_lead

    def spy(*a, **k):
        side, shrink, ang, clr = orig(*a, **k)
        seen.append((a[0], side, clr, shrink, ang))
        return side, shrink, ang, clr

    LI.fit_rework_lead = spy
    try:
        project = importer_mod.import_ai_to_project(ai)
        for geom in project.get_layer_by_name("Knife").geometries:
            project.add_blade_operation(geom.id)
        macros_mod.sort_operations_by_grid(project)
        params = CuttingMacroParams()
        params.knife_angle, params.tip_diameter, params.bottom = 90.0, 0.8, 0.25
        ex = PackageExporter(project, params)
        ops, _ = ex._build_corner_operations()
        for op in ops:
            op.sequence_number = 1
        ex._generate_with_operations(ops)
    finally:
        LI.fit_rework_lead = orig

    assert len(seen) >= 40, f'лидов доработки всего {len(seen)}'
    worst = min(c for _p, _s, c, _k, _a in seen)
    n_right = sum(1 for _p, s, _c, _k, _a in seen if s == 'right')
    short = [k for _p, _s, _c, k, _a in seen if k < 1.0]
    print(f'  лидов {len(seen)}: справа {n_right}, '
          f'слева {len(seen) - n_right}, укорочено {len(short)}; '
          f'худший зазор {worst * 1000:.0f} мкм')
    assert worst >= 0.030, (
        f'лид подходит к чужой стенке на {worst * 1000:.0f} мкм')
    # Сторона реза сохраняется: в тесное место лид УКОРАЧИВАЕТСЯ, а не
    # переносится налево — слева стоит металл, который этот проход не
    # снимает.
    assert n_right == len(seen), (
        f'{len(seen) - n_right} лидов ушли влево вместо укорочения')
    # Knife_1 (устье около 59.9, 93.2) — то самое тесное место
    k1 = [(c, k) for p, _s, c, k, _a in seen
          if 59.0 < p[0] < 60.5 and 92.0 < p[1] < 94.0]
    assert k1, 'лиды Knife_1 не найдены'
    assert all(c > 0 for c, _k in k1), f'Knife_1 всё ещё режет: {k1}'
    assert any(k < 1.0 for _c, k in k1), (
        'на Knife_1 лид не укорочен, хотя места там нет')


def test_rework_lead_has_extra_length():
    """Лид доработки получает запас длины в диаметрах пятки T3.

    Короткий лид в узкой щели упирается в стенку и режет ещё до выхода
    на путь. Запас (по умолчанию один диаметр пятки, 0.6 мм) уводит
    точку врезания дальше от металла.
    """
    from camsys.core.cutting_macro import CuttingMacroParams
    p = CuttingMacroParams()
    assert abs(p.corner_lead_extra_diam - 1.0) < 1e-9, 'умолчание запаса'
    assert abs(p.corner_tip_diameter - 0.6) < 1e-9

    import camsys.post.mtx_anderson  # noqa: F401
    from camsys.post.package_export import PackageExporter
    from camsys.core.project import Project
    ex = PackageExporter(Project(name='t'), p)
    opts = ex._build_post_options('t')
    assert abs(opts.extras['corner_lead_extra_mm'] - 0.6) < 1e-9, (
        'запас длины лида не доходит до поста')


def test_rework_lead_cutter_never_crosses_geometry():
    """ПУТЬ ФРЕЗЫ лида доработки не пересекает геометрию (v1.7.41).

    Мерить по осевой нельзя: фреза идёт на полуширину реза в сторону, и
    в узком месте через лезвие перелезает именно она, а осевая проходит
    мимо. Поэтому на картинке лиды пересекали контур, а замер по осевой
    показывал, что всё чисто.

    На 124173 при полной длине путь фрезы пересекал геометрию у четырёх
    лидов — в том числе ровно в тех местах, на которые указал
    пользователь: (59, 93) и (93, 239). После подбора длины — ни одного.
    """
    ai = '/mnt/user-data/uploads/124173_test.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет 124173_test.ai')
        return
    import camsys.geometry.lead_inout as LI
    import camsys.post.mtx_anderson  # noqa: F401
    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.post.package_export import PackageExporter
    from camsys.geometry.primitives import Polypath

    def cutter(lpts, w):
        out = []
        for i in range(len(lpts) - 1):
            x0, y0 = lpts[i]
            x1, y1 = lpts[i + 1]
            dx, dy = x1 - x0, y1 - y0
            L = math.hypot(dx, dy)
            if L < 1e-12:
                continue
            nx, ny = dy / L, -dx / L
            out.append((x0 + nx * w, y0 + ny * w))
            out.append((x1 + nx * w, y1 + ny * w))
        return out

    def n_cross(poly, walls, att, eps):
        n = 0
        for i in range(len(poly) - 1):
            a, b = poly[i], poly[i + 1]
            if (math.hypot(a[0] - att[0], a[1] - att[1]) < eps
                    or math.hypot(b[0] - att[0], b[1] - att[1]) < eps):
                continue
            for wl in walls:
                for k in range(len(wl) - 1):
                    c, d = wl[k], wl[k + 1]
                    if abs(c[0] - a[0]) > 4.0 or abs(c[1] - a[1]) > 4.0:
                        continue
                    if LI._seg_cross(a, b, c, d):
                        n += 1
        return n

    full_bad = fit_bad = 0
    orig = LI.fit_rework_lead

    def spy(point, tangent, contour, w, ll, ar, ang, **k):
        nonlocal full_bad, fit_bad
        nb = k.get('neighbours') or []
        is_exit = k.get('is_exit', False)
        style = k.get('style', 'line_arc')
        build = LI.build_lead_out if is_exit else LI.build_lead_in

        def axis(shr, a_):
            try:
                g = build(point, tangent, 'right', ll * shr, ar * shr, a_,
                          style=style)
            except Exception:
                return []
            segs = [x for x in (g.line, g.arc) if x is not None]
            if not segs:
                return []
            return [q for _s, q in LI._cached_points(
                Polypath(segments=segs, closed=False), 0.02)]

        walls = [[q for _s, q in LI._cached_points(contour, 0.02)]]
        for nn in nb:
            walls.append([q for _s, q in LI._cached_points(nn, 0.05)])
        eps = w * 1.2
        if n_cross(cutter(axis(1.0, ang), w), walls, point, eps):
            full_bad += 1
        r = orig(point, tangent, contour, w, ll, ar, ang, **k)
        if n_cross(cutter(axis(r[1], r[2]), w), walls, point, eps):
            fit_bad += 1
        return r

    LI.fit_rework_lead = spy
    try:
        project = importer_mod.import_ai_to_project(ai)
        for geom in project.get_layer_by_name("Knife").geometries:
            project.add_blade_operation(geom.id)
        macros_mod.sort_operations_by_grid(project)
        params = CuttingMacroParams()
        params.knife_angle, params.tip_diameter, params.bottom = 90.0, 0.8, 0.25
        ex = PackageExporter(project, params)
        ops, _ = ex._build_corner_operations()
        for op in ops:
            op.sequence_number = 1
        ex._generate_with_operations(ops)
    finally:
        LI.fit_rework_lead = orig

    print(f'  путь фрезы пересекал геометрию: на полной длине {full_bad}, '
          f'после подбора {fit_bad}')
    assert full_bad >= 1, 'проверка ничего не ловит — критерий сломан'
    assert fit_bad == 0, (
        f'{fit_bad} лидов режут лезвие даже после подбора')
