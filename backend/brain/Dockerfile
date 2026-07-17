FROM python:3.12-slim
WORKDIR /app
# graphviz  -> `dot` binary for architecture diagrams (Pass 4, fail-soft).
# libreoffice-writer -> `soffice` headless for DOCX->PDF export (Pass 6). It is
#   the only realistic way to preserve DOCX layout/branding. Fail-soft in code,
#   but the binary is required for PDF export to actually produce output.
# fonts-dejavu-core -> a minimal font set so LibreOffice renders text without
#   falling back to unstyled defaults.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        graphviz \
        libreoffice-writer \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py proposal_templates.py document_engine.py intake_template.py supabase_client.py branding.py diagram_engine.py export_engine.py .
COPY assets/ ./assets/
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
