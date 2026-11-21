FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY gp_os ./gp_os
COPY samples ./samples
COPY sources.json ./
RUN useradd --create-home --uid 10001 app && mkdir /app/data && chown -R app:app /app
USER app
CMD ["python", "-m", "gp_os", "serve", "--host", "0.0.0.0", "--port", "8001"]
