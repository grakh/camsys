"""Тесты CamSession — фасадный API для UI / клиентов."""

import sys, os, json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from camsys.core.session import CamSession


AI_PATH = '/mnt/user-data/uploads/118917.ai'


def test_session_initial_state():
    """Свежая сессия: проекта нет, есть дефолтные параметры."""
    sess = CamSession()
    state = sess.get_state()
    
    assert state['project'] is None
    assert state['cutting_params']['knife_angle'] == 70.0  # дефолт
    assert state['post_name'] == "MTX Anderson GVM V2.13"
    assert not sess.has_project()


def test_session_load_ai():
    """После load_ai в state появляется проект."""
    if not os.path.exists(AI_PATH):
        print("  SKIP — нет .ai")
        return
    
    sess = CamSession()
    state = sess.load_ai(AI_PATH)
    
    assert state['project'] is not None
    assert state['project']['name'] == '118917'
    
    # Слои на месте
    layer_names = [l['name'] for l in state['project']['layers']]
    assert 'Knife' in layer_names
    
    # Реперы распознаны
    assert len(state['project']['fiducials']) == 2


def test_session_create_operations():
    """Создание blade-операций — должно появиться по 1 на каждый замкнутый контур."""
    if not os.path.exists(AI_PATH):
        return
    sess = CamSession()
    sess.load_ai(AI_PATH)
    
    created = sess.create_blade_operations()
    
    state = sess.get_state()
    assert len(state['project']['operations']) == 5
    assert len(created) == 5


def test_session_sort_and_renumber():
    """Сортировка по сетке + перенумерация."""
    if not os.path.exists(AI_PATH):
        return
    sess = CamSession()
    sess.load_ai(AI_PATH)
    sess.create_blade_operations()
    sess.sort_by_grid(direction='LB', grouping='columns')
    
    state = sess.get_state()
    # после sort_operations_by_grid sequence_number проставляется
    seqs = [op['sequence_number'] for op in state['project']['operations']]
    assert seqs == [1, 2, 3, 4, 5]


def test_session_set_cutting_params():
    """Установка параметров макроса через **kwargs."""
    sess = CamSession()
    sess.set_cutting_params(
        knife_angle=60,
        tip_diameter=1.2,
        top=0.437,
        bottom=0.19,
        generate_corner_3d=True,
    )
    
    params = sess.get_cutting_params_dict()
    assert params['knife_angle'] == 60
    assert abs(params['tip_diameter'] - 1.2) < 1e-9
    assert params['top'] == 0.437
    assert params['bottom'] == 0.19
    assert params['generate_corner_3d'] is True


def test_session_set_params_from_dict():
    """Установка параметров из JSON-словаря (как из UI)."""
    sess = CamSession()
    sess.set_cutting_params_from_dict({
        'knife_angle': 90,
        'tip_diameter': 0.6,
        'top': 1.0,
        'bottom': 0.3,
        'direction': 'vertical',  # Enum через строку
        'lead_inside': {
            'angle': 30, 'length': 2.0, 'offset': -5.0, 'sign_offset': '-'
        },
    })
    
    p = sess.cutting_params
    assert p.knife_angle == 90
    assert p.direction.value == 'vertical'
    assert p.lead_inside.angle == 30
    assert p.lead_inside.offset == -5.0


def test_session_preview_filenames():
    """Preview имён файлов до экспорта."""
    if not os.path.exists(AI_PATH):
        return
    sess = CamSession()
    sess.load_ai(AI_PATH)
    sess.create_blade_operations()
    sess.sort_by_grid()
    # Малый лимит длины → каждый нож в своей программе (5 ножей = 5 _M)
    sess.set_cutting_params_from_dict({
        'knife_angle': 60,
        'max_geom_len': 100,  # очень малый → каждая деталь отдельно
    })
    
    names = sess.preview_package_filenames()
    
    assert any('all_R' in n for n in names)
    assert any('revers_R' in n for n in names)
    # При малом лимите — по программе на нож (5)
    assert sum(1 for n in names if '_M.anc' in n) == 5
    assert any('_SV.anc' in n for n in names)
    assert any('_corner.anc' in n for n in names)


def test_session_grouping_combines():
    """При большом лимите все ножи объединяются в одну программу."""
    if not os.path.exists(AI_PATH):
        return
    sess = CamSession()
    sess.load_ai(AI_PATH)
    sess.create_blade_operations()
    sess.sort_by_grid()
    sess.set_cutting_params_from_dict({
        'knife_angle': 60,
        'max_geom_len': 100000,  # огромный → все в одной
    })
    names = sess.preview_package_filenames()
    # Все ножи в одной программе → ровно 1 _M файл
    assert sum(1 for n in names if '_M.anc' in n) == 1


def test_session_export_package():
    """Полный экспорт пакета."""
    if not os.path.exists(AI_PATH):
        return
    
    sess = CamSession()
    sess.load_ai(AI_PATH)
    sess.create_blade_operations()
    sess.sort_by_grid()
    sess.set_cutting_params_from_dict({
        'knife_angle': 60, 'tip_diameter': 1.2, 'top': 0.437, 'bottom': 0.19,
        'max_geom_len': 100,  # очень малый → каждый нож в своей программе
    })
    
    out_dir = '/tmp/session_test_out'
    import shutil
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    
    written = sess.export_package(out_dir)
    
    # 5 _M + _all_R + _revers_R + _SV + _corner = 9
    assert len(written) == 9
    
    # Все файлы существуют и не пустые (кроме corner если нет углов)
    for f in written:
        assert os.path.exists(f['path'])
        assert f['size'] > 0


def test_session_export_to_dict():
    """Экспорт в словарь {name: content} без записи."""
    if not os.path.exists(AI_PATH):
        return
    sess = CamSession()
    sess.load_ai(AI_PATH)
    sess.create_blade_operations()
    sess.set_cutting_params(knife_angle=60)
    
    files = sess.export_package_to_dict()
    
    assert isinstance(files, dict)
    assert any('all_R' in n for n in files)
    # Содержимое — это G-код в виде строк
    for content in files.values():
        assert isinstance(content, str)


def test_session_analyze_corners():
    """Анализ острых углов через API."""
    if not os.path.exists(AI_PATH):
        return
    sess = CamSession()
    sess.load_ai(AI_PATH)
    
    result = sess.analyze_sharp_corners(threshold_deg=90.0)
    
    assert 'total' in result
    assert 'per_geometry' in result
    # На 118917 нет острых углов
    assert result['total'] == 0


def test_session_save_load_state():
    """JSON-сохранение и восстановление параметров."""
    if not os.path.exists(AI_PATH):
        return
    sess1 = CamSession()
    sess1.load_ai(AI_PATH)
    sess1.set_cutting_params(knife_angle=80, tip_diameter=1.5)
    
    json_path = '/tmp/session_state.json'
    sess1.save_state_to_json(json_path)
    
    # Новая сессия восстанавливает из JSON
    sess2 = CamSession()
    sess2.load_state_from_json(json_path)
    
    assert sess2.cutting_params.knife_angle == 80
    assert sess2.cutting_params.tip_diameter == 1.5
    # И проект тоже подтянулся из source_ai_path
    assert sess2.has_project()
    assert sess2.project.name == '118917'


def test_blade_order_inside_first():
    """Проверка зафиксированного правила: в blade-операции сначала идёт
    внутренний обход (INSIDE/CW), потом внешний (OUTSIDE/CCW).
    Это соответствует производственному стандарту флексографии."""
    if not os.path.exists(AI_PATH):
        return
    
    sess = CamSession()
    sess.load_ai(AI_PATH)
    sess.create_blade_operations()
    
    state = sess.get_state()
    # Возьмём первую операцию
    op_id = state['project']['operations'][0]['id']
    op = sess.project.find_operation(op_id)
    
    from camsys.core.project import ContourSide
    
    assert len(op.toolpaths) == 2
    assert op.toolpaths[0].side == ContourSide.INSIDE, \
        f"Первый проход должен быть INSIDE, получили {op.toolpaths[0].side}"
    assert op.toolpaths[1].side == ContourSide.OUTSIDE, \
        f"Второй проход должен быть OUTSIDE, получили {op.toolpaths[1].side}"


def test_generated_anc_has_correct_direction():
    """В сгенерированном .anc первый PART должен идти CW (внутренний),
    второй CCW (внешний) — как в эталоне Альфакама."""
    if not os.path.exists(AI_PATH):
        return
    
    sess = CamSession()
    sess.load_ai(AI_PATH)
    sess.create_blade_operations()
    sess.set_cutting_params(knife_angle=60)
    
    files = sess.export_package_to_dict()
    
    # Возьмём чистовую программу для одного ножа (_1_M.anc)
    m_files = [name for name in files if '_1_M.anc' in name]
    assert m_files, "Не найден файл _1_M.anc"
    content = files[m_files[0]]
    
    # Найдём координаты PART1 и PART2
    import re
    parts_data = {}
    current_part = None
    for line in content.split('\n'):
        m = re.search(r'PART(\d+)\)', line)
        if m:
            current_part = int(m.group(1))
            parts_data[current_part] = []
            continue
        if current_part is None:
            continue
        # координаты вне G0 / G1 Z
        if ('G0' in line) or ('G1 Z' in line):
            continue
        cm = re.search(r'X([-\d.]+)\s+Y([-\d.]+)', line)
        if cm:
            parts_data[current_part].append((float(cm.group(1)), 
                                              float(cm.group(2))))
    
    def signed_area(pts):
        if len(pts) < 3: return 0
        a = 0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i+1) % len(pts)]
            a += x1*y2 - x2*y1
        return a / 2
    
    # Один M-файл = один нож = 2 PART (INSIDE + OUTSIDE)
    assert 1 in parts_data and 2 in parts_data
    
    area1 = signed_area(parts_data[1])
    area2 = signed_area(parts_data[2])
    
    print()
    print(f'    PART1: {len(parts_data[1])} точек, площадь={area1:.1f}')
    print(f'    PART2: {len(parts_data[2])} точек, площадь={area2:.1f}')
    
    # PART1 = INSIDE = CW = отрицательная площадь
    # PART2 = OUTSIDE = CCW = положительная площадь
    assert area1 < 0, f"PART1 (INSIDE) должен быть CW, площадь={area1}"
    assert area2 > 0, f"PART2 (OUTSIDE) должен быть CCW, площадь={area2}"


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
