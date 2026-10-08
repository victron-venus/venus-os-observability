FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72
RUN pip install --no-cache-dir paho-mqtt==1.6.1
COPY relay.py /app/relay.py
WORKDIR /app
RUN addgroup -S relay && adduser -S -G relay relay
USER relay
EXPOSE 8095
CMD ["python3", "-u", "relay.py"]
