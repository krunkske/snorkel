FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# /data is mounted from the host — holds rows.json and the images/ folder
VOLUME ["/data"]

ENV ROWS_FILE=/data/rows.json \
    IMAGES_DIR=/data/images \
    FLASK_ENV=production

EXPOSE 5000

CMD ["python", "app/main.py"]
