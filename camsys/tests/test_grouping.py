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
