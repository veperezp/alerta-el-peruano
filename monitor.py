"""
Monitor de Normas Legales - El Peruano
----------------------------------------
Revisa la página de Normas Legales de El Peruano (usa el listado que
carga por defecto al abrir la página, sin filtrar por fecha), busca
coincidencias con una lista de palabras clave, y envía una notificación
por Telegram cuando encuentra algo nuevo que no se había notificado antes.

Pensado para ejecutarse cada 5 minutos vía GitHub Actions.
"""

import os
import json
import hashlib
import sys
from playwright.sync_api import sync_playwright
import requests

URL = "https://diariooficial.elperuano.pe/Normas"

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
MAX_TELEGRAM_LEN = 3500

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def normalize(text: str) -> str:
    text = text.lower()
    replacements = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "°": "", "º": ""}
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
        return
    if len(text) > MAX_TELEGRAM_LEN:
        text = text[:MAX_TELEGRAM_LEN] + "\n\n[...mensaje truncado, revisa la web]"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                                     "disable_web_page_preview": False}, timeout=30)
    resp.raise_for_status()


def get_page_lines():
    """Abre la página con un navegador real (headless). NO toca los campos
    de fecha ni el botón Buscar: el listado más reciente ya carga solo por
    defecto al abrir la página, así que solo esperamos a que termine de
    renderizarse y leemos el texto visible."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ))
        page.goto(URL, timeout=90000, wait_until="load")

        # Margen para que el listado por defecto termine de cargar vía AJAX
        page.wait_for_timeout(10000)

        print(f"[diagnóstico] Título de la página: {page.title()}")

        body_text = page.inner_text("body")

        # Evidencia para verificar / depurar
        try:
            page.screenshot(path="debug_screenshot.png", full_page=True)
            print("[diagnóstico] Captura guardada en debug_screenshot.png")
        except Exception as e:
            print(f"[diagnóstico] No se pudo tomar captura: {e}")
        try:
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[diagnóstico] HTML completo guardado en debug_page.html")
        except Exception as e:
            print(f"[diagnóstico] No se pudo guardar HTML: {e}")

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
    print("[diagnóstico] Primeras 25 líneas extraídas:")
    for l in lines[:25]:
        print(f"    {l}")

    new_hits = []
    for line in lines:
        norm = normalize(line)
        if any(kw in norm for kw in KEYWORDS):
            h = hashlib.sha256(norm.encode("utf-8")).hexdigest()
            if h not in seen:
                seen.add(h)
                new_hits.append(line)

    if new_hits:
        message = "📰 Posible(s) norma(s) relevante(s) en El Peruano:\n\n" + "\n\n".join(new_hits[:15])
        message += f"\n\n🔗 {URL}"
        send_telegram(message)
        save_seen(seen)
        print(f"Se enviaron {len(new_hits)} notificación(es) nueva(s).")
    else:
        print("Sin novedades en esta revisión.")


if __name__ == "__main__":
    main()
