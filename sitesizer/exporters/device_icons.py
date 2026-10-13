"""Network device figures of the topology (Cisco-style, like the hand-drawn schemes): a switch is a blue
box with arrows on top, the core a darker one with arrows both ways, a FortiGate a brick wall, WAN a cloud.

Plain SVG, so that the app (``QSvgRenderer``) and the draw.io file (an image in the node's style)
show exactly the same figure.
"""

from __future__ import annotations

import base64

ICON_W = 44.0
ICON_H = 30.0

_ARROWS = (
    '<path d="M21 6.6h17" stroke="#fff" stroke-width="1.6"/><path d="M41 6.6l-4-2.2v4.4z" fill="#fff"/>'
    '<path d="M37 10.6h-17" stroke="#fff" stroke-width="1.6"/><path d="M16.5 10.6l4-2.2v4.4z" fill="#fff"/>'
)


def _box(top: str, front: str, side: str, extra: str = "") -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 40" width="64" height="40">'
        f'<path d="M2 15 L18 3 L62 3 L46 15 Z" fill="{top}" stroke="#fff" stroke-width="0.8"/>'
        f'<path d="M2 15 L46 15 L46 37 L2 37 Z" fill="{front}" stroke="#fff" stroke-width="0.8"/>'
        f'<path d="M46 15 L62 3 L62 25 L46 37 Z" fill="{side}" stroke="#fff" stroke-width="0.8"/>'
        f"{_ARROWS}{extra}</svg>"
    )


SWITCH = _box("#5B8FC7", "#1F5A96", "#164577")
CORE = _box(
    "#3D6EA6",
    "#163A5A",
    "#0F2A43",
    # a layer-3 switch: arrows up / down on the front as well
    '<path d="M24 34V21" stroke="#fff" stroke-width="1.6"/><path d="M24 18.5l-2.4 4h4.8z" fill="#fff"/>'
    '<path d="M31 19v13" stroke="#fff" stroke-width="1.6"/><path d="M31 35l-2.4-4h4.8z" fill="#fff"/>',
)
FIREWALL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 40" width="64" height="40">'
    '<path d="M2 13 L14 4 L62 4 L50 13 Z" fill="#E06B5F" stroke="#fff" stroke-width="0.8"/>'
    '<path d="M50 13 L62 4 L62 28 L50 37 Z" fill="#8E1B1B" stroke="#fff" stroke-width="0.8"/>'
    '<rect x="2" y="13" width="48" height="24" fill="#C62828" stroke="#fff" stroke-width="0.8"/>'
    '<g stroke="#fff" stroke-width="1.2">'
    '<path d="M2 21h48M2 29h48"/>'
    '<path d="M14 13v8M30 13v8M46 13v8M8 21v8M22 21v8M38 21v8M14 29v8M30 29v8M46 29v8"/>'
    "</g></svg>"
)
CLOUD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 40" width="64" height="40">'
    '<path d="M16 34 C6 34 4 24 12 21 C10 12 22 8 27 14 C30 5 46 6 46 16 C56 15 60 26 52 31 '
    'C50 34 48 34 46 34 Z" fill="#E9EEF3" stroke="#7C8894" stroke-width="1.6"/>'
    "</svg>"
)

ICONS = {
    "wan": CLOUD,
    "firewall": FIREWALL,
    "core_switch": CORE,
    "wifi_switch": SWITCH,
    "camera_switch": SWITCH,
    "access_switch": SWITCH,
}


def icon_svg(kind: str) -> str:
    return ICONS.get(kind, SWITCH)


def drawio_image(kind: str) -> str:
    """The figure as draw.io wants an image in a style: ``data:image/svg+xml,<base64>`` (no ``;base64``)."""
    return "data:image/svg+xml," + base64.b64encode(icon_svg(kind).encode()).decode()
