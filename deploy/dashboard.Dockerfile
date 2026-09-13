FROM python:3.12-slim
RUN pip install --no-cache-dir google-api-python-client google-auth
ENV HOME=/dash
WORKDIR /dash
COPY deploy/dashboard_server.py /dash/server.py
EXPOSE 8080
CMD ["python3", "server.py"]