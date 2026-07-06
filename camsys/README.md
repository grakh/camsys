# camsys — CAM-система для прецизионной обточки флексографических ножей

Самописная CAM-система с подключаемыми постпроцессорами под станки 
с контроллером NUM Power MTX (Anderson Europe GVM и его клоны).

## Запуск

В корне проекта (рядом с папкой `camsys/`) лежит **main.py** — точка входа.

```bash
# Показать справку
python3 main.py --help

# Список слоёв в .ai
python3 main.py list-layers path/to/file.ai

# Подробная информация о .ai (слои, контуры, реперы)
python3 main.py info path/to/file.ai

# Полный pipeline: .ai → .anc
python3 main.py export path/to/file.ai output.anc

# С параметрами:
python3 main.py export file.ai out.anc \
    --sheet-thickness 0.5 \
    --z-depth 0.2 \
    --fiducial-distance 700 \
    --fiducial-marks \
    --post "MTX Anderson GVM V2.13"

# Показать встроенную базу инструментов
python3 main.py tools-list

# Запустить все тесты
python3 main.py tests
```

## Workflow дизайнера (будущий UI)

```
1. Импорт .ai
   → автоматическая аппроксимация кривых Безье биарком
   → слои с геометрией показаны в дереве (как в Альфакаме)

2. Выбор обточки
   → дизайнер выделяет контуры
   → присваивает им номер операции (Op No)
   → элементы с одним номером попадают в один SHAPE

3. Генерация путей
   → автоматически 2 ToolPath: внешний + внутренний обход
     (формирование лезвия с двух сторон)

4. Доработка тонкой фрезой (опционально)
   → дизайнер ставит точки начала и конца на контуре
   → добавляется CORNER_REWORK с другим инструментом

5. Правка
   → диалоги: Tool Editor, Rough/Finish (Types/General/Lead-In-Out),
     Tool Directions
   → макросы: сортировка по сетке, группировка пар, реверс направления

6. Вывод
   → подключаемый постпроцессор (.amp-style → .anc и т.д.)
```

## Технология

- Материал: травлёный металл, лист 0.45–1.0 мм, флексографические ножи
- Координаты: XY от реперов через CCD-камеру (ALIGN), Z=0 = подложка
- Инструмент: V-биты с хвостовиком 3 / 3.175 мм
- Pick-up tool change, лазерное измерение (Renishaw NC4)
- Лезвие формируется с двух сторон одного контура (внешний + внутренний обход)
- Эквидистанту считает СТАНОК после лазерного измерения, CAM передаёт
  ProgToolEquidistant и сами пути
- iHOC и MicroPerf — НЕ автоматизированы (оператор сам)

## Структура

```
проект/
├── main.py                      ← точка входа (CLI)
└── camsys/
    ├── core/                    МОДЕЛЬ ПРОЕКТА
    │   ├── project.py           Project, Layer, Geometry, Operation,
    │   │                        ToolPath, Macro, CutSettings, Fiducial,
    │   │                        EntryExitConfig, CompensationMode,
    │   │                        XYCorners, ToolDirections
    │   ├── importer.py          .ai → Project (с биарк-фитом)
    │   └── macros.py            sort_by_grid, group_blade_pairs,
    │                            renumber, mark_operations
    │
    ├── geometry/                ГЕО-ДВИЖОК
    │   ├── primitives.py        Bezier, Line, Arc, Polypath
    │   └── biarc.py             Биарк-фит (точность 1 мкм)
    │
    ├── io_/
    │   └── ai_parser.py         Парсер .ai по слоям через pikepdf
    │
    ├── tools/
    │   └── tool_db.py           Tool, ToolDB,
    │                            parse_alphacam_tool_name,
    │                            make_tool_from_alphacam_name
    │
    ├── post/                    ПОДКЛЮЧАЕМЫЕ ПОСТЫ
    │   ├── base.py              PostProcessor, PostMetadata,
    │   │                        PostOptions, PostRegistry
    │   └── mtx_anderson.py      Anderson Europe GVM MTX V2.13
    │
    │   ВАЖНО: каждый пост — это Python-класс, наследник PostProcessor.
    │   Файлы .amp от Альфакама НЕ интерпретируются автоматически — они
    │   используются как референс при написании Python-класса. Когда 
    │   придёт ваш новый станок, для него будет новый класс рядом
    │   с mtx_anderson.py (например, mtx_your_machine.py).
    │
    ├── ui/                      (TODO: main_window, viewer_2d)
    └── tests/                   38/38 тестов проходят
```

## Тесты

```
python3 main.py tests
```

или отдельно:
```
python3 camsys/tests/test_tool_db.py         # 17/17 — Tool, парсер имён
python3 camsys/tests/test_biarc.py           # 14/14 — биарк-фит
python3 camsys/tests/test_project_pipeline.py # 3/3 — импорт .ai → Project
python3 camsys/tests/test_post_pipeline.py    # 4/4 — пост, макросы, .anc
```

## Что готово

- ✅ Tool DB с математикой конуса, парсер имён Альфакама (STD_D0_6BASE90°)
- ✅ Парсер .ai по слоям через pikepdf
- ✅ Биарк-фит кривых Безье с точностью 1 мкм
- ✅ Модель проекта: Operations, ToolPaths, Macros, Fiducials, 
     ToolDirections, EntryExitConfig, CompensationMode
- ✅ Макросы: sort_by_grid, group_blade_pairs, renumber, mark_operations
- ✅ Подключаемые постпроцессоры (PostRegistry)
- ✅ Первый рабочий пост: MTX Anderson GVM V2.13
- ✅ End-to-end: .ai → Project → макросы → пост → .anc
- ✅ CLI (main.py) с командами list-layers, info, export, tools-list, tests

## Что дальше

1. **G12/G13 плавные входы** в постпроцессоре (есть в EntryExitConfig,
   но не выводятся в .anc — нужно реализовать)
2. **Применение ToolDirections** к геометрии (реверс контура, смена ccw 
   у дуг, выбор start point)
3. **UI** (PySide6): главное окно по образцу Альфакама — дерево слоёв,
   Operations panel, 2D-вьюер, диалоги Tool Editor / Lead-In-Out / 
   Tool Directions
4. **Второй постпроцессор** для вашего нового станка (когда будут
   отличия от Anderson)
5. **Улучшение биарк-фита** до асимметричного (приблизит долю дуг к 55%)
