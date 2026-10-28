# Debian packages instead of pip: prebuilt for amd64, arm64 and armv7 (Raspberry Pi),
# the same set install-linux.sh uses on Debian / Raspberry Pi OS.
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pil python3-pymysql ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY qsl_mailer /opt/qsl-mailer/qsl_mailer
RUN printf '#!/bin/sh\nexec python3 -m qsl_mailer "$@"\n' > /usr/local/bin/qsl-mailer \
    && chmod +x /usr/local/bin/qsl-mailer

# /config holds config.ini, state.sqlite, preview/ and an optional background.jpg
WORKDIR /config
ENV PYTHONPATH=/opt/qsl-mailer \
    QSL_MAILER_CONFIG=/config/config.ini \
    PYTHONUNBUFFERED=1
VOLUME /config
ENTRYPOINT ["qsl-mailer"]
CMD ["--loop", "900"]
