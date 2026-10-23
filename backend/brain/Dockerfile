FROM python:3.12-slim
WORKDIR /app
# graphviz  -> `dot` binary for architecture diagrams (Pass 4, fail-soft).
# libreoffice-writer -> `soffice` headless for DOCX->PDF export (Pass 6). It is
#   the only realistic way to preserve DOCX layout/branding. Fail-soft in code,
#   but the binary is required for PDF export to actually produce output.
# fonts-dejavu-core -> a minimal font set so LibreOffice renders text without
#   falling back to unstyled defaults.
# poppler-utils -> `pdftoppm` to rasterise a client RFP/SOW for vision-model
#   reading. Most client tenders arrive as scanned or "Print to PDF" documents
#   with no usable text layer -- the ESNAD SOW that motivated this yielded 468
#   characters across 20 pages, all of them page numbers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        graphviz \
        libreoffice-writer \
        fonts-dejavu-core \
        curl ca-certificates \
        librsvg2-bin \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# D2 renders architecture diagrams with real nested containers (DMZ / secure
# zone / data zone), which Graphviz lays out poorly. Graphviz stays installed as
# the fallback, so a failed D2 fetch never breaks diagram rendering.
# 0.9.0 matches the version diagrams are checked against locally (the solution
# stack uses grid layout).
# The download is checked against the SHA-256 the release publishes for that
# exact file (read from the GitHub release metadata on 2026-10-12). A different
# file under the same name fails the build. Change both sums with D2_VERSION.
ARG D2_VERSION=0.9.0
ARG D2_SHA256_AMD64=5669ddc46b99e942cc96078f4a4e36d5e62103348f4c05179ede27802fdd87a9
ARG D2_SHA256_ARM64=ac2c028697199479acb321db1e3d68caee9f2ba492ed73caa3cd13f3829bf913
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) d2arch=amd64; d2sum="$D2_SHA256_AMD64" ;; \
        arm64) d2arch=arm64; d2sum="$D2_SHA256_ARM64" ;; \
        *) d2arch="" ;; \
    esac; \
    if [ -n "$d2arch" ]; then \
        curl -fsSL "https://github.com/terrastruct/d2/releases/download/v${D2_VERSION}/d2-v${D2_VERSION}-linux-${d2arch}.tar.gz" \
            -o /tmp/d2.tgz \
        && echo "${d2sum}  /tmp/d2.tgz" | sha256sum -c - \
        && tar -xzf /tmp/d2.tgz -C /tmp \
        && install -m 0755 "$(find /tmp -path '*/bin/d2' -type f | head -1)" /usr/local/bin/d2 \
        && rm -rf /tmp/d2.tgz /tmp/d2-v* \
        && d2 --version; \
    else echo "no d2 build for $arch - Graphviz fallback only"; fi
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
# Every module, by glob. This was a hand-kept list, and a module missing from
# it crash-looped the container on import: the project's most repeated mistake.
# tests/ is a directory, so it is not matched.
COPY *.py ./
COPY assets/ ./assets/
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
