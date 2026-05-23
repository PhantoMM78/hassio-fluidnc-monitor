ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip jq
RUN pip install websockets paho-mqtt --break-system-packages

COPY run.sh /run.sh
COPY fluidnc_monitor.py /fluidnc_monitor.py
RUN chmod +x /run.sh

CMD ["/run.sh"]
