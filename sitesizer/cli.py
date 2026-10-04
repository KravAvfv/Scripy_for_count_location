"""Command-line entry point.

Interactive mode reproduces the console prototype's question flow::

    python -m sitesizer.cli

Automation mode reads a ``SiteInput`` (or a project) JSON and prints/writes the result::

    python -m sitesizer.cli --json site.json [--out result.json] [--xlsx BoM.xlsx] [--pdf report.pdf]
    echo '{"sockets": 10, "cameras": 5}' | python -m sitesizer.cli --json -

``--compat`` applies the prototype's original assumptions (quantity-based variants, legacy
power estimate, old FortiLink limits) — handy for checking regressions.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from .core.catalog import Catalog, CatalogError, load_catalog, prototype_compat_catalog
from .core.models import ApGroup, SiteInput, SiteResult
from .core.project import ProjectError, load_project
from .core.report import default_export_name, result_to_dict
from .core.sizing import size_site
from .i18n import Translator

MANUAL_UK = """
╔══════════════════════════════════════════════════════════════════════╗
║          BoM-калькулятор мережевого обладнання — як це працює        ║
╚══════════════════════════════════════════════════════════════════════╝

Скрипт задає кілька питань і на основі відповідей рахує, скільки
комутаторів, точок доступу та Firewall потрібно на локацію, а також
формує Excel-файл з обґрунтуванням для замовника.

  Резерв на ріст (y/n)   — +20% до AP / розеток / камер ПЕРЕД розрахунком світчів.
  Ядро (y / n / Enter)   — y: завжди; n: ніколи; Enter: якщо світчів доступу ≥ 3.
  Критичність (1–4)      — 1: HA FW, ядро N+1, UPS, OOB; 2: UPS; 3–4: базово.
  2 блоки живлення       — для рівнів 1–2 рекомендовано моделі з 2 БЖ (hot-swap).
  Режим                  — Швидкий: лише BoM; Розширений: + IP-план, СКС, шафа,
                           ліцензії, ЗІП.
────────────────────────────────────────────────────────────────────────
"""

YES = ("y", "yes", "так", "т")
NO = ("n", "no", "ні", "н")


def _ask(prompt: str, parse: Callable[[str], object], input_fn: Callable[[str], str]) -> object:
    while True:
        raw = input_fn(prompt).strip()
        try:
            return parse(raw)
        except ValueError as err:
            print(f"  {err or 'Некоректне значення.'} Спробуйте ще раз.")


def _int(min_value: int = 0) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        try:
            val = int(raw)
        except ValueError:
            raise ValueError("Помилка: введіть ціле число.") from None
        if val < min_value:
            raise ValueError(f"Значення має бути ≥ {min_value}.")
        return val

    return parse


def _yes_no(default: bool | None = False) -> Callable[[str], bool]:
    def parse(raw: str) -> bool:
        low = raw.lower()
        if low in YES:
            return True
        if low in NO:
            return False
        if low == "" and default is not None:
            return default
        raise ValueError("Введіть 'y' або 'n'.")

    return parse


def collect_inputs(catalog: Catalog, input_fn: Callable[[str], str] = input) -> SiteInput:
    """Interactive question flow (same order as the prototype, plus the PSU question)."""
    t = Translator("uk")
    print(
        "\n--- Режим розрахунку ---\n  1) Швидкий     — тільки обладнання (BoM)\n"
        "  2) Розширений  — BoM + IP-план, СКС, шафа, ліцензії, ЗІП"
    )

    def mode(raw: str) -> str:
        if raw in ("1", "2"):
            return "quick" if raw == "1" else "extended"
        raise ValueError("Введіть 1 або 2.")

    site_mode = _ask("Ваш вибір: ", mode, input_fn)
    sockets = _ask("\nВведіть кількість Ethernet розеток (Access): ", _int(), input_fn)
    cameras = _ask("Введіть кількість камер (CCTV): ", _int(), input_fn)

    print("\n--- Точки доступу (Wi-Fi) ---")
    zones = list(catalog.ap_zones.items())
    n_groups = _ask("Скільки типів зон розміщення AP на локації? (напр. офіс + вулиця = 2): ", _int(), input_fn)
    groups: list[ApGroup] = []
    for i in range(int(n_groups)):  # type: ignore[call-overload]
        print(f"\nЗона #{i + 1}:\nОберіть тип розміщення:")
        for idx, (_, zone) in enumerate(zones, start=1):
            print(f"  {idx}) {t.pick(zone.label)} — {zone.model}")

        def zone_choice(raw: str) -> str:
            if raw.isdigit() and 1 <= int(raw) <= len(zones):
                return zones[int(raw) - 1][0]
            raise ValueError("Немає такого варіанту.")

        zone_key = _ask("Ваш вибір: ", zone_choice, input_fn)
        qty = _ask("  Кількість AP цього типу: ", _int(1), input_fn)
        groups.append(ApGroup(zone=str(zone_key), qty=int(qty)))  # type: ignore[call-overload]

    wifi_clients = None
    if site_mode == "extended":
        print("\n--- Дані для IP-плану ---")
        wifi_clients = (
            _ask("Очікувана кількість ОДНОЧАСНИХ Wi-Fi клієнтів (0 = оцінити автоматично): ", _int(), input_fn) or None
        )

    print("\n--- Критичність локації ---")
    for key, tier in catalog.tiers.items():
        print(f"  {key}) {t.pick(tier.label)} ({t.pick(tier.description)})")

    def tier_choice(raw: str) -> int:
        if raw in catalog.tiers:
            return int(raw)
        raise ValueError("Немає такого варіанту.")

    tier_id = int(_ask("Ваш вибір: ", tier_choice, input_fn))  # type: ignore[call-overload]
    suggested = catalog.tier(tier_id).dual_psu
    hint = "Y/n" if suggested else "y/N"
    redundant_psu = _ask(f"Комутатори з двома блоками живлення (hot-swap)? ({hint}): ", _yes_no(suggested), input_fn)
    agg = input_fn("Чи потрібен окремий комутатор агрегації (ядро)? (y/n, Enter=авто): ").strip().lower()
    reserve = _ask("Додати 20% резерву ємності для майбутнього масштабування? (y/n): ", _yes_no(False), input_fn)
    name = input_fn("Назва локації (для назви файлу, можна залишити пустим): ").strip() or "Location"
    return SiteInput.model_validate(
        {
            "name": name,
            "mode": site_mode,
            "sockets": sockets,
            "cameras": cameras,
            "ap_groups": [g.model_dump() for g in groups],
            "tier": tier_id,
            "aggregation": agg,
            "reserve": reserve,
            "redundant_psu": redundant_psu,
            "wifi_clients_expected": wifi_clients,
        }
    )


def print_result(result: SiteResult, lang: str = "uk") -> None:
    t = Translator(lang)
    width = 100
    print("\n" + "═" * width)
    print(f" {result.input.name}")
    print("═" * width)
    for line in result.bom:
        qty = "—" if line.qty is None else str(line.qty)
        print(f" {line.category[:34]:34} {line.model[:24]:24} {qty:>5}")
        reason = line.reason
        while reason:
            print(f"     {reason[: width - 6]}")
            reason = reason[width - 6 :]
    if result.checks:
        print("─" * width)
        icon = {"error": "✖", "warning": "!", "info": "i"}
        for c in result.checks:
            print(f" [{icon[c.severity.value]}] {c.message}")
    if result.ip_plan:
        print("─" * width)
        for s in result.ip_plan.segments:
            extra = f"  {s.network:18} gw {s.gateway:15} {s.dhcp_range}" if s.network else ""
            print(f" VLAN {s.vlan:<4} {s.name[:32]:32} {s.hosts:>5} → /{s.prefix} ({s.capacity}){extra}")
    print("─" * width)
    print(f" {t.t('cli.power', w=round(result.power.total_w), va=result.power.ups_va)}")


def _export(result: SiteResult, catalog: Catalog, xlsx: str | None, pdf: str | None, lang: str) -> None:
    if xlsx:
        from .exporters.xlsx import export_xlsx

        path = export_xlsx(result, xlsx, catalog=catalog, lang=lang)
        print(f"Excel: {path}")
    if pdf:
        from .exporters.pdf import export_pdf

        path = export_pdf(result, pdf, catalog=catalog, lang=lang)
        print(f"PDF: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sitesizer", description="Fortinet location sizing (BoM, IP plan).")
    parser.add_argument("--json", metavar="FILE", help="read SiteInput/project JSON ('-' = stdin) instead of asking")
    parser.add_argument("--out", metavar="FILE", help="write the result as JSON ('-' = stdout)")
    parser.add_argument("--xlsx", metavar="FILE", nargs="?", const="", help="export Excel (default name if empty)")
    parser.add_argument("--pdf", metavar="FILE", nargs="?", const="", help="export PDF report")
    parser.add_argument("--catalog", metavar="FILE", help="use a custom catalog JSON")
    parser.add_argument("--compat", action="store_true", help="prototype-compatible assumptions")
    parser.add_argument("--lang", default="uk", choices=("uk", "en"))
    parser.add_argument("--quiet", action="store_true", help="do not print the BoM table")
    args = parser.parse_args(argv)

    try:
        catalog = load_catalog(args.catalog)
        if args.compat:
            catalog = prototype_compat_catalog(catalog)
    except CatalogError as err:
        print(err, file=sys.stderr)
        return 2

    sites: list[SiteInput]
    if args.json:
        try:
            if args.json == "-":
                raw = json.load(sys.stdin)
                sites = (
                    [SiteInput.model_validate(raw)]
                    if "sites" not in raw
                    else [SiteInput.model_validate(s["input"]) for s in raw["sites"]]
                )
            else:
                sites = [s.input for s in load_project(args.json).sites]
        except (ProjectError, json.JSONDecodeError, ValueError) as err:
            print(f"Помилка вхідних даних: {err}", file=sys.stderr)
            return 2
    else:
        print(MANUAL_UK)
        try:
            sites = [collect_inputs(catalog)]
        except (KeyboardInterrupt, EOFError):
            print("\nСкасовано користувачем.")
            return 1

    results = [size_site(site, catalog, lang=args.lang) for site in sites]
    if not args.quiet and args.out != "-":
        for result in results:
            print_result(result, args.lang)

    if args.out:
        payload = [result_to_dict(r) for r in results]
        text = json.dumps(payload[0] if len(payload) == 1 else payload, ensure_ascii=False, indent=2)
        if args.out == "-":
            print(text)
        else:
            Path(args.out).write_text(text + "\n", encoding="utf-8")

    interactive = not args.json
    for result in results:
        xlsx = args.xlsx
        if xlsx == "" or (xlsx is None and interactive):
            xlsx = default_export_name(result.input.name, "xlsx")
        pdf = args.pdf if args.pdf is None or args.pdf else default_export_name(result.input.name, "pdf", "Report")
        try:
            _export(result, catalog, xlsx, pdf, args.lang)
        except ImportError as err:  # exporters not available in a stripped build
            print(f"Експорт недоступний: {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
