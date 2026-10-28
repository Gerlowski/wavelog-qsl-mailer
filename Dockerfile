FROM python:3.12-slim

WORKDIR /src
COPY pyproject.toml README.md ./
COPY qsl_mailer ./qsl_mailer
RUN pip install --no-cache-dir . && rm -rf /src

# /config holds config.ini, state.sqlite, preview/ and an optional background.jpg
WORKDIR /config
ENV QSL_MAILER_CONFIG=/config/config.ini \
    PYTHONUNBUFFERED=1
VOLUME /config
ENTRYPOINT ["qsl-mailer"]
CMD ["--loop", "900"]
