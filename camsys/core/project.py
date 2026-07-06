"""
core/project.py — модель данных CAM-проекта.

Архитектурный обзор (workflow дизайнера):

    1. ИМПОРТ:    .ai → Project.import_from_ai() → слои с геометрией
    2. ВЫБОР:     дизайнер выделяет контур → создаётся Operation
    3. ПУТИ:      Operation генерирует ToolPath'ы (внешний + внутренний)
    4. ДОРАБОТКА: точки начала/конца тонкой фрезой → ещё ToolPath
    5. ПРАВКА:    смена инструмента, входы/выходы, параметры
    6. ВЫВОД:     Project.export_to_anc() → .anc файл

Связь сущностей:

    Project
      ├── name, description, sheet_thickness, ...
      ├── layers: list[Layer]                ← из .ai
      │     └── geometries: list[Geometry]   ← контуры (Polypath)
      ├── operations: list[Operation]        ← порядок обточки
      │     ├── geometry_ref                 ← ссылка на Geometry
      │     ├── tool_number                  ← из ToolDB
      │     ├── settings                     ← подачи, обороты, Z, эквидистанта
      │     └── toolpaths: list[ToolPath]    ← сгенерированные пути
      ├── macros: list[Macro]                ← шаблоны операций
      └── fiducials: list[Fiducial]          ← реперы для CCD-привязки

Все сущности обладают стабильным id (UUID), чтобы UI мог надёжно
ссылаться при выделении / undo / сериализации.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
import uuid


# ─────────────────────────────────────────────────────────────────────────
#  ОБЩИЕ ТИПЫ
# ─────────────────────────────────────────────────────────────────────────

def new_id() -> str:
    """Короткий уникальный ID для сущностей проекта."""
    return uuid.uuid4().hex[:12]


class PassType(Enum):
    """Тип прохода — определяет ProgToolEquidistant и работу контроллера."""
    ROUGH = "rough"            # черновой с припуском
    FINISH = "finish"          # чистовой без припуска
    SINGLE = "single"          # один проход (если черновой/чистовой не делятся)


class ContourSide(Enum):
    """С какой стороны контура идёт фреза. Определяет компенсацию G41/G42
    или знак ProgToolEquidistant, который применяет контроллер MTX."""
    OUTSIDE = "outside"        # снаружи замкнутого контура (металл внутри)
    INSIDE = "inside"          # внутри замкнутого контура (металл снаружи)
    LEFT = "left"              # слева от направления движения (для открытых)
    RIGHT = "right"            # справа от направления движения (для открытых)


class OpenDirection(Enum):
    """Tool Directions → Open Geometries → Direction."""
    REVERSE = "reverse"
    NO_CHANGE = "no_change"


class OpenSide(Enum):
    """Tool Directions → Open Geometries → Side."""
    LEFT = "left"
    RIGHT = "right"
    CHANGE_LR = "change_lr"
    CENTRE = "centre"
    NO_CHANGE = "no_change"


class ClosedDirection(Enum):
    """Tool Directions → Closed Geometries → Direction."""
    CW = "cw"
    CCW = "ccw"
    REVERSE = "reverse"
    NO_CHANGE = "no_change"


class ClosedSide(Enum):
    """Tool Directions → Closed Geometries → Side."""
    OUTSIDE = "outside"
    INSIDE = "inside"
    LEFT = "left"
    RIGHT = "right"
    CHANGE_OUT_IN = "change_out_in"
    CENTRE = "centre"
    NO_CHANGE = "no_change"
    AUTO_FOR_POCKETS = "auto_for_pockets"
    AUTO_FOR_CUTOUTS = "auto_for_cutouts"


class StartPointMode(Enum):
    """Set Start Point on Closed Geometries."""
    NO = "no"
    MANUAL = "manual"
    AUTO = "auto"


@dataclass
class ToolDirections:
    """Направления обхода и стороны для геометрий (отдельный диалог
    Tool Directions в Альфакаме — см. скриншот 5).
    
    Применяется к выбранным Geometry до создания Operation. Меняет 
    физическую структуру Polypath (порядок сегментов, флаги ccw у Arc).
    """
    # Открытые геометрии
    open_direction: OpenDirection = OpenDirection.NO_CHANGE
    open_side: OpenSide = OpenSide.NO_CHANGE
    
    # Замкнутые геометрии
    closed_direction: ClosedDirection = ClosedDirection.NO_CHANGE
    closed_side: ClosedSide = ClosedSide.OUTSIDE
    
    # Стартовая точка замкнутого контура
    start_point_mode: StartPointMode = StartPointMode.NO
    start_for_inside: str = "Start of Longest Edge"
    start_for_outside: str = "Start of Longest Edge"
    start_for_centre: str = "Start of Longest Edge"


class OperationKind(Enum):
    """Класс операции."""
    BLADE_FORMING = "blade"      # формирование лезвия: 2 прохода (out + in / L + R)
    CORNER_REWORK = "corner"     # доработка угла тонкой фрезой (одна сторона)
    SCRIBE = "scribe"            # риска / маркировка по центру линии
    FIDUCIAL_DRILL = "fid_drill" # сверление реперных точек (для сведения координат)


# ─────────────────────────────────────────────────────────────────────────
#  ГЕОМЕТРИЯ (импортированная из .ai)
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Geometry:
    """Один геометрический объект из .ai — контур, открытый путь или точка.

    После импорта Безье уже разложены в Line+Arc через biarc-фит, и хранятся
    как Polypath. Это и есть конструкционная линия, на которую будут опираться
    операции обточки.
    """
    id: str = field(default_factory=new_id)
    name: str = ""

    # Геометрические данные (импортируются как primitives.Polypath)
    # Здесь хранится ссылка на объект Polypath из geometry/primitives.py
    polypath: Any = None  # Polypath

    # Метаданные исходника
    source_layer: str = ""        # имя слоя в .ai
    source_index: int = -1        # порядковый номер в слое

    # Флаги
    is_closed: bool = True
    is_visible: bool = True
    is_locked: bool = False       # запрет редактирования


@dataclass
class Layer:
    """Слой из .ai — например, 'Knife', 'REZ', 'info'."""
    id: str = field(default_factory=new_id)
    name: str = ""
    color: str = "#00ff00"        # для визуализации (по умолчанию зелёный, как Альфакам)
    visible: bool = True
    locked: bool = False
    geometries: List[Geometry] = field(default_factory=list)


@dataclass
class Fiducial:
    """Репер — точка для оптической привязки CCD-камерой."""
    id: str = field(default_factory=new_id)
    x: float = 0.0
    y: float = 0.0
    name: str = ""               # 'FID1', 'FID2', ...


# ─────────────────────────────────────────────────────────────────────────
#  ИНСТРУМЕНТАЛЬНЫЕ ПУТИ
# ─────────────────────────────────────────────────────────────────────────

class LeadStyle(Enum):
    """Стиль входа/выхода (как в диалоге Lead-In/Out Альфакама)."""
    NONE = "none"                  # без захода (прямо на контур)
    LINE = "line"                  # только прямая (Line)
    ARC = "arc"                    # только дуга (Arc)
    LINE_ARC_TANGENTIAL = "both"   # Line + Arc tangential (стандартный для G12/G13)


@dataclass
class EntryExitConfig:
    """Конфигурация захода/отхода — повторяет диалог Lead-In/Out Альфакама.
    
    Если style = LINE_ARC_TANGENTIAL — то это и есть G12/G13 в .anc
    (плавный касательный заход через линию + дугу).
    
    Параметр start_offset: дополнительное смещение точки старта ВДОЛЬ
    контура от его базовой точки (поле «Смещение по» в макросе Cutting).
    Используется когда внутренний и внешний проходы должны стартовать в
    разных точках на контуре (типовые значения: -5 для внутр., -4 для внешн.).
    Знак показывает направление смещения вдоль обхода: 
      положительное — вперёд по направлению обхода,
      отрицательное — назад против направления обхода.
    """
    enabled: bool = True
    style: LeadStyle = LeadStyle.LINE_ARC_TANGENTIAL
    
    # Длина и радиус задаются в долях радиуса инструмента (Tool Rad x N).
    # Это удобно: при смене инструмента геометрия захода масштабируется.
    line_length_x_tool_rad: float = 1.0  # Line Length (Tool Rad x)
    arc_radius_x_tool_rad: float = 1.0   # Arc Radius (Tool Rad x)
    
    # Углы (для line: угол к контуру)
    approach_angle: float = 45.0         # Approach Angle (для входа) / Retract Angle (для выхода)
    
    # Подача
    feedrate_modifier_pct: int = 100     # Feedrate Modifier, %
    
    # Смещение точки старта вдоль контура (поле «Смещение по» в макросе)
    start_offset: float = 0.0            # мм, со знаком
    # True если start_offset был ЯВНО задан юзером (UI). False — дефолт,
    # эмиттер может применять автоподбор позиции старта.
    user_set_offset: bool = False
    
    # Дополнительно
    sloping: bool = False                # Sloping (Z под углом)
    use_ramp_angle: bool = False
    ramp_angle: float = 0.0
    
    # Для выхода: Overlap (отрицательный — для Support Tag)
    overlap: float = 0.0
    chord_tolerance_arc: float = 0.01


class CompensationMode(Enum):
    """Compensation в Альфакаме (General → Compensation)."""
    TOOL_CENTRE = "tool_centre"            # путь идёт по центру (без смещения)
    MACHINE_G41_G42 = "machine_g41_g42"    # компенсация на стороне станка (G41/G42)
    G41_G42_ON_TOOL_CENTRE = "g41_g42_on_tool_centre"  # путь по центру + явный G41/G42


class XYCorners(Enum):
    """Обработка углов траектории (General → XY Corners)."""
    ROLL_ROUND = "roll_round"  # обкатить углы дугой радиуса инструмента
    STRAIGHT = "straight"      # острый угол (по умолчанию)
    LOOP = "loop"              # петля на внутреннем угле


@dataclass
class ToolPath:
    """Один путь фрезы по геометрии — это конкретное движение в .anc.

    Принципиально: путь — это просто УКАЗАНИЕ как обработать конкретную
    геометрию. Реальное смещение (эквидистанту) применяет станок после 
    лазерного измерения. CAM передаёт лишь параметры в .anc-шапке.
    """
    id: str = field(default_factory=new_id)
    geometry_id: str = ""             # на какую Geometry опирается
    
    # Какая сторона / как обходить
    side: ContourSide = ContourSide.OUTSIDE
    
    # Сегмент пути (если нужно обработать не весь контур, а часть —
    # например, для CORNER_REWORK от точки A до точки B)
    start_t: float = 0.0              # параметр начала вдоль контура [0..1]
    end_t: float = 1.0                # параметр конца [0..1]; 1.0 = до конца
    
    # Входы/выходы
    entry: EntryExitConfig = field(default_factory=EntryExitConfig)
    exit: EntryExitConfig = field(default_factory=EntryExitConfig)
    
    # Видимость в превью
    visible: bool = True


# ─────────────────────────────────────────────────────────────────────────
#  НАСТРОЙКИ ОБРАБОТКИ
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class CutSettings:
    """Технологические параметры одной операции.
    
    Соответствует диалогу Rough/Finish в Альфакаме: вкладки Types, General,
    Levels and Cuts, Machining Data, Lead-In/Out.
    """
    # ── Tool (General → Tool) ──
    tool_number: int = 1              # Op No → Tool Number
    
    # ── Compensation (General → Compensation) ──
    compensation: CompensationMode = CompensationMode.MACHINE_G41_G42
    apply_compensation_on_rapid: bool = False  # "Apply Compensation on Rapid Approach/Retract"
    
    # ── XY Corners (General → XY Corners) ──
    xy_corners: XYCorners = XYCorners.STRAIGHT
    
    # ── Loops (General → Loops) ──
    loop_radius: float = 0.0
    knife_loops: bool = False                  # для V-bit при острых углах
    loop_corner_angle_threshold: float = 135.0 # применять петлю если угол меньше
    
    # ── Open paths (General → Auto Break-out) ──
    auto_break_out_cut: bool = False
    break_out_length: float = 0.0
    
    # ── Тип прохода ──
    pass_type: PassType = PassType.SINGLE
    
    # ── Геометрия резания (Levels and Cuts) ──
    contact_height: float = 0.25      # высота контакта над кончиком, мм
    prog_z_depth: float = 0.19        # ProgZDepth, ручная корректировка
    
    # ── Подачи и обороты (Machining Data — берутся из Tool, можно override) ──
    spindle_rpm: int = 70000          # S
    feed_cut: int = 2500              # Fixed Feed
    feed_plunge: int = 1000           # Fixed Down Feed
    
    # ── iHOC (отключено по умолчанию — оператор вручную) ──
    use_ihoc: bool = False
    ihoc_x: float = 0.0
    ihoc_y: float = 0.0


# ─────────────────────────────────────────────────────────────────────────
#  ОПЕРАЦИЯ
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Operation:
    """Одна операция обточки. Объединяет геометрию, инструмент и пути.

    Базовая операция BLADE_FORMING на замкнутом контуре автоматически 
    создаёт 2 ToolPath (OUTSIDE и INSIDE). Дизайнер может добавить ещё
    операции CORNER_REWORK тонкой фрезой между указанными точками.
    """
    id: str = field(default_factory=new_id)
    name: str = ""                    # подпись в Operations panel
    kind: OperationKind = OperationKind.BLADE_FORMING
    
    # Номер операции, который видит дизайнер (как OpNo в Альфакаме).
    # Это явная нумерация, которую назначает пользователь — несколько 
    # выделенных элементов могут получить один номер (тогда они станут 
    # одной операцией). При экспорте в .anc этот номер попадает в имена
    # SHAPE/PART. Если 0 — берётся индекс в списке operations.
    sequence_number: int = 0
    
    # На какой геометрии (Geometry.id)
    geometry_ids: List[str] = field(default_factory=list)
    
    # Какой инструмент и режим
    settings: CutSettings = field(default_factory=CutSettings)
    
    # Сгенерированные пути (пересчитываются при изменении геометрии/настроек)
    toolpaths: List[ToolPath] = field(default_factory=list)
    
    # Видимость / отключение операции
    enabled: bool = True
    
    # Свободные атрибуты-пометки (как ATTR_REVERS=1 в макросах Альфакама).
    # Используются макросами для группировки/фильтрации без жёсткой схемы.
    # Например: {"reverse_pass": True, "group_id": "col_3"}
    attributes: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────
#  МАКРОСЫ
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Macro:
    """Шаблон операции для повторного применения.

    Аналог "макросов" в Альфакаме: набор настроек (инструмент, подачи,
    стиль входов/выходов и т.д.), который применяется одной кнопкой к
    выделенному контуру. Это сильно ускоряет работу с типовыми ножами.
    """
    id: str = field(default_factory=new_id)
    name: str = ""
    description: str = ""
    
    # Шаблон настроек
    kind: OperationKind = OperationKind.BLADE_FORMING
    settings: CutSettings = field(default_factory=CutSettings)
    entry: EntryExitConfig = field(default_factory=EntryExitConfig)
    exit: EntryExitConfig = field(default_factory=EntryExitConfig)


# ─────────────────────────────────────────────────────────────────────────
#  ПРОЕКТ
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Project:
    """Весь CAM-проект: импортированная геометрия + операции + макросы.

    Сериализуется в JSON для сохранения на диск.
    Один Project соответствует одной 'программе' для станка → один .anc файл
    (или пара R/F).
    """
    id: str = field(default_factory=new_id)
    
    # ── Метаданные ──
    name: str = ""                    # 'B-1379', 'Z96', и т.п.
    description: str = ""             # 'ИЗДАТЕЛЬСТВО САТОРИ ООО / 20x20'
    
    source_ai_path: str = ""          # путь к .ai
    sheet_thickness: float = 0.45     # толщина листа, мм
    
    # ── Слои геометрии (из .ai) ──
    layers: Dict[str, Layer] = field(default_factory=dict)
    
    # ── Реперы для CCD-привязки ──
    fiducials: List[Fiducial] = field(default_factory=list)
    fiducial_distance: float = 700.0  # PT_PT_DIS из .amp поста
    
    # ── Операции обработки (в порядке выполнения) ──
    operations: List[Operation] = field(default_factory=list)
    
    # ── Макросы (шаблоны операций) ──
    macros: List[Macro] = field(default_factory=list)
    
    # ── Постпроцессор ──
    # Имя выбранного PostProcessor из PostRegistry. По умолчанию — пост 
    # для Anderson GVM MTX V2.13. Когда добавится поддержка вашего нового 
    # станка — здесь будет имя его поста.
    post_name: str = "MTX Anderson GVM V2.13"
    
    # ─────────────────────────────────────────────────────────────────────
    #  ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ─────────────────────────────────────────────────────────────────────
    
    def add_layer(self, name: str, color: str = "#00ff00") -> Layer:
        layer = Layer(name=name, color=color)
        self.layers[layer.id] = layer
        return layer
    
    def get_layer_by_name(self, name: str) -> Optional[Layer]:
        for layer in self.layers.values():
            if layer.name == name:
                return layer
        return None
    
    def get_geometry(self, gid: str) -> Optional[Geometry]:
        for layer in self.layers.values():
            for geom in layer.geometries:
                if geom.id == gid:
                    return geom
        return None
    
    def find_operation(self, oid: str) -> Optional[Operation]:
        for op in self.operations:
            if op.id == oid:
                return op
        return None
    
    def add_blade_operation(self, geometry_id: str,
                            settings: Optional[CutSettings] = None,
                            entry_inside: Optional['EntryExitConfig'] = None,
                            entry_outside: Optional['EntryExitConfig'] = None,
                            exit_inside: Optional['EntryExitConfig'] = None,
                            exit_outside: Optional['EntryExitConfig'] = None,
                            ) -> Operation:
        """Создаёт стандартную операцию формирования лезвия:
        2 ToolPath на одном замкнутом контуре, в порядке производственного
        процесса флексографии:
            1) внутренний обход (INSIDE) — по часовой стрелке (CW)
            2) внешний обход (OUTSIDE) — против часовой стрелки (CCW)
        
        Каждый ToolPath может иметь СВОИ параметры Lead-In/Out (включая 
        смещение точки старта вдоль контура — параметр «Смещение по» 
        в макросе Cutting).
        
        Args:
            geometry_id: id геометрии в проекте
            settings: общие настройки реза (если None — дефолтные)
            entry_inside / entry_outside: Lead-In для INSIDE / OUTSIDE
            exit_inside / exit_outside: Lead-Out для INSIDE / OUTSIDE
                Если None — берётся EntryExitConfig() с дефолтами.
        """
        geom = self.get_geometry(geometry_id)
        if geom is None:
            raise ValueError(f"Geometry {geometry_id} не найдена")
        
        op = Operation(
            name=f"Blade {geom.name or geometry_id[:6]}",
            kind=OperationKind.BLADE_FORMING,
            geometry_ids=[geometry_id],
            settings=settings or CutSettings(),
        )
        
        if geom.is_closed:
            tp_inside = ToolPath(
                geometry_id=geometry_id, side=ContourSide.INSIDE,
                entry=entry_inside or EntryExitConfig(),
                exit=exit_inside or EntryExitConfig(),
            )
            tp_outside = ToolPath(
                geometry_id=geometry_id, side=ContourSide.OUTSIDE,
                entry=entry_outside or EntryExitConfig(),
                exit=exit_outside or EntryExitConfig(),
            )
            # Порядок физических резов: сначала ВНУТРЕННИЙ рез, потом ВНЕШНИЙ
            # (чтобы кусок не отделился преждевременно при сквозном резе).
            # После фикса конвенции компенсации (оба G41):
            #   ContourSide.OUTSIDE (CCW) — режет ВНУТРЕННИЙ контур
            #   ContourSide.INSIDE  (CW)  — режет ВНЕШНИЙ контур
            # Поэтому очерёдность: OUTSIDE, потом INSIDE.
            op.toolpaths = [tp_outside, tp_inside]
        else:
            tp_left = ToolPath(
                geometry_id=geometry_id, side=ContourSide.LEFT,
                entry=entry_inside or EntryExitConfig(),
                exit=exit_inside or EntryExitConfig(),
            )
            tp_right = ToolPath(
                geometry_id=geometry_id, side=ContourSide.RIGHT,
                entry=entry_outside or EntryExitConfig(),
                exit=exit_outside or EntryExitConfig(),
            )
            op.toolpaths = [tp_left, tp_right]
        
        self.operations.append(op)
        return op
    
    def add_corner_rework(self, geometry_id: str, start_t: float, end_t: float,
                          tool_number: int) -> Operation:
        """Создаёт операцию доработки тонкой фрезой между параметрами 
        start_t и end_t на контуре."""
        settings = CutSettings(
            tool_number=tool_number,
            pass_type=PassType.SINGLE,
        )
        op = Operation(
            name=f"Corner rework T{tool_number}",
            kind=OperationKind.CORNER_REWORK,
            geometry_ids=[geometry_id],
            settings=settings,
        )
        op.toolpaths = [
            ToolPath(
                geometry_id=geometry_id,
                side=ContourSide.OUTSIDE,
                start_t=start_t,
                end_t=end_t,
            ),
        ]
        self.operations.append(op)
        return op
    
    def make_fiducial_drill_operation(self, tool_number: int = 1,
                                      drill_depth: float = 0.1) -> Optional[Operation]:
        """Создаёт операцию сверления реперных точек.
        
        Это стратегия (как в макросе Cutting): берёт точки реперов, найденные
        парсером в .ai макете, и формирует DRILL-операцию. Точки сверления 
        той же фрезой — для последующего сведения координат на другом 
        оборудовании.
        
        Операция НЕ добавляется в self.operations автоматически — её 
        добавляет PackageExporter только в нужные программы (_all_R/_revers_R).
        
        Returns:
            Operation с kind=FIDUCIAL_DRILL, или None если реперов меньше 2.
            Точки сверления хранятся в attributes['drill_points'] как список
            (x, y) кортежей. drill_depth — в attributes['drill_depth'].
        """
        if len(self.fiducials) < 2:
            return None
        
        settings = CutSettings(
            tool_number=tool_number,
            pass_type=PassType.SINGLE,
        )
        op = Operation(
            name="Fiducial Drill",
            kind=OperationKind.FIDUCIAL_DRILL,
            geometry_ids=[],
            settings=settings,
        )
        # Точки сверления = позиции реперов
        op.attributes['drill_points'] = [(f.x, f.y) for f in self.fiducials[:2]]
        op.attributes['drill_depth'] = drill_depth
        # У DRILL-операции нет toolpath'ов в обычном смысле — постпроцессор
        # обрабатывает её по attributes['drill_points'].
        return op
