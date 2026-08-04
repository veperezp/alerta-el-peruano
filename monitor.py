"""
Monitor de Normas Legales - El Peruano
----------------------------------------
Revisa la página de Normas Legales de El Peruano, busca coincidencias
con una lista de palabras clave, y envía una notificación por Telegram
cuando encuentra algo nuevo que no se había notificado antes.

Pensado para ejecutarse cada 5 minutos vía GitHub Actions.
"""

import os
import re
import json
import hashlib
import sys
from playwright.sync_api import sync_playwright
import requests

URL = "https://diariooficial.elperuano.pe/Normas"

# Palabras/frases clave a monitorear (en minúsculas, sin necesidad de tildes
# porque el texto se normaliza antes de comparar)
KEYWORDS = [
    "sunat",
    "bienes fiscalizados",
    "insumos quimicos",
    "insumo quimico",
    "contrato administrativo de servicios",
    "regimen cas",
    "decreto legislativo 1057",
    "decreto legislativo n 1057",
    "ley 27444",
    "ley n 27444",
    "ley del procedimiento administrativo general",
    "hidrocarburos",
]

STATE_FILE = "seen.json"
MAX_TELEGRAM_LEN = 3500  # margen bajo el límite real de Telegram (4096)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para que la búsqueda sea más tolerante."""
    text = text.lower()
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "ñ": "n", "°": "", "º": "",
    }
    for a, b in replacements.items():
        text = text.replace(a, b)
    return text


def load_seen() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                return set()
    return set()


def save_seen(seen: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def send_telegram(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID como variables de entorno.")
        sys.exit(1)

    if len(text) > MAX_TELEGRAM_LEN:
        text = text[:MAX_TELEGRAM_LEN] + "\n\n[...mensaje truncado, revisa la web]"

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    resp.raise_for_status()


def get_page_lines() -> list:
    """Abre la página con un navegador real (headless) para que el
    JavaScript termine de cargar el listado, y devuelve el texto visible
    dividido en líneas."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        )
        page.goto(URL, timeout=60000, wait_until="load")

        # Intento best-effort: si hay un botón "Buscar" para disparar la
        # carga del listado del día, lo pulsa. Si no lo encuentra, sigue
        # igual (no debe interrumpir la ejecución).
        try:
            page.get_by_role("button", name=re.compile("buscar", re.IGNORECASE)).first.click(timeout=3000)
        except Exception:
            pass

        # Margen extra para que la llamada AJAX del listado termine de responder
        page.wait_for_timeout(8000)

        body_text = page.inner_text("body")
        browser.close()

    lines = [l.strip() for l in body_text.split("\n") if l.strip()]
    return lines


def main():
    seen = load_seen()

    try:
        lines = get_page_lines()
    except Exception as e:
        print(f"Error cargando la página: {e}")
        sys.exit(1)

    print(f"Líneas de texto extraídas de la página: {len(lines)}")

    new_hits = []
    for line in lines:
        norm = normalize(line)
        if any(kw in norm for kw in KEYWORDS):
            h = hashlib.sha256(norm.encode("utf-8")).hexdigest()
            if h not in seen:
                seen.add(h)
                new_hits.append(line)

    if new_hits:
        message = "📰 Posible(s) norma(s) relevante(s) en El Peruano:\n\n"
        message += "\n\n".join(new_hits[:15])
        message += f"\n\n🔗 {URL}"
        send_telegram(message)
        save_seen(seen)
        print(f"Se enviaron {len(new_hits)} notificación(es) nueva(s).")
    else:
        print("Sin novedades en esta revisión.")


if __name__ == "__main__":
    main()
