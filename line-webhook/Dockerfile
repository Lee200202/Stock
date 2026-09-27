# LINE Webhook 轉送服務（v73）。部署步驟見 docs/0927-line/部署與驗證.md。
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py ./
COPY static ./static
USER nobody
# 一個 worker；請求完成後沒有待跑的背景執行緒，交接由 Cloud Tasks 保存。
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
