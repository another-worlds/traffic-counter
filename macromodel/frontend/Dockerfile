# macromodel-ui: Solara server rendering the ipyleaflet map app.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8866
CMD ["solara", "run", "sol_app.py", "--host", "0.0.0.0", "--port", "8866"]
