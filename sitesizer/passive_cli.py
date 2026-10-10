"""Count the passive parts of existing cabinets — from your Excel file or typed in.

    python passive_count.py "BO123 схема.xlsx"             # rack scheme / specification you already have
    python passive_count.py --cab "5B: W1 A4 V1" --cab "7A: A2 F1"
    python passive_count.py                                # asks for the cabinets one by one
    python passive_count.py file.xlsx --out пасивка.xlsx --sockets 300 --cameras 40 --aps 20

Cabinet text: tag (floor + letter) and switch counts: W — Wi-Fi, A — access, V — video,
C — core, F — firewall. Without ``--fw`` the firewall stands in the cabinet marked ``F`` (or the first).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from .core.catalog import CatalogError, load_catalog
from .core.passive_calc import Cabinet, cabinets_from_xlsx, count_passive, format_count, parse_cabinet, write_xlsx


def ask_cabinets(input_fn: Callable[[str], str] = input) -> tuple[list[Cabinet], str]:
    print("Введіть шафи по одній: тег і комутатори, напр.  5B: W1 A4 V1   (W — Wi-Fi, A — доступ,")
    print("V — відео, C — ядро, F — фаєрвол). Порожній рядок — завершити.")
    cabinets: list[Cabinet] = []
    while True:
        raw = input_fn(f"  шафа {len(cabinets) + 1}: ").strip()
        if not raw:
            if cabinets:
                break
            continue
        try:
            cabinets.append(parse_cabinet(raw))
        except ValueError as err:
            print(f"  {err}")
    fw = input_fn("Шафа з фаєрволом (Enter — позначена F або перша): ").strip()
    return cabinets, fw


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="passive_count", description="Пасивка для шаф: з Excel або вручну.")
    p.add_argument("xlsx", nargs="?", help="ваш Excel: схема шаф (BO123-5B-ASW01) або специфікація з кількостями")
    p.add_argument("--cab", action="append", default=[], metavar="'5B: W1 A4 V1'", help="шафа (можна кілька разів)")
    p.add_argument("--fw", default="", metavar="TAG", help="тег шафи з фаєрволом, напр. 7A")
    p.add_argument("--sockets", type=int, default=0, help="розеток (для кабелю, модулів, патч-кордів)")
    p.add_argument("--cameras", type=int, default=0)
    p.add_argument("--aps", type=int, default=0, help="точок доступу")
    p.add_argument("--avg-run", type=int, default=0, metavar="M", help="середня траса, м")
    p.add_argument("--out", metavar="FILE", help="записати результат у Excel")
    p.add_argument("--catalog", metavar="FILE", help="свій каталог JSON (моделі й коди)")
    args = p.parse_args(argv)

    try:
        catalog = load_catalog(args.catalog)
    except CatalogError as err:
        print(err, file=sys.stderr)
        return 2
    warnings: list[str] = []
    fw = args.fw
    if args.xlsx:
        try:
            cabinets, warnings, _how = cabinets_from_xlsx(args.xlsx, catalog)
        except (OSError, ValueError) as err:
            print(f"Не вдалося прочитати {args.xlsx}: {err}", file=sys.stderr)
            return 2
    elif args.cab:
        try:
            cabinets = [parse_cabinet(c) for c in args.cab]
        except ValueError as err:
            print(err, file=sys.stderr)
            return 2
    else:
        try:
            cabinets, fw = ask_cabinets()
        except (KeyboardInterrupt, EOFError):
            print("\nСкасовано.")
            return 1
    if not cabinets:
        for w in warnings:
            print(w, file=sys.stderr)
        return 1
    pc = count_passive(cabinets, catalog, fw, args.sockets, args.cameras, args.aps, args.avg_run)
    pc.warnings = warnings
    print(format_count(pc))
    out = args.out
    if out is None and args.xlsx:
        out = str(Path(args.xlsx).with_name(Path(args.xlsx).stem + " — пасивка.xlsx"))
    if out:
        print(f"\nЗбережено: {write_xlsx(pc, out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
