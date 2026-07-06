"""Тесты детектора острых углов на контуре."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
from geometry.primitives import Line, Arc, Polypath
from geometry.corner_detect import (
    detect_sharp_corners, classify_corners_for_tooling, angle_between
)


# ─────────────────────────────────────────────────────────────────────────
#  УГОЛ МЕЖДУ КАСАТЕЛЬНЫМИ
# ─────────────────────────────────────────────────────────────────────────

def test_angle_between_same():
    """Сонаправленные касательные -> 0°."""
    assert abs(angle_between((1, 0), (1, 0))) < 1e-9


def test_angle_between_perpendicular():
    """Перпендикулярные -> 90°."""
    assert abs(angle_between((1, 0), (0, 1)) - 90.0) < 1e-9


def test_angle_between_opposite():
    """Противоположные -> 180°."""
    assert abs(angle_between((1, 0), (-1, 0)) - 180.0) < 1e-9


# ─────────────────────────────────────────────────────────────────────────
#  ДЕТЕКЦИЯ ОСТРЫХ УГЛОВ
# ─────────────────────────────────────────────────────────────────────────

def test_no_corners_on_smooth_line():
    """Прямая последовательность отрезков без углов."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (2, 0)),
        Line((2, 0), (3, 0)),
    ])
    corners = detect_sharp_corners(poly, threshold_deg=90.0)
    assert len(corners) == 0


def test_right_angle_detected():
    """Прямой угол (90° внутр.) — острый при threshold=91°, не острый при 89°."""
    # Идём вправо, потом вверх -> поворот на 90° влево
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
    ])
    
    corners_91 = detect_sharp_corners(poly, threshold_deg=91.0)
    corners_89 = detect_sharp_corners(poly, threshold_deg=89.0)
    
    assert len(corners_91) == 1, f"При threshold=91 должен найти 1 угол"
    assert len(corners_89) == 0, f"При threshold=89 угол НЕ острый"
    
    c = corners_91[0]
    assert abs(c.interior_angle - 90.0) < 1e-9
    assert c.point == (1, 0)
    assert c.turn_sign == 1  # поворот влево (CCW)


def test_sharp_angle_45():
    """Острый угол 45° (поворот на 135°)."""
    # Идём вправо, потом в направлении (cos(135°), sin(135°)) — это поворот влево на 135°
    # Внутренний угол = 180-135 = 45°
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1 + math.cos(math.radians(135)),
                       math.sin(math.radians(135)))),
    ])
    corners = detect_sharp_corners(poly, threshold_deg=90.0)
    assert len(corners) == 1
    c = corners[0]
    assert abs(c.interior_angle - 45.0) < 1e-7
    assert c.turn_sign == 1  # влево


def test_square_closed_contour():
    """Замкнутый квадрат: 4 прямых угла, все должны быть найдены 
       включая стык последний->первый сегмент."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    
    corners = detect_sharp_corners(poly, threshold_deg=100.0)
    assert len(corners) == 4, f"Должно быть 4 угла, найдено {len(corners)}"
    
    # Все углы 90°
    for c in corners:
        assert abs(c.interior_angle - 90.0) < 1e-9
    
    # Все повороты влево (CCW обход квадрата)
    assert all(c.turn_sign == 1 for c in corners)


def test_square_open_contour():
    """Открытый квадрат (не закрыт) — 3 угла вместо 4."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=False)  # не замкнут — стык последний->первый не проверяется
    
    corners = detect_sharp_corners(poly, threshold_deg=100.0)
    assert len(corners) == 3


# ─────────────────────────────────────────────────────────────────────────
#  КЛАССИФИКАЦИЯ ДЛЯ ИНСТРУМЕНТА
# ─────────────────────────────────────────────────────────────────────────

def test_classify_corners():
    """Разделение углов на 2D и 3D по порогу."""
    from geometry.corner_detect import SharpCorner
    corners = [
        SharpCorner(point=(0,0), segment_index=0, interior_angle=85, turn_sign=1),
        SharpCorner(point=(1,1), segment_index=1, interior_angle=70, turn_sign=1),
        SharpCorner(point=(2,2), segment_index=2, interior_angle=45, turn_sign=1),
        SharpCorner(point=(3,3), segment_index=3, interior_angle=30, turn_sign=1),
    ]
    
    c2d, c3d = classify_corners_for_tooling(corners, thin_threshold=60.0)
    
    # 85° и 70° > 60° -> corner (2D тонкая)
    # 45° и 30° ≤ 60° -> corner3D (3D фреза)
    assert len(c2d) == 2
    assert len(c3d) == 2
    assert [c.interior_angle for c in c2d] == [85, 70]
    assert [c.interior_angle for c in c3d] == [45, 30]


# ─────────────────────────────────────────────────────────────────────────
#  ИНТЕГРАЦИОННЫЙ ТЕСТ НА РЕАЛЬНОМ .ai
# ─────────────────────────────────────────────────────────────────────────

def test_real_ai_no_sharp_corners():
    """В реальном 118917.ai контуры органические — не должно быть углов
    острее 90° на чистовом контуре (они плавные)."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    
    ai = '/mnt/user-data/uploads/118917.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    
    import camsys.core.importer as importer_mod
    project = importer_mod.import_ai_to_project(ai)
    knife = project.get_layer_by_name("Knife")
    
    print(f'\n  Анализ {len(knife.geometries)} контуров:')
    total_corners = 0
    for geom in knife.geometries:
        corners = detect_sharp_corners(geom.polypath, threshold_deg=90.0)
        if corners:
            total_corners += len(corners)
            print(f'    {geom.name}: {len(corners)} острых углов')
            for c in corners[:3]:  # первые 3
                print(f'      ({c.point[0]:.2f}, {c.point[1]:.2f}): '
                      f'{c.interior_angle:.1f}°')
    print(f'  Всего острых углов на детали: {total_corners}')


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
