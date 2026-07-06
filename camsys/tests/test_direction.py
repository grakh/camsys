"""Тесты определения направления обхода контура."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
from geometry.primitives import Line, Arc, Polypath, vec_dist
from geometry.direction import (
    signed_area, is_ccw, is_cw,
    reverse_segment, reverse_polypath,
    ensure_ccw, ensure_cw, normalize_for_side,
)


def approx(a, b, tol=1e-9):
    return abs(a - b) < tol


# ─────────────────────────────────────────────────────────────────────────
#  ПЛОЩАДЬ СО ЗНАКОМ
# ─────────────────────────────────────────────────────────────────────────

def test_signed_area_ccw_square():
    """Квадрат против часовой: A > 0, |A| = 1."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    area = signed_area(poly)
    assert area > 0
    assert approx(area, 1.0)


def test_signed_area_cw_square():
    """Квадрат по часовой: A < 0, |A| = 1."""
    poly = Polypath(segments=[
        Line((0, 0), (0, 1)),
        Line((0, 1), (1, 1)),
        Line((1, 1), (1, 0)),
        Line((1, 0), (0, 0)),
    ], closed=True)
    area = signed_area(poly)
    assert area < 0
    assert approx(area, -1.0)


def test_is_ccw_circle():
    """Окружность из 4 дуг — должна правильно определяться как CCW."""
    # 4 четверти круга радиуса 1, CCW обход
    poly = Polypath(segments=[
        Arc((1, 0), (0, 1), (0, 0), ccw=True),
        Arc((0, 1), (-1, 0), (0, 0), ccw=True),
        Arc((-1, 0), (0, -1), (0, 0), ccw=True),
        Arc((0, -1), (1, 0), (0, 0), ccw=True),
    ], closed=True)
    
    assert is_ccw(poly)
    # Площадь приближённо равна πr² = π
    area = signed_area(poly, arc_samples=32)
    assert approx(area, math.pi, tol=0.05)


# ─────────────────────────────────────────────────────────────────────────
#  РЕВЕРС СЕГМЕНТОВ
# ─────────────────────────────────────────────────────────────────────────

def test_reverse_line():
    """Реверс отрезка: меняем местами a и b."""
    seg = Line((0, 0), (1, 2))
    rev = reverse_segment(seg)
    assert rev.a == (1, 2)
    assert rev.b == (0, 0)


def test_reverse_arc():
    """Реверс дуги: меняем a/b, ccw флипуется."""
    seg = Arc((1, 0), (0, 1), (0, 0), ccw=True)
    rev = reverse_segment(seg)
    assert rev.a == (0, 1)
    assert rev.b == (1, 0)
    assert rev.ccw == False
    assert rev.center == (0, 0)


def test_reverse_polypath_changes_sign():
    """После реверса знак площади меняется."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    
    a_before = signed_area(poly)
    rev = reverse_polypath(poly)
    a_after = signed_area(rev)
    
    assert approx(a_before + a_after, 0.0)


def test_reverse_does_not_mutate():
    """reverse_polypath возвращает новый объект, исходный не меняется."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 0)),
    ], closed=True)
    
    a_before = signed_area(poly)
    n_before = len(poly.segments)
    
    _ = reverse_polypath(poly)
    
    assert signed_area(poly) == a_before
    assert len(poly.segments) == n_before


# ─────────────────────────────────────────────────────────────────────────
#  НОРМАЛИЗАЦИЯ ПОД СТОРОНУ
# ─────────────────────────────────────────────────────────────────────────

def test_ensure_ccw_already_ccw():
    """Если контур уже CCW — функция возвращает его без изменений."""
    poly = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    
    result = ensure_ccw(poly)
    assert is_ccw(result)
    assert signed_area(result) == signed_area(poly)


def test_ensure_ccw_was_cw():
    """Если контур был CW — функция его развернёт."""
    poly_cw = Polypath(segments=[
        Line((0, 0), (0, 1)),
        Line((0, 1), (1, 1)),
        Line((1, 1), (1, 0)),
        Line((1, 0), (0, 0)),
    ], closed=True)
    
    result = ensure_ccw(poly_cw)
    assert is_ccw(result)


def test_normalize_for_side_outside():
    """OUTSIDE (внутренний рез) → CW."""
    poly_cw = Polypath(segments=[
        Line((0, 0), (0, 1)),
        Line((0, 1), (1, 1)),
        Line((1, 1), (1, 0)),
        Line((1, 0), (0, 0)),
    ], closed=True)
    
    result = normalize_for_side(poly_cw, "OUTSIDE")
    assert is_cw(result)


def test_normalize_for_side_inside():
    """INSIDE (внешний рез) → CCW."""
    poly_ccw = Polypath(segments=[
        Line((0, 0), (1, 0)),
        Line((1, 0), (1, 1)),
        Line((1, 1), (0, 1)),
        Line((0, 1), (0, 0)),
    ], closed=True)
    
    result = normalize_for_side(poly_ccw, "INSIDE")
    assert is_ccw(result)


# ─────────────────────────────────────────────────────────────────────────
#  ИНТЕГРАЦИЯ: РЕАЛЬНЫЙ КОНТУР
# ─────────────────────────────────────────────────────────────────────────

def test_real_ai_knife_direction():
    """Узнаём направление обхода ножа в реальном файле."""
    ai = '/mnt/user-data/uploads/118917.ai'
    if not os.path.exists(ai):
        print('  SKIP — нет файла')
        return
    
    # Импорт делаем через те же имена модулей что выше — это позволяет
    # сохранить тождество классов Line/Arc/Polypath между importer
    # и тестируемыми функциями. Иначе при разных путях импорта Python
    # создаст две копии модуля geometry.primitives с разными классами,
    # isinstance(seg, Line) перестанет срабатывать.
    parent = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    sys.path.insert(0, parent)
    
    # Перезагружаем camsys.geometry.primitives → теперь его Line/Arc
    # это те же объекты что и geometry.primitives, потому что 
    # camsys.geometry это просто пакет в parent/camsys/geometry,
    # а geometry — тот же путь, поскольку мы sys.path.insert('.') 
    # сделали ранее. Здесь — стандартный обход:
    
    # Просто прочитаем .ai через локальный (sys.path-based) импортёр
    # и используем уже импортированные функции из geometry.direction.
    import importlib
    
    # Загрузим pikepdf-парсер напрямую
    from io_.ai_parser import get_layer_paths
    from geometry.primitives import Bezier, Polypath, vec_dist, EPS
    from geometry.biarc import fit_bezier
    
    # Импортируем минимальный геометрический пайплайн из .ai →  Polypath,
    # без обращения к camsys.* (избегаем дубликата модулей)
    ai_paths = get_layer_paths(ai, "Knife")
    print()
    
    n_done = 0
    for idx, ap in enumerate(ai_paths):
        # Пропускаем клипы (большие) и реперы (открытые маленькие)
        bb = ap.bbox()
        if (bb[2] - bb[0]) > 600:
            continue
        if not ap.is_closed():
            continue
        
        # Конвертация AI-пути в Polypath
        from geometry.primitives import Line
        segs = []
        cur = ap.segments[0].points[0]
        start = cur
        for s in ap.segments[1:]:
            if s.op == 'L':
                end = s.points[0]
                if vec_dist(cur, end) > EPS:
                    segs.append(Line(cur, end))
                cur = end
            elif s.op == 'C':
                cp1, cp2, end = s.points
                bez = Bezier(cur, cp1, cp2, end)
                segs.extend(fit_bezier(bez))
                cur = end
            elif s.op == 'Z':
                if vec_dist(cur, start) > EPS:
                    segs.append(Line(cur, start))
        
        poly = Polypath(segments=segs, closed=True)
        area = signed_area(poly, arc_samples=8)
        direction = "CCW" if area > 0 else "CW"
        n_done += 1
        print(f'    Knife_{n_done}: {direction}, |A|={abs(area):.1f} мм²')


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
