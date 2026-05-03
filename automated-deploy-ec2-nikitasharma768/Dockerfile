FROM python:3.10-slim

WORKDIR /app

# system deps for building wheels (if needed)
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app

EXPOSE 8000

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "app:app"]
