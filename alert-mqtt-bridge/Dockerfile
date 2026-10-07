FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
RUN pip install --no-cache-dir paho-mqtt==1.6.1
COPY relay.py /app/relay.py
WORKDIR /app
RUN addgroup -S relay && adduser -S -G relay relay
USER relay
EXPOSE 8095
CMD ["python3", "-u", "relay.py"]
