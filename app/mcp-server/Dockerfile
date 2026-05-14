FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV DISCVAULT_API=http://backend:5000

EXPOSE 6090

# Default: SSE mode on port 6090. Override with CMD for stdio.
ENTRYPOINT ["python", "server.py"]
CMD ["--http", "--port", "6090"]
