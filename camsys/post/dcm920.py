"""Станок DCM 920 (контроллер HCFA HCQ9P-1700) — v1.7.89.

Пост — по шаблону пользователя DCM_Engraving_V1_23.amp (hansmill.ru).

Программа DCM строится ПЕРЕВОДОМ готовой программы AEC GVM (.anc): та же
геометрия, те же лиды, тот же порядок проходов и та же компенсация
G41/G42 — меняется только обёртка и синтаксис:

- комментарии `%` вместо `;`, без DLA/SCLN, SSDE, видеорежима, меток;
- каждый проход — блок `%SHAPE n BEGIN` … `%SHAPE n END`, перед концом
  длина пути `%PATH … MM`;
- подвод: `G0 X Y FF500`, врезание `G1 Z F`, затем отдельной строкой
  `G42`/`G41` и заход `G1 X Y F`; выход — строка `G40` и `G1 X Y`;
- подача в мм/с (`F/60`, мм/мин округлены до 10), холостые — `FF500`;
- `M3 K<обороты>` один раз в начале;
- G, Z, F модальные, но в начале каждого `%SHAPE` выводятся полностью
  (V1_23);
- реперы — циклы $210/$211/$200 шаблона: каждый прокол в своём `%SHAPE`.

Любая строка движения, которую переводчик не узнаёт, — ошибка: молча
выкинуть движение из программы станка нельзя.
"""
from __future__ import annotations

import math
import re
from typing import Dict, Optional, Tuple

MACHINE_AEC = 'AEC_GVM'
MACHINE_DCM = 'DCM_920'
MACHINES = ((MACHINE_AEC, 'AEC GVM'), (MACHINE_DCM, 'DCM 920'))
DCM_EXT = '.npg'

_NUM = r'-?\d+(?:\.\d+)?'
_LN = re.compile(r'^\s*N?\d+\s?')


def _fmt(v: float, dec: int) -> str:
    s = f"{v:.{dec}f}"
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    if s in ('-0', ''):
        s = '0'
    return s


def _feed(f_mm_min: float) -> str:
    """Подача шаблона: мм/мин, округлённые до 10 ($755), делённые на 60,
    2 знака ($750-752)."""
    f = round(f_mm_min / 10.0) * 10.0
    return _fmt(f / 60.0, 2)


def _words(s: str) -> Dict[str, str]:
    out = {}
    for m in re.finditer(r'\b([XYZRFS])(' + _NUM + r')', s):
        out[m.group(1)] = m.group(2)
    return out


class _Writer:
    def __init__(self):
        self.lines = []
        self.n = 0

    def __call__(self, text: str):
        self.n += 1
        self.lines.append(f"N{self.n} {text}")


def anc_to_dcm(anc: str, tool_note: Optional[str] = None) -> str:
    """Переводит программу AEC GVM (.anc, наш пост) в программу DCM 920."""
    w = _Writer()
    created = ''
    m = re.search(r'NC-Program created on:\s*(.*?)\s*\*\*', anc)
    if m:
        created = m.group(1)
    if tool_note is None:
        # Фреза — из шапки AEC: стойке DCM радиус компенсации G41/G42
        # задаёт оператор, ему нужна эквидистанта.
        me = re.search(r'ProgToolEquidistant\s*=\s*(' + _NUM + r')/2', anc)
        mt = re.search(r'Geo\.Rad\s*=\s*(' + _NUM + r')/2', anc)
        ma = re.search(r'Geo\.Ang\s*=\s*(' + _NUM + r')', anc)
        if me:
            e = float(me.group(1))
            tool_note = (f"TOOL: tip {mt.group(1) if mt else '?'} mm, "
                         f"angle {ma.group(1) if ma else '?'} deg, "
                         f"G41/G42 radius {_fmt(e / 2.0, 4)} mm "
                         f"(equidistant {_fmt(e, 4)})")

    # ── шапка ($12) ──
    w("%****************************************************")
    w("%** https://hansmill.ru                            **")
    w("%** DCM High Speed Engraving Machine               **")
    w(f"%** NC-Program created on: {created:<24}**")
    w("%****************************************************")
    if tool_note:
        w(f"% {tool_note}")
    w("%")
    w("%***** Presettings *********************************")
    w("%")
    w("G17")
    w("G90 F50")
    w("G53")
    w("G0 Z53 FF500")
    w("%*********************************************************")
    w("M988 % Ext. Chip Suction Unit ON")
    w("M991 % Shutter Milling Spindle OPEN")
    w("M979 % Control Vacuum ON")
    w("M996 % Tool Cover DOWN")
    w("%***************************************************")

    st = {
        'spindle': None, 'spindle_on': False, 'shape': 0, 'open': False,
        'first_z': True, 'z': None, 'x': None, 'y': None,
        'pend_xy': None, 'g': None, 'mz': None, 'mf': None,
        'path': 0.0, 'feed': None, 'in_body': False, 'drill': None,
    }

    def open_shape():
        if not st['spindle_on'] and st['spindle'] is not None:
            w(f"M3 K{st['spindle']}")
            st['spindle_on'] = True
        st['shape'] += 1
        st['open'] = True
        w(f"%SHAPE {st['shape']} BEGIN")
        st['g'] = st['mz'] = st['mf'] = None      # $MODAL OFF
        st['path'] = 0.0

    def close_shape(with_path=True):
        if st['open']:
            if with_path:
                w(f"%PATH {_fmt(st['path'], 3)} MM")
            w(f"%SHAPE {st['shape']} END")
            st['open'] = False

    def move(g: str, x: str, y: str, r: Optional[str] = None,
             f: Optional[float] = None):
        parts = []
        if st['g'] != g:
            parts.append(g)
            st['g'] = g
        parts += [f"X{x}", f"Y{y}"]
        z = st['z']
        if z is not None and st['mz'] != z:
            parts.append(f"Z{z}")
            st['mz'] = z
        if r is not None:
            parts.append(f"R{r}")
        fs = _feed(f) if f is not None else None
        if fs is not None and st['mf'] != fs:
            parts.append(f"F{fs}")
            st['mf'] = fs
        w(' '.join(parts))
        # длина пути
        x0, y0 = st['x'], st['y']
        x1, y1 = float(x), float(y)
        if x0 is not None:
            c = math.hypot(x1 - x0, y1 - y0)
            if r is None:
                st['path'] += c
            else:
                R = abs(float(r))
                a = 2.0 * math.asin(min(1.0, c / (2.0 * R))) if R > 0 else 0
                st['path'] += R * a
        st['x'], st['y'] = x1, y1

    # Реперы ($210/$211/$200): копим проколы и выводим по шаблону.
    ZS, ZR = '10', '3'

    lines = anc.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        s = _LN.sub('', raw, count=1).strip()
        tag = ''
        if ';' in s:
            s, tag = s.split(';', 1)
            s = s.strip()
            tag = tag.strip()
        if not s:
            continue
        if s.startswith('.SHAPE'):
            st['in_body'] = True
            continue
        if s.startswith('.PRGEND'):
            st['in_body'] = False
            continue
        if not st['in_body']:
            m = re.match(r'M3\s+S(\d+)', s)
            if m and st['spindle'] is None:
                st['spindle'] = _spindle(int(m.group(1)))
            continue
        if s.startswith(('(', 'SSDE', 'GOTO', '.', 'CLN', 'WAIT', 'TOOLDATA',
                         'PREPART', 'POSTPART', '#', 'IF', 'ELSE', 'ENDIF',
                         'PRN', 'COR', 'ATR', 'TRS')):
            continue
        m = re.match(r'M3\s+S(\d+)', s)
        if m:
            if st['spindle'] is None:
                st['spindle'] = _spindle(int(m.group(1)))
            continue
        wd = _words(s)
        tagp = tag.split(',')[0].strip() if tag else ''

        # ── реперы ──
        if tagp in ('210', '211'):
            _drill_line(s, wd, tag, st, w, open_shape, close_shape, ZS, ZR)
            continue
        if st.get('drill_open'):
            # первый не-реперный блок закрывает цикл ($200)
            w(f"G0 Z{ZS} FF500")
            close_shape(with_path=False)
            st['drill_open'] = False
            st['g'] = 'G0'
            st['mz'] = ZS
            st['z'] = ZS

        # ── холостой по Z ──
        m = re.match(r'G0\s+Z(' + _NUM + r')$', s)
        if m:
            z = m.group(1)
            if st['first_z'] and st['shape'] == 0:
                st['first_z'] = False          # $25: первый Z пропускается
                st['z'] = z
                continue
            w(f"G0 Z{z} FF500")
            st['g'] = 'G0'
            st['mz'] = z
            st['z'] = z
            if st['open']:
                close_shape()
            continue
        # ── холостой по XY ──
        m = re.match(r'G0\s+X(' + _NUM + r')\s+Y(' + _NUM + r')$', s)
        if m:
            if st['open']:
                close_shape()
            open_shape()
            st['pend_xy'] = (m.group(1), m.group(2))
            continue
        # ── врезание ──
        m = re.match(r'G1\s+Z(' + _NUM + r')\s+F(' + _NUM + r')$', s)
        if m and st['pend_xy'] is not None:
            x, y = st['pend_xy']
            st['pend_xy'] = None
            w(f"G0 X{x} Y{y} FF500")
            st['g'] = 'G0'
            z_old = st['z']
            z = m.group(1)
            fs = _feed(float(m.group(2)))
            w(f"G1 Z{z} F{fs}")
            st['g'], st['mz'], st['mf'] = 'G1', z, fs
            st['z'] = z
            st['x'], st['y'] = float(x), float(y)
            if z_old is not None:
                st['path'] += abs(float(z_old) - float(z))
            continue
        # ── заход с компенсацией ──
        m = re.match(r'(G4[12])\s+X(' + _NUM + r')\s+Y(' + _NUM + r')'
                     r'(?:\s+F(' + _NUM + r'))?', s)
        if m:
            w(m.group(1))
            f = float(m.group(4)) if m.group(4) else st['feed']
            st['feed'] = f
            # Заход — конец первого рабочего блока, модальность ещё
            # выключена ($MODAL OFF до первой подачи): G1 и F полностью,
            # Z в этой строке шаблон не пишет.
            st['g'] = st['mf'] = None
            st['mz'] = st['z']
            move('G1', m.group(2), m.group(3), f=f)
            continue
        # ── выход с отменой компенсации ──
        m = re.match(r'G1\s+G40\s+X(' + _NUM + r')\s+Y(' + _NUM + r')', s)
        if m:
            w("G40")
            move('G1', m.group(1), m.group(2), f=st['feed'])
            continue
        # ── контур ──
        m = re.match(r'(?:(G[123])\s+)?X(' + _NUM + r')\s+Y(' + _NUM + r')'
                     r'(?:\s+R(' + _NUM + r'))?(?:\s+F(' + _NUM + r'))?'
                     r'(?:\s+SCLN\(\d+\))?$', s)
        if m and st['open']:
            g = m.group(1) or st.get('cg') or 'G1'
            st['cg'] = g
            if m.group(5):
                st['feed'] = float(m.group(5))
            if g == 'G1' and m.group(4):
                raise ValueError(f"DCM: дуга без G2/G3: {raw!r}")
            move(g, m.group(2), m.group(3), r=m.group(4), f=st['feed'])
            continue
        # концовка AEC (G153 G53 G0 Z50, X785 Y600) — своя у шаблона
        if re.match(r'G153|G53|G8|G154|G52', s):
            continue
        if re.match(r'X' + _NUM + r'\s+Y' + _NUM + r'$', s) and not st['open']:
            continue
        if re.match(r'M(30|987|992|978|988|991|979|996)\b', s):
            continue
        if re.match(r'[GXYZ]', s):
            raise ValueError(f"DCM: непонятная строка движения: {raw!r}")

    if st.get('drill_open'):
        w(f"G0 Z{ZS} FF500")
        close_shape(with_path=False)
    if st['open']:
        close_shape()

    # ── концовка ($15) ──
    w("%")
    w("%***** Postsettings ********************************")
    w("%")
    w("G53")
    w("G0 Z53 FF500")
    w("% X785 Y600")
    w("M987  % Ext. chip suction unit OFF")
    w("M992  % Shutter milling spindle CLOSE")
    w("%")
    w("%***** END OF PROGRAM ******************************")
    w("%")
    w("M30")
    return '\n'.join(w.lines) + '\n'


def _spindle(s: int) -> int:
    """$20: S < 15000 — в сотнях, больше 100000 — потолок."""
    if s < 15000:
        s *= 100
    elif s > 100000:
        s = 100000
    return s


def _drill_line(s, wd, tag, st, w, open_shape, close_shape, ZS, ZR):
    """Реперы AEC (;210 / ;211) → циклы $210/$211 шаблона DCM.

    AEC:  G0 X Y ;210,1 · G1 Z10 F5000 ;210,2 · G0 Z3 ;210,3 ·
          G1 Zb F1500 ;211,4 · Z10 F5000 ;211,1 · G0 X Y ;211,2 ·
          G1 Zb F1500 ;211,3 · Z10 F5000 ;211,1 …
    DCM:  каждый прокол — свой %SHAPE: G0 Z[ZS] · G0 X Y · G0 Z[ZR] ·
          G1 Z[ZB] F.
    """
    m = re.match(r'G0\s+X(' + _NUM + r')\s+Y(' + _NUM + r')$', s)
    if m:
        st['drill_xy'] = (m.group(1), m.group(2))
        return
    m = re.match(r'(?:G1\s+)?Z(' + _NUM + r')\s+F(' + _NUM + r')$', s)
    if m:
        z = m.group(1)
        if float(z) >= float(ZR):      # подъём между проколами
            return
        x, y = st.get('drill_xy') or (None, None)
        if x is None:
            raise ValueError("DCM: прокол репера без координат")
        fs = _feed(float(m.group(2)))
        if not st.get('drill_open'):
            # $210 First Hole
            if st['open']:
                close_shape()
            open_shape()
            w(f"G0 Z{ZS} FF500")
        else:
            # $211 Next holes
            w(f"G0 Z{ZS} FF500")
            close_shape(with_path=False)
            open_shape()
        w(f"G0 X{x} Y{y} FF500")
        w(f"G0 Z{ZR} FF500")
        w(f"G1 Z{z} F{fs}")
        st['drill_open'] = True
        st['g'], st['mz'], st['mf'] = 'G1', z, fs
        st['z'] = z
        return
    m = re.match(r'G0\s+Z(' + _NUM + r')$', s)
    if m:
        return                           # Z3 перед первым проколом — в шаблоне
    raise ValueError(f"DCM: непонятная строка репера: {s!r}")


def convert_files(files: Dict[str, str], machine: str,
                  tool_note: Optional[str] = None) -> Dict[str, str]:
    """Набор программ AEC → набор для выбранного станка (имя, текст)."""
    if machine != MACHINE_DCM:
        return files
    out = {}
    for name, text in files.items():
        if name.lower().endswith('.anc'):
            out[name[:-4] + DCM_EXT] = anc_to_dcm(text, tool_note)
        else:
            out[name] = text
    return out
