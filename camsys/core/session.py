"""
core/session.py — фасадный API для UI / внешних клиентов.

CamSession — это «сеанс работы» с одним проектом. Все операции, которые
может делать пользователь (импорт, генерация операций, сортировка, экспорт)
проходят через стабильный интерфейс этого класса.

Это позволяет:
    1. UI на PySide6 — просто вызывает методы сессии в ответ на действия
    2. CLI (main.py) — то же самое
    3. Внешние скрипты — могут импортировать camsys.core.session и работать
    4. Будущая HTTP/RPC обёртка — обёртывает методы сессии

Принципы:
    - Методы возвращают простые dict-структуры, удобные для сериализации
    - Все параметры можно передать как dict (JSON), не нужно создавать
      экземпляры dataclass'ов в UI
    - Сессия хранит состояние: текущий Project, текущие параметры макроса
    - Изменения дискретны: после каждого действия можно сделать get_state()
      и получить актуальный снимок
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path
import json
import re as _re
from dataclasses import asdict, is_dataclass
from enum import Enum

from .project import (
    Project, Layer, Geometry, Operation, ToolPath,
    ContourSide, OperationKind, PassType,
)
from .cutting_macro import CuttingMacroParams, CutDirection, LeadInOutParams
from .macros import (
    sort_operations_by_grid, group_blade_pairs, renumber_operations,
    GridDirection, GridGrouping,
)
from .importer import import_ai_to_project
from ..post.base import PostRegistry, PostOptions
from ..post.package_export import PackageExporter
from ..geometry.corner_detect import detect_sharp_corners


# ─────────────────────────────────────────────────────────────────────────
#  СЕРИАЛИЗАЦИЯ
# ─────────────────────────────────────────────────────────────────────────

def to_dict(obj: Any) -> Any:
    """Универсальная сериализация: dataclass, Enum, контейнеры → JSON-совместимо.
    
    Используется для отдачи структур UI/клиенту. Тяжёлые поля 
    (типа polypath с тысячами сегментов) НЕ сериализуются — даём только
    метаданные. Для геометрии есть отдельный метод get_geometry_polypath().
    """
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    # неизвестный тип — строкой
    return str(obj)


# ─────────────────────────────────────────────────────────────────────────
#  СЕССИЯ
# ─────────────────────────────────────────────────────────────────────────

class CamSession:
    """Сеанс работы CAM-системы. Один сеанс = один проект.
    
    Использование (пример из UI):
        sess = CamSession()
        sess.load_ai("path/to/file.ai")
        sess.create_blade_operations()
        sess.sort_by_grid()
        sess.set_cutting_params(knife_angle=70, top=0.5, bottom=0.25)
        files = sess.export_package("output_dir/")
    
    Использование (из JSON-клиента):
        sess.set_cutting_params_from_dict({"knife_angle": 70, ...})
        result = sess.export_package_to_dict()
    """
    
    def __init__(self):
        self.project: Optional[Project] = None
        self.cutting_params: CuttingMacroParams = CuttingMacroParams()
        self.post_name: str = "MTX Anderson GVM V2.13"
        
        # Регистрируем известные посты (импорт регистрирует через побочный эффект)
        import camsys.post.mtx_anderson  # noqa: F401
    
    # ─────────────────────────────────────────────────────────────────────
    #  ИНФОРМАЦИЯ О СЕССИИ
    # ─────────────────────────────────────────────────────────────────────
    
    def has_project(self) -> bool:
        return self.project is not None
    
    def get_state(self) -> Dict[str, Any]:
        """Полный снимок состояния сессии — для UI или удалённого клиента."""
        if self.project is None:
            return {
                'project': None,
                'cutting_params': to_dict(self.cutting_params),
                'post_name': self.post_name,
            }
        
        return {
            'project': self._project_snapshot(),
            'cutting_params': to_dict(self.cutting_params),
            'post_name': self.post_name,
            'available_posts': PostRegistry.names(),
        }
    
    def _project_snapshot(self) -> Dict[str, Any]:
        """Структура проекта (без тяжёлой геометрии)."""
        p = self.project
        return {
            'id': p.id,
            'name': p.name,
            'description': p.description,
            'source_ai_path': p.source_ai_path,
            'sheet_thickness': p.sheet_thickness,
            'fiducial_distance': p.fiducial_distance,
            'layers': [
                {
                    'id': layer.id,
                    'name': layer.name,
                    'color': layer.color,
                    'visible': layer.visible,
                    'geometries_count': len(layer.geometries),
                    'closed_count': sum(1 for g in layer.geometries if g.is_closed),
                    'open_count': sum(1 for g in layer.geometries if not g.is_closed),
                    'geometries': [
                        {
                            'id': g.id, 'name': g.name,
                            'is_closed': g.is_closed,
                            'is_visible': g.is_visible,
                            'segments_count': len(g.polypath.segments) if g.polypath else 0,
                            'bbox': self._geometry_bbox(g),
                        }
                        for g in layer.geometries
                    ],
                }
                for layer in p.layers.values()
            ],
            'fiducials': [
                {'id': f.id, 'name': f.name, 'x': f.x, 'y': f.y}
                for f in p.fiducials
            ],
            'operations': [
                {
                    'id': op.id,
                    'name': op.name,
                    'kind': op.kind.value,
                    'sequence_number': op.sequence_number,
                    'enabled': op.enabled,
                    'geometry_ids': list(op.geometry_ids),
                    'toolpaths_count': len(op.toolpaths),
                    'tool_number': op.settings.tool_number,
                    'pass_type': op.settings.pass_type.value,
                    'attributes': dict(op.attributes),
                }
                for op in p.operations
            ],
        }
    
    @staticmethod
    def _geometry_bbox(g: Geometry) -> Optional[Dict[str, float]]:
        if not g.polypath or not g.polypath.segments:
            return None
        from ..geometry.primitives import Line, Arc
        xs, ys = [], []
        for seg in g.polypath.segments:
            for pt in (seg.a, seg.b):
                xs.append(pt[0]); ys.append(pt[1])
        if not xs:
            return None
        return {
            'min_x': min(xs), 'max_x': max(xs),
            'min_y': min(ys), 'max_y': max(ys),
        }
    
    # ─────────────────────────────────────────────────────────────────────
    #  ИМПОРТ
    # ─────────────────────────────────────────────────────────────────────
    
    def load_ai(self, ai_path: str, knife_layer: str = "Knife",
                biarc_tolerance: float = 0.001) -> Dict[str, Any]:
        """Импортирует .ai файл и заменяет текущий проект.
        
        После импорта применяется merge_segments_to_arcs для упрощения
        контуров: длинные цепочки мелких сегментов (биарковая аппроксимация
        кривых Безье в .ai) превращаются в одну дугу. Это уменьшает размер 
        .anc в 5-10 раз и устраняет ошибку «уничтожение контура» на станке 
        (фреза получает плавную дугу вместо «дробилки» из мелких G1).
        
        Returns:
            Снимок состояния (как get_state()).
        """
        from ..geometry.path_offset import merge_segments_to_arcs
        
        self.project = import_ai_to_project(
            ai_path,
            project_name=Path(ai_path).stem,
            knife_layer=knife_layer,
            biarc_tolerance=biarc_tolerance,
        )
        # Оптимизируем геометрии ТОЛЬКО слоя ножей: остальные слои 
        # (реперы, технические метки и т.д.) оставляем как импортировались.
        for layer in self.project.layers.values():
            if layer.name != knife_layer:
                continue
            for geom in layer.geometries:
                if geom.polypath and geom.polypath.segments:
                    geom.polypath = merge_segments_to_arcs(
                        geom.polypath, tol=0.02, min_chain=3)
        # ВСЕГДА обновляем prefix при загрузке нового файла — раньше 
        # проверка `if not self.cutting_params.output_prefix` пропускала 
        # обновление если пред. файл уже установил prefix, из-за чего 
        # экспорт нового файла шёл со СТАРЫМ именем (было: открываешь 
        # 121600.ai → prefix="121600"; открываешь 121676.ai → prefix 
        # остаётся "121600" → все .anc пишутся с номером старого файла).
        self.cutting_params.output_prefix = self.project.name
        return self.get_state()
    
    # ─────────────────────────────────────────────────────────────────────
    #  СЛОИ
    # ─────────────────────────────────────────────────────────────────────
    
    def set_layer_visibility(self, layer_name: str, visible: bool) -> None:
        if self.project is None:
            return
        layer = self.project.get_layer_by_name(layer_name)
        if layer is not None:
            layer.visible = visible
    
    def list_layers(self) -> List[Dict[str, Any]]:
        if self.project is None:
            return []
        return [
            {
                'name': layer.name,
                'color': layer.color,
                'visible': layer.visible,
                'geometries_count': len(layer.geometries),
            }
            for layer in self.project.layers.values()
        ]
    
    # ─────────────────────────────────────────────────────────────────────
    #  ОПЕРАЦИИ
    # ─────────────────────────────────────────────────────────────────────
    
    def create_blade_operations(self, layer_name: str = "Knife"
                                ) -> List[Dict[str, Any]]:
        """Создаёт blade-операции для всех контуров заданного слоя."""
        if self.project is None:
            raise RuntimeError("Сначала вызовите load_ai()")
        layer = self.project.get_layer_by_name(layer_name)
        if layer is None:
            raise ValueError(f"Слой '{layer_name}' не найден")
        
        created = []
        for geom in layer.geometries:
            if not geom.is_closed:
                continue
            op = self.project.add_blade_operation(geom.id)
            created.append({'id': op.id, 'name': op.name})
        
        # Создаём отдельную FIDUCIAL_DRILL операцию на каждый репер.
        # Юзер сможет выделять/отключать реперы через operations-таблицу 
        # или ПКМ на канвасе (как ножи).
        # Пропускаем если уже созданы (по fiducial_id в attributes).
        from ..core.project import OperationKind
        existing_fid_ids = {
            op.attributes.get('fiducial_id')
            for op in self.project.operations
            if op.kind == OperationKind.FIDUCIAL_DRILL
        }
        fid_ops = self.project.create_fiducial_operations(
            drill_depth=self.cutting_params.bottom)
        for op in fid_ops:
            if op.attributes.get('fiducial_id') in existing_fid_ids:
                continue
            self.project.operations.append(op)
            created.append({'id': op.id, 'name': op.name})
        
        return created
    
    def sort_by_grid(self, direction: str = "LB", grouping: str = "columns",
                     col_tolerance: float = 5.0, row_tolerance: float = 5.0):
        """Сортирует операции по сетке.
        
        Args:
            direction: 'LB', 'LT', 'RB', 'RT'
            grouping: 'columns' или 'rows'
        """
        if self.project is None:
            return
        dir_map = {
            'LB': GridDirection.LB, 'LT': GridDirection.LT,
            'RB': GridDirection.RB, 'RT': GridDirection.RT,
        }
        gr_map = {
            'columns': GridGrouping.COLUMNS,
            'rows': GridGrouping.ROWS,
        }
        sort_operations_by_grid(
            self.project,
            direction=dir_map.get(direction.upper(), GridDirection.LB),
            grouping=gr_map.get(grouping.lower(), GridGrouping.COLUMNS),
            col_tolerance=col_tolerance,
            row_tolerance=row_tolerance,
        )
    
    def set_operation_sequence_number(self, op_id: str, number: int) -> None:
        if self.project is None:
            return
        op = self.project.find_operation(op_id)
        if op is not None:
            op.sequence_number = number
    
    def set_operation_enabled(self, op_id: str, enabled: bool) -> None:
        if self.project is None:
            return
        op = self.project.find_operation(op_id)
        if op is not None:
            op.enabled = enabled
    
    # ─────────────────────────────────────────────────────────────────────
    #  АНАЛИЗ ГЕОМЕТРИИ
    # ─────────────────────────────────────────────────────────────────────
    
    def analyze_sharp_corners(self, threshold_deg: float = 90.0
                              ) -> Dict[str, Any]:
        """Анализирует острые углы во всех контурах ножей.
        
        Returns:
            {
                'total': N,
                'per_geometry': {geom_id: [{point, angle, ...}, ...]}
            }
        """
        if self.project is None:
            return {'total': 0, 'per_geometry': {}}
        
        layer = self.project.get_layer_by_name("Knife")
        if layer is None:
            return {'total': 0, 'per_geometry': {}}
        
        result = {}
        total = 0
        for geom in layer.geometries:
            corners = detect_sharp_corners(geom.polypath, threshold_deg)
            if corners:
                result[geom.id] = [
                    {
                        'point': list(c.point),
                        'interior_angle': c.interior_angle,
                        'turn_sign': c.turn_sign,
                        'segment_index': c.segment_index,
                    }
                    for c in corners
                ]
                total += len(corners)
        
        return {'total': total, 'per_geometry': result}
    
    # ─────────────────────────────────────────────────────────────────────
    #  ПАРАМЕТРЫ CUTTING-МАКРОСА
    # ─────────────────────────────────────────────────────────────────────
    
    def set_cutting_params(self, **kwargs) -> None:
        """Установка параметров макроса. Принимает плоский **kwargs:
        
            sess.set_cutting_params(
                knife_angle=70, tip_diameter=1.2,
                top=0.5, bottom=0.25,
                generate_corner=True, generate_corner_3d=False,
            )
        """
        for key, value in kwargs.items():
            if hasattr(self.cutting_params, key):
                setattr(self.cutting_params, key, value)
            else:
                raise ValueError(f"Неизвестный параметр макроса: {key}")
    
    def set_cutting_params_from_dict(self, data: Dict[str, Any]) -> None:
        """Версия для JSON-клиентов: принимает плоский словарь.
        
        Поля lead_inside / lead_outside могут быть вложенными словарями
        с ключами angle, length, offset, sign_offset.
        """
        for key, value in data.items():
            if key == 'direction':
                # Enum через строковое значение
                self.cutting_params.direction = CutDirection(value)
            elif key in ('lead_inside', 'lead_outside') and isinstance(value, dict):
                lead = LeadInOutParams(**value)
                setattr(self.cutting_params, key, lead)
            elif hasattr(self.cutting_params, key):
                setattr(self.cutting_params, key, value)
    
    def get_cutting_params_dict(self) -> Dict[str, Any]:
        """Возвращает текущие параметры как JSON-сериализуемый словарь."""
        return to_dict(self.cutting_params)
    
    # ─────────────────────────────────────────────────────────────────────
    #  ПРЕДВАРИТЕЛЬНЫЙ ПРОСМОТР (без записи на диск)
    # ─────────────────────────────────────────────────────────────────────
    
    def resolve_nc_dir(self) -> "Path":
        """Определяет папку для .anc по пути исходного .ai.

        Стратегия (в порядке приоритета):
          1) ищем УЖЕ существующую папку '<номер>-NC'/'_NC'/'NC' рядом с .ai
             и на уровень выше, где номер берётся И из имени папки заказа
             (родитель .ai), И из цифр имени .ai;
          2) если такой нет — берём ЛЮБУЮ существующую папку, чьё имя
             оканчивается на '-NC'/'_NC'/'NC', рядом с .ai (если она одна);
          3) если ничего нет — создаём '<номер>-NC', где номер = имя папки
             заказа (родитель .ai), иначе цифры из имени .ai.

        Имя папки заказа надёжнее имени файла: путь обычно
        ...\\<номер>\\<номер>.ai, а NC-папка — ...\\<номер>\\<номер>-NC.
        """
        from pathlib import Path
        if self.project is None or not self.project.source_ai_path:
            raise RuntimeError(
                "Неизвестен путь исходного .ai — переоткройте файл .ai")
        ai = Path(self.project.source_ai_path)

        def digits(s: str) -> str:
            return ''.join(ch for ch in s if ch.isdigit())

        # Кандидаты «номера»: имя папки заказа и цифры имени файла
        numbers = []
        for n in (ai.parent.name, digits(ai.parent.name), ai.stem, digits(ai.stem)):
            if n and n not in numbers:
                numbers.append(n)
        suffixes = ["-NC", "_NC", "NC"]
        search_bases = [ai.parent, ai.parent.parent]

        # 1) точное совпадение '<номер><суффикс>'
        for base in search_bases:
            for num in numbers:
                for suf in suffixes:
                    cand = base / f"{num}{suf}"
                    if cand.is_dir():
                        return cand
        # 2) любая существующая папка, оканчивающаяся на NC-суффикс (если одна)
        for base in search_bases:
            if not base.is_dir():
                continue
            nc_like = [d for d in base.iterdir()
                       if d.is_dir() and any(d.name.endswith(s) for s in suffixes)]
            if len(nc_like) == 1:
                return nc_like[0]
        # 3) создаём по имени папки заказа (или цифрам имени файла)
        base_num = ai.parent.name or digits(ai.stem) or ai.stem
        return ai.parent / f"{base_num}-NC"

    def _archive_old_anc(self, nc_dir: "Path") -> Optional[str]:
        """Если в папке NC уже есть .anc — переносит их в новую подпапку
        old0/old1/... Возвращает путь созданной папки или None."""
        import shutil
        from pathlib import Path
        nc_dir = Path(nc_dir)
        existing = sorted(p for p in nc_dir.glob("*.anc") if p.is_file())
        if not existing:
            return None
        n = 0
        while (nc_dir / f"old{n}").exists():
            n += 1
        old_dir = nc_dir / f"old{n}"
        old_dir.mkdir()
        for p in existing:
            shutil.move(str(p), str(old_dir / p.name))
        return str(old_dir)

    def export_package_auto(self, nc_dir_override: Optional[str] = None) -> Dict[str, Any]:
        """Главный авто-экспорт: папка NC вычисляется из пути .ai, старые .anc
        архивируются в oldN, затем пишутся новые.

        ВАЖНО про порядок: сначала генерируем весь пакет в память (может
        бросить ошибку — тогда папка не тронута), и ТОЛЬКО при успехе
        архивируем старое и пишем новое. Иначе сбой генерации оставил бы
        папку пустой, а рабочие файлы — уехавшими в old.

        Args:
            nc_dir_override: если задан — использует эту папку вместо 
                автоопределения. Нужно для сшивок (папка = номер заказа, 
                не имя стички).

        Returns:
            {'dir': папка NC, 'archived': папка oldN или None, 'written': [...]}
        """
        from pathlib import Path
        if self.project is None:
            raise RuntimeError("Нет проекта")
        if not self.project.operations:
            self.create_blade_operations()

        # 1. Генерация В ПАМЯТЬ (до любых изменений на диске)
        exporter = PackageExporter(
            self.project, self.cutting_params, post_name=self.post_name)
        # Прокидываем progress-callback если он был установлен
        # (main_window ставит перед вызовом для показа прогресс-диалога)
        cb = getattr(self, '_progress_callback', None)
        files = exporter.generate(progress_callback=cb)
        if not files:
            raise RuntimeError(
                "Генератор не вернул ни одного файла. Проверьте, что есть "
                "активные ножи (галки) и включены типы программ.")

        # 2. Папка NC и архивация старого (только после успешной генерации)
        if nc_dir_override:
            nc = Path(nc_dir_override)
        else:
            nc = self.resolve_nc_dir()
        nc.mkdir(parents=True, exist_ok=True)
        archived = self._archive_old_anc(nc)

        # 3. Запись новых файлов
        written_paths = exporter.write_files(str(nc), files)
        written = [
            {'path': str(p), 'name': p.name, 'size': p.stat().st_size}
            for p in written_paths
        ]
        return {'dir': str(nc), 'archived': archived, 'written': written}

    def export_package_position(self, order_number: Optional[str] = None,
                                nc_dir_override: Optional[str] = None
                                ) -> Dict[str, Any]:
        """POSITION-вариант экспорта — заказ в собственных локальных
        координатах (LB-репер в (0,0), парный репер на оси X).

        Подход — простой пост-процессинг: генерим обычный пакет .anc,
        затем в каждом файле применяем POSITION-трансформ к каждой паре
        `X<num> Y<num>` в моторных строках. Константы шапки/трейлера
        (парковка, CCD-оффсеты) не трогаются.

        Формулы (см. camsys.core.position):
            С поворотом -90° (пара реперов вертикальная):
                new_x =  y - lb_y
                new_y = -(x - lb_x)
            Без поворота (пара горизонтальная):
                new_x = x - lb_x
                new_y = y - lb_y

        Args:
            order_number: если задан и активна сшивка (`_stitch_info`) —
                используем реперы этого заказа. Иначе — все реперы проекта.
            nc_dir_override: базовая папка NC (POSITION создастся внутри).

        Returns:
            {'dir', 'archived', 'written', 'transform': {...}}
        """
        from pathlib import Path
        from .position import (
            pick_alignment_pair, compute_position_transform, PositionTransform,
            _transform_anc_text,
        )
        from .project import OperationKind

        if self.project is None:
            raise RuntimeError("Нет проекта")
        if not self.project.operations:
            self.create_blade_operations()

        # 1. Определяем пару реперов заказа и трансформ.
        stitch = getattr(self, '_stitch_info', None)
        region = None
        if order_number and stitch is not None:
            region = stitch.get_region_by_order(order_number)
            if region is None:
                raise RuntimeError(
                    f"Заказ {order_number} не найден в сшивке "
                    f"({stitch.stitch_number}). "
                    f"Доступные: {', '.join(stitch.orders)}")
            order_fids = [f for f in self.project.fiducials
                          if f.id in region.fiducial_ids]
        else:
            order_fids = list(self.project.fiducials)

        pair = pick_alignment_pair(order_fids)
        warnings_out: List[str] = []

        if pair is None:
            # Ноль реперов у заказа — POSITION невозможен, возвращаем
            # результат-заглушку с warning. Обычный экспорт при этом уже
            # прошёл; UI покажет warning оператору.
            return {
                'dir': None, 'archived': [], 'written': [],
                'transform': None, 'skipped': True,
                'skip_reason': 'no_fiducials',
                'warnings': [
                    f"POSITION не создан: у заказа "
                    f"{order_number or '(нет номера)'} нет реперов "
                    f"(< 1 фидука после автодетекта регионов)."
                ],
            }

        lb, other = pair
        transform = compute_position_transform([lb] if other is None
                                                else [lb, other])
        pair_ids = {lb.id} if other is None else {lb.id, other.id}

        # ── Identity-случай: LB уже в (0,0) и поворота нет ──
        # Обычно это первый заказ сшивки (LB=FID1 в (0,0) по конвенции
        # макетчика). POSITION-трансформ вырождается в тождество, файлы
        # получились бы байт-в-байт как обычные — писать бессмысленно.
        IDENTITY_EPS = 0.01  # 10 микрон, безопасный порог
        if (not transform.rotate_cw90
                and abs(transform.lb_x) < IDENTITY_EPS
                and abs(transform.lb_y) < IDENTITY_EPS):
            return {
                'dir': None, 'archived': [], 'written': [],
                'transform': {
                    'lb_x': transform.lb_x, 'lb_y': transform.lb_y,
                    'rotate_cw90': False, 'dist': transform.dist,
                    'pair': ([lb.name] if other is None
                             else [lb.name, other.name]),
                },
                'skipped': True,
                'skip_reason': 'identity',
                'warnings': [
                    f"POSITION для заказа {order_number} пропущен: "
                    f"LB-репер уже в (0,0), заказ УЖЕ в локальных "
                    f"координатах — POSITION-файлы совпали бы с обычными."
                ],
            }

        if other is None:
            warnings_out.append(
                f"Второй репер у заказа {order_number} не найден: POSITION "
                f"рассчитан по горизонтали (только сдвиг, без поворота). "
                f"Оператор должен довести правый репер вручную на станке."
            )

        # 2. Временно правим состояние проекта под POSITION-генерацию:
        #      • fiducial_distance = локальная дистанция пары
        #      • output_prefix = номер заказа (иначе префикс = имя стички)
        #      • не-пара FIDUCIAL_DRILL: mark excluded=True
        #        (иначе стрэй-репер листа попадёт в drill)
        #      • ножи и drill ДРУГИХ заказов сшивки: stitch_filtered_out=True
        #        (чтобы генерация выдала только этот заказ независимо от того,
        #        какой заказ активен в UI)
        # После генерации всё восстанавливаем в finally.
        saved_dist = self.cutting_params.fiducial_distance
        saved_prefix = self.cutting_params.output_prefix
        saved_project_name = self.project.name
        saved_excluded: Dict[str, bool] = {}
        saved_filtered: Dict[str, Any] = {}  # op_id -> prev value or _MISSING

        _MISSING = object()

        self.cutting_params.fiducial_distance = (
            transform.dist if transform.dist is not None else saved_dist)
        if order_number:
            # Файловая форма ключа для prefix'а: "121254#1" → "121254_c2"
            from ..io_.stitch import order_key_to_filename
            fname_key = order_key_to_filename(order_number)
            self.cutting_params.output_prefix = fname_key
            self.project.name = fname_key

        allowed_knife_ids = set(region.knife_ids) if region is not None else None
        allowed_fid_ids_for_filter = (
            set(region.fiducial_ids) if region is not None else None)

        for op in self.project.operations:
            # (a) Отсекаем стрэй-реперы через excluded (даже если они в region)
            if op.kind == OperationKind.FIDUCIAL_DRILL:
                fid_id = op.attributes.get('fiducial_id', '')
                if fid_id and fid_id not in pair_ids:
                    saved_excluded[op.id] = bool(
                        op.attributes.get('excluded', False))
                    op.attributes['excluded'] = True

            # (b) Фильтруем ножи/углы/drill чужих заказов сшивки
            if region is None:
                continue
            prev = op.attributes.get('stitch_filtered_out', _MISSING)
            if op.kind == OperationKind.BLADE_FORMING:
                keep = any(g in allowed_knife_ids for g in op.geometry_ids)
                if not keep:
                    saved_filtered[op.id] = prev
                    op.attributes['stitch_filtered_out'] = True
                else:
                    saved_filtered[op.id] = prev
                    op.attributes.pop('stitch_filtered_out', None)
            elif op.kind == OperationKind.CORNER_REWORK:
                parent = op.attributes.get('parent_geom_id', '')
                keep = parent in allowed_knife_ids
                saved_filtered[op.id] = prev
                if not keep:
                    op.attributes['stitch_filtered_out'] = True
                else:
                    op.attributes.pop('stitch_filtered_out', None)
            elif op.kind == OperationKind.FIDUCIAL_DRILL:
                fid_id = op.attributes.get('fiducial_id', '')
                keep = fid_id in allowed_fid_ids_for_filter
                saved_filtered[op.id] = prev
                if not keep:
                    op.attributes['stitch_filtered_out'] = True
                else:
                    op.attributes.pop('stitch_filtered_out', None)

        try:
            exporter = PackageExporter(
                self.project, self.cutting_params, post_name=self.post_name)
            cb = getattr(self, '_progress_callback', None)
            files = exporter.generate(progress_callback=cb)
        finally:
            self.cutting_params.fiducial_distance = saved_dist
            self.cutting_params.output_prefix = saved_prefix
            self.project.name = saved_project_name
            for op_id, was in saved_excluded.items():
                op = self.project.find_operation(op_id)
                if op is not None:
                    op.attributes['excluded'] = was
            for op_id, prev in saved_filtered.items():
                op = self.project.find_operation(op_id)
                if op is None:
                    continue
                if prev is _MISSING:
                    op.attributes.pop('stitch_filtered_out', None)
                else:
                    op.attributes['stitch_filtered_out'] = prev

        if not files:
            raise RuntimeError(
                "POSITION: генератор не вернул ни одного файла.")

        # 3. Пост-процессинг: пробегаем по каждому файлу и переводим
        #    координаты X/Y моторных строк в локальную СК заказа.
        files_pos = {
            name: _transform_anc_text(content, transform)
            for name, content in files.items()
        }

        # 4. Пишем в подпапку POSITION/ с архивом старого содержимого.
        if nc_dir_override:
            base = Path(nc_dir_override)
        else:
            base = self.resolve_nc_dir()
        nc = base / "POSITION"
        nc.mkdir(parents=True, exist_ok=True)
        archived = self._archive_old_anc(nc)

        written = []
        # Валидация: у POSITION-заготовки LB в (0,0), и все ножи ФИЗИЧЕСКИ
        # находятся на куске материала, ограниченном bbox заказа. Значит
        # все координаты движения ножа должны быть > 0 (даже 0 недопустим —
        # нужны поля материала). Drill-точки исключаем: LB-drill ЛЕЖИТ в
        # (0,0), это норма (калибровочный прокол на границе заготовки).
        _knife_tag = _re.compile(r';(?:40|50|60),\d')
        _xy_re = _re.compile(r'X(-?\d+(?:\.\d+)?)\s+Y(-?\d+(?:\.\d+)?)')
        for name, content in files_pos.items():
            p = nc / name
            p.write_text(content, encoding='utf-8')
            written.append({'path': str(p), 'name': name,
                            'size': p.stat().st_size})
            # Проверка «X, Y > 0» на моторных строках ножа
            bad_x, bad_y = 0, 0
            min_x, min_y = float('inf'), float('inf')
            for line in content.splitlines():
                if not _knife_tag.search(line):
                    continue
                m = _xy_re.search(line)
                if not m:
                    continue
                x = float(m.group(1)); y = float(m.group(2))
                if x <= 0: bad_x += 1
                if y <= 0: bad_y += 1
                if x < min_x: min_x = x
                if y < min_y: min_y = y
            if bad_x or bad_y:
                warnings_out.append(
                    f"POSITION/{name}: {bad_x + bad_y} точек ножа выходят за "
                    f"физические границы заготовки (X ≤ 0 или Y ≤ 0). "
                    f"min X={min_x:.3f}, min Y={min_y:.3f}. "
                    f"Проверьте правильность пары реперов заказа."
                )

        return {
            'dir': str(nc),
            'archived': archived,
            'written': written,
            'warnings': warnings_out,
            'transform': {
                'lb_x': transform.lb_x, 'lb_y': transform.lb_y,
                'rotate_cw90': transform.rotate_cw90,
                'dist': transform.dist,
                'pair': ([lb.name] if other is None
                         else [lb.name, other.name]),
            },
        }

    def preview_package_filenames(self) -> List[str]:
        """Возвращает прогнозируемые имена выходных файлов с учётом 
        текущих параметров и количества операций."""
        if self.project is None:
            return []
        
        prefix = self.cutting_params.output_prefix or self.project.name
        angle = int(self.cutting_params.knife_angle)
        names = []
        
        if self.cutting_params.generate_rough_all:
            names.append(f"{prefix}_{angle}_all_R.anc")
        if self.cutting_params.generate_reverse:
            names.append(f"{prefix}_{angle}_revers_R.anc")
        if self.cutting_params.generate_finish_per_op:
            # Считаем сколько программ получится после группировки
            from .macros import assign_program_numbers
            n_programs = assign_program_numbers(
                self.project,
                max_geom_len=self.cutting_params.max_geom_len,
                direction=self.cutting_params.direction.value,
                corridor_tolerance=self.cutting_params.corridor_tolerance,
                passes_per_part=2,
            )
            for i in range(1, n_programs + 1):
                names.append(f"{prefix}_{angle}_{i}_M.anc")
        if self.cutting_params.generate_sv:
            names.append(f"{prefix}_{angle}_SV.anc")
        if self.cutting_params.generate_corner:
            names.append(f"{prefix}_{angle}_corner.anc")
        if self.cutting_params.generate_corner_3d:
            names.append(f"{prefix}_{angle}_corner3D.anc")
        
        return names
    
    # ─────────────────────────────────────────────────────────────────────
    #  ЭКСПОРТ
    # ─────────────────────────────────────────────────────────────────────
    
    def export_package(self, output_dir: str) -> List[Dict[str, Any]]:
        """Главный экспорт: пакет .anc файлов на диск.
        
        Returns:
            Список { 'path': str, 'name': str, 'size': int } для UI.
        """
        if self.project is None:
            raise RuntimeError("Нет проекта")
        
        # Если ещё нет операций — создадим автоматически
        if not self.project.operations:
            self.create_blade_operations()
        
        exporter = PackageExporter(
            self.project, self.cutting_params, post_name=self.post_name
        )
        # Прокидываем progress-callback если установлен
        cb = getattr(self, '_progress_callback', None)
        files = exporter.generate(progress_callback=cb)
        if not files:
            raise RuntimeError(
                "Генератор не вернул ни одного файла. Проверьте, что есть "
                "активные ножи (галки) и включены типы программ.")
        # Архивируем существующие .anc в подпапку oldN (перед записью новых)
        from pathlib import Path
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        self._archive_old_anc(out_path)
        
        written = exporter.write_files(output_dir, files)
        return [
            {'path': str(p), 'name': p.name, 'size': p.stat().st_size}
            for p in written
        ]
    
    def export_package_to_dict(self) -> Dict[str, str]:
        """Возвращает пакет файлов в виде словаря {name: content}
        без записи на диск. Полезно для HTTP/RPC API."""
        if self.project is None:
            raise RuntimeError("Нет проекта")
        if not self.project.operations:
            self.create_blade_operations()
        
        exporter = PackageExporter(
            self.project, self.cutting_params, post_name=self.post_name
        )
        cb = getattr(self, '_progress_callback', None)
        return exporter.generate(progress_callback=cb)
    
    # ─────────────────────────────────────────────────────────────────────
    #  СОХРАНЕНИЕ / ЗАГРУЗКА СОСТОЯНИЯ СЕССИИ (JSON)
    # ─────────────────────────────────────────────────────────────────────
    
    def save_state_to_json(self, path: str) -> None:
        """Сохраняет cutting_params + post_name в JSON.
        
        Геометрию НЕ сохраняем — она восстанавливается из .ai.
        """
        data = {
            'source_ai_path': self.project.source_ai_path if self.project else None,
            'cutting_params': self.get_cutting_params_dict(),
            'post_name': self.post_name,
        }
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False),
                              encoding='utf-8')
    
    def load_state_from_json(self, path: str) -> None:
        """Восстанавливает состояние из JSON (включая повторный импорт .ai)."""
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        
        if data.get('source_ai_path'):
            self.load_ai(data['source_ai_path'])
        
        if 'cutting_params' in data:
            self.set_cutting_params_from_dict(data['cutting_params'])
        
        if 'post_name' in data:
            self.post_name = data['post_name']
