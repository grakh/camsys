"""
post/base.py — абстрактный интерфейс постпроцессора.

Постпроцессор — это плагин, преобразующий Project в G-код конкретного 
контроллера. Несколько постов могут жить параллельно в проекте, выбор 
осуществляется по имени или по типу станка.

Архитектура:

    Project ──► PostProcessor (выбран в проекте) ──► .anc / .nc / .gcode
    
    PostProcessor (абстракт)
        ├─ MtxAndersonGVM           ← Anderson GVM с MTX V2.13
        ├─ MtxYourMachine           ← ваш новый станок (копия Andersen)
        └─ (другие в будущем)

Зачем абстракция:
    1. Уже сейчас известно про второй станок — нужно с самого начала.
    2. Алгоритм генерации траекторий (биарк, обход, входы) — общий,
       а конкретный синтаксис G-кода и системные циклы — у каждого свои.
    3. Тестировать пост можно отдельно от UI и геометрии.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from io import StringIO


# ─────────────────────────────────────────────────────────────────────────
#  МЕТАДАННЫЕ И КОНФИГ ПОСТА
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class PostMetadata:
    """Идентификация и описание поста."""
    name: str                          # 'MTX Anderson GVM V2.13'
    machine: str = ""                  # 'Anderson Europe GVM'
    controller: str = ""               # 'NUM Power MTX'
    file_extension: str = ".anc"       # с точкой
    version: str = "1.0"
    author: str = ""
    description: str = ""


@dataclass
class PostOptions:
    """Опции экспорта, общие для всех постов.
    
    Каждый конкретный пост может игнорировать или интерпретировать их 
    по-своему. Также может иметь свои дополнительные опции (наследник).
    """
    # Имя выходной программы (без расширения), попадёт в шапку
    program_name: str = "UNTITLED"
    
    # Технология
    sheet_thickness: float = 0.45      # мм (= ProgDieHeight в MTX)
    z_depth: float = 0.19              # мм (= ProgZDepth в MTX)
    safe_z: float = 10.0               # мм — высота безопасного перехода
    
    # Реперы / привязка
    fiducial_distance: float = 700.0   # PT_PT_DIS, мм
    
    # Точность форматирования координат
    coord_precision: int = 5           # знаков после запятой в координатах
    feed_precision: int = 0            # знаков после запятой в подаче
    
    # Включать ли вспомогательные секции
    include_fiducial_marks: bool = False  # сверление меток реперов в конце
    include_video_block: bool = True      # блок для CCD-привязки оператором
    
    # Свободные опции, специфичные для конкретного поста (любой словарь)
    extras: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────
#  АБСТРАКТНЫЙ ИНТЕРФЕЙС
# ─────────────────────────────────────────────────────────────────────────

class PostProcessor(ABC):
    """Базовый класс для всех постпроцессоров.
    
    Наследник должен реализовать generate() — единый метод, возвращающий
    готовый текст программы. Внутри он сам решает как раскладывать на 
    секции и какие циклы вызывать.
    """
    
    @property
    @abstractmethod
    def metadata(self) -> PostMetadata:
        """Метаданные поста (имя, станок, версия)."""
        ...
    
    @abstractmethod
    def generate(self, project: "Project", options: PostOptions) -> str:
        """Главный метод: проект + опции → текст программы.
        
        Реализация должна:
            1. Сформировать шапку (PRESETTINGS, TOOLDATA, ALIGN)
            2. Для каждой Operation проекта — её ToolPath'ы:
               вывести точку входа, врезание, движения по сегментам,
               выход, подъём
            3. Применить EntryExitConfig (плавные G12/G13 или прямые)
            4. Сформировать концовку (M30)
        
        Args:
            project: импортированный и подготовленный Project
            options: общие настройки экспорта
        
        Returns:
            Текст G-кода как строка, готовый к записи в файл.
        """
        ...
    
    # ── Утилиты для наследников ──
    
    def format_coord(self, value: float, precision: int = 5) -> str:
        """Форматирует координату с заданной точностью, убирая лишние нули."""
        s = f"{value:.{precision}f}"
        if '.' in s:
            s = s.rstrip('0').rstrip('.')
        if not s or s == '-':
            return '0'
        return s


# ─────────────────────────────────────────────────────────────────────────
#  РЕЕСТР ПОСТПРОЦЕССОРОВ
# ─────────────────────────────────────────────────────────────────────────

class PostRegistry:
    """Реестр доступных постов. UI показывает список из этого реестра,
    проект сохраняет имя выбранного поста."""
    
    _instances: Dict[str, PostProcessor] = {}
    
    @classmethod
    def register(cls, post: PostProcessor) -> None:
        cls._instances[post.metadata.name] = post
    
    @classmethod
    def get(cls, name: str) -> Optional[PostProcessor]:
        return cls._instances.get(name)
    
    @classmethod
    def all(cls) -> List[PostProcessor]:
        return list(cls._instances.values())
    
    @classmethod
    def names(cls) -> List[str]:
        return list(cls._instances.keys())
