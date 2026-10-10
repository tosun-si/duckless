# The DuckLess runner plus Soda v4 and its DuckDB connector. Build it, push it to a registry the
# runner account can read, and run it with `duckless exec`.
FROM ghcr.io/astral-sh/uv:0.8 AS uv

FROM ghcr.io/tosun-si/duckless-runner:0.4.1
USER root
COPY --from=uv /uv /usr/local/bin/uv
# Same DuckDB as the runner: soda-duckdb accepts it, and the baked extensions stay compatible.
RUN uv pip install --python /usr/local/src/app/.venv/bin/python --no-cache "soda-duckdb==4.26.0" "duckdb==1.5.6" \
 && rm /usr/local/bin/uv
COPY --chown=app:app verify.py /usr/local/src/app/verify.py
USER app
