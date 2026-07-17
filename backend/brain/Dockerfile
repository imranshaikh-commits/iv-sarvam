FROM python:3.12-slim
WORKDIR /app
# graphviz provides the `dot` binary used to render architecture diagrams (Pass 4).
# Rendering is fail-soft in code, but the binary is required for diagrams to embed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py proposal_templates.py document_engine.py intake_template.py supabase_client.py branding.py diagram_engine.py .
COPY assets/ ./assets/
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
