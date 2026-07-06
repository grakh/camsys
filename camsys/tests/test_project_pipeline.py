"""Интеграционный тест: импорт .ai -> создание Project с операциями."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# для импорта 'from ..io_.ai_parser' требуется родительский пакет;
# обходим — добавим родителя camsys в sys.path и импортируем через 'camsys.'

# Чтобы не возиться с relative imports в тестах, импортируем напрямую:
import importlib.util

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Структурируем как пакет — сделаем простой импорт через sys.path
sys.path.insert(0, os.path.dirname(ROOT))

import camsys.core.project as project_mod
import camsys.core.importer as importer_mod


def test_import_real_ai():
    """Импорт реального 118917.ai -> Project с 6 слоями."""
    ai = '/mnt/user-data/uploads/118917.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла 118917.ai')
        return
    
    project = importer_mod.import_ai_to_project(ai, project_name="118917-test")
    
    print(f'  Имя:  {project.name}')
    print(f'  Слоёв: {len(project.layers)}')
    for layer in project.layers.values():
        print(f'    {layer.name:10s}: {len(layer.geometries)} объектов')
    print(f'  Реперов: {len(project.fiducials)}')
    for f in project.fiducials:
        print(f'    {f.name}: ({f.x:.3f}, {f.y:.3f})')
    
    # Проверки
    assert project.name == "118917-test"
    assert "Knife" in [l.name for l in project.layers.values()]
    knife = project.get_layer_by_name("Knife")
    assert knife is not None
    # 5 контуров ножа должны попасть
    assert len(knife.geometries) == 5, f"ожидали 5, получили {len(knife.geometries)}"
    # 2 репера
    assert len(project.fiducials) == 2


def test_create_blade_operations():
    """Создание операций обточки для всех контуров слоя Knife."""
    ai = '/mnt/user-data/uploads/118917.ai'
    if not os.path.exists(ai):
        return
    
    project = importer_mod.import_ai_to_project(ai)
    knife = project.get_layer_by_name("Knife")
    
    # Создаём blade-операцию для каждого контура
    for geom in knife.geometries:
        op = project.add_blade_operation(geom.id)
        # Каждая операция должна иметь 2 toolpath: OUTSIDE и INSIDE
        assert len(op.toolpaths) == 2
        sides = {tp.side for tp in op.toolpaths}
        assert project_mod.ContourSide.OUTSIDE in sides
        assert project_mod.ContourSide.INSIDE in sides
    
    print(f'  Создано операций: {len(project.operations)}')
    print(f'  Всего ToolPath-ов: {sum(len(o.toolpaths) for o in project.operations)}')
    assert len(project.operations) == 5


def test_add_corner_rework():
    """Добавление операции доработки тонкой фрезой."""
    ai = '/mnt/user-data/uploads/118917.ai'
    if not os.path.exists(ai):
        return
    
    project = importer_mod.import_ai_to_project(ai)
    knife = project.get_layer_by_name("Knife")
    geom = knife.geometries[0]
    
    # Сначала обычная blade-операция
    op_blade = project.add_blade_operation(geom.id)
    
    # Потом доработка от t=0.3 до t=0.45 тонкой фрезой T2
    op_corner = project.add_corner_rework(geom.id, start_t=0.3, end_t=0.45,
                                          tool_number=2)
    
    assert op_corner.kind == project_mod.OperationKind.CORNER_REWORK
    assert len(op_corner.toolpaths) == 1
    assert op_corner.toolpaths[0].start_t == 0.3
    assert op_corner.toolpaths[0].end_t == 0.45
    assert op_corner.settings.tool_number == 2
    
    print(f'  Blade op: {op_blade.name}, {len(op_blade.toolpaths)} путей')
    print(f'  Corner op: {op_corner.name}, доработка t=[0.3..0.45]')


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
