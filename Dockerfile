# GenIDtals REST API server.
#
#   docker build -t genidtals .
#   docker run -p 8080:8080 \
#     -e GENIDTALS_API_KEYS="$(genidtals keygen --api-key)" \
#     -e GENIDTALS_KEYS="$(genidtals keygen)" \
#     -v genidtals-data:/data genidtals
#
# Supply keys from your secret manager. Never bake them into the image.
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY genidtals ./genidtals
RUN pip wheel --no-cache-dir --wheel-dir /wheels ".[server]"

FROM python:3.12-slim
RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin genidtals \
    && mkdir -p /data && chown genidtals /data && chmod 700 /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links /wheels genidtals[server] && rm -rf /wheels
USER genidtals
ENV GENIDTALS_HOME=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=4)"
ENTRYPOINT ["genidtals"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
