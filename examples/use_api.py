"""
Пример использования API camsys.core.session.CamSession.

Показывает 3 типичных сценария:
    1. Полный pipeline в коде (как из UI или Excel-макроса)
    2. Передача параметров через JSON (как из удалённого клиента)  
    3. Анализ перед экспортом (для предупреждений в UI)

Запуск из корня проекта:
    py examples/use_api.py
"""

import sys
import os
import json

# Делаем camsys импортируемым
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camsys.core.session import CamSession


def example_1_full_pipeline():
    """Пример 1: типичный workflow от UI."""
    print("=" * 60)
    print("ПРИМЕР 1: полный pipeline")
    print("=" * 60)
    
    sess = CamSession()
    
    # 1. Импорт .ai
    state = sess.load_ai('/mnt/user-data/uploads/118917.ai')
    print(f"Загружено: {state['project']['name']}")
    print(f"Слоёв: {len(state['project']['layers'])}")
    print(f"Реперов: {len(state['project']['fiducials'])}")
    
    # 2. Создание blade-операций
    created = sess.create_blade_operations()
    print(f"\nСоздано операций: {len(created)}")
    
    # 3. Сортировка слева направо снизу вверх
    sess.sort_by_grid(direction='LB', grouping='columns')
    print("Отсортировано по сетке (LB columns)")
    
    # 4. Установка параметров макроса
    sess.set_cutting_params(
        knife_angle=60,
        tip_diameter=1.2,
        top=0.437,
        bottom=0.19,
        generate_corner_3d=False,  # на этих ножах нет очень острых углов
    )
    
    # 5. Превью имён файлов перед экспортом
    print("\nБудут созданы файлы:")
    for name in sess.preview_package_filenames():
        print(f"  {name}")
    
    # 6. Экспорт
    out_dir = '/tmp/api_example_1'
    written = sess.export_package(out_dir)
    
    print(f"\nЗаписано {len(written)} файлов в {out_dir}")
    for f in written:
        print(f"  {f['name']:<40s} {f['size']:>8} байт")


def example_2_json_api():
    """Пример 2: параметры через JSON (удалённый клиент, веб-форма)."""
    print("\n" + "=" * 60)
    print("ПРИМЕР 2: JSON API")
    print("=" * 60)
    
    # Как будто пришёл запрос от UI: вот файл, вот параметры
    request_json = """
    {
      "knife_angle": 70,
      "tip_diameter": 0.6,
      "top": 0.5,
      "bottom": 0.25,
      "generate_rough_all": true,
      "generate_reverse": false,
      "generate_finish_per_op": true,
      "generate_sv": false,
      "generate_corner": true,
      "generate_corner_3d": false,
      "lead_inside": {
        "angle": 45, "length": 1.0, "offset": -6.0, "sign_offset": "-"
      }
    }
    """
    
    sess = CamSession()
    sess.load_ai('/mnt/user-data/uploads/118917.ai')
    sess.create_blade_operations()
    sess.sort_by_grid()
    
    # Применяем параметры из JSON
    params_dict = json.loads(request_json)
    sess.set_cutting_params_from_dict(params_dict)
    
    # Экспорт без записи на диск — словарь {filename: content}
    files_dict = sess.export_package_to_dict()
    
    print(f"Сгенерировано в памяти: {len(files_dict)} файлов")
    for name, content in files_dict.items():
        n_lines = content.count('\n')
        print(f"  {name:<40s} {n_lines:>5} строк")


def example_3_analysis_before_export():
    """Пример 3: анализ перед экспортом (для UI с предупреждениями)."""
    print("\n" + "=" * 60)
    print("ПРИМЕР 3: анализ перед экспортом")
    print("=" * 60)
    
    sess = CamSession()
    sess.load_ai('/mnt/user-data/uploads/118917.ai')
    
    # Проверка острых углов: нужны ли corner / corner3D?
    sharp = sess.analyze_sharp_corners(threshold_deg=90.0)
    print(f"\nОстрых углов на детали (< 90°): {sharp['total']}")
    
    sharp_60 = sess.analyze_sharp_corners(threshold_deg=60.0)
    print(f"Очень острых углов (< 60°): {sharp_60['total']}")
    
    # Рекомендации
    if sharp['total'] == 0:
        print("→ corner.anc и corner3D.anc не нужны")
        sess.set_cutting_params(generate_corner=False, generate_corner_3d=False)
    elif sharp_60['total'] == 0:
        print("→ corner.anc нужен (тонкая 2D-фреза), corner3D — нет")
        sess.set_cutting_params(generate_corner=True, generate_corner_3d=False)
    else:
        print("→ нужны и corner.anc и corner3D.anc")
        sess.set_cutting_params(generate_corner=True, generate_corner_3d=True)
    
    # Снимок состояния — UI может его показать как сводку проекта
    state = sess.get_state()
    print(f"\nСводка проекта '{state['project']['name']}':")
    for layer in state['project']['layers']:
        if layer['name'] == 'Knife':
            print(f"  Ножей: {layer['closed_count']} замкнутых, "
                  f"{layer['open_count']} открытых")
    
    # Сохранение состояния для повторного использования
    json_path = '/tmp/api_example_3_state.json'
    sess.save_state_to_json(json_path)
    print(f"\nСостояние сохранено в {json_path}")


if __name__ == "__main__":
    if not os.path.exists('/mnt/user-data/uploads/118917.ai'):
        print("Файл /mnt/user-data/uploads/118917.ai не найден")
        sys.exit(1)
    
    example_1_full_pipeline()
    example_2_json_api()
    example_3_analysis_before_export()
    
    print("\n" + "=" * 60)
    print("Все примеры выполнены успешно")
