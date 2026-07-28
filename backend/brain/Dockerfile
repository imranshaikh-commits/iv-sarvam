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
        curl ca-certificates \
        libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# D2 renders architecture diagrams with real nested containers (DMZ / secure
# zone / data zone), which Graphviz lays out poorly. Graphviz stays installed as
# the fallback, so a failed D2 fetch never breaks diagram rendering.
ARG D2_VERSION=0.6.9
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in amd64) d2arch=amd64 ;; arm64) d2arch=arm64 ;; *) d2arch="" ;; esac; \
    if [ -n "$d2arch" ]; then \
        curl -fsSL "https://github.com/terrastruct/d2/releases/download/v${D2_VERSION}/d2-v${D2_VERSION}-linux-${d2arch}.tar.gz" \
            -o /tmp/d2.tgz \
        && tar -xzf /tmp/d2.tgz -C /tmp \
        && install -m 0755 "/tmp/d2-v${D2_VERSION}/bin/d2" /usr/local/bin/d2 \
        && rm -rf /tmp/d2.tgz "/tmp/d2-v${D2_VERSION}" \
        && d2 --version; \
    else echo "no d2 build for $arch - Graphviz fallback only"; fi
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py proposal_templates.py document_engine.py intake_template.py supabase_client.py branding.py diagram_engine.py export_engine.py chat_state.py .
COPY assets/ ./assets/
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
