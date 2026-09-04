#!/usr/bin/env python3
"""Build the Homarr board config that hosts the reference-matched overview."""

import json
from pathlib import Path

ROOT = Path("/home/juyoungoh/dashcraft")
OUTPUT = ROOT / "homarr-nenrikido-config.json"
HOST = "10.1.188.19"

CUSTOM_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, #__next { min-height: 100%; }
body {
  font-family: Inter, ui-sans-serif, system-ui, sans-serif;
  background-position: center !important;
  background-size: cover !important;
  background-attachment: fixed !important;
}
body::before {
  content: '';
  position: fixed;
  inset: 0;
  z-index: -1;
  background: linear-gradient(90deg, rgba(21,25,11,.46), rgba(47,37,20,.48));
  backdrop-filter: blur(10px) saturate(.72);
}
.mantine-AppShell-header {
  min-height: 46px !important;
  height: 46px !important;
  background: rgba(26,27,15,.58) !important;
  border-bottom: 1px solid rgba(235,239,214,.08) !important;
  backdrop-filter: blur(14px);
}
.mantine-AppShell-main { padding: 54px 12px 12px !important; }
.grid-stack { margin: 0 auto !important; max-width: 1420px !important; }
.grid-stack-item-content,
.mantine-Paper-root:has(iframe) {
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  overflow: visible !important;
}
.mantine-Paper-root:has(iframe) { padding: 0 !important; }
iframe { background: transparent !important; border: 0 !important; }
.mantine-Group-root img[alt='logo'] { display: none !important; }
@media (max-width: 600px) {
  .mantine-AppShell-main { padding: 50px 6px 8px !important; }
}
""".strip()

config = {
    "schemaVersion": 2,
    "configProperties": {"name": "default"},
    "categories": [],
    "wrappers": [{"id": "default", "position": 0}],
    "apps": [],
    "widgets": [
        {
            "id": "iframe-widget",
            "type": "iframe",
            "properties": {
                "embedUrl": f"http://{HOST}:7878/overview",
                "allowFullScreen": False,
                "allowScrolling": True,
                "allowTransparency": True,
                "allowPayment": False,
                "allowAutoPlay": False,
                "allowMicrophone": False,
                "allowCamera": False,
                "allowGeolocation": False,
            },
            "area": {"type": "wrapper", "properties": {"id": "default"}},
            "shape": {
                "lg": {"location": {"x": 0, "y": 0}, "size": {"width": 12, "height": 8}},
                "md": {"location": {"x": 0, "y": 0}, "size": {"width": 8, "height": 9}},
                "sm": {"location": {"x": 0, "y": 0}, "size": {"width": 3, "height": 16}},
            },
        }
    ],
    "settings": {
        "common": {"searchEngine": {"type": "google", "properties": {}}},
        "customization": {
            "layout": {
                "enabledLeftSidebar": False,
                "enabledRightSidebar": False,
                "enabledDocker": False,
                "enabledPing": False,
                "enabledSearchbar": True,
            },
            "pageTitle": "Home",
            "logoImageUrl": "",
            "faviconUrl": "",
            "backgroundImageUrl": f"http://{HOST}:7878/assets/background.jpg",
            "customCss": CUSTOM_CSS,
            "colors": {"primary": "lime", "secondary": "green", "shade": 7},
            "appOpacity": 0,
            "gridstack": {
                "columnCountSmall": 3,
                "columnCountMedium": 8,
                "columnCountLarge": 12,
            },
        },
        "access": {"allowGuests": True},
    },
}

OUTPUT.write_text(json.dumps(config, indent=2) + "\n")
print(OUTPUT)
