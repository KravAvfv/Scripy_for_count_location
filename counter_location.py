"""
Location Equipment Counter / BoM Generator
===========================================

Генерує специфікацію (BoM) мережевого обладнання для локації:
Wi-Fi комутатори, комутатори доступу, комутатори для камер,
точки доступу, комутатор агрегації та Firewall — з обґрунтуванням
вибору кожної позиції та експортом у форматований Excel-файл.

Архітектура:
    - CATALOG            — єдине місце з моделями/характеристиками обладнання
    - print_manual()      — пояснення логіки, друкується на старті скрипта
    - collect_inputs()    — інтерактивний збір вхідних даних від користувача
    - compute_topology()  — рахує кількість світчів/FW один раз (спільно для BoM та IP-плану)
    - build_bom()         — чиста бізнес-логіка формування специфікації
    - build_ip_plan()     — (розширений режим) орієнтовний розрахунок підмереж
    - export_to_excel()   — форматований вивід у .xlsx (1 або 2 листи)
"""

import math
import os
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from openpyxl import load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

# ============================================================
# 1. КАТАЛОГ ОБЛАДНАННЯ
#    ⚠️ Перевірте значення позначені TODO за вашими нотатками —
#    рукописний текст на дошці місцями важко розшифрувати однозначно.
# ============================================================

PORTS_48_USABLE = 48
PORTS_24_USABLE = 24

CATALOG = {
    "agg_switch": {"model": "FS-1024E", "ports": 24, "uplink_10g": True},

    # Кожен список відсортований: спочатку "преміум"/hot-swap варіант з порогом кількості,
    # потім базовий варіант (min_qty = 0) як fallback.
    "wifi_switch": [
        {"model": "FS-624F-FPOE", "ports": 24, "hot_swap_psu": True, "min_qty": 25},  # TODO: перевірити поріг "25х"
        {"model": "FS-124G-FPOE", "ports": 24, "hot_swap_psu": False, "min_qty": 0},
    ],
    "access_switch": [
        {"model": "FS-448E", "ports": 48, "hot_swap_psu": True, "min_qty": 25},  # TODO: перевірити поріг
        {"model": "FS-148F", "ports": 48, "hot_swap_psu": False, "min_qty": 0},
    ],
    "camera_switch": [
        {"model": "FS-448E-POE", "ports": 48, "poe_budget_w": 740, "hot_swap_psu": True, "min_qty": 25},
        {"model": "FS-148F-FPOE", "ports": 48, "poe_budget_w": 740, "hot_swap_psu": False, "min_qty": 0},
    ],

    # Орієнтовне споживання типової PoE-камери (для перевірки бюджету).
    "camera_avg_watt": 15,

    "access_points": {
        "1": {"model": "FAP-221K", "label": "3D-ферма (стелажі)"},
        "2": {"model": "FAP-231K", "label": "Коридори"},
        "3": {"model": "FAP-241K", "label": "Мала щільність користувачів (<20 на AP)"},
        "4": {"model": "FAP-441K", "label": "Велика щільність користувачів (>20 на AP)"},
        "5": {"model": "FAP-234G", "label": "Вулиця / зовнішнє розміщення"},
    },

    "firewalls": [
        {"model": "FG-80F", "max_switches": 16, "has_10g": False},
        {"model": "FG-120G", "max_switches": 32, "has_10g": True},
        {"model": "FG-200G", "max_switches": 64, "has_10g": True},
        {"model": "FG-400G", "max_switches": 128, "has_10g": True},
    ],
}

# Орієнтовне споживання (Вт) для довідкового розрахунку UPS. Груба оцінка —
# не заміняє реальний power budget з даташитів, лише порядок величини.
POWER_ESTIMATE_W = {
    "switch_base": 50,      # некритичний L2-світч без PoE
    "switch_poe": 150,      # базове споживання PoE-світча (без урахування самого PoE-бюджету камер/AP)
    "firewall": 45,
}

# ------------------------------------------------------------
# Рівні критичності локації — визначають рівень резервування.
# ------------------------------------------------------------
CRITICALITY_TIERS = {
    "1": {
        "label": "Критична (ЦОД / головний майданчик, простій неприпустимий)",
        "fw_ha": True,
        "agg_redundant": True,
        "add_ups": True,
        "add_oob": True,
        "sla": "24x7, відновлення ≤4 год, апаратний резерв на майданчику",
    },
    "2": {
        "label": "Висока (важливий регіональний офіс)",
        "fw_ha": False,
        "agg_redundant": False,
        "add_ups": True,
        "add_oob": False,
        "sla": "Наступний робочий день (NBD), холодний резерв на складі",
    },
    "3": {
        "label": "Стандартна (типова філія)",
        "fw_ha": False,
        "agg_redundant": False,
        "add_ups": False,
        "add_oob": False,
        "sla": "Стандартна гарантія виробника",
    },
    "4": {
        "label": "Низька (склад / тимчасовий об'єкт)",
        "fw_ha": False,
        "agg_redundant": False,
        "add_ups": False,
        "add_oob": False,
        "sla": "Best-effort, ремонт за фактом звернення",
    },
}


# ============================================================
# 2. МАНУАЛ — друкується на старті, перед будь-якими питаннями
# ============================================================

def print_manual() -> None:
    manual = """
╔══════════════════════════════════════════════════════════════════════╗
║          BoM-калькулятор мережевого обладнання — як це працює        ║
╚══════════════════════════════════════════════════════════════════════╝

Скрипт задає кілька питань і на основі відповідей рахує, скільки
комутаторів, точок доступу та Firewall потрібно на локацію, а також
формує Excel-файл з обґрунтуванням для замовника.

── ЯК ВПЛИВАЮТЬ ВАШІ ВІДПОВІДІ ────────────────────────────────────────

  Резерв 20% на ріст (y/n)
      "y" → кількість AP / розеток / камер збільшується на 20% ПЕРЕД
            тим, як рахуються комутатори. Це впливає на РЕЗУЛЬТАТ –
            може додати ще один комутатор, якщо впритул не вистачало
            портів.

  Комутатор агрегації / ядро (y / n / Enter)
      "y"     → ядро додається завжди, незалежно від кількості світчів.
      "n"     → ядро НЕ додається взагалі (усі світчі йдуть напряму
                у Firewall — підходить лише для дуже малих локацій).
      Enter   → скрипт вирішує сам: якщо світчів доступу ≥ 3 — ядро
                додається автоматично, інакше ні.

  Критичність локації (1–4)
      Впливає НЕ на кількість портів, а на рівень відмовостійкості:
        1) Критична  → Firewall береться ПАРОЮ (HA), ядро — ПАРОЮ
                        (N+1), додається рядок UPS та рядок резервного
                        каналу управління (OOB, напр. 4G-роутер).
        2) Висока    → додається лише рядок UPS (без дублювання заліза).
        3) Стандартна→ нічого додаткового, звичайна конфігурація.
        4) Низька    → мінімальна конфігурація, без додаткових рядків.

  Режим розрахунку (Швидкий / Розширений)
      Швидкий    → тільки перелік обладнання (BoM).
      Розширений → BoM + окремий лист "IP-план" з орієнтовним розміром
                   підмереж (VLAN Data/Wi-Fi/CCTV/Management) з запасом
                   на ріст. Це ЧЕРНЕТКА для мережевика, не готова
                   адресація — конкретні мережі/VLAN ID проставляєте самі.

────────────────────────────────────────────────────────────────────────
"""
    print(manual)


# ============================================================
# 3. ДОПОМІЖНІ ФУНКЦІЇ ВВОДУ
# ============================================================

def ask_int(prompt: str, min_value: int = 0) -> int:
    while True:
        raw = input(prompt).strip()
        try:
            val = int(raw)
            if val < min_value:
                print(f"  Значення має бути ≥ {min_value}. Спробуйте ще раз.")
                continue
            return val
        except ValueError:
            print("  Помилка: введіть ціле число.")


def ask_yes_no(prompt: str) -> bool:
    while True:
        raw = input(prompt).strip().lower()
        if raw in ("y", "yes", "так", "т"):
            return True
        if raw in ("n", "no", "ні", "н", ""):
            return False
        print("  Введіть 'y' або 'n'.")


def ask_choice(prompt: str, options: Dict[str, dict]) -> str:
    print(prompt)
    for key, opt in options.items():
        print(f"  {key}) {opt['label']} — {opt['model']}")
    while True:
        raw = input("Ваш вибір: ").strip()
        if raw in options:
            return raw
        print("  Немає такого варіанту, спробуйте ще раз.")


def ask_labeled_choice(prompt: str, options: Dict[str, dict]) -> str:
    """Як ask_choice, але для словників без ключа 'model' (напр. рівні критичності)."""
    print(prompt)
    for key, opt in options.items():
        print(f"  {key}) {opt['label']}")
    while True:
        raw = input("Ваш вибір: ").strip()
        if raw in options:
            return raw
        print("  Немає такого варіанту, спробуйте ще раз.")


def ask_mode() -> str:
    print("\n--- Режим розрахунку ---")
    print("  1) Швидкий     — тільки обладнання (BoM)")
    print("  2) Розширений  — обладнання + орієнтовний IP-план (підмережі під VLAN)")
    while True:
        raw = input("Ваш вибір: ").strip()
        if raw == "1":
            return "quick"
        if raw == "2":
            return "extended"
        print("  Введіть 1 або 2.")


def pick_variant(options: List[dict], qty: int) -> dict:
    """Обирає модель за порогом кількості (найвищий min_qty, який ще <= qty)."""
    for opt in sorted(options, key=lambda o: -o["min_qty"]):
        if qty >= opt["min_qty"]:
            return opt
    return options[-1]


def pick_firewall(total_switches: int, requires_10g: bool) -> dict:
    """Обирає модель Firewall за лімітом FortiLink (max_switches) та потребою в 10G."""
    for fw in CATALOG["firewalls"]:
        if requires_10g and not fw["has_10g"]:
            continue
        if total_switches <= fw["max_switches"]:
            return fw
    return {"model": "Потрібна старша модель (FG-600G+)", "max_switches": None, "has_10g": True}


def suggest_subnet(host_count: int, buffer: float = 0.3) -> dict:
    """
    Пропонує розмір підмережі (кількість host-бітів) під задану кількість
    пристроїв із запасом на ріст (за замовчуванням 30%).
    Це орієнтовний розрахунок — не враховує вашу конкретну схему адресації.
    """
    needed = max(host_count, 1)
    needed = math.ceil(needed * (1 + buffer)) + 2  # +2: адреса мережі та broadcast
    bits = 0
    while (2 ** bits) < needed:
        bits += 1
    mask = 32 - bits
    capacity = max(2 ** bits - 2, 0)
    return {"mask": mask, "capacity": capacity}


# ============================================================
# 4. ЗБІР ВХІДНИХ ДАНИХ
# ============================================================

def collect_ap_groups() -> List[dict]:
    """Дозволяє задати кілька зон з різними типами точок доступу за один прогін."""
    groups = []
    print("\n--- Точки доступу (Wi-Fi) ---")
    n_groups = ask_int("Скільки типів зон розміщення AP на локації? (напр. офіс + вулиця = 2): ", min_value=0)
    for i in range(n_groups):
        print(f"\nЗона #{i + 1}:")
        ap_type = ask_choice("Оберіть тип розміщення:", CATALOG["access_points"])
        qty = ask_int("  Кількість AP цього типу: ", min_value=1)
        groups.append({"type_key": ap_type, "qty": qty})
    return groups


def collect_inputs() -> Optional[dict]:
    try:
        mode = ask_mode()

        sockets = ask_int("\nВведіть кількість Ethernet розеток (Access): ")
        cameras = ask_int("Введіть кількість камер (CCTV): ")
        ap_groups = collect_ap_groups()

        wifi_clients_expected = None
        if mode == "extended":
            print("\n--- Дані для IP-плану ---")
            wifi_clients_expected = ask_int(
                "Очікувана кількість ОДНОЧАСНИХ Wi-Fi клієнтів (0 = оцінити автоматично): ",
                min_value=0,
            )

        criticality = ask_labeled_choice("\n--- Критичність локації ---", CRITICALITY_TIERS)
        need_agg_input = input(
            "Чи потрібен окремий комутатор агрегації (ядро)? (y/n, Enter=авто): "
        ).strip().lower()
        reserve = ask_yes_no("Додати 20% резерву ємності для майбутнього масштабування? (y/n): ")
        location_name = input("Назва локації (для назви файлу, можна залишити пустим): ").strip() or "Location"
    except (ValueError, KeyboardInterrupt):
        print("\nПомилка вводу або скасовано користувачем.")
        return None

    total_aps = sum(g["qty"] for g in ap_groups)

    if reserve:
        total_aps = math.ceil(total_aps * 1.2)
        sockets = math.ceil(sockets * 1.2)
        cameras = math.ceil(cameras * 1.2)
        # пропорційно збільшуємо кожну групу AP теж, щоб зберегти співвідношення типів
        for g in ap_groups:
            g["qty"] = math.ceil(g["qty"] * 1.2)
        if wifi_clients_expected:
            wifi_clients_expected = math.ceil(wifi_clients_expected * 1.2)

    return {
        "mode": mode,
        "ap_groups": ap_groups,
        "total_aps": total_aps,
        "sockets": sockets,
        "cameras": cameras,
        "wifi_clients_expected": wifi_clients_expected,
        "need_agg_input": need_agg_input,
        "location_name": location_name,
        "criticality": criticality,
    }


# ============================================================
# 5. ТОПОЛОГІЯ (спільні розрахунки для BoM та IP-плану)
# ============================================================

def compute_topology(inputs: dict) -> dict:
    tier = CRITICALITY_TIERS[inputs["criticality"]]
    total_aps = inputs["total_aps"]
    sockets = inputs["sockets"]
    cameras = inputs["cameras"]

    wifi_count = math.ceil(total_aps / PORTS_24_USABLE) if total_aps > 0 else 0
    access_count = math.ceil(sockets / PORTS_48_USABLE) if sockets > 0 else 0
    camera_count = math.ceil(cameras / PORTS_48_USABLE) if cameras > 0 else 0
    total_access = wifi_count + access_count + camera_count

    agg_needed = (
        inputs["need_agg_input"] == "y"
        or (inputs["need_agg_input"] not in ("y", "n") and total_access >= 3)
    )
    agg_count = (2 if tier["agg_redundant"] else 1) if agg_needed else 0
    total_switches = total_access + agg_count

    requires_10g = agg_count > 0
    fw_count = (2 if tier["fw_ha"] else 1) if total_switches > 0 else 0

    return {
        "tier": tier,
        "wifi_count": wifi_count,
        "access_count": access_count,
        "camera_count": camera_count,
        "total_access": total_access,
        "agg_needed": agg_needed,
        "agg_count": agg_count,
        "total_switches": total_switches,
        "requires_10g": requires_10g,
        "fw_count": fw_count,
    }


# ============================================================
# 6. БІЗНЕС-ЛОГІКА — ФОРМУВАННЯ BOM
# ============================================================

def build_bom(inputs: dict, topo: dict) -> List[List]:
    bom_data: List[List] = []
    tier = topo["tier"]

    # --- AP (кожна зона окремим рядком) ---
    for g in inputs["ap_groups"]:
        ap = CATALOG["access_points"][g["type_key"]]
        bom_data.append([
            f"Точки доступу — {ap['label']}",
            ap["model"],
            g["qty"],
            f"Тип розміщення: {ap['label']}.",
        ])

    # --- Wi-Fi комутатори (живлення AP) ---
    wifi_count = topo["wifi_count"]
    if wifi_count > 0:
        wifi_sw = pick_variant(CATALOG["wifi_switch"], wifi_count)
        note = " (hot-swap PSU для відмовостійкості)" if wifi_sw["hot_swap_psu"] else ""
        bom_data.append([
            "Wi-Fi Комутатори", wifi_sw["model"], wifi_count,
            f"Фізичні порти: {PORTS_24_USABLE} під AP + запас під Uplink на світч{note}.",
        ])

    # --- Access комутатори ---
    access_count = topo["access_count"]
    if access_count > 0:
        access_sw = pick_variant(CATALOG["access_switch"], access_count)
        note = " (hot-swap PSU)" if access_sw["hot_swap_psu"] else ""
        bom_data.append([
            "Користувацькі Комутатори", access_sw["model"], access_count,
            f"Фізичні порти: {PORTS_48_USABLE} на розетку + 10G SFP+ Uplink{note}. L2-рішення без PoE.",
        ])

    # --- Camera комутатори (+ перевірка PoE-бюджету) ---
    camera_count = topo["camera_count"]
    cameras = inputs["cameras"]
    if camera_count > 0:
        cam_sw = pick_variant(CATALOG["camera_switch"], camera_count)
        cams_per_switch = math.ceil(cameras / camera_count)
        est_watt = cams_per_switch * CATALOG["camera_avg_watt"]
        budget = cam_sw["poe_budget_w"]
        poe_note = (
            f"PoE-бюджет {budget} Вт покриває ~{cams_per_switch} камер (оцінка {est_watt} Вт)."
            if est_watt <= budget
            else f"⚠️ Оцінка споживання ({est_watt} Вт) БЛИЗЬКА/ПЕРЕВИЩУЄ бюджет {budget} Вт — перевірте потужність камер."
        )
        bom_data.append([
            "Відеоспостереження", cam_sw["model"], camera_count,
            f"Фізичні порти: {PORTS_48_USABLE} камер на світч. {poe_note}",
        ])

    # --- Агрегація ---
    if topo["agg_needed"]:
        agg_count = topo["agg_count"]
        if tier["agg_redundant"]:
            agg_reason = (
                f"Об'єднання трафіку: {topo['total_access']} комутаторів доступу. "
                "2х FS-1024E у стеку/MC-LAG — N+1 резервування ядра для критичної локації (відмова одного не кладе мережу)."
            )
        else:
            agg_reason = (
                f"Об'єднання трафіку: на локації {topo['total_access']} комутаторів доступу. "
                "Ядро 10G збирає всі магістралі."
            )
        bom_data.append(["Комутатор Агрегації", CATALOG["agg_switch"]["model"], agg_count, agg_reason])

    # --- Firewall ---
    total_switches = topo["total_switches"]
    fw_count = topo["fw_count"]
    if total_switches > 0:
        fw = pick_firewall(total_switches, topo["requires_10g"])
        fw_model = fw["model"]
        if fw["max_switches"] is not None:
            fw_reason = f"Ліміт FortiLink: керує до {fw['max_switches']} світчів (потрібно {total_switches})."
            if topo["requires_10g"]:
                fw_reason += " Модель має 10G SFP+ порти для оптичних магістралей."
        else:
            fw_reason = f"На локації {total_switches} комутаторів — перевищено ліміт наявних моделей."
        if tier["fw_ha"]:
            fw_reason += " HA-пара (Active-Passive) — для критичної локації один Firewall є неприпустимою точкою відмови."
        bom_data.append(["Міжмережевий Екран", fw_model, fw_count, fw_reason])

    # --- Додаткові позиції за рівнем критичності ---
    if tier["add_ups"]:
        est_watt = (
            (wifi_count + access_count) * POWER_ESTIMATE_W["switch_base"]
            + camera_count * POWER_ESTIMATE_W["switch_poe"]
            + topo["agg_count"] * POWER_ESTIMATE_W["switch_base"]
            + fw_count * POWER_ESTIMATE_W["firewall"]
        )
        bom_data.append([
            "Безперебійне живлення",
            "TODO: підібрати за потужністю",
            1,
            f"Орієнтовне навантаження активного обладнання ≈{est_watt} Вт (без PoE-бюджету камер/AP). "
            "Підібрати UPS з запасом ≥30% та часом автономності відповідно до вимог локації.",
        ])

    if tier["add_oob"]:
        bom_data.append([
            "Резервний канал управління (OOB)",
            "TODO: 4G/5G-роутер",
            1,
            "Незалежний канал доступу до Firewall/консолей на випадок відмови основного каналу зв'язку — "
            "критично для дистанційного відновлення без виїзду.",
        ])

    # --- Довідковий рядок з рівнем критичності та SLA (не позиція для закупівлі) ---
    bom_data.append([
        "Довідково: критичність / SLA",
        "—",
        "—",
        f"Рівень: {tier['label']}. Рекомендований SLA підтримки: {tier['sla']}.",
    ])

    return bom_data


# ============================================================
# 7. РОЗШИРЕНИЙ РЕЖИМ — ОРІЄНТОВНИЙ IP-ПЛАН
# ============================================================

def build_ip_plan(inputs: dict, topo: dict) -> List[List]:
    wifi_hosts = inputs["wifi_clients_expected"]
    if not wifi_hosts:
        # Груба оцінка, якщо користувач не знає точно: ~15 клієнтів на AP.
        wifi_hosts = inputs["total_aps"] * 15

    segments = [
        ("VLAN Data (проводові користувачі)", inputs["sockets"]),
        ("VLAN Wi-Fi (клієнтські пристрої)", wifi_hosts),
        ("VLAN CCTV (камери)", inputs["cameras"]),
        (
            "VLAN Management (світчі, FW, AP)",
            topo["total_switches"] + topo["fw_count"] + inputs["total_aps"],
        ),
    ]

    rows = []
    for name, host_count in segments:
        if host_count <= 0:
            continue
        sub = suggest_subnet(host_count)
        rows.append([
            name,
            host_count,
            f"/{sub['mask']}",
            sub["capacity"],
            "Розрахунок із запасом ~30% на ріст. Мережу/VLAN ID підставте під власну схему нумерації.",
        ])
    return rows


# ============================================================
# 8. ПРОСТА СХЕМА ТОПОЛОГІЇ (PNG, вбудовується в Excel)
# ============================================================

_DIAGRAM_NAVY = "#1F4E78"
_DIAGRAM_NAVY_TEXT = "#FFFFFF"
_DIAGRAM_LIGHT = "#EAF0F6"
_DIAGRAM_LIGHT_EDGE = "#B7C4D1"
_DIAGRAM_LIGHT_TEXT = "#1F2D3A"
_DIAGRAM_LINE = "#9AA7B4"
_DIAGRAM_MUTED = "#7C8894"


def _diagram_box(ax, xc, yc, w, h, lines, fill, edge, text_color, fontsize=9.5, weight="bold"):
    b = FancyBboxPatch((xc - w / 2, yc - h / 2), w, h,
                        boxstyle="round,pad=0.02,rounding_size=0.10",
                        linewidth=1.3, edgecolor=edge, facecolor=fill, zorder=2)
    ax.add_patch(b)
    ax.text(xc, yc, "\n".join(lines), ha="center", va="center",
             fontsize=fontsize, color=text_color, weight=weight, linespacing=1.35, zorder=3)


def _diagram_connect(ax, x1, y1, x2, y2):
    ax.plot([x1, x2], [y1, y2], color=_DIAGRAM_LINE, linewidth=1.2, zorder=1)


def build_topology_diagram(inputs: dict, topo: dict, out_path: str) -> Optional[str]:
    """
    Малює просту, мінімалістичну схему топології на основі фактичних
    результатів розрахунку (пропускає категорії з нульовою кількістю).
    Повертає шлях до PNG або None, якщо малювати нічого (немає обладнання).
    """
    tier = topo["tier"]

    # Категорії світчів, які реально присутні в BoM
    switch_layer = []
    if topo["wifi_count"] > 0:
        wifi_sw = pick_variant(CATALOG["wifi_switch"], topo["wifi_count"])
        switch_layer.append(("Wi-Fi КОМУТАТОРИ", f"{wifi_sw['model']} × {topo['wifi_count']}",
                              f"{inputs['total_aps']} точок доступу"))
    if topo["access_count"] > 0:
        access_sw = pick_variant(CATALOG["access_switch"], topo["access_count"])
        switch_layer.append(("КОРИСТУВАЦЬКІ", f"{access_sw['model']} × {topo['access_count']}",
                              f"{inputs['sockets']} розеток"))
    if topo["camera_count"] > 0:
        cam_sw = pick_variant(CATALOG["camera_switch"], topo["camera_count"])
        switch_layer.append(("ВІДЕОСПОСТЕРЕЖЕННЯ", f"{cam_sw['model']} × {topo['camera_count']}",
                              f"{inputs['cameras']} камер"))

    if not switch_layer and topo["fw_count"] == 0:
        return None  # немає обладнання — малювати нічого

    has_core = topo["agg_count"] > 0
    n = max(len(switch_layer), 1)

    # Динамічна висота полотна залежно від того, що показуємо
    fw_y = 0.6
    core_y = 1.9 if has_core else None
    sw_y = (3.15 if has_core else 1.9) if switch_layer else None
    end_y = (sw_y + 0.8) if sw_y else fw_y
    footnote_lines = []
    if tier["add_ups"]:
        footnote_lines.append("+ UPS")
    if tier["add_oob"]:
        footnote_lines.append("+ Резервний канал управління (OOB)")
    foot_y = end_y + 0.6 if footnote_lines else None
    total_h = (foot_y if foot_y else end_y) + 0.55

    fig, ax = plt.subplots(figsize=(9, 9 * total_h / 10), dpi=160)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, total_h)
    ax.axis("off")
    ax.invert_yaxis()

    center_x = 5

    # --- Firewall ---
    if topo["fw_count"] > 0:
        fw = pick_firewall(topo["total_switches"], topo["requires_10g"])
        fw_lines = ["FIREWALL", f"{fw['model']} × {topo['fw_count']}" + (" (HA)" if tier["fw_ha"] else "")]
        _diagram_box(ax, center_x, fw_y, 3.4, 0.8, fw_lines, _DIAGRAM_NAVY, _DIAGRAM_NAVY, _DIAGRAM_NAVY_TEXT, 10)

    # --- Core ---
    if has_core:
        core_lines = ["ЯДРО (АГРЕГАЦІЯ)",
                      f"{CATALOG['agg_switch']['model']} × {topo['agg_count']}" + (" (N+1)" if tier["agg_redundant"] else "")]
        _diagram_box(ax, center_x, core_y, 3.0, 0.75, core_lines, _DIAGRAM_NAVY, _DIAGRAM_NAVY, _DIAGRAM_NAVY_TEXT, 9.5)
        if topo["fw_count"] > 0:
            _diagram_connect(ax, center_x, fw_y + 0.4, center_x, core_y - 0.375)

    # --- Switch layer ---
    if switch_layer:
        parent_y = core_y if has_core else fw_y
        parent_half_h = 0.375 if has_core else 0.4
        positions = [(10 * (i + 1) / (n + 1)) for i in range(n)]
        for xpos, (title, model_line, endpoint) in zip(positions, switch_layer):
            _diagram_box(ax, xpos, sw_y, 2.7, 0.8, [title, model_line], _DIAGRAM_LIGHT, _DIAGRAM_LIGHT_EDGE,
                         _DIAGRAM_LIGHT_TEXT, 8.7)
            if topo["fw_count"] > 0 or has_core:
                _diagram_connect(ax, center_x, parent_y + parent_half_h, xpos, sw_y - 0.4)
            ax.text(xpos, end_y, endpoint, ha="center", va="center", fontsize=8.3, color=_DIAGRAM_MUTED, style="italic")

    # --- Footnote (UPS / OOB) ---
    if footnote_lines:
        ax.text(center_x, foot_y, "   •   ".join(footnote_lines),
                 ha="center", va="center", fontsize=8.2, color=_DIAGRAM_MUTED, style="italic")

    plt.savefig(out_path, facecolor="white", bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return out_path


# ============================================================
# 9. ЕКСПОРТ У EXCEL (з форматуванням, листи BoM / IP-план / Схема)
# ============================================================

def _style_sheet(ws: Worksheet, columns: List[str]) -> None:
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    thin = Side(style="thin", color="B7B7B7")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        cell.border = border

    max_lengths = [len(c) for c in columns]
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(columns)):
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = border
            col_idx = cell.column - 1
            val_len = len(str(cell.value)) if cell.value is not None else 0
            max_lengths[col_idx] = max(max_lengths[col_idx], val_len)

    for i, length in enumerate(max_lengths, start=1):
        width = min(max(length + 2, 12), 90)
        ws.column_dimensions[get_column_letter(i)].width = width

    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 28


def export_to_excel(
    bom_data: List[List],
    location_name: str,
    ip_plan_data: Optional[List[List]] = None,
    diagram_path: Optional[str] = None,
) -> str:
    bom_columns = ["Категорія", "Модель", "Кількість (шт)", "Обґрунтування для Замовника"]
    ip_columns = ["Сегмент / VLAN", "Пристроїв (з запасом)", "Рекомендована маска", "Місткість (хостів)", "Примітка"]

    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_name = "".join(c for c in location_name if c.isalnum() or c in ("-", "_")) or "Location"
    filename = f"BoM_{safe_name}_{date_str}.xlsx"

    df_bom = pd.DataFrame(bom_data, columns=bom_columns)
    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        df_bom.to_excel(writer, index=False, sheet_name="BoM")
        if ip_plan_data:
            df_ip = pd.DataFrame(ip_plan_data, columns=ip_columns)
            df_ip.to_excel(writer, index=False, sheet_name="IP-план")

    wb = load_workbook(filename)
    _style_sheet(wb["BoM"], bom_columns)
    if ip_plan_data:
        _style_sheet(wb["IP-план"], ip_columns)

    if diagram_path and os.path.exists(diagram_path):
        ws_diagram = wb.create_sheet("Схема")
        ws_diagram.sheet_view.showGridLines = False
        img = XLImage(diagram_path)
        # Трохи зменшуємо, щоб схема відкривалась одразу видимою на екрані, без прокрутки.
        scale = min(1.0, 900 / img.width)
        img.width = int(img.width * scale)
        img.height = int(img.height * scale)
        ws_diagram.add_image(img, "A1")

    wb.save(filename)
    return os.path.abspath(filename)


# ============================================================
# 10. MAIN
# ============================================================

def generate_user_friendly_bom():
    print_manual()

    inputs = collect_inputs()
    if inputs is None:
        return

    topo = compute_topology(inputs)
    bom_data = build_bom(inputs, topo)
    if not bom_data:
        print("\nЗа введеними даними обладнання не потрібне.")
        return

    ip_plan_data = build_ip_plan(inputs, topo) if inputs["mode"] == "extended" else None

    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_name = "".join(c for c in inputs["location_name"] if c.isalnum() or c in ("-", "_")) or "Location"
    diagram_path = build_topology_diagram(inputs, topo, f"_diagram_{safe_name}_{date_str}.png")

    path = export_to_excel(bom_data, inputs["location_name"], ip_plan_data, diagram_path)

    # Тимчасовий PNG вбудований в Excel — окремий файл більше не потрібен.
    if diagram_path and os.path.exists(diagram_path):
        os.remove(diagram_path)

    print(f"\nГотово! Згенеровано файл специфікації для «{inputs['location_name']}».")
    sheets = ["BoM"] + (["IP-план"] if ip_plan_data else []) + (["Схема"] if diagram_path else [])
    print(f"Файл містить листи: {', '.join(sheets)}.")
    print(f"Шлях до файлу: {path}")


if __name__ == "__main__":
    generate_user_friendly_bom()
