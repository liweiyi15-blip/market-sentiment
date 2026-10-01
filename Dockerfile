FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py schedule.py state.py delivery.py tasks.py ./
COPY tests ./tests
RUN python -m unittest discover -s tests -q
CMD ["python", "-u", "main.py"]
