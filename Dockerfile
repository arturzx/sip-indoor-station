FROM debian:trixie-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    LISTEN_ADDRESS=0.0.0.0

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-gi python3-aiohttp \
        gir1.2-gstreamer-1.0 gir1.2-gst-plugins-bad-1.0 \
        gstreamer1.0-nice \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data

WORKDIR /app
COPY src ./src

VOLUME /data
EXPOSE 8080/tcp 5060/udp
CMD ["python3", "-m", "sip_indoor_station"]
