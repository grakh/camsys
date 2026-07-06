"""Интеграционный тест: .ai -> Project -> макросы -> постпроцессор -> .anc."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import camsys.core.project as project_mod
import camsys.core.importer as importer_mod
import camsys.core.macros as macros_mod
import camsys.post.base as post_base
import camsys.post.mtx_anderson  # для регистрации поста


def test_post_registry():
    """Реестр постпроцессоров должен иметь как минимум MTX Anderson GVM."""
    names = post_base.PostRegistry.names()
    print(f'  Доступные посты: {names}')
    assert "MTX Anderson GVM V2.13" in names
    
    post = post_base.PostRegistry.get("MTX Anderson GVM V2.13")
    assert post is not None
    assert post.metadata.controller == "NUM Power MTX"


def test_macros_grid_sort():
    """Сортировка по сетке должна расположить операции по порядку обхода."""
    ai = '/mnt/user-data/uploads/118917.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    
    project = importer_mod.import_ai_to_project(ai)
    knife = project.get_layer_by_name("Knife")
    
    # Создаём операции в случайном порядке (для проверки сортировки)
    import random
    random.seed(42)
    geoms = list(knife.geometries)
    random.shuffle(geoms)
    for g in geoms:
        project.add_blade_operation(g.id)
    
    # До сортировки
    centers_before = [macros_mod.operation_center(op, project) 
                      for op in project.operations]
    
    # Сортируем слева-направо, снизу-вверх
    macros_mod.sort_operations_by_grid(
        project,
        direction=macros_mod.GridDirection.LB,
        grouping=macros_mod.GridGrouping.COLUMNS,
    )
    
    centers_after = [macros_mod.operation_center(op, project) 
                     for op in project.operations]
    
    print('  Центры после сортировки (LB columns):')
    for i, (x, y) in enumerate(centers_after):
        op = project.operations[i]
        print(f'    op{i+1} seq={op.sequence_number}: X={x:.2f}, Y={y:.2f}')
    
    # Все 5 ножей в .ai стоят на одном Y=~115, X от 35 до 305 с шагом ~60.
    # Поскольку 5 точек на одной горизонтали — это 5 столбцов, после
    # сортировки LB они должны идти слева-направо: X возрастает.
    xs = [c[0] for c in centers_after]
    assert xs == sorted(xs), f"X не возрастает: {xs}"


def test_generate_anc():
    """Генерация .anc на реальном файле."""
    ai = '/mnt/user-data/uploads/118917.ai'
    if not os.path.exists(ai):
        return
    
    # 1. Импорт
    project = importer_mod.import_ai_to_project(ai, project_name="118917")
    knife = project.get_layer_by_name("Knife")
    
    # 2. Операции
    for geom in knife.geometries:
        project.add_blade_operation(geom.id)
    
    # 3. Сортировка
    macros_mod.sort_operations_by_grid(project)
    
    # 4. Все операции с sequence_number = 1 (один SHAPE на весь файл,
    #    как в эталоне 118917_60_all_R.anc)
    for op in project.operations:
        op.sequence_number = 1
    
    # 5. Постпроцессор
    post = post_base.PostRegistry.get("MTX Anderson GVM V2.13")
    options = post_base.PostOptions(
        program_name="118917_60_all_R",
        sheet_thickness=0.437,
        z_depth=0.19,
        fiducial_distance=700,
        include_fiducial_marks=True,
    )
    
    anc_text = post.generate(project, options)
    
    print(f'  Сгенерировано .anc: {len(anc_text)} символов, '
          f'{anc_text.count(chr(10))} строк')
    
    # Сохраним для просмотра
    out_path = '/tmp/118917_generated.anc'
    with open(out_path, 'w') as f:
        f.write(anc_text)
    print(f'  Файл: {out_path}')
    
    # Базовые проверки структуры
    assert "PRESETTINGS" in anc_text
    assert "TOOLDATA" in anc_text
    assert ".SHAPE1" in anc_text
    assert ".PART1" in anc_text
    assert ".PART10" in anc_text  # 5 ножей × 2 прохода
    assert "M30" in anc_text
    assert ".ERR_1" in anc_text
    assert "G42" in anc_text  # внешняя сторона
    assert "G41" in anc_text  # внутренняя сторона
    # Параметры из эталона
    assert "ABS(0.19)" in anc_text
    assert "0.437" in anc_text
    assert "700" in anc_text
    # Реперы
    assert "304.8" in anc_text


def test_compare_to_reference():
    """Сравнить структуру сгенерированного .anc с эталоном."""
    ref_path = '/mnt/user-data/uploads/118917_60_all_R.anc'
    if not os.path.exists(ref_path):
        return
    
    with open(ref_path) as f:
        ref = f.read()
    
    gen_path = '/tmp/118917_generated.anc'
    if not os.path.exists(gen_path):
        return
    with open(gen_path) as f:
        gen = f.read()
    
    # Базовые маркеры структуры
    import re
    ref_shapes = set(re.findall(r'SHAPE(\d+)', ref))
    gen_shapes = set(re.findall(r'SHAPE(\d+)', gen))
    ref_parts = set(re.findall(r'PART(\d+)', ref))
    gen_parts = set(re.findall(r'PART(\d+)', gen))
    
    print(f'  Эталон: SHAPE={sorted(int(s) for s in ref_shapes)}, '
          f'PART={sorted(int(p) for p in ref_parts)}')
    print(f'  Наш:    SHAPE={sorted(int(s) for s in gen_shapes)}, '
          f'PART={sorted(int(p) for p in gen_parts)}')
    
    # Количество G1/G2/G3 движений
    def count_moves(text):
        return {
            'G0': len(re.findall(r'\bG0\b', text)),
            'G1': len(re.findall(r'\bG1\b', text)),
            'G2': len(re.findall(r'\bG2\b', text)),
            'G3': len(re.findall(r'\bG3\b', text)),
            'G41': len(re.findall(r'\bG41\b', text)),
            'G42': len(re.findall(r'\bG42\b', text)),
        }
    
    ref_counts = count_moves(ref)
    gen_counts = count_moves(gen)
    
    print(f'  {"Move":4} {"Эталон":>8} {"Наш":>8}  отн.')
    for k in ['G0', 'G1', 'G2', 'G3', 'G41', 'G42']:
        ratio = (gen_counts[k] / ref_counts[k]) if ref_counts[k] else 0
        print(f'  {k:4} {ref_counts[k]:>8} {gen_counts[k]:>8}  {ratio:.2f}x')


if __name__ == "__main__":
    import inspect
    tests = [(n,f) for n,f in inspect.getmembers(sys.modules[__name__])
             if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for n, f in tests:
        try:
            print(f"\n-> {n}")
            f()
            passed += 1
            print(f"  [OK] OK")
        except Exception as e:
            failed.append((n, e))
            print(f"  [FAIL] {e!r}")
            import traceback; traceback.print_exc()
    print(f"\n{passed}/{len(tests)} тестов пройдено")
    sys.exit(0 if not failed else 1)
