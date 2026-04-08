FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/app/

VOLUME ["/data"]

ENV ROWS_FILE=/data/rows.json \
    IMAGES_DIR=/data/images \
    STATE_FILE=/data/state.json

EXPOSE 5000

CMD ["python", "/app/app/main.py"]
