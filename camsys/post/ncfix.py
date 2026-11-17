"""Fix G-code для программ CAMSYS — как кнопка «⚒ Fix G-code» в Anctor.

v1.8.3. Пользователь: «столкновений нет, фикс нужно». Fix Anctor правил
в программах CAMSYS микро-кадры < 10 мкм (стойка: Cornering error 10219
«Split» уже на 7 мкм), шумные прямые из мелких дуг/отрезков, микро-
изломы до 6 мкм и приводил прямой/обратный проходы одного ножа к общей
геометрии. Теперь CAMSYS делает то же сам, тем же кодом
(camsys/post/anctor_fix.py — копия функций Anctor V8.4), в том же
порядке, что `ErrAnc._fix_gcode`:

1. группы: основные / угловые (по маске имени, 'corner') / 3D
   (ToolType = 1 — не трогаются);
2. R_min — одно значение на всё, как настройка Anctor «мин. радиус
   макета» (по умолчанию 0.5);
3. пары «прямой + обратный проход одной геометрии» по всем программам;
4. `nc_fix_text` для каждой программы;
5. обратные проходы строятся точным обращением исправленных прямых;
6. `nc_explicit_leads` — явные G2/G3 в дугах захода/выхода.

Перенос входа на прямую (`fix_move_entry`) у Anctor по умолчанию
выключен — здесь тоже не делается.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

from . import anctor_fix as F

DEFAULT_RMIN = 0.5
DEFAULT_CORNER_MASK = 'corner'


def fix_package(files: Dict[str, str], rmin: float = DEFAULT_RMIN,
                corner_mask: str = DEFAULT_CORNER_MASK,
                fix_radius: bool = True, max_passes: int = 4
                ) -> Tuple[Dict[str, str], List[str]]:
    """Правит набор программ .anc до неподвижной точки.

    Один проход Fix бывает не последним: слитая прямая рядом с пологой
    дугой (R82) на следующем проходе сливается ещё раз. Anctor с авто-Fix
    правил бы такие программы снова, поэтому проходы повторяются, пока
    тексты не перестанут меняться (не больше max_passes). Журнал — от
    первого прохода плюс строка про дополнительные.
    """
    cur = dict(files)
    log_all: List[str] = []
    for k in range(max_passes):
        new, log = _fix_once(cur, rmin, corner_mask, fix_radius)
        changed = [n for n in cur if new.get(n) != cur.get(n)]
        if k == 0:
            log_all = log
        elif changed:
            log_all.append(f"дополнительный проход {k + 1}: досведено "
                           f"{len(changed)} программ")
        cur = new
        if not changed:
            break
    return cur, log_all


def _fix_once(files: Dict[str, str], rmin: float, corner_mask: str,
              fix_radius: bool) -> Tuple[Dict[str, str], List[str]]:
    """Один проход — ровно как кнопка Fix в Anctor."""
    names = [n for n in files if n.lower().endswith('.anc')]
    if not names:
        return dict(files), []
    grp_of = {}
    for nm in names:
        tx = files[nm]
        if F.nc_is_3d(tx):
            grp_of[nm] = 2
        else:
            grp_of[nm] = 1 if F.nc_is_corner(nm, corner_mask) else 0
    r = math.ceil(float(rmin) * 1e5 - 1e-6) / 1e5 if rmin and rmin > 0 \
        else None
    rmins = {0: r, 1: r, 2: None}

    texts = {nm: files[nm] for nm in names}
    conts = {}
    for nm, tx in texts.items():
        if grp_of[nm] == 2:
            continue
        for ct, c in F.nc_contours(tx).items():
            conts[(nm, ct)] = c
    pairs = [(a, b) for a, b in F.nc_find_pairs(conts)
             if grp_of[a[0]] == grp_of[b[0]]]
    skip, sym, prot = {}, {}, {}
    for (fa, ca), (fb, cb) in pairs:
        sym.setdefault(fa, set()).add(ca)
        skip.setdefault(fb, set()).add(cb)
        prot.setdefault(fa, []).append(conts[(fb, cb)]['start'])

    results = {}
    for nm in names:
        if rmins.get(grp_of[nm]) is None:
            continue
        nt, lg, st3 = F.nc_fix_text(
            texts[nm], min_radius=rmins[grp_of[nm]],
            skip_ct=skip.get(nm, ()), sym_ct=sym.get(nm, ()),
            protect=prot.get(nm, ()), fix_radius=fix_radius)
        results[nm] = [nt, list(lg), st3]

    canon_c = {}
    for fa in set(p[0][0] for p in pairs):
        if fa not in results:
            continue
        canon_c[fa] = F.nc_contours(results[fa][0] if results[fa][0]
                                    is not None else texts[fa])
    jobs = {}
    for (fa, ca), (fb, cb) in pairs:
        if fa in canon_c:
            jobs.setdefault(fb, {})[cb] = (canon_c[fa].get(ca), fa, ca)
    for fb, jb in jobs.items():
        if fb not in results:
            continue
        tb = results[fb][0] if results[fb][0] is not None else texts[fb]
        nt, errs = F.nc_rebuild_reverse(tb, {cb: v[0] for cb, v in jb.items()})
        for cb, why in errs.items():
            results[fb][1].append(
                '  ! обратный проход (контур %d) не приведён к геометрии '
                '%s/%d: %s' % (cb, jb[cb][1], jb[cb][2], why))
        if nt is not None and nt != tb:
            results[fb][0] = nt
        ok_n = len(jb) - len(errs)
        if ok_n:
            results[fb][1].append(
                '  обратных проходов построено по прямому (общая '
                'геометрия): %d' % ok_n)

    out = dict(files)
    log = []
    for nm in names:
        if nm not in results:
            continue
        nt, lg, _ = results[nm]
        if nt is not None:
            nt = F.nc_explicit_leads(nt, files[nm])
            out[nm] = nt
        body = [l for l in lg if l.strip()]
        if nt is not None and nt != files[nm]:
            log.append(f"{nm}: ИСПРАВЛЕН")
            log.extend(body)
        elif any('!' in l for l in body):
            log.append(f"{nm}:")
            log.extend(l for l in body if '!' in l)
    return out, log
