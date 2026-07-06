#!/usr/bin/env python3
"""
main.py — точка входа camsys (демо-режим, без UI).

Запуск:
    python3 main.py <input.ai> [output.anc]
    
Без аргументов запускает на встроенном примере 118917.ai.

Делает полный pipeline:
    1. Импорт .ai
    2. Создание blade-операций для всех контуров слоя Knife
    3. Сортировка операций по сетке (слева-направо)
    4. Перенумерация
    5. Экспорт в .anc через постпроцессор MTX Anderson GVM

Это демо показывает что библиотека работает. Полноценный CAM с UI
(дерево слоёв, выделение мышью, диалоги настроек) — следующая итерация.
"""

from __future__ import annotations
import sys
import os
import argparse
from pathlib import Path


def cmd_list_layers(args):
    """list-layers <input.ai>  — перечислить слои в .ai"""
    from camsys.io_.ai_parser import list_layers
    layers = list_layers(args.input_ai)
    print(f"Слои в {args.input_ai}:")
    for name in layers:
        print(f"  - {name}")


def cmd_info(args):
    """info <input.ai>  — показать что в файле (слои, контуры, реперы)"""
    from camsys.core.importer import import_ai_to_project
    
    project = import_ai_to_project(args.input_ai)
    
    print(f"Проект: {project.name}")
    print(f"Источник: {project.source_ai_path}")
    print()
    print("Слои:")
    for layer in project.layers.values():
        n_closed = sum(1 for g in layer.geometries if g.is_closed)
        n_open = len(layer.geometries) - n_closed
        print(f"  {layer.name:12s}  замкнутых={n_closed:3d}, открытых={n_open:3d}")
    
    print()
    print(f"Реперов: {len(project.fiducials)}")
    for f in project.fiducials:
        print(f"  {f.name}: ({f.x:.3f}, {f.y:.3f})")


def cmd_export(args):
    """export <input.ai> [output.anc]  — полный pipeline до .anc"""
    from camsys.core.importer import import_ai_to_project
    from camsys.core.macros import (
        sort_operations_by_grid, GridDirection, GridGrouping
    )
    from camsys.post.base import PostRegistry, PostOptions
    import camsys.post.mtx_anderson  # регистрирует пост в Registry

    input_ai = args.input_ai
    output_anc = args.output_anc or _default_output_path(input_ai)
    
    print(f"→ Импорт: {input_ai}")
    project = import_ai_to_project(input_ai, project_name=Path(input_ai).stem)
    
    knife = project.get_layer_by_name(args.knife_layer)
    if knife is None or not knife.geometries:
        print(f"  ОШИБКА: слой '{args.knife_layer}' не найден или пустой")
        return 1
    print(f"  слой '{knife.name}': {len(knife.geometries)} контуров")
    print(f"  реперов: {len(project.fiducials)}")
    
    print("→ Операции:")
    for geom in knife.geometries:
        op = project.add_blade_operation(geom.id)
        print(f"  {op.name}: {len(op.toolpaths)} путей")
    
    print("→ Сортировка по сетке (LB columns)")
    sort_operations_by_grid(
        project,
        direction=GridDirection.LB,
        grouping=GridGrouping.COLUMNS,
    )
    
    # Один SHAPE на весь файл (как в эталоне)
    if args.one_shape:
        print("→ Все операции → SHAPE1 (как в эталоне)")
        for op in project.operations:
            op.sequence_number = 1
    
    print(f"→ Постпроцессор: {args.post}")
    post = PostRegistry.get(args.post)
    if post is None:
        print(f"  ОШИБКА: пост '{args.post}' не найден")
        print(f"  Доступны: {PostRegistry.names()}")
        return 1
    
    options = PostOptions(
        program_name=Path(input_ai).stem + "_export",
        sheet_thickness=args.sheet_thickness,
        z_depth=args.z_depth,
        fiducial_distance=args.fiducial_distance,
        include_fiducial_marks=args.fiducial_marks,
    )
    
    anc_text = post.generate(project, options)
    
    output_path = Path(output_anc)
    output_path.write_text(anc_text)
    print(f"→ Записан файл: {output_path}")
    print(f"  размер: {len(anc_text)} символов, "
          f"{anc_text.count(chr(10))} строк")
    return 0


def cmd_package(args):
    """package <input.ai> [output_dir]  — генерация пакета файлов (как макрос Cutting)"""
    from camsys.core.importer import import_ai_to_project
    from camsys.core.macros import (
        sort_operations_by_grid, GridDirection, GridGrouping
    )
    from camsys.core.cutting_macro import CuttingMacroParams, CutDirection
    from camsys.post.package_export import PackageExporter
    import camsys.post.mtx_anderson  # регистрирует пост

    input_ai = args.input_ai
    output_dir = args.output_dir or '.'
    
    print(f"-> Импорт: {input_ai}")
    project = import_ai_to_project(input_ai, project_name=Path(input_ai).stem)
    
    knife = project.get_layer_by_name(args.knife_layer)
    if knife is None or not knife.geometries:
        print(f"  ОШИБКА: слой '{args.knife_layer}' не найден или пуст")
        return 1
    print(f"  слой '{knife.name}': {len(knife.geometries)} контуров")
    
    print("-> Создание операций")
    for geom in knife.geometries:
        project.add_blade_operation(geom.id)
    
    print("-> Сортировка по сетке")
    sort_operations_by_grid(project, direction=GridDirection.LB,
                            grouping=GridGrouping.COLUMNS)
    
    # Параметры макроса
    params = CuttingMacroParams(
        knife_angle=args.angle,
        tip_diameter=args.tip,
        top=args.top,
        bottom=args.bottom,
        output_prefix=args.prefix or Path(input_ai).stem,
        generate_rough_all=True,
        generate_reverse=args.reverse,
        generate_finish_per_op=True,
        generate_sv=args.sv,
        generate_corner=args.corner,
        generate_corner_3d=args.corner_3d,
        sharp_angle_threshold=args.sharp_threshold,
    )
    
    print(f"\n-> Параметры:")
    print(f"   угол ножа:  {params.knife_angle}°")
    print(f"   пятка:      {params.tip_diameter} мм")
    print(f"   верх:       {params.top} мм")
    print(f"   низ:        {params.bottom} мм")
    print(f"   реверс:     {params.generate_reverse}")
    print(f"   SV:         {params.generate_sv}")
    print(f"   corner:     {params.generate_corner}")
    print(f"   corner3D:   {params.generate_corner_3d}")
    
    print(f"\n-> Генерация пакета в {output_dir}")
    exporter = PackageExporter(project, params)
    written = exporter.write_all(output_dir)
    
    print(f"\nЗаписано файлов: {len(written)}")
    for path in written:
        size = path.stat().st_size
        print(f"  {path.name:<45s} {size:>8} байт")
    return 0



    """tools-list  — показать встроенную базу инструментов"""
    from camsys.tools.tool_db import demo_db
    db = demo_db()
    print(f"Инструменты ({len(db.tools)}):")
    print(f"{'#':>3} {'Имя':<12} {'d_tip':>7} {'угол':>6} {'F':>6} {'S':>7}")
    for n, t in sorted(db.tools.items()):
        print(f"{n:>3} {t.name:<12} {t.tip_diameter:>7.3f} "
              f"{t.open_angle:>6.1f} {t.feed_cut:>6} {t.spindle_rpm:>7}")


def cmd_gui(args):
    """gui  — запустить графический интерфейс (PySide6)"""
    try:
        from camsys.ui.main_window import run_app
    except ImportError as e:
        print("Не удалось импортировать UI. Установите PySide6:")
        print("  pip install PySide6")
        print(f"\nОшибка: {e}")
        return 1
    return run_app()


def cmd_tools_list(args):
    """tools-list  — показать встроенную базу инструментов"""
    from camsys.tools.tool_db import demo_db
    db = demo_db()
    print(f"Инструменты ({len(db.tools)}):")
    print(f"{'#':>3} {'Имя':<12} {'d_tip':>7} {'угол':>6} {'F':>6} {'S':>7}")
    for n, t in sorted(db.tools.items()):
        print(f"{n:>3} {t.name:<12} {t.tip_diameter:>7.3f} "
              f"{t.open_angle:>6.1f} {t.feed_cut:>6} {t.spindle_rpm:>7}")


def cmd_tests(args):
    """tests  — прогон всех тестов"""
    import subprocess, os
    here = Path(__file__).parent.resolve()
    camsys_dir = here / 'camsys'
    tests_dir = camsys_dir / 'tests'
    test_files = sorted(tests_dir.glob('test_*.py'))
    
    # Принудительный UTF-8 для дочерних процессов Python — иначе на Windows
    # PowerShell использует CP1251 и не может выводить ✓/✗/→ в тестах.
    child_env = os.environ.copy()
    child_env['PYTHONIOENCODING'] = 'utf-8'
    child_env['PYTHONUTF8'] = '1'
    
    print(f"Найдено тестовых файлов: {len(test_files)}\n")
    total_passed = 0
    total_tests = 0
    any_failed = False
    
    for tf in test_files:
        print(f"=== {tf.name} ===")
        result = subprocess.run(
            [sys.executable, str(tf)],
            capture_output=True, text=True,
            cwd=str(camsys_dir),
            env=child_env,
            encoding='utf-8',
            errors='replace',
        )
        
        if args.verbose:
            # Подробный вывод: всё что напечатали тесты
            if result.stdout:
                for line in result.stdout.rstrip().split('\n'):
                    print(f"  {line}")
            if result.stderr:
                print("  --- STDERR ---")
                for line in result.stderr.rstrip().split('\n'):
                    print(f"  {line}")
        else:
            # Компактный вывод: только итоговая строка + ошибки если есть
            import re
            lines = [ln for ln in result.stdout.split('\n') if ln.strip()]
            
            # Итоговая строка
            summary_line = None
            for ln in reversed(lines):
                if re.search(r'\d+/\d+\s+тестов\s+пройдено', ln):
                    summary_line = ln.strip()
                    break
            
            if summary_line:
                print(f"  {summary_line}")
            else:
                # Нет итоговой строки — что-то пошло не так
                print("  ОШИБКА: тест не выдал итоговую строку")
                if result.returncode != 0 and result.stderr:
                    print("  --- STDERR ---")
                    for line in result.stderr.rstrip().split('\n')[:20]:
                        print(f"  {line}")
            
            # Падения внутри теста ("✗ ...") тоже покажем кратко
            fail_lines = [ln for ln in lines if '✗' in ln]
            for fl in fail_lines:
                print(f"  {fl.strip()}")
        
        # Парсим итоговую строку для общего счёта
        import re
        lines = [ln for ln in result.stdout.split('\n') if ln.strip()]
        found = False
        for ln in reversed(lines):
            m = re.search(r'(\d+)/(\d+)\s+тестов\s+пройдено', ln)
            if m:
                p, t = int(m.group(1)), int(m.group(2))
                total_passed += p
                total_tests += t
                if p < t:
                    any_failed = True
                found = True
                break
        if not found:
            any_failed = True
        if result.returncode != 0:
            any_failed = True
    
    print(f"\nИТОГО: {total_passed}/{total_tests} тестов пройдено")
    return 0 if not any_failed else 1


def _default_output_path(input_ai: str) -> str:
    """Имя выходного .anc по умолчанию."""
    p = Path(input_ai)
    return str(p.with_suffix('.anc'))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='camsys — CAM для прецизионной обточки флексографических ножей',
        epilog='Примеры:\n'
               '  python3 main.py info path/to/file.ai\n'
               '  python3 main.py export path/to/file.ai out.anc\n'
               '  python3 main.py tests',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='cmd', required=True, help='команда')
    
    # info
    p_info = sub.add_parser('info', help='показать содержимое .ai (слои, контуры, реперы)')
    p_info.add_argument('input_ai', help='путь к .ai файлу')
    p_info.set_defaults(func=cmd_info)
    
    # list-layers
    p_ll = sub.add_parser('list-layers', help='перечислить слои в .ai')
    p_ll.add_argument('input_ai')
    p_ll.set_defaults(func=cmd_list_layers)
    
    # export
    p_exp = sub.add_parser('export', help='полный pipeline: .ai → .anc')
    p_exp.add_argument('input_ai', help='входной .ai')
    p_exp.add_argument('output_anc', nargs='?', help='выходной .anc (опционально)')
    p_exp.add_argument('--knife-layer', default='Knife', help='имя слоя с ножами')
    p_exp.add_argument('--post', default='MTX Anderson GVM V2.13',
                       help='имя постпроцессора')
    p_exp.add_argument('--sheet-thickness', type=float, default=0.437,
                       help='толщина листа, мм')
    p_exp.add_argument('--z-depth', type=float, default=0.19,
                       help='глубина врезания (ProgZDepth), мм')
    p_exp.add_argument('--fiducial-distance', type=float, default=700.0,
                       help='расстояние между реперами PT_PT_DIS, мм')
    p_exp.add_argument('--fiducial-marks', action='store_true',
                       help='выгравировать метки реперов в конце программы')
    p_exp.add_argument('--one-shape', action='store_true', default=True,
                       help='все операции в один SHAPE (по умолчанию)')
    p_exp.set_defaults(func=cmd_export)
    
    # package — главная команда: пакет файлов как у макроса Cutting
    p_pkg = sub.add_parser('package', help='пакет .anc файлов '
                                            '(R, M1..MN, SV, corner, corner3D)')
    p_pkg.add_argument('input_ai', help='входной .ai')
    p_pkg.add_argument('output_dir', nargs='?', default='.',
                       help='папка для выходных файлов (по умолчанию текущая)')
    p_pkg.add_argument('--knife-layer', default='Knife')
    p_pkg.add_argument('--angle', type=float, default=70.0,
                       help='угол ножа, ° (по умолчанию 70)')
    p_pkg.add_argument('--tip', type=float, default=1.2,
                       help='Пятка / диаметр кончика, мм (по умолчанию 1.2)')
    p_pkg.add_argument('--top', type=float, default=0.5,
                       help='Верх / толщина листа, мм (по умолчанию 0.5)')
    p_pkg.add_argument('--bottom', type=float, default=0.25,
                       help='Низ / глубина врезания, мм (по умолчанию 0.25)')
    p_pkg.add_argument('--prefix', default='',
                       help='префикс имён файлов (по умолчанию из имени .ai)')
    p_pkg.add_argument('--reverse', action='store_true', default=True,
                       help='генерировать _revers_R.anc (по умолчанию да)')
    p_pkg.add_argument('--no-reverse', dest='reverse', action='store_false')
    p_pkg.add_argument('--sv', action='store_true', default=True,
                       help='генерировать _SV.anc (по умолчанию да)')
    p_pkg.add_argument('--no-sv', dest='sv', action='store_false')
    p_pkg.add_argument('--corner', action='store_true', default=True,
                       help='генерировать _corner.anc')
    p_pkg.add_argument('--no-corner', dest='corner', action='store_false')
    p_pkg.add_argument('--corner-3d', action='store_true', default=False,
                       help='генерировать _corner3D.anc')
    p_pkg.add_argument('--sharp-threshold', type=float, default=90.0,
                       help='порог острого угла, ° (по умолчанию 90)')
    p_pkg.set_defaults(func=cmd_package)

    # tools-list
    p_t = sub.add_parser('tools-list', help='показать базу инструментов')
    p_t.set_defaults(func=cmd_tools_list)
    
    # gui — графический интерфейс
    p_gui = sub.add_parser('gui', help='запустить графический интерфейс')
    p_gui.set_defaults(func=cmd_gui)
    
    # tests
    p_test = sub.add_parser('tests', help='прогон всех тестов')
    p_test.add_argument('-v', '--verbose', action='store_true',
                        help='подробный вывод (детали каждого теста)')
    p_test.set_defaults(func=cmd_tests)
    
    # ── АВТО-РАСПОЗНАВАНИЕ ──
    # Если пользователь запустил с .ai файлом первым аргументом без 
    # явной команды — подставляем 'package' как самую частую:
    #   py main.py 118953.ai            → py main.py package 118953.ai
    #   py main.py 118953.ai out/       → py main.py package 118953.ai out/
    if argv is None:
        raw_args = sys.argv[1:]
    else:
        raw_args = list(argv)
    
    known_commands = {'info', 'list-layers', 'export', 'package',
                      'tools-list', 'tests', 'gui', '-h', '--help'}
    if (raw_args and raw_args[0] not in known_commands 
            and raw_args[0].lower().endswith('.ai')):
        # Проверим, что файл действительно существует — иначе пользователь
        # скорее всего ошибся в команде, а не хотел package
        if not Path(raw_args[0]).is_file():
            print(f"Файл '{raw_args[0]}' не найден.")
            print()
            print("Возможно, вы хотели:")
            print(f"  py main.py gui                       — запустить интерфейс")
            print(f"  py main.py package <полный_путь.ai>  — экспорт пакета")
            print(f"  py main.py --help                    — все команды")
            return 1
        raw_args = ['package'] + raw_args
        print(f"(подставлена команда 'package' для {raw_args[1]})\n")
    
    args = parser.parse_args(raw_args)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
