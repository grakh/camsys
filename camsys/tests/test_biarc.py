"""Тесты гео-примитивов и биарк-фиттинга."""

import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geometry.primitives import (
    Bezier, Line, Arc, vec_dist, vec_norm, EPS
)
from geometry.biarc import fit_bezier, arc_from_tangent, _max_deviation


def approx(a, b, tol=1e-9):
    return abs(a - b) < tol


# ─────────────────────────────────────────────────────────────────────────
#  ARC: длина, касательные
# ─────────────────────────────────────────────────────────────────────────

def test_arc_length_quarter_ccw():
    """Четверть окружности радиуса 1, CCW: (1,0) -> (0,1), центр (0,0)."""
    a = Arc((1.0, 0.0), (0.0, 1.0), (0.0, 0.0), ccw=True)
    expected = math.pi / 2
    assert approx(a.length(), expected)
    assert approx(a.radius, 1.0)


def test_arc_length_quarter_cw():
    """Та же четверть, но в обратную сторону: (0,1) -> (1,0), CW."""
    a = Arc((0.0, 1.0), (1.0, 0.0), (0.0, 0.0), ccw=False)
    expected = math.pi / 2
    assert approx(a.length(), expected)


def test_arc_tangent_ccw():
    """Дуга (1,0) -> (0,1) CCW: касательная в начале (1,0) должна быть (0,1)."""
    a = Arc((1.0, 0.0), (0.0, 1.0), (0.0, 0.0), ccw=True)
    t = a.tangent_at_start()
    assert approx(t[0], 0.0)
    assert approx(t[1], 1.0)


def test_arc_point_at_mid():
    """Полудуга радиуса 1, CCW: середина должна быть в (0, 1) для дуги (1,0)->(-1,0)."""
    a = Arc((1.0, 0.0), (-1.0, 0.0), (0.0, 0.0), ccw=True)
    mid = a.point_at(0.5)
    assert approx(mid[0], 0.0, 1e-12)
    assert approx(mid[1], 1.0, 1e-12)


# ─────────────────────────────────────────────────────────────────────────
#  ARC_FROM_TANGENT
# ─────────────────────────────────────────────────────────────────────────

def test_arc_from_tangent_quarter():
    """Касательная (1,0) в (1,0), конец (0,1) -> дуга радиуса 1 центр (1,1)?
    Не совсем. Касательная (1,0) в точке (1,0) -> радиус идёт перпендикулярно вверх ->
    центр в (1, R). Чтобы дуга прошла через (0,1) — R должен быть таким, что 
    (1, R) равноудалена от (1,0) и (0,1).
    |center - (1,0)| = R, |center - (0,1)| = R
    (1-1)² + R² = (1-0)² + (R-1)²  ->  R² = 1 + R² - 2R + 1  ->  2R = 2  ->  R = 1
    Центр (1,1)."""
    seg = arc_from_tangent((1.0, 0.0), (0.0, 1.0), (1.0, 0.0))
    # Хм, но касательная (1,0) направлена в положительный X, а нам нужно идти к (0,1) — назад.
    # Этот вариант должен дать None или дугу с длинным путём.
    # Лучше пример: касательная (0,1), от (0,0), до (1,1) с центром (1,0)
    seg2 = arc_from_tangent((0.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    assert seg2 is not None
    if isinstance(seg2, Arc):
        # Центр на перпендикуляре к (0,1) — это ось X, проходит через (0,0)
        # Расстояние до (1,1) = расстояние до (0,0) -> центр на серединном перпендикуляре
        # Сер. перп. отрезка (0,0)-(1,1) — вертикаль x=0.5 через (0.5,0.5) с направлением (-1,1)/√2
        # Пересечение с осью X: y=0, тогда x = 0.5 + 0.5 = 1 -> центр (1, 0)
        assert approx(seg2.center[0], 1.0, 1e-9)
        assert approx(seg2.center[1], 0.0, 1e-9)
        assert approx(seg2.radius, 1.0, 1e-9)


def test_arc_from_tangent_collinear_forward():
    """Касательная вдоль хорды -> должна получиться Line, не Arc."""
    seg = arc_from_tangent((0.0, 0.0), (2.0, 0.0), (1.0, 0.0))
    assert isinstance(seg, Line)


# ─────────────────────────────────────────────────────────────────────────
#  BEZIER: вырожденные случаи
# ─────────────────────────────────────────────────────────────────────────

def test_bezier_is_line():
    """Все 4 точки на прямой."""
    b = Bezier((0,0), (1,0), (2,0), (3,0))
    assert b.is_line()


def test_bezier_is_curved():
    b = Bezier((0,0), (1,1), (2,1), (3,0))
    assert not b.is_line()


def test_bezier_degenerate():
    b = Bezier((5,5), (5,5), (5,5), (5,5))
    assert b.is_degenerate()


def test_bezier_point_at_endpoints():
    b = Bezier((0,0), (1,2), (3,2), (4,0))
    p0 = b.point_at(0.0)
    p1 = b.point_at(1.0)
    assert approx(p0[0], 0) and approx(p0[1], 0)
    assert approx(p1[0], 4) and approx(p1[1], 0)


# ─────────────────────────────────────────────────────────────────────────
#  FIT_BEZIER: основной фит-тест
# ─────────────────────────────────────────────────────────────────────────

def test_fit_bezier_line():
    """Безье-прямая -> должен вернуть одну Line."""
    b = Bezier((0,0), (1,0), (2,0), (3,0))
    segs = fit_bezier(b)
    assert len(segs) == 1
    assert isinstance(segs[0], Line)


def test_fit_bezier_quarter_circle():
    """Безье, близкая к четверти круга. Точная четверть круга радиуса 1
    от (1,0) до (0,1) с контр. точками (1, k) и (k, 1) где k = 4(√2-1)/3 ≈ 0.5523.
    """
    k = 4 * (math.sqrt(2) - 1) / 3
    b = Bezier((1.0, 0.0), (1.0, k), (k, 1.0), (0.0, 1.0))
    segs = fit_bezier(b, tolerance=0.001)
    
    # Должно получиться немного сегментов (1-2 биарка), 
    # потому что Безье очень близка к одной дуге.
    print(f'  Аппроксимация четверти круга: {len(segs)} сегмент(а/ов)')
    for s in segs:
        if isinstance(s, Arc):
            print(f'    Arc: R={s.radius:.6f}, длина={s.length():.6f}')
        else:
            print(f'    Line: len={s.length():.6f}')
    
    # Проверим отклонение
    err = _max_deviation(b, segs, n_samples=50)
    print(f'  Макс. отклонение: {err*1000:.4f} мкм')
    assert err <= 0.001


def test_fit_bezier_s_curve():
    """S-образная Безье — биарк должен сработать (один S = одна пара дуг)."""
    b = Bezier((0,0), (1,2), (3,-2), (4,0))
    segs = fit_bezier(b, tolerance=0.001)
    print(f'  S-кривая: {len(segs)} сегмент(а/ов)')
    err = _max_deviation(b, segs, n_samples=50)
    print(f'  Макс. отклонение: {err*1000:.4f} мкм')
    assert err <= 0.001
    # И начало/конец совпадают
    assert vec_dist(segs[0].a, b.p0) < EPS
    assert vec_dist(segs[-1].b, b.p3) < EPS


def test_fit_bezier_tight_tolerance():
    """С очень жёстким допуском должна сработать рекурсия (больше сегментов)."""
    b = Bezier((0,0), (10,30), (40,30), (50,0))  # большая дуга
    coarse = fit_bezier(b, tolerance=0.1)
    fine   = fit_bezier(b, tolerance=0.0001)
    print(f'  Tolerance 0.1 мм:    {len(coarse)} сегментов')
    print(f'  Tolerance 0.0001 мм: {len(fine)} сегментов')
    # Жёсткий допуск — больше сегментов
    assert len(fine) >= len(coarse)
    # И ошибка строго в допуске
    err_fine = _max_deviation(b, fine, n_samples=100)
    print(f'  Макс. отклонение при tol=0.0001: {err_fine*1e6:.4f} нм')
    assert err_fine <= 0.0001


# ─────────────────────────────────────────────────────────────────────────
#  ЗАПУСК
# ─────────────────────────────────────────────────────────────────────────

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
