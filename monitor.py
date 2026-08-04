"""
Monitor de Normas Legales - El Peruano
----------------------------------------
Revisa la página de Normas Legales de El Peruano, busca coincidencias
con una lista de palabras clave, y envía una notificación por Telegram
cuando encuentra algo nuevo que no se había notificado antes.

Esta versión incluye diagnóstico: intenta llenar el rango de fechas y
pulsar "Buscar", y guarda una captura de pantalla + el HTML completo
como evidencia, para poder ajustar los selectores si algo falla.

Pensado para ejecutarse cada 5 minutos vía GitHub Actions.
"""

import os
import re
import json
import hashlib
import sys
import datetime
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
    "combustible",
    "SUNAT",    
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


def try_fill_dates(page):
    """Intenta llenar los campos de fecha con el día de hoy, probando
    varias estrategias porque no conocemos los selectores exactos."""
    today_iso = datetime.date.today().isoformat()          # 2026-08-03
    today_ddmmyyyy = datetime.date.today().strftime("%d/%m/%Y")  # 03/08/2026

    filled_any = False

    # Estrategia 1: inputs nativos type="date"
    try:
        date_inputs = page.locator('input[type="date"]')
        count = date_inputs.count()
        if count > 0:
            for i in range(count):
                date_inputs.nth(i).fill(today_iso)
                filled_any = True
            print(f"[fechas] Rellenados {count} input(s) type=date con {today_iso}")
    except Exception as e:
        print(f"[fechas] Estrategia type=date falló: {e}")

    # Estrategia 2: inputs con id/name/placeholder que mencionen desde/hasta/fecha
    if not filled_any:
        for kw in ["desde", "hasta", "fecha"]:
            for attr in ["id", "name", "placeholder"]:
                try:
                    sel = f'input[{attr}*="{kw}" i]'
                    loc = page.locator(sel)
                    c = loc.count()
                    if c > 0:
                        for i in range(c):
                            loc.nth(i).fill(today_ddmmyyyy)
                            filled_any = True
                        print(f"[fechas] Rellenado(s) {c} input(s) via {sel} con {today_ddmmyyyy}")
                except Exception:
                    pass

    return filled_any


def try_click_buscar(page):
    """Intenta pulsar un botón/enlace de 'Buscar' probando varias formas."""
    strategies = [
        lambda: page.get_by_role("button", name=re.compile("buscar", re.IGNORECASE)).first.click(timeout=4000),
        lambda: page.get_by_role("link", name=re.compile("buscar", re.IGNORECASE)).first.click(timeout=4000),
        lambda: page.locator('text=/buscar/i').first.click(timeout=4000),
        lambda: page.locator('input[type="submit"]').first.click(timeout=4000),
        lambda: page.locator('button').first.click(timeout=4000),
    ]
    for i, strat in enumerate(strategies):
        try:
            strat()
            print(f"[buscar] Estrategia {i+1} de clic funcionó.")
            return True
        except Exception:
            continue
    print("[buscar] Ninguna estrategia de clic en 'Buscar' funcionó.")
    return False


def describe_page_elements(page):
    """Imprime en el log qué inputs y botones detecta, para poder
    ajustar los selectores mirando el log si hace falta."""
    try:
        inputs = page.locator("input")
        n = inputs.count()
        print(f"[diagnóstico] Se encontraron {n} elemento(s) <input> en la página:")
        for i in range(min(n, 20)):
            el = inputs.nth(i)
            try:
                attrs = el.evaluate(
                    "e => ({id: e.id, name: e.name, type: e.type, placeholder: e.placeholder})"
                )
                print(f"    input #{i}: {attrs}")
            except Exception:
                pass
    except Exception as e:
        print(f"[diagnóstico] No se pudo listar inputs: {e}")

    try:
        buttons = page.locator("button, input[type=submit], a")
        n = buttons.count()
        texts = []
        for i in range(min(n, 40)):
            try:
                t = buttons.nth(i).inner_text(timeout=500).strip()
                if t:
                    texts.append(t)
            except Exception:
                pass
        print(f"[diagnóstico] Textos de botones/enlaces visibles (primeros 40): {texts}")
    except Exception as e:
        print(f"[diagnóstico] No se pudo listar botones: {e}")


def get_page_lines():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ))
        page.goto(URL, timeout=90000, wait_until="load")
        page.wait_for_timeout(3000)

        print(f"[diagnóstico] Título de la página: {page.title()}")
        describe_page_elements(page)

        try_fill_dates(page)
        try_click_buscar(page)

        page.wait_for_timeout(10000)  # margen para que responda el AJAX

        body_text = page.inner_text("body")

        # Evidencia para depurar si aún no funciona
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
