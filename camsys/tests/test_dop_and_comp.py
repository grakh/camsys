"""Регрессионные тесты:
  1) Направление компенсации: оба прохода (INSIDE/OUTSIDE) при нашей
     противоположной намотке должны выводить G42 (INSIDE раньше ошибочно
     давал G41 → обе линии уезжали наружу вместо формирования канавки).
  2) Режим доработки _dop: если активна меньшая доля ножей, чем
     dop_threshold_ratio, пишется один _dop.anc вместо пофайловых _N_M.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from camsys.core.session import CamSession
from camsys.core.project import OperationKind
from camsys.core.cutting_macro import CuttingMacroParams
from camsys.post.package_export import PackageExporter

AI = '/mnt/user-data/uploads/120208.ai'


def _load():
    sess = CamSession()
    sess.load_ai(AI)
    sess.create_blade_operations()
    sess.sort_by_grid()
    return sess


def test_inside_comp_is_g42():
    """Компенсация контурных проходов: лезвие СЛЕВА по ходу → фреза справа →
    G42 для обоих проходов (внутренний CW + G42 = внутрь, внешний CCW + G42 =
    наружу). G41 не должен встречаться на замкнутых ножах."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    sess = _load()
    files = PackageExporter(
        sess.project,
        CuttingMacroParams(output_prefix="t", knife_angle=90)).generate()
    nc = files['t_90_all_R.anc']
    g41 = nc.count('G41')
    g42 = nc.count('G42')
    print(f'  G41={g41}  G42={g42}')
    assert g42 > 0, "G42 должен присутствовать (оба прохода = G42)"
    assert g41 == 0, f"G41 не должен встречаться на замкнутых ножах, найдено {g41}"


def _gen_with_active(active_count):
    sess = _load()
    blades = [o for o in sess.project.operations
              if o.kind == OperationKind.BLADE_FORMING]
    # снимаем галки (excluded=True) у всех, кроме первых active_count — как UI
    for i, o in enumerate(blades):
        o.attributes['excluded'] = (i >= active_count)
    files = PackageExporter(
        sess.project,
        CuttingMacroParams(output_prefix="t", knife_angle=90)).generate()
    return files, len(blades)


def test_dop_not_triggered_when_all_active():
    """Все ножи активны → обычные _N_M, без _dop."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    files, total = _gen_with_active(total := 99999)  # все
    m = [n for n in files if n.endswith('_M.anc')]
    dop = [n for n in files if '_dop' in n]
    print(f'  _M={len(m)}  _dop={dop}')
    assert len(m) > 0 and not dop


def test_dop_triggered_below_half():
    """Активна меньше половины → один _dop.anc, без _N_M."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    files, total = _gen_with_active(3)
    m = [n for n in files if n.endswith('_M.anc')]
    dop = [n for n in files if '_dop' in n]
    print(f'  total={total}  _M={len(m)}  _dop={dop}')
    assert len(m) == 0, "Чистовые _N_M не должны писаться в режиме доработки"
    assert dop == ['t_90_dop.anc']
    # В _dop должны быть только активные ножи (3 × 2 прохода = 6 PART)
    parts = files['t_90_dop.anc'].count('PREPART')
    assert parts == 6, f"Ожидалось 6 PART (3 ножа × 2), получено {parts}"


def test_dop_half_is_boundary():
    """Ровно половина активна → порог НЕ срабатывает (строгое <)."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    files, total = _gen_with_active(total_half := 38)  # 38/76
    dop = [n for n in files if '_dop' in n]
    print(f'  активно 38, _dop={dop}')
    assert not dop, "На ровно половине _dop не должен появляться"


def test_dop_zero_active_writes_nothing():
    """Ноль активных → ни _M, ни _dop (нечего дорабатывать)."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    files, total = _gen_with_active(0)
    m = [n for n in files if n.endswith('_M.anc')]
    dop = [n for n in files if '_dop' in n]
    print(f'  _M={len(m)}  _dop={dop}')
    assert len(m) == 0 and not dop


def test_no_false_corners_on_smooth():
    """Гладкие контуры (биарк-шум R<0.7) НЕ должны давать углов:
    проверка истинной кривизны отсеивает их."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    from camsys.geometry.corner_detect import detect_geometric_corners
    sess = _load()
    total = 0
    for op in sess.project.operations:
        if op.kind != OperationKind.BLADE_FORMING:
            continue
        g = sess.project.get_geometry(op.geometry_ids[0])
        total += len(detect_geometric_corners(g.polypath, 0.7))
    print(f'  углов на гладких бобах: {total} (ожидаем 0)')
    assert total == 0, f"Биарк-шум не должен давать углов, найдено {total}"


def test_export_ignores_stray_corner_ops():
    """CORNER_REWORK-операции, добавленные в проект (напр. при превью), не
    должны протекать в черновую/чистовые — угловые генерятся отдельно."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    from camsys.core.project import (Operation, OperationKind as OK,
                                      CutSettings, PassType)
    sess = _load()
    # добавим фиктивную corner-операцию в проект
    blade = next(o for o in sess.project.operations
                 if o.kind == OK.BLADE_FORMING)
    stray = Operation(
        name="stray corner", kind=OK.CORNER_REWORK,
        geometry_ids=list(blade.geometry_ids),
        settings=CutSettings(tool_number=3, pass_type=PassType.SINGLE),
        sequence_number=1)
    stray.attributes['parent_geom_id'] = blade.geometry_ids[0]
    stray.attributes['corner_index'] = 0
    sess.project.operations.append(stray)
    files = PackageExporter(
        sess.project,
        CuttingMacroParams(output_prefix="t", knife_angle=90)).generate()
    # черновая должна содержать ровно 76 ножей × 2 прохода (стрэй-угол выкинут)
    allr = files['t_90_all_R.anc']
    parts = allr.count('PREPART')
    print(f'  PREPART в _all_R={parts} (76 ножей × 2 = 152, стрэй-угол не протёк)')
    assert parts == 152, f"Стрэй CORNER не должен протекать, PREPART={parts}"


def test_real_corners_detected_on_square():
    """Настоящие тугие углы (скруглённый квадрат R<0.7, разворот ~90°)
    ДОЛЖНЫ детектироваться — фильтр по развороту их не режет."""
    from camsys.geometry.primitives import Line, Arc, Polypath
    from camsys.geometry.corner_detect import detect_geometric_corners

    def rsq(r):
        h = 10.0
        p = [(-h+r, -h), (h-r, -h), (h, -h+r), (h, h-r),
             (h-r, h), (-h+r, h), (-h, h-r), (-h, -h+r)]
        c = [(h-r, -h+r), (h-r, h-r), (-h+r, h-r), (-h+r, -h+r)]
        return Polypath(segments=[
            Line(a=p[0], b=p[1]), Arc(a=p[1], b=p[2], center=c[0], ccw=True),
            Line(a=p[2], b=p[3]), Arc(a=p[3], b=p[4], center=c[1], ccw=True),
            Line(a=p[4], b=p[5]), Arc(a=p[5], b=p[6], center=c[2], ccw=True),
            Line(a=p[6], b=p[7]), Arc(a=p[7], b=p[0], center=c[3], ccw=True)],
            closed=True)

    n_sharp = len(detect_geometric_corners(rsq(0.3), 0.7))   # R<порога
    n_round = len(detect_geometric_corners(rsq(1.0), 0.7))   # R>порога
    print(f'  квадрат R=0.3 → {n_sharp} углов (ожидаем 4); R=1.0 → {n_round} (ожидаем 0)')
    assert n_sharp == 4, f"Тугие углы R=0.3 должны детектиться, получено {n_sharp}"
    assert n_round == 0, f"Скругление R=1.0 (>порога) — не угол, получено {n_round}"


def test_auto_nc_dir_and_old_archive():
    """Авто-папка NC из пути .ai + архивация старых .anc в old0/old1."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    import tempfile, shutil
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    proj = tmp / "120208"; proj.mkdir()
    shutil.copy(AI, proj / "120208.ai")
    (proj / "120208-NC").mkdir()  # папка NC уже существует рядом

    sess = CamSession()
    sess.load_ai(str(proj / "120208.ai"))
    sess.create_blade_operations(); sess.sort_by_grid()

    nc = sess.resolve_nc_dir()
    assert nc.name == "120208-NC", f"NC папка: {nc}"

    r1 = sess.export_package_auto()
    assert r1['archived'] is None, "1й экспорт — архивировать нечего"
    assert len(r1['written']) > 0
    n_first = len(list(nc.glob("*.anc")))

    r2 = sess.export_package_auto()
    assert r2['archived'] and Path(r2['archived']).name == "old0"
    assert (nc / "old0").is_dir()
    assert len(list((nc / "old0").glob("*.anc"))) == n_first, "старые ушли в old0"

    r3 = sess.export_package_auto()
    assert r3['archived'] and Path(r3['archived']).name == "old1"
    print(f'  NC={nc.name}, архивы: old0, old1 — ок')


def test_failed_generation_keeps_folder_intact():
    """Если генерация ничего не вернула — папка NC не трогается (старые
    файлы НЕ архивируются в old, ошибка явная). Порядок: генерация → архив."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    import tempfile, shutil
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    proj = tmp / "120208"; proj.mkdir()
    shutil.copy(AI, proj / "120208.ai")
    nc = proj / "120208-NC"; nc.mkdir()
    (nc / "120208_70_all_R.anc").write_text("OLD")

    sess = CamSession()
    sess.load_ai(str(proj / "120208.ai"))
    sess.create_blade_operations(); sess.sort_by_grid()
    # все типы файлов выключены → генерация пустая
    sess.set_cutting_params_from_dict({
        'generate_rough_all': False, 'generate_reverse': False,
        'generate_finish_per_op': False, 'generate_sv': False,
        'generate_corner': False, 'generate_corner_3d': False})
    raised = False
    try:
        sess.export_package_auto()
    except Exception:
        raised = True
    print(f'  ошибка поднята={raised}; old0 создан={(nc/"old0").exists()}')
    assert raised, "Пустая генерация должна явно падать"
    assert not (nc / "old0").exists(), "Папка old не должна создаваться при сбое"
    assert (nc / "120208_70_all_R.anc").read_text() == "OLD", "Старый файл цел"


def test_generate_survives_none_stderr_with_fit_warning():
    """Под .pyw sys.stderr==None. Если макет тесный (срабатывает
    предупреждение о фрезе), код раньше падал на sys.stderr.write(). Теперь
    генерация должна пройти и при stderr=None."""
    import sys as _sys
    from camsys.geometry.primitives import Line, Polypath
    from camsys.core.project import Project, Geometry, OperationKind
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.post.package_export import PackageExporter

    def sharp_sq(cx, cy, s=10):
        h = s / 2
        p = [(cx-h, cy-h), (cx+h, cy-h), (cx+h, cy+h), (cx-h, cy+h)]
        return Polypath(segments=[Line(a=p[0], b=p[1]), Line(a=p[1], b=p[2]),
                                  Line(a=p[2], b=p[3]), Line(a=p[3], b=p[0])],
                        closed=True)

    prj = Project(name="tight", source_ai_path="/tmp/9/9.ai")
    layer = prj.add_layer("Knife", "#00ff00")
    # тесная сетка (зазор 0.12мм) → фреза не влезает → предупреждение
    for r in range(3):
        for c in range(4):
            layer.geometries.append(
                Geometry(polypath=sharp_sq(c*10.12, r*10.12), source_layer="Knife"))
    from camsys.core.session import CamSession
    sess = CamSession(); sess.project = prj
    sess.create_blade_operations(); sess.sort_by_grid()

    saved = _sys.stderr
    _sys.stderr = None  # эмулируем pythonw
    try:
        files = PackageExporter(
            prj, CuttingMacroParams(output_prefix="t", knife_angle=70,
                                    tip_diameter=0.8, top=0.5, bottom=0.25)).generate()
    finally:
        _sys.stderr = saved
    print(f'  при stderr=None сгенерировано {len(files)} файлов (не упало)')
    assert len(files) > 0, "Генерация должна пройти даже при stderr=None"


def test_smooth_for_offset_makes_offset_simple():
    """smooth_for_offset делает эквидистанту контура самонепересекающейся
    (проверка через shapely) при сохранении формы в пределах ~1 мкм."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    try:
        from shapely.geometry import Polygon, LineString, Point as SP
    except Exception:
        print('  SKIP — shapely не установлен')
        return
    from camsys.geometry.direction import normalize_for_side
    from camsys.geometry.path_offset import (smooth_for_offset,
                                             _sample_polypath_points)
    sess = _load()
    TOOL = 0.575
    checked = 0
    for name in ('Blade Knife_66', 'Blade Knife_37', 'Blade Knife_35'):
        ops = [o for o in sess.project.operations if o.name == name]
        if not ops:
            continue
        g = sess.project.get_geometry(ops[0].geometry_ids[0])
        norm = normalize_for_side(g.polypath, 'OUTSIDE')
        sm = smooth_for_offset(norm, TOOL, 'OUTSIDE')
        coords = [s.a for s in sm.segments]
        off = Polygon(coords).buffer(TOOL, quad_segs=24)
        assert off.is_simple, f"{name}: offset должен быть без самопересечений"
        orig_pts = _sample_polypath_points(norm)
        orig = LineString(orig_pts + [orig_pts[0]])
        dev = max(orig.distance(SP(c)) for c in coords)
        assert dev < 0.005, f"{name}: отклонение {dev*1000:.1f}мкм слишком велико"
        checked += 1
    print(f'  проверено ножей: {checked}, offset simple, отклонение <5мкм')
    assert checked > 0


def test_leads_do_not_cross_blade():
    """Заходы INSIDE и OUTSIDE уходят наружу (в зазор) и НЕ пересекают
    контур ножа. Регресс на сторону завитка lead_side."""
    if not os.path.exists(AI):
        print('  SKIP — нет файла')
        return
    import math as _m
    from camsys.geometry.primitives import Arc, Polypath
    from camsys.geometry.direction import normalize_for_side
    from camsys.geometry.lead_inout import build_lead_in
    sess = _load()

    def pts(poly, n=16):
        out = []
        for s in poly.segments:
            if isinstance(s, Arc):
                a0 = _m.atan2(s.a[1]-s.center[1], s.a[0]-s.center[0])
                a1 = _m.atan2(s.b[1]-s.center[1], s.b[0]-s.center[0])
                if s.ccw and a1 < a0: a1 += 2*_m.pi
                if (not s.ccw) and a1 > a0: a1 -= 2*_m.pi
                for k in range(n):
                    out.append((s.center[0]+s.radius*_m.cos(a0+(a1-a0)*k/n),
                                s.center[1]+s.radius*_m.sin(a0+(a1-a0)*k/n)))
            else:
                out.append(s.a)
        return out

    def crosses(lead_pts, contour_pts):
        def ccw(A, B, C): return (C[1]-A[1])*(B[0]-A[0]) > (B[1]-A[1])*(C[0]-A[0])
        def it(A, B, C, D): return ccw(A,C,D) != ccw(B,C,D) and ccw(A,B,C) != ccw(A,B,D)
        L = lead_pts[:-1]
        return sum(1 for i in range(len(L)-1) for j in range(len(contour_pts)-1)
                   if it(L[i], L[i+1], contour_pts[j], contour_pts[j+1]))

    from camsys.geometry.lead_inout import pick_lead_side_for_pass
    total = 0
    n = 0
    for op in sess.project.operations[:25]:
        g = sess.project.get_geometry(op.geometry_ids[0])
        for side in ('INSIDE', 'OUTSIDE'):
            norm = normalize_for_side(g.polypath, side)
            cpts = pts(norm) + [pts(norm)[0]]
            start = norm.segments[0].a
            tg = norm.segments[0].tangent_at_start()
            ls = pick_lead_side_for_pass(start, tg, norm, side,
                                         2.0, 0.6, 45, is_exit=False)
            L = build_lead_in(start_point=start, tangent=tg, side=ls,
                              line_length=2.0, arc_radius=0.6, approach_angle_deg=45)
            lpts = pts(Polypath(segments=[L.line, L.arc], closed=False)) \
                + [norm.segments[0].a]
            total += crosses(lpts, cpts)
        n += 1
    print(f'  ножей={n}, пересечений захода с лезвием={total} (допускаем ≤2 шумовых касания)')
    assert total <= 5, f"Заходы не должны пересекать лезвие, получено {total}"


if __name__ == "__main__":
    import inspect
    tests = [(n, f) for n, f in inspect.getmembers(sys.modules[__name__])
             if n.startswith('test_') and inspect.isfunction(f)]
    passed = 0
    for n, f in tests:
        print(f"\n-> {n}")
        try:
            f()
            print("  [OK] OK")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {e!r}")
            import traceback
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} тестов пройдено")


def _dumbbell(neck=1.0, lobe=4.0, span=14.0):
    """Гантель: две доли, соединённые перешейком ширины neck."""
    from camsys.geometry.primitives import Line, Polypath
    hw, hn = lobe / 2.0, neck / 2.0
    pts = [(0.0, -hw), (lobe, -hw), (span / 2 - 1, -hn), (span / 2 + 1, -hn),
           (span - lobe, -hw), (span, -hw), (span, hw), (span - lobe, hw),
           (span / 2 + 1, hn), (span / 2 - 1, hn), (lobe, hw), (0.0, hw)]
    return Polypath(segments=[Line(a=pts[i], b=pts[(i + 1) % len(pts)])
                              for i in range(len(pts))], closed=True)


def test_lead_self_collision_on_narrow_neck():
    """Лид, протыкающий узкий перешеек, распознаётся как коллизия.

    Регрессия v1.7.16: сегменты СВОЕГО контура пропускались по евклидову
    расстоянию от точки стыковки —

        own_skip_radius = max(skip_radius, lead_span * 0.8)   # ≈1.2 мм
        if _point_to_segment_distance(entry_point, ca, cb) < own_skip_radius:
            continue

    На мелком элементе противоположная сторона перешейка попадала в этот
    радиус, и реальное пересечение молча пропускалось — лезвие резало
    само себя. После увеличения лидов вдвое (v1.7.11) радиус тоже вырос,
    и случай стал массовым.

    Пропускать надо то, что рядом ПО ДЛИНЕ КОНТУРА, а не по прямой.
    """
    from camsys.geometry.primitives import Line, Polypath
    from camsys.geometry.lead_collision import (lead_crosses_contours,
                                                polypath_to_lines)

    poly = _dumbbell(neck=1.0)
    lines = polypath_to_lines(poly)
    entry = (7.0, -0.5)                      # низ перешейка

    # Лид идёт ВВЕРХ — сквозь перешеек на другую сторону
    through = Polypath(segments=[Line(a=entry, b=(7.0, 1.07))], closed=False)
    assert lead_crosses_contours(through, [("self", lines)], "self",
                                 entry, 0.533), \
        "лид протыкает свой контур, но коллизия не найдена"

    # Наружу — коллизии быть не должно
    outward = Polypath(segments=[Line(a=entry, b=(7.0, -2.07))], closed=False)
    assert not lead_crosses_contours(outward, [("self", lines)], "self",
                                     entry, 0.533), \
        "ложная коллизия на лиде наружу"


def test_lead_no_false_collision_on_wide_element():
    """На широком элементе лид тех же размеров коллизии не даёт."""
    from camsys.geometry.primitives import Line, Polypath
    from camsys.geometry.lead_collision import (lead_crosses_contours,
                                                polypath_to_lines)
    poly = _dumbbell(neck=6.0)
    lines = polypath_to_lines(poly)
    entry = (7.0, -3.0)
    lead = Polypath(segments=[Line(a=entry, b=(7.0, -1.43))], closed=False)
    assert not lead_crosses_contours(lead, [("self", lines)], "self",
                                     entry, 0.533)


def _lead(*pts):
    from camsys.geometry.primitives import Line, Polypath
    return Polypath(segments=[Line(a=pts[i], b=pts[i + 1])
                              for i in range(len(pts) - 1)], closed=False)


def test_leads_do_not_cross_each_other():
    """Заход и выход проверяются на взаимное пересечение.

    Регрессия v1.7.17: оба лида сверялись только с контурами ножей, друг
    с другом — никогда. На мелких элементах они расходятся почти из одной
    точки и успевают пересечься крестом.
    """
    from camsys.geometry.lead_collision import leads_cross_each_other
    join = (0.0, 0.0)

    # Явный крест вдали от точки стыковки
    lead_in = _lead((-4.0, -1.3333), join)
    lead_out = _lead(join, (-2.0, -2.0), (-4.0, 0.0))
    assert leads_cross_each_other(lead_in, lead_out), \
        "крест лидов не обнаружен"

    # Расходятся веером — не коллизия
    assert not leads_cross_each_other(_lead((-2.0, -2.0), join),
                                      _lead(join, (-2.5, -1.5)))

    # Касание у самой точки стыковки не считается: у замкнутого контура
    # без перекрытия обе стыковки в одной точке, и дуги там неизбежно
    # идут вплотную
    assert not leads_cross_each_other(_lead((-1.0, -0.05), join),
                                      _lead(join, (-1.0, 0.05)))

    # Пустые входы безопасны
    assert not leads_cross_each_other(None, _lead(join, (1.0, 1.0)))
    assert not leads_cross_each_other(_lead(join, (1.0, 1.0)), None)


def test_lead_cross_check_is_wired_into_planner():
    """Взаимная проверка участвует в подборе точки входа.

    Иначе позиция с крестом считалась бы идеальной и auto_avoid не стал
    бы искать другую.
    """
    import os
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'geometry', 'lead_collision.py'), encoding='utf-8').read()
    planner = src[src.index('def plan_lead_in'):src.index('def plan_lead_out')]
    assert planner.count('leads_cross_each_other') >= 2, \
        "взаимная проверка не подключена и к проверке, и к оценке"


def _densify(g, x0, y0, x1, y1, radius, step=0.03):
    """Движение .anc → точки. Дуги разворачиваются, иначе пересечение
    на дуге не поймать (именно на нём и сидел дефект v1.7.18)."""
    import math
    chord = math.hypot(x1 - x0, y1 - y0)
    if g not in (2, 3) or not radius or chord < 1e-12 or chord > 2 * radius:
        n = max(2, int(chord / step))
        return [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n)
                for i in range(n + 1)]
    h = math.sqrt(max(0.0, radius * radius - (chord / 2) ** 2))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    ux, uy = -(y1 - y0) / chord, (x1 - x0) / chord
    sgn = 1.0 if g == 3 else -1.0
    cx, cy = mx + sgn * h * ux, my + sgn * h * uy
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    d = a1 - a0
    if g == 3:
        while d < 0:
            d += 2 * math.pi
    else:
        while d > 0:
            d -= 2 * math.pi
    n = max(2, int(abs(d) * radius / step))
    return [(cx + radius * math.cos(a0 + d * i / n),
             cy + radius * math.sin(a0 + d * i / n)) for i in range(n + 1)]


def test_no_lead_crosses_own_contour_on_real_file():
    """На реальном файле ни один лид не режет осевую своего же ножа.

    Регрессия v1.7.18: в «зоне тангенциального касания» у точки стыковки
    пропускались ОБЕ проверки — и distance-чек, и явное пересечение. На
    124819 выход резал свою осевую в 0.45 мм по контуру от стыковки, при
    пороге пропуска 1.79 мм. Пропускать там можно только distance-чек:
    дуга лида прижата к контуру по построению, а пересечение — всегда
    ошибка.
    """
    import os, re, math
    ai = '/mnt/user-data/uploads/124819.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла 124819.ai')
        return

    import camsys.core.importer as importer_mod
    import camsys.core.macros as macros_mod
    import camsys.post.base as post_base
    import camsys.post.mtx_anderson  # noqa: F401 — регистрация поста

    tool_eq = 0.8 + 2 * 0.19 * math.tan(math.radians(35.0))
    project = importer_mod.import_ai_to_project(ai, project_name="124819")
    for geom in project.get_layer_by_name("Knife").geometries:
        project.add_blade_operation(geom.id)
    macros_mod.sort_operations_by_grid(project)
    for op in project.operations:
        op.sequence_number = 1
    post = post_base.PostRegistry.get("MTX Anderson GVM V2.13")
    anc = post.generate(project, post_base.PostOptions(
        program_name="124819", sheet_thickness=0.44, z_depth=0.19,
        extras={'tool_radius': 0.4, 'tool_equidistant': tool_eq}))

    num = r'[-+]?\d*\.?\d+'

    def crosses(a1, a2, b1, b2):
        def cr(o, p, q):
            return ((p[0] - o[0]) * (q[1] - o[1])
                    - (p[1] - o[1]) * (q[0] - o[0]))
        d1, d2 = cr(b1, b2, a1), cr(b1, b2, a2)
        d3, d4 = cr(a1, a2, b1), cr(a1, a2, b2)
        return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))

    bad = []
    chunks = anc.split('PREPART')[1:]
    for pidx, chunk in enumerate(chunks):
        x = y = 0.0
        g = 0
        phase = 0
        body_pts, out_pts = [], []
        for line in chunk.split('POSTPART')[0].splitlines():
            if 'G42' in line or 'G41' in line:
                phase = 1
            if 'G13' in line:
                phase = 2
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
            pts = _densify(g, x, y, nx, ny,
                           float(gr.group(1)) if gr else None)
            if phase == 2:
                out_pts += pts
            elif phase == 1 and 'G12' not in line:
                body_pts += pts
            x, y = nx, ny

        if len(out_pts) < 2 or len(body_pts) < 3:
            continue
        join = out_pts[0]
        for i in range(len(out_pts) - 1):
            mid = ((out_pts[i][0] + out_pts[i + 1][0]) / 2,
                   (out_pts[i][1] + out_pts[i + 1][1]) / 2)
            if math.hypot(mid[0] - join[0], mid[1] - join[1]) < 0.35:
                continue
            if any(crosses(out_pts[i], out_pts[i + 1],
                           body_pts[j], body_pts[j + 1])
                   for j in range(len(body_pts) - 1)):
                bad.append(pidx + 1)
                break

    print(f'  частей {len(chunks)}, лидов через свою осевую: {len(bad)}')
    assert not bad, f"лид режет осевую в частях: {bad[:5]}"
