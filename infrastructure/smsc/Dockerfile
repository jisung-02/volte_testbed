FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY smsc.py sip.py tpdu.py ./

ENV PYTHONUNBUFFERED=1
EXPOSE 5060/udp

CMD ["python", "smsc.py"]
