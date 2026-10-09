"""Статические проверки UI-модулей — без запуска Qt.

Диалоги и панели живьём в тестах не поднять (нужен дисплей), поэтому
целый класс ошибок в них ловится только руками пользователя. Здесь
разбираем исходник в AST и проверяем то, что проверяемо статически.

Повод: v1.7.4 уронила диалог «Значения по умолчанию» — локальная функция
`_spin` вызывалась на 9 строк раньше своего `def`:

    w_corner_thr = _spin(...)        # ← UnboundLocalError
    ...
    def _spin(rng, dec, step, val):
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'ui')


def _ui_sources():
    out = []
    for name in sorted(os.listdir(UI_DIR)):
        if name.endswith('.py'):
            path = os.path.join(UI_DIR, name)
            out.append((name, open(path, encoding='utf-8').read()))
    return out


def _local_defs(func_node):
    """{имя: строка_def} для функций, объявленных ПРЯМО в теле func_node."""
    found = {}
    for node in func_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node.lineno
    return found


def _first_use_line(func_node, name, def_line):
    """Первая строка, где имя ВЫЗЫВАЕТСЯ, если она раньше def."""
    worst = None
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == name:
            if fn.lineno < def_line:
                if worst is None or fn.lineno < worst:
                    worst = fn.lineno
    return worst


def test_no_local_function_used_before_definition():
    """Локальная функция не вызывается раньше своего def.

    Именно так упал диалог умолчаний в v1.7.4: вставленная строка встала
    выше `def _spin`, и Python дал UnboundLocalError уже при открытии
    диалога. Синтаксис при этом валиден, импорт проходит — поймать можно
    только такой проверкой либо живым кликом.
    """
    problems = []
    for filename, source in _ui_sources():
        tree = ast.parse(source, filename=filename)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for name, def_line in _local_defs(func).items():
                use_line = _first_use_line(func, name, def_line)
                if use_line is not None:
                    problems.append(
                        f"{filename}:{use_line} — {func.name}() вызывает "
                        f"{name}() до её def на строке {def_line}")
    for p in problems:
        print('  ' + p)
    assert not problems, (
        f"использование локальной функции до определения: {len(problems)}")


def test_defaults_dialog_saves_every_widget_it_shows():
    """Каждое поле диалога умолчаний попадает в _set_defaults.

    Ловит вторую половину той же ошибки: поле добавили в форму, а в
    сохранение забыли — тогда оно молча не сохраняется (ровно так вёл
    себя порог радиуса угла до v1.7.4, живя только в датаклассе).
    """
    source = open(os.path.join(UI_DIR, 'main_window.py'),
                  encoding='utf-8').read()
    tree = ast.parse(source)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and \
                node.name == '_action_edit_defaults':
            target = node
            break
    assert target is not None, "_action_edit_defaults не найдена"

    # Виджеты диалога: локальные имена вида w_*
    widgets = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id.startswith('w_'):
                    widgets.add(tgt.id)
    assert widgets, "виджеты не распознаны"

    # Имена, читаемые внутри вызова self._set_defaults({...})
    saved = set()
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == '_set_defaults'):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id.startswith('w_'):
                saved.add(sub.id)

    missing = sorted(widgets - saved)
    for name in missing:
        print(f'  не сохраняется: {name}')
    assert not missing, f"поля диалога не попадают в _set_defaults: {missing}"


def test_sort_by_grid_is_never_called_bare():
    """UI не зовёт session.sort_by_grid() без grouping.

    Регрессия v1.7.6: три из четырёх точек вызова звали его голым, а
    умолчание в session — 'columns'. Поэтому при выбранном
    «→ Горизонтально» обход всё равно шёл столбцами снизу-вверх, и
    перебеги на экране выглядели вертикальными. Направление обхода должно
    браться из переключателя, а не из умолчания библиотеки.
    """
    bare = []
    for filename, source in _ui_sources():
        tree = ast.parse(source, filename=filename)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == 'sort_by_grid'):
                continue
            has_grouping = any(kw.arg == 'grouping' for kw in node.keywords) \
                or len(node.args) >= 2
            if not has_grouping:
                bare.append(f"{filename}:{node.lineno}")
    for b in bare:
        print(f'  голый вызов: {b}')
    assert not bare, f"sort_by_grid без grouping: {bare}"


def test_direction_radio_is_connected():
    """Переключатель «Направление» подключён к обработчику.

    Регрессия v1.7.6: радио эмитило paramsChanged, а этот сигнал НИКУДА
    не был подключён — смена направления не делала ничего.
    """
    source = open(os.path.join(UI_DIR, 'main_window.py'),
                  encoding='utf-8').read()
    assert 'dir_horiz.toggled.connect' in source, \
        "dir_horiz ни к чему не подключён — переключатель мёртвый"


def _defaults_keys(source):
    """(сохраняемые ключи, читаемые ключи) для умолчаний main_window."""
    tree = ast.parse(source)
    saved, read = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute):
            continue
        if fn.attr == '_set_defaults':
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    for k in sub.keys:
                        if isinstance(k, ast.Constant) and \
                                isinstance(k.value, str):
                            saved.add(k.value)
        elif fn.attr == '_get_default':
            if node.args and isinstance(node.args[0], ast.Constant) and \
                    isinstance(node.args[0].value, str):
                read.add(node.args[0].value)
    return saved, read


def test_every_saved_default_is_read_back():
    """Каждое сохранённое умолчание где-то читается.

    Ловит «сохранили и забыли применить»: ключ пишется в
    camsys_settings.json, но ни один _get_default его не запрашивает —
    значит настройка не действует, а оператор думает, что действует.
    """
    source = open(os.path.join(UI_DIR, 'main_window.py'),
                  encoding='utf-8').read()
    saved, read = _defaults_keys(source)
    assert saved, "ключи умолчаний не распознаны"
    orphans = sorted(saved - read)
    for key in orphans:
        print(f'  сохраняется, но не читается: {key}')
    assert not orphans, f"умолчания без применения: {orphans}"


def test_startup_applies_defaults_without_knife_params():
    """При запуске применяются умолчания, КРОМЕ параметров ножа.

    Регрессия v1.7.8/1.7.9: _apply_defaults_to_fields() зовётся только при
    открытии .ai, поэтому до первого файла панель показывала
    конструкторские значения (порог угла 0.70 вместо сохранённых 0.60).
    Параметры ножа при этом трогать нельзя: угол и высота приходят из XML
    заказа, а метка «не задано» розовым на пустом старте выглядит как
    ошибка.
    """
    source = open(os.path.join(UI_DIR, 'main_window.py'),
                  encoding='utf-8').read()
    tree = ast.parse(source)
    bodies = {}
    for func in ast.walk(tree):
        if isinstance(func, ast.FunctionDef):
            bodies[func.name] = ast.get_source_segment(source, func) or ''

    # Панель создаётся и сразу получает умолчания
    holder = next((n for n, b in bodies.items()
                   if 'CuttingParamsPanel()' in b), None)
    assert holder, "создание панели параметров не найдено"
    print(f'  панель создаётся в {holder}()')
    assert '_apply_startup_defaults' in bodies[holder], \
        f"{holder}() не применяет умолчания при запуске"

    # Стартовый путь идёт без параметров ножа
    startup = bodies.get('_apply_startup_defaults', '')
    assert 'include_knife=False' in startup, \
        "стартовые умолчания трогают параметры ножа"

    # Параметры ножа реально под флагом, а поля панели — нет
    apply_body = bodies.get('_apply_defaults_to_fields', '')
    assert 'if include_knife:' in apply_body, "флаг include_knife не работает"
    guarded = apply_body.split('if include_knife:', 1)[1]
    tail = guarded.split('_set(lambda: p.max_geom_len', 1)[0]
    for knife_field in ('p.angle', 'p.tip', 'p.top', 'p.bottom'):
        assert knife_field in tail, f"{knife_field} не под include_knife"
    for common in ('corner_radius_threshold', 'lead_in_angle',
                   'max_geom_len'):
        assert common in apply_body, f"{common} не применяется"
        assert common not in tail, f"{common} ошибочно под include_knife"


def test_viewer_and_post_use_same_lead_scale():
    """Вьювер и пост масштабируют лиды одинаково — от эквидистанты.

    Регрессия v1.7.11/1.7.12: пост исправили на tool_eq, а во вьювере
    осталось tool_offset (половина). Картинка расходилась с программой
    вдвое, и выход выглядел почти прямой линией — дуга была слишком
    мелкой, чтобы её заметить.
    """
    post = open(os.path.join(os.path.dirname(UI_DIR), 'post',
                             'mtx_anderson.py'), encoding='utf-8').read()
    view = open(os.path.join(UI_DIR, 'viewer_2d.py'), encoding='utf-8').read()

    # В посте масштаб — эквидистанта
    assert 'lead_scale = tool_eq' in post, "пост не масштабирует от эквидистанты"

    # Ни одна arc_radius не считается от половины
    for name, src in (('mtx_anderson.py', post), ('viewer_2d.py', view)):
        bad = [ln.strip() for ln in src.splitlines()
               if 'arc_radius' in ln and 'tool_offset' in ln
               and 'lead_scale' not in ln]
        for b in bad:
            print(f'  {name}: {b}')
        assert not bad, f"{name}: радиус лида масштабируется половиной"

    assert '_lead_scale_view' in view, "во вьювере нет общего масштаба лидов"


def test_offset_sign_flip_is_gui_only():
    """Знак «Смещения» перевёрнут ТОЛЬКО в GUI.

    Оператору привычнее видеть смещение с обратным знаком, но наружу — в
    .anc, в JSON заказа и в camsys_settings.json — должно уходить прежнее
    значение. Инверсия обязана жить в двух хелперах панели; прямой
    .value()/.setValue() на этих полях мимо них — ошибка в два раза
    по смыслу.
    """
    source = open(os.path.join(UI_DIR, 'main_window.py'),
                  encoding='utf-8').read()

    # Инверсия объявлена ровно одна
    assert source.count('GUI_OFFSET_SIGN = ') == 1, \
        "знак смещения объявлен не в одном месте"

    # Мимо хелперов к полям никто не ходит
    bad = []
    for i, line in enumerate(source.splitlines(), 1):
        for field in ('lead_in_offset', 'lead_out_offset'):
            if f'{field}.value()' in line or f'{field}.setValue(' in line:
                # разрешено только внутри самих хелперов
                if 'GUI_OFFSET_SIGN' in line or 'setSingleStep' in line:
                    continue
                bad.append(f'{i}: {line.strip()}')
    for b in bad:
        print(f'  мимо хелпера: {b}')
    assert not bad, "к полю смещения обращаются в обход хелперов"

    # Хелперы на месте
    for name in ('lead_in_offset_value', 'lead_out_offset_value',
                 'set_lead_in_offset_value', 'set_lead_out_offset_value'):
        assert f'def {name}' in source, f"нет хелпера {name}"


def test_offset_sign_roundtrip():
    """Внутреннее значение переживает круг GUI → поле → GUI."""
    sign = -1.0  # GUI_OFFSET_SIGN
    for internal in (-5.0, -0.5, 0.0, 2.5, 12.0):
        shown = internal * sign          # что видит оператор
        back = shown * sign              # что уходит наружу
        assert abs(back - internal) < 1e-12
    # Умолчание -5 показывается как +5
    assert -5.0 * sign == 5.0


def test_no_method_calls_itself_directly():
    """Метод не вызывает сам себя без условия выхода.

    Регрессия v1.7.14: при переводе обращений на хелперы знака смещения
    глобальная замена попала внутрь самих хелперов —

        def lead_in_offset_value(self):
            return self.lead_in_offset_value() * self.GUI_OFFSET_SIGN

    Синтаксис валиден, импорт проходит, тесты панели не падают. Ошибка
    вылезала только при отрисовке:
    «maximum recursion depth exceeded while calling a Python object».

    Прямая саморекурсия без ветвления в UI-коде всегда ошибка: рекурсивных
    обходов здесь нет.
    """
    problems = []
    for filename, source in _ui_sources():
        tree = ast.parse(source, filename=filename)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Ветвление внутри — возможна легитимная рекурсия, пропускаем
            if any(isinstance(n, (ast.If, ast.While, ast.Try))
                   for n in ast.walk(func)):
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                calls_self = (
                    (isinstance(fn, ast.Attribute) and fn.attr == func.name
                     and isinstance(fn.value, ast.Name) and fn.value.id == 'self')
                    or (isinstance(fn, ast.Name) and fn.id == func.name))
                if calls_self:
                    problems.append(
                        f"{filename}:{node.lineno} — {func.name}() "
                        f"вызывает сама себя")
    for p in problems:
        print(f'  {p}')
    assert not problems, f"саморекурсия: {len(problems)}"


def test_corner_lead_side_is_pinned_to_cut_side():
    """Сторона лида доработки зафиксирована «справа по ходу».

    Эмиттер пишет G42, значит тонкая фреза снимает металл справа по ходу
    и заходить обязана оттуда же — по уже снятому металлу. Слева стоит
    нетронутое лезвие.

    Регрессия v1.7.38: сторона искалась пробами лида и тестом
    «внутри/снаружи замкнутого контура ножа». На узком языке и в кармане
    этот тест меняет ответ на длине в доли миллиметра, и лид уходил в
    лезвие. Пробы убраны — сторона известна точно.
    """
    source = open(os.path.join(UI_DIR, 'viewer_2d.py'), encoding='utf-8').read()
    assert 'ЗНАК ЛИДА = ЗНАК ЭКВИДИСТАНТЫ' not in source, \
        "вернулся старый подбор стороны лида угла пробами"
    assert '_probe_len' not in source and '_probe_rad' not in source, \
        "остались пробы лида для выбора стороны"
    i = source.index('СТОРОНА ЛИДА ДОРАБОТКИ')
    blk = source[i:i + 3000]
    assert 'fit_rework_lead' in blk, \
        "вьювер подбирает лид не тем же кодом, что пост"
    assert 'neighbours=' in blk, \
        "в замер не переданы соседние ножи"


def test_viewer_applies_corner_lead_multipliers():
    """Вьювер домножает лид доработки на те же множители, что и пост.

    Регрессия v1.7.39: пост домножал длину и радиус лида угла/кармана на
    corner_lead_length_mult / corner_lead_radius_mult (по умолчанию 0.5),
    а вьювер этого не делал — и рисовал лиды ВДВОЕ длиннее, чем уходит в
    программу. Пользователь видел пересекающиеся лиды там, где в .anc их
    не было.
    """
    source = open(os.path.join(UI_DIR, 'viewer_2d.py'), encoding='utf-8').read()
    assert '_c_lmult' in source and '_c_rmult' in source, \
        "множители лида доработки во вьювере не применяются"
    assert 'user_factor * _c_lmult * _lead_scale_view' in source, \
        "длина лида доработки считается без множителя"
    assert '_lead_scale_view = ' in source
    assert source.index('_lead_scale_view = ') < source.index('_c_lmult = 1.0'), \
        "_c_lmult объявлен раньше _lead_scale_view"


def test_export_dialog_shows_machinability_report():
    """Журнал проходимости доходит до итогового окна экспорта.

    Регрессия v1.7.23: канал был построен ещё в v1.7.0 —
    PostOptions.extras → PackageExporter.machinability_report →
    session.export_package_auto()['machinability'] — но UI результат не
    читал. Записи копились и молча пропадали: оператор не видел ни
    исправленных дуг, ни узких мест, ни неспасаемых участков.
    """
    source = open(os.path.join(UI_DIR, 'main_window.py'),
                  encoding='utf-8').read()
    assert "r_main.get('machinability'" in source, \
        "UI не читает журнал из результата экспорта"
    assert 'ПРОХОДИМОСТЬ ФРЕЗЫ' in source, \
        "журнал не попадает в детали итогового окна"
    assert 'УЗКОЕ МЕСТО' in source, \
        "узкие места не выделяются в сводке"


def test_session_returns_machinability_from_export():
    """session отдаёт журнал наружу отдельным ключом."""
    src = open(os.path.join(os.path.dirname(UI_DIR), 'core', 'session.py'),
               encoding='utf-8').read()
    assert "'machinability':" in src
    assert "'warnings':" in src


def test_no_dead_controls_in_params():
    """Ни один параметр макроса не должен только записываться.

    Болезнь повторилась дважды: сначала галка «Сглаживание под фрезу»
    (убрана в 1.7), потом «Включить реверс» (убрана в 1.7.42). Обе
    стояли в интерфейсе, обе писались в заказ — и обе никем не читались.
    Вторая была хуже: её снимали, а реверсная программа всё равно
    генерировалась (ею управляет `generate_reverse`).

    Тест считает поле «живым», если оно читается где-то кроме своего
    объявления и кроме main_window (который его только пишет).

    В `KNOWN_UNUSED` — поля, которые уже лежат мёртвыми и про которые
    известно; список НЕ должен расти.
    """
    import re
    root = os.path.dirname(UI_DIR)
    src = open(os.path.join(root, 'core', 'cutting_macro.py'),
               encoding='utf-8').read()
    body = src[src.index('class CuttingMacroParams'):]
    fields = [m.group(1) for m in
              re.finditer(r'^    ([a-z_][a-z0-9_]*)\s*:\s*[^=\n]+=',
                          body, re.M)]
    assert len(fields) > 20, f'полей нашлось всего {len(fields)}'

    sources = []
    for dirpath, _dirs, files in os.walk(root):
        if '__pycache__' in dirpath or os.sep + 'tests' in dirpath:
            continue
        for f in files:
            if f.endswith('.py'):
                sources.append(os.path.join(dirpath, f))

    KNOWN_UNUSED = {'programs_per_line', 'program_width', 'sheet_length',
                    'fast_mode', 'sharp_angle_threshold'}
    dead = []
    for name in fields:
        used = False
        pat = re.compile(r'\b' + re.escape(name) + r'\b')
        for path in sources:
            text = open(path, encoding='utf-8').read()
            if 'main_window.py' in path:
                continue
            lines = text.splitlines()
            for m in pat.finditer(text):
                ln = text[:m.start()].count('\n')
                if (path.endswith('cutting_macro.py')
                        and re.match(r'^    ' + name + r'\s*:', lines[ln])):
                    continue
                used = True
                break
            if used:
                break
        if not used:
            dead.append(name)

    new_dead = sorted(set(dead) - KNOWN_UNUSED)
    print(f'  полей {len(fields)}, мёртвых {len(dead)} '
          f'(известных {len(KNOWN_UNUSED)})')
    assert not new_dead, (
        f'параметры только пишутся и никем не читаются: {new_dead} — '
        f'либо подключить, либо убрать вместе с их элементом интерфейса')
    assert 'enable_reverse' not in fields, (
        'вернулась галка «Включить реверс», которая ничем не управляла')


def test_reverse_is_controlled_only_by_generation_flag():
    """Реверсной программой управляет ровно один флаг — generate_reverse."""
    src = open(os.path.join(UI_DIR, 'main_window.py'), encoding='utf-8').read()
    assert 'use_reverse' not in src, 'мёртвая галка реверса вернулась в UI'
    assert 'self.gen_reverse' in src, 'настоящая галка реверса пропала'


def test_viewer_fits_lead_on_axis_not_on_equidistant():
    """Подбор лида во вьювере считается по ОСЕВОЙ, как и в посте.

    Регрессия v1.7.43: вьювер передавал в `fit_rework_lead` стартовую
    точку эквидистанты (`polypath_offset`) — пути, уже смещённого на
    полуширину реза. А функция смещает переданный путь ЕЩЁ раз, чтобы
    получить путь фрезы: мерилась кривая на 0.55 мм дальше настоящей,
    пересечений она не находила, и лид не укорачивался.

    Из-за этого ровно в тех местах, где программа лид укорачивала
    (59,93 и 93,239), картинка показывала его прежней длины.
    """
    source = open(os.path.join(UI_DIR, 'viewer_2d.py'), encoding='utf-8').read()
    i = source.index('fit_rework_lead')
    blk = source[i:i + 1800]
    assert '_axis_v' in blk, 'вьювер не берёт осевую для подбора лида'
    assert 'polypath_offset.segments[0].a' not in blk, (
        'в подбор лида снова уходит эквидистанта вместо осевой')
    # Инструмент для замера — полуширина реза, а не полная
    assert 'effective_tool_offset' in blk, (
        'в подбор не передана полуширина реза')
