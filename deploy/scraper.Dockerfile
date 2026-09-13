FROM python:3.12-slim

RUN pip install --no-cache-dir fastapi uvicorn python-jobspy

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN python3 -m playwright install chromium --with-deps 2>/dev/null || true

COPY deploy/scraper_service.py /app/scraper_service.py
WORKDIR /app

EXPOSE 8001
CMD ["uvicorn", "scraper_service:app", "--host", "0.0.0.0", "--port", "8001"]