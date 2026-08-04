name: Monitor El Peruano

on:
  schedule:
    - cron: '*/5 * * * *'
  workflow_dispatch: {}

permissions:
  contents: write

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          playwright install --with-deps chromium

      - name: Run monitor script
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python monitor.py

      - name: Subir evidencia de depuración
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: debug-evidence
          path: |
            debug_screenshot.png
            debug_page.html
          retention-days: 3
          if-no-files-found: ignore

      - name: Commit updated state (si hubo novedades)
        run: |
          git config user.name "monitor-bot"
          git config user.email "monitor-bot@users.noreply.github.com"
          git add seen.json
          git diff --staged --quiet || git commit -m "Actualiza registro de normas detectadas"
          git push
