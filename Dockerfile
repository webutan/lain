FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create data directory for persistent storage (memos, etc.)
RUN mkdir -p /app/data

COPY bot.py .
COPY kradfile-u .

# Expose the Anki sync API port
EXPOSE 8765

CMD ["python", "bot.py"]
