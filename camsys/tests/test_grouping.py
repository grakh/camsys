"""Тесты assign_program_numbers — группировка деталей в программы."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.project import (Project, Geometry, Operation, OperationKind,
                          CutSettings, PassType)
from geometry.primitives import Line, Polypath
from core.macros import (assign_program_numbers, operation_geom_length,
                         sort_operations_by_grid, GridDirection, GridGrouping)


def _make_project_with_grid(rows, cols, knife_w=50.0, knife_h=180.0,
                            gap_x=10.0, gap_y=10.0):
    """Создаёт проект с сеткой ножей rows×cols.
    Каждый нож — прямоугольный контур knife_w × knife_h."""
    prj = Project(name="grid")
    layer = prj.add_layer("Knife", "#00ff00")
    
    op_idx = 0
    for r in range(rows):
        for c in range(cols):
            x0 = c * (knife_w + gap_x)
            y0 = r * (knife_h + gap_y)
            # прямоугольный контур
            poly = Polypath(segments=[
                Line((x0, y0), (x0+knife_w, y0)),
                Line((x0+knife_w, y0), (x0+knife_w, y0+knife_h)),
                Line((x0+knife_w, y0+knife_h), (x0, y0+knife_h)),
                Line((x0, y0+knife_h), (x0, y0)),
            ], closed=True)
            geom = Geometry(name=f"K_{r}_{c}", polypath=poly,
                            source_layer="Knife", is_closed=True)
            layer.geometries.append(geom)
            
            op = Operation(
                name=f"Op_{r}_{c}",
                kind=OperationKind.BLADE_FORMING,
                geometry_ids=[geom.id],
                settings=CutSettings(tool_number=1, pass_type=PassType.FINISH),
            )
            prj.operations.append(op)
            op_idx += 1
    return prj


def test_geom_length_rectangle():
    """Длина периметра прямоугольника 50×180 = 460."""
    prj = _make_project_with_grid(1, 1, knife_w=50, knife_h=180)
    op = prj.operations[0]
    L = operation_geom_length(op, prj)
    assert abs(L - 460.0) < 1e-6, f"Получили {L}"


def test_pick_via_huge_limit():
    """Огромный лимит → все детали в одной программе."""
    prj = _make_project_with_grid(3, 3)
    n = assign_program_numbers(prj, max_geom_len=1000000, passes_per_part=2)
    assert n == 1
    for op in prj.operations:
        assert op.attributes['program_number'] == 1


def test_length_mode_splits():
    """4 строки по 1 ножу: каждая строка > лимита → 4 программы (по строке)."""
    # Сетка 4×1: 4 строки по 1 ножу. При горизонтали каждая строка = коридор.
    # Лимит 100 геом × 2 = 200 путей < 920 (длина одной детали).
    # КАЖДЫЙ коридор > лимита, в каждом 1 деталь → коридор не делится 
    # (нужно ≥2 деталей чтобы делить), каждый в своей программе.
    prj = _make_project_with_grid(4, 1, knife_w=50, knife_h=180)
    n = assign_program_numbers(prj, max_geom_len=100, passes_per_part=2,
                               direction="horizontal")
    progs = [op.attributes['program_number'] for op in prj.operations]
    assert n == 4, f"Программ: {n}, progs={progs}"


def test_length_mode_combines():
    """Большой лимит → все детали в одной программе."""
    prj = _make_project_with_grid(2, 3, knife_w=50, knife_h=180)
    n = assign_program_numbers(prj, max_geom_len=100000, passes_per_part=2,
                               direction="horizontal")
    progs = [op.attributes['program_number'] for op in prj.operations]
    assert len(set(progs)) == 1, f"Программ: {set(progs)}"


def test_corridor_atomicity():
    """Коридоры (строки) — атомарны. 2 строки × 3 детали, лимит вмещает 
    ровно 1 строку → 2 программы (строка1, строка2), без разрыва."""
    prj = _make_project_with_grid(2, 3, knife_w=50, knife_h=180)
    # Каждая строка = 3 × 920 = 2760 путей.
    # Лимит = 3000 путей (новая семантика: max_geom_len уже в путях).
    # Одна строка (2760) влезает, две (5520) нет.
    # → программа = строка, всего 2 программы.
    n = assign_program_numbers(prj, max_geom_len=3000, passes_per_part=2,
                               direction="horizontal")
    progs = [op.attributes['program_number'] for op in prj.operations]
    assert n == 2, f"Программ: {n}"
    # Первые 3 — одна строка, в одной программе
    assert len(set(progs[:3])) == 1
    assert len(set(progs[3:])) == 1
    # Разные программы для разных строк
    assert progs[0] != progs[3]


def test_corridor_split_when_too_long():
    """Один коридор сам длиннее лимита → делим только его на равные части.
    Строка из 4 деталей при лимите ровно вмещающем 2 детали → 2 программы 2+2."""
    prj = _make_project_with_grid(1, 4, knife_w=50, knife_h=180)
    # Одна строка из 4 деталей × 920 = 3680 путей.
    # Лимит = 2000 путей. Коридор 3680 > 2000.
    # Делим на ceil(3680/2000) = 2 части → 2+2.
    n = assign_program_numbers(prj, max_geom_len=2000, passes_per_part=2,
                               direction="horizontal")
    progs = [op.attributes['program_number'] for op in prj.operations]
    assert n == 2, f"Программ: {n}"
    assert progs == [1, 1, 2, 2], f"Получили {progs}"


def test_multiple_corridors_pack():
    """4 строки по 1 детали, лимит вмещает 2 целых строки → 2 программы 2+2."""
    prj = _make_project_with_grid(4, 1, knife_w=50, knife_h=180)
    # 4 коридора (строки) по 1 детали = 920 каждый.
    # Лимит = 2000 путей. Две строки (1840) влезают, три (2760) нет.
    # → программы: [строки 1-2, строки 3-4] = 2 программы по 2 строки.
    n = assign_program_numbers(prj, max_geom_len=2000, passes_per_part=2,
                               direction="horizontal")
    progs = [op.attributes['program_number'] for op in prj.operations]
    assert n == 2, f"Программ: {n}"
    # 2 детали в первой программе, 2 во второй
    assert progs.count(1) == 2 and progs.count(2) == 2


def test_horizontal_orders_by_rows():
    """Горизонталь: строки снизу-вверх (от LB), внутри слева направо."""
    prj = _make_project_with_grid(2, 3, knife_w=50, knife_h=180)
    assign_program_numbers(prj, max_geom_len=1000000, direction="horizontal",
                           passes_per_part=2)
    from core.macros import operation_center
    centers = [operation_center(op, prj) for op in prj.operations]
    y_first3 = set(round(c[1], 1) for c in centers[:3])
    y_last3 = set(round(c[1], 1) for c in centers[3:])
    assert len(y_first3) == 1, f"Первые 3 не в одной строке: {centers[:3]}"
    assert len(y_last3) == 1, f"Последние 3 не в одной строке: {centers[3:]}"
    # Снизу вверх: первая строка имеет МЕНЬШИЙ Y
    y1 = list(y_first3)[0]
    y2 = list(y_last3)[0]
    assert y1 < y2, f"Снизу вверх не работает: y1={y1}, y2={y2}"
    # Внутри строки X возрастает (слева направо)
    x_first3 = [c[0] for c in centers[:3]]
    assert x_first3 == sorted(x_first3), f"Не слева направо: {x_first3}"


def test_vertical_orders_by_columns():
    """Вертикаль: столбцы слева направо, внутри снизу вверх."""
    prj = _make_project_with_grid(3, 2, knife_w=50, knife_h=180)
    assign_program_numbers(prj, max_geom_len=1000000, direction="vertical",
                           passes_per_part=2)
    from core.macros import operation_center
    centers = [operation_center(op, prj) for op in prj.operations]
    # Первые 3 — один столбец (одинаковый X)
    x_first3 = set(round(c[0], 1) for c in centers[:3])
    x_last3 = set(round(c[0], 1) for c in centers[3:])
    assert len(x_first3) == 1, f"Первые 3 не в одном столбце: {centers[:3]}"
    # Слева направо: первый столбец имеет МЕНЬШИЙ X
    x1 = list(x_first3)[0]
    x2 = list(x_last3)[0]
    assert x1 < x2, f"Слева направо не работает: x1={x1}, x2={x2}"
    # Внутри столбца Y возрастает (снизу вверх)
    y_first3 = [c[1] for c in centers[:3]]
    assert y_first3 == sorted(y_first3), f"Не снизу вверх: {y_first3}"


def test_uniform_split():
    """1 длинная строка из 4 деталей при лимите вмещающем ~2 → делим на 2+2."""
    prj = _make_project_with_grid(1, 4, knife_w=50, knife_h=180)
    # Один коридор (строка) длиной 4×920 = 3680 путей. Лимит 3000 путей.
    # Коридор 3680 > 3000 → делим на ceil(3680/3000) = 2 части → 2+2.
    n = assign_program_numbers(prj, max_geom_len=3000, passes_per_part=2,
                               direction="horizontal")
    progs = [op.attributes['program_number'] for op in prj.operations]
    assert n == 2, f"Программ: {n}"
    p1 = progs.count(1)
    p2 = progs.count(2)
    assert abs(p1 - p2) <= 1, f"Неравномерное деление: {progs}"


def test_uniform_split_three_programs():
    """1 длинная строка из 9 деталей при лимите → 3 равные части по 3."""
    prj = _make_project_with_grid(1, 9, knife_w=50, knife_h=180)
    # Один коридор длиной 9×920 = 8280 путей. Лимит 3000 путей.
    # Коридор > лимита → делим на ceil(8280/3000) = 3 части → ~3+3+3.
    n = assign_program_numbers(prj, max_geom_len=3000, passes_per_part=2,
                               direction="horizontal")
    progs = [op.attributes['program_number'] for op in prj.operations]
    assert n == 3, f"Программ: {n}"
    counts = [progs.count(i) for i in range(1, 4)]
    for c in counts:
        assert 2 <= c <= 4, f"Неравномерно: {counts}"


if __name__ == "__main__":
    import inspect
    tests = [(n,f) for n,f in inspect.getmembers(sys.modules[__name__])
             if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for n, f in tests:
        try:
            f(); passed += 1; print(f"  [OK] {n}")
        except Exception as e:
            failed.append((n, e)); print(f"  [FAIL] {n}: {e!r}")
            import traceback; traceback.print_exc()
    print(f"\n{passed}/{len(tests)} тестов пройдено")
    sys.exit(0 if not failed else 1)


def test_rows_and_columns_give_different_order():
    """Строки и столбцы дают РАЗНЫЙ порядок обхода.

    Строками (горизонтально): нижний ряд слева-направо, затем верхний.
    Столбцами (вертикально): левый столбец снизу-вверх, затем следующий.
    """
    import os
    from camsys.core.macros import (sort_operations_by_grid, GridDirection,
                                    GridGrouping, operation_center)
    import camsys.core.importer as importer_mod

    ai = '/mnt/user-data/uploads/124173.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    project = importer_mod.import_ai_to_project(ai)
    for geom in project.get_layer_by_name("Knife").geometries[:12]:
        project.add_blade_operation(geom.id)

    def order(grouping):
        sort_operations_by_grid(project, direction=GridDirection.LB,
                                grouping=grouping)
        return [operation_center(op, project) for op in project.operations]

    rows = order(GridGrouping.ROWS)
    cols = order(GridGrouping.COLUMNS)
    print(f'  строками: {[(round(x), round(y)) for x, y in rows[:4]]}')
    print(f'  столбцами: {[(round(x), round(y)) for x, y in cols[:4]]}')
    assert rows != cols, "группировка не влияет на порядок"


def _sv_layout(positions, w=60.0, h=100.0):
    """Проект из прямоугольников в заданных позициях + операции ножей."""
    from camsys.core.project import Project, Geometry
    from camsys.geometry.primitives import Line, Polypath
    project = Project(name="SV")
    layer = project.add_layer("Knife")
    for x, y in positions:
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        poly = Polypath(segments=[Line(a=pts[i], b=pts[(i + 1) % 4])
                                  for i in range(4)], closed=True)
        geom = Geometry(polypath=poly)
        layer.geometries.append(geom)
        project.add_blade_operation(geom.id)
    return project


def _sv_pick(project):
    """Что отберётся в _SV.anc: список центров."""
    import camsys.post.mtx_anderson  # noqa: F401 — регистрация поста
    from camsys.core.cutting_macro import CuttingMacroParams
    from camsys.core.project import OperationKind
    from camsys.core.macros import operation_center
    from camsys.post.package_export import PackageExporter

    exporter = PackageExporter(project, CuttingMacroParams())
    centers = [(op, operation_center(op, project))
               for op in project.operations
               if op.kind != OperationKind.FIDUCIAL_DRILL]
    axis = exporter._sv_single_line(centers)
    if axis is not None:
        k = 0 if axis == 'row' else 1
        ordered = sorted(centers, key=lambda oc: oc[1][k])
        return axis, [ordered[0][1], ordered[-1][1]]
    if len(centers) <= 4:
        return axis, [c for _op, c in centers]
    return axis, None       # ветка «4 угла»


def test_sv_single_row_takes_only_first_and_last():
    """Строка элементов → в SV только крайние, не 1-2 и (n-1)-n.

    На одной строке четыре «угла» bbox вырождаются: слева-сверху и
    слева-снизу оказываются соседние элементы 1 и 2 (их центры по Y
    отличаются на доли миллиметра), справа — (n-1) и n. Для контроля
    сведения это бесполезно.
    """
    for count in (3, 4, 8):
        project = _sv_layout([(70.0 * i, 0.0) for i in range(count)])
        axis, picked = _sv_pick(project)
        print(f'  строка из {count}: ось={axis}, отобрано {len(picked)}')
        assert axis == 'row'
        assert len(picked) == 2, f"строка из {count}: отобрано {len(picked)}"
        # Именно крайние по X
        assert picked[0][0] < picked[1][0]
        assert abs(picked[1][0] - picked[0][0]) > 70.0 * (count - 2)


def test_sv_single_column_takes_only_first_and_last():
    """Столбец элементов — то же самое по вертикали."""
    project = _sv_layout([(0.0, 110.0 * i) for i in range(5)])
    axis, picked = _sv_pick(project)
    print(f'  столбец: ось={axis}, отобрано {len(picked)}')
    assert axis == 'col'
    assert len(picked) == 2
    assert picked[0][1] < picked[1][1]


def test_sv_grid_still_takes_four_corners():
    """Настоящая сетка — по-прежнему четыре угла."""
    project = _sv_layout([(70.0 * i, 110.0 * j)
                          for i in range(3) for j in range(3)])
    axis, picked = _sv_pick(project)
    print(f'  сетка 3x3: ось={axis}')
    assert axis is None, "сетка ошибочно принята за одну линию"
    assert picked is None    # ушли в ветку «4 угла»

    # 2x2 — все четыре и есть углы
    project = _sv_layout([(70.0 * i, 110.0 * j)
                          for i in range(2) for j in range(2)])
    axis, picked = _sv_pick(project)
    assert axis is None and len(picked) == 4
