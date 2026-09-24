FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

USER 65532:65532
STOPSIGNAL SIGINT
CMD ["python", "app.py"]
