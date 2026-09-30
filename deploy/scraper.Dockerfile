FROM python:3.12-slim

RUN pip install --no-cache-dir fastapi uvicorn python-jobspy google-auth playwright

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
# Full Chromium, headless shell skipped: naukri's Akamai wall returns 403 for
# the headless-shell fingerprint and 200 for this one (checked 2026-09-26).
RUN python3 -m playwright install --with-deps --no-shell chromium

COPY deploy/scraper_service.py /app/scraper_service.py
WORKDIR /app

EXPOSE 8001
CMD ["uvicorn", "scraper_service:app", "--host", "0.0.0.0", "--port", "8001"]