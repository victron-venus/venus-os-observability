FROM python:3.12-alpine
RUN pip install --no-cache-dir paho-mqtt==1.6.1
COPY relay.py /app/relay.py
WORKDIR /app
RUN addgroup -S relay && adduser -S -G relay relay
USER relay
EXPOSE 8095
CMD ["python3", "-u", "relay.py"]
